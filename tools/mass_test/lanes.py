# -*- coding: utf-8 -*-
"""测试场景库：每个 lane 是一段真实用户路径的 HTTP 驱动。

所有请求经 MassClient（实时日志+限流+质检）；断言失败/异常进问题台账，
不中断 lane（尽量多收集问题），但 P0 级阻断时提前返回。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from .client import BRIDGE_DUMMY_KEY, MassClient

PROVIDER = "custom"


class CheckCollector:
    """lane 内断言收集器：ok/fail 都记录，fail 进台账。"""

    def __init__(self, client: MassClient, ledger: Any, lane: str):
        self.client = client
        self.ledger = ledger
        self.lane = lane
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.failures: list[str] = []
        self.skips: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "", severity: str = "P1") -> bool:
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(f"{name}: {detail}")
            self.ledger.report(severity, name, lane=self.lane, where=detail)
        return ok

    def skip(self, name: str, reason: str) -> None:
        """前置不满足：显式跳过并落台账，不得计入 passed，也不能静默消失。"""
        self.skipped += 1
        self.skips.append(f"{name}: {reason}")
        self.ledger.report("P3", f"SKIPPED {name}", lane=self.lane, where=reason)

    def summary(self) -> dict[str, Any]:
        return {"lane": self.lane, "passed": self.passed, "failed": self.failed,
                "skipped": self.skipped, "failures": self.failures, "skips": self.skips}


# ---------------------------------------------------------------- 通用步骤

def upload_book(client: MassClient, txt_path: str | Path) -> str:
    with open(txt_path, "rb") as handle:
        response = client.post(
            "/api/uploads", quality=False,
            files={"file": (Path(txt_path).name, handle, "text/plain")},
            data={"kind": "novel"})
    response.raise_for_status()
    upload = response.json().get("upload") or {}
    book_id = str(upload.get("book_id") or "")
    if not book_id:
        raise RuntimeError(f"upload did not yield book_id: {str(upload)[:200]}")
    return book_id


def wait_preparation(client: MassClient, job_id: str, timeout: float = 1800,
                     poll: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/preparation-jobs/{job_id}", quality=False)
        if response.status_code == 200:
            last = response.json()
            # INTERRUPTED 是显式终态（进程重启后的待恢复态），不能等到超时。
            if last.get("status") in ("READY", "FAILED", "CANCELLED", "INTERRUPTED"):
                return last
        time.sleep(poll)
    return dict(last, status="TIMEOUT")


def start_session(client: MassClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    frames = client.ndjson("POST", "/api/sessions/start", {
        "provider": PROVIDER, "base_url": _bridge_base(client),
        "api_key": BRIDGE_DUMMY_KEY, "model": "zcode-1", **body})
    return frames


def send_message(client: MassClient, session_id: str, message: str) -> list[dict[str, Any]]:
    return client.ndjson("POST", f"/api/sessions/{session_id}/messages",
                         {"message": message, "provider": PROVIDER,
                          "base_url": _bridge_base(client),
                          "api_key": BRIDGE_DUMMY_KEY, "model": "zcode-1"})


def _bridge_base(client: MassClient) -> str:
    import os
    return os.getenv("FATE_TEST_BASE_URL", "http://127.0.0.1:21590/v1")


def final_state(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """最后一个 state 帧的内层游戏状态（data["state"]）。

    服务端协议（core/server.py _stream_response）：游戏状态位于 data["state"]，
    session_id 在 data 顶层；旧实现返回外层 data 会让 save_stage/options
    永远取空，造成对正确产品的误判。
    """
    for frame in reversed(frames):
        if frame.get("type") == "state":
            state = (frame.get("data") or {}).get("state")
            if isinstance(state, dict):
                return state
    return {}


def durable_state(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """最后一个正式状态帧（opening/committed）；流式中间帧不算正式产出。"""
    for frame in reversed(frames):
        if frame.get("type") == "state":
            state = (frame.get("data") or {}).get("state")
            if isinstance(state, dict) and state.get("save_stage") in ("opening", "committed"):
                return state
    return {}


def session_id_of(frames: list[dict[str, Any]]) -> str:
    """session_id 位于帧 data 顶层，仅正式提交后存在；缺失返回空串。"""
    for frame in reversed(frames):
        if frame.get("type") == "state":
            sid = str((frame.get("data") or {}).get("session_id") or "")
            if sid:
                return sid
    return ""


def six_options_ok(state: dict[str, Any]) -> bool:
    """正式选项契约：A–F 恰六项、顺序一致、文本非空（与 save_contract 一致）。"""
    from core.api.save_contract import valid_options

    return valid_options((state or {}).get("options"))


def stream_error(frames: list[dict[str, Any]]) -> str:
    for frame in frames:
        if frame.get("type") == "error":
            return str((frame.get("data") or {}).get("message") or frame.get("data"))
    return ""


# ---------------------------------------------------------------- Phase 1 冒烟

def smoke_lane(client: MassClient, ledger: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """全流程冒烟第一段：上传两本书 → 截选本 window 准备 → 基础开局 →
    小截选本 fullbook 准备（队列桥逐块产答）→ 强化开局（智能体编队）。"""
    lane = "smoke"
    ck = CheckCollector(client, ledger, lane)
    full_txt: str = ctx["full_txt"]
    excerpt_txt: str = ctx["excerpt_txt"]
    excerpt12_txt: str = ctx["excerpt12_txt"]
    expected_full: int = ctx.get("app_full_chapters") or 0
    expected_first_last: list = ctx.get("app_full_first_last") or []

    # 1. 全本上传 + 切章（与产品切章器离线基准精确对齐：数量+连续覆盖+首末标题）
    book_full = upload_book(client, full_txt)
    ctx["book_full"] = book_full
    record = client.get(f"/api/books/{book_full}", quality=False).json()
    rows_full = (record.get("book") or {}).get("chapters") or []
    chapters_full = len(rows_full)
    ck.check("全本上传切章成功", chapters_full > 0, f"chapters={chapters_full}")
    if expected_full:
        idxs = [row.get("index") for row in rows_full]
        contiguous = idxs == list(range(1, expected_full + 1))
        ck.check("全本切章与基准精确一致（数量+连续覆盖）",
                 chapters_full == expected_full and contiguous,
                 f"api={chapters_full} expected={expected_full} contiguous={contiguous}")
        if expected_first_last and rows_full:
            first_last = [str(rows_full[0].get("title")), str(rows_full[-1].get("title"))]
            ck.check("全本首末章标题与基准一致", first_last == [str(x) for x in expected_first_last],
                     f"api={first_last} expected={expected_first_last}")

    # 2. 截选本上传 + window 准备（抽取式，无模型调用）→ 基础开局
    book_ex = upload_book(client, excerpt_txt)
    ctx["book_excerpt"] = book_ex
    expected_ex: int = ctx.get("app_excerpt40_chapters") or 0
    if expected_ex:
        rows_ex = ((client.get(f"/api/books/{book_ex}", quality=False).json()
                    .get("book") or {}).get("chapters") or [])
        idxs_ex = [row.get("index") for row in rows_ex]
        ck.check("截选本切章与基准精确一致（数量+连续覆盖）",
                 len(rows_ex) == expected_ex and idxs_ex == list(range(1, expected_ex + 1)),
                 f"api={len(rows_ex)} expected={expected_ex}")
    job = client.post(f"/api/books/{book_ex}/preparation-jobs", quality=False,
                      json={"mode": "window", "idempotency_key": f"smoke-w-{int(time.time())}"})
    ck.check("创建 window 准备任务", job.status_code in (200, 202),
             f"status={job.status_code} body={job.text[:200]}")
    job_id = str(job.json().get("job_id") or "")
    prep = wait_preparation(client, job_id, timeout=300)
    ck.check("window 准备 READY", prep.get("status") == "READY", str(prep)[:200])

    # 3. 基础模式开局（流式叙事，经队列桥）
    frames_b = start_session(client, {
        "mode": "基础", "book_id": book_ex, "story_agent_mode": False,
        "story_richness": 500})
    state_b = durable_state(frames_b)
    session_b = session_id_of(frames_b)
    err_b = stream_error(frames_b)
    ck.check("基础开局产出正式状态",
             bool(session_b) and state_b.get("save_stage") in ("opening", "committed"),
             f"err={err_b[:200]} stage={state_b.get('save_stage')}")
    ck.check("基础开局选项完整", six_options_ok(state_b),
             f"options={len(state_b.get('options') or [])}")
    ctx["session_basic"] = session_b

    # 4. 小截选本 fullbook 准备（逐块模型调用走队列桥，由 ZCode 产答）
    book_small = upload_book(client, excerpt12_txt)
    ctx["book_excerpt12"] = book_small
    job_f = client.post(f"/api/books/{book_small}/preparation-jobs", quality=False,
                        json={"mode": "fullbook", "idempotency_key": f"smoke-f-{int(time.time())}",
                              "provider": PROVIDER, "base_url": _bridge_base(client),
                              "api_key": BRIDGE_DUMMY_KEY, "model": "zcode-1"})
    ck.check("创建 fullbook 准备任务", job_f.status_code in (200, 202),
             f"status={job_f.status_code} body={job_f.text[:200]}")
    job_fid = str(job_f.json().get("job_id") or "")
    ctx["fullbook_job_id"] = job_fid
    if job_fid:
        prep_f = wait_preparation(client, job_fid, timeout=2400)
        ck.check("fullbook 准备 READY", prep_f.get("status") == "READY",
                 f"status={prep_f.get('status')} errors={str(prep_f.get('errors') or '')[:200]}")
        ctx["fullbook_ready"] = prep_f.get("status") == "READY"
        anchors_dir = Path(ctx.get("inst_var", "")) / "books" / book_small / "anchors" \
            if ctx.get("inst_var") else None
        if anchors_dir is not None and anchors_dir.is_dir():
            early = sorted(p.name for p in anchors_dir.glob("*.json"))
            ck.check("蒸馏中逐章锚点已落盘", bool(early), f"anchors={early[:8]}")

    # 5. 强化模式开局（智能体编队经队列桥——问题1修复验证）
    if ctx.get("fullbook_ready"):
        frames = start_session(client, {
            "mode": "强化", "book_id": book_small, "preparation_job_id": job_fid,
            "story_agent_mode": True, "story_richness": 500})
        state_a = durable_state(frames)
        session_a = session_id_of(frames)
        err = stream_error(frames)
        ck.check("强化开局产出正式状态",
                 bool(session_a) and state_a.get("save_stage") in ("opening", "committed"),
                 f"err={err[:200]} stage={state_a.get('save_stage')}")
        ctx["session_agent"] = session_a

    return ck.summary()


def smoke_loop_lane(client: MassClient, ledger: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """冒烟第二段：基础会话 8 回合 + 任务/托管/问答 + 存读档 + 导出 + 阅读器/Copilot。"""
    lane = "smoke_loop"
    ck = CheckCollector(client, ledger, lane)
    session_b = str(ctx.get("session_basic") or "")
    book_full = str(ctx.get("book_full") or "")
    if not session_b:
        ck.check("前置：基础会话已就绪", False, "ctx.session_basic 缺失")
        return ck.summary()

    actions = ["我推门进入客栈大堂打探消息", "沿着城墙根绕到后巷", "向掌柜打听北上的商队",
               "在集市上观察行人的动静", "抄近路穿过城隍庙", "登上城楼远眺全城",
               "回到住处整理今日所得", "天黑前赶到渡口问船家"]
    for index, action in enumerate(actions, 1):
        frames = send_message(client, session_b, action)
        state = durable_state(frames)
        err = stream_error(frames)
        ck.check(f"回合{index}提交并产出选项",
                 state.get("save_stage") == "committed" and six_options_ok(state),
                 f"err={err[:120]} options={len(state.get('options') or [])} "
                 f"stage={state.get('save_stage')}")
        if index == 3:
            offer = client.post(f"/api/sessions/{session_b}/quests/offer", quality=False,
                                json={"kind": "short"})
            if offer.status_code == 200:
                accept = client.post(f"/api/sessions/{session_b}/quests/accept")
                ck.check("任务接受成功", accept.status_code == 200, accept.text[:150])
            else:
                ck.check("任务 offer 可生成", False, f"status={offer.status_code}")
        if index == 5:
            auto = client.post(f"/api/sessions/{session_b}/autoplay-choice")
            ck.check("托管单回合选择", auto.status_code == 200, auto.text[:150])
        if index == 6:
            ask = client.post(f"/api/sessions/{session_b}/ask", quality=False,
                              json={"question": "当前任务目标是什么？"})
            ck.check("规则问答可用", ask.status_code == 200, ask.text[:150])

    # 存 / 读 / 列表
    save = client.post(f"/api/sessions/{session_b}/save", quality=False,
                       json={"save_id": "smoke-1"})
    ck.check("存档成功", save.status_code == 200, save.text[:150])
    saves = client.get("/api/saves", quality=False).json()
    rows = saves if isinstance(saves, list) else (saves.get("saves") or [])
    ck.check("存档列表包含 smoke-1",
             any(str(s.get("save_id")) == "smoke-1" for s in rows), str(saves)[:150])
    if save.status_code == 200:
        load = client.post("/api/saves/load", quality=False, json={"save_id": "smoke-1"})
        ck.check("免会话读档创建新会话", load.status_code == 200, load.text[:150])

    # 导出（账本完整时）
    export = client.post(f"/api/sessions/{session_b}/export-novel", quality=False,
                         json={"style": "faithful"})
    ck.check("导出小说成功", export.status_code == 200,
             f"status={export.status_code} body={export.text[:150]}")

    # 阅读器 / 锚点 / 搜索（全本，无模型调用）
    if book_full:
        record = client.get(f"/api/books/{book_full}", quality=False).json()
        chapters = (record.get("book") or {}).get("chapters") or []
        if ck.check("阅读器章节目录非空", bool(chapters), "无章节"):
            first = int(chapters[0]["index"])
            body = client.get(f"/api/books/{book_full}/chapters/{first}",
                              quality=False).json()
            text = str((body.get("chapter") or {}).get("text") or "")
            ck.check("章节正文可读且非空", len(text) > 50, f"len={len(text)}")
            search = client.get(f"/api/books/{book_full}/search", quality=False,
                                params={"q": text[10:16], "limit": 5})
            ck.check("全书搜索命中", search.status_code == 200 and search.json().get("results"),
                     search.text[:150])
            anchors = client.get(f"/api/books/{book_full}/chapters/{first}/anchors")
            ck.check("锚点端点可用（未蒸馏时 anchor 可为 null）",
                     anchors.status_code == 200, anchors.text[:120])

    # Copilot：文档检索（工作区已修手册路径）
    docs = client.get("/api/copilot/docs", quality=False, params={"q": "开局"})
    ck.check("Copilot 手册检索有结果",
             docs.status_code == 200 and docs.json().get("results"), docs.text[:150])
    return ck.summary()
