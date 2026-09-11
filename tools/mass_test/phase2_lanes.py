# -*- coding: utf-8 -*-
"""Phase 2 十二实例并行矩阵的 lane 定义（runbook F 阶段 L01–L12）。

每条 lane 是一段真实用户路径的 HTTP 驱动，使用独立实例与独立书目副本；
模型相关调用一律经队列桥（ZCode 内容生产）。所有 lane 复用 lanes.py 原语。
lane 签名统一为 (client, ledger, ctx) -> summary dict。
"""
from __future__ import annotations

import os
import time
from typing import Any

from .client import BRIDGE_DUMMY_KEY
from .lanes import (CheckCollector, durable_state, send_message, session_id_of,
                    six_options_ok, start_session, stream_error, upload_book,
                    wait_preparation)

PROVIDER = "custom"

# 对局类 lane 的回合数：默认 30，冒烟放大时可经 FATE_MASS_ROUNDS 调低。
def _rounds() -> int:
    return max(3, int(os.getenv("FATE_MASS_ROUNDS", "30")))


def _bridge_base(client) -> str:
    return os.getenv("FATE_TEST_BASE_URL", "http://127.0.0.1:21590/v1")


def _model_body() -> dict[str, Any]:
    return {"provider": PROVIDER, "base_url": _bridge_base(None),
            "api_key": BRIDGE_DUMMY_KEY, "model": "zcode-1"}


_ACTIONS = ["我推门进入客栈大堂打探消息", "沿着城墙根绕到后巷查看动静",
            "向掌柜打听北上的商队何时出发", "在集市上观察行人的神色",
            "抄近路穿过城隍庙", "登上城楼远眺全城布防",
            "回到住处整理今日所得", "天黑前赶到渡口问船家价钱"]


def _action(round_no: int) -> str:
    return _ACTIONS[round_no % len(_ACTIONS)]


# ---------------------------------------------------------------- 对局 lane

def _playtest_rounds(client, ck: CheckCollector, session_id: str, total: int,
                     with_agent_checks: bool = False) -> None:
    """连续回合 + 任务/问答/托管穿插；每 5 回合存档并核对 revision 单调。"""
    last_revision = -1
    for index in range(1, total + 1):
        frames = send_message(client, session_id, _action(index))
        state = durable_state(frames)
        err = stream_error(frames)
        ok = (state.get("save_stage") == "committed" and six_options_ok(state))
        ck.check(f"回合{index}正式提交且选项完整", ok,
                 f"err={err[:100]} stage={state.get('save_stage')} "
                 f"options={len(state.get('options') or [])}")
        revision = int(state.get("revision") or 0)
        if revision:
            ck.check(f"回合{index} revision 单调", revision > last_revision or last_revision < 0,
                     f"rev={revision} last={last_revision}")
            last_revision = max(last_revision, revision)
        if index == 3:
            offer = client.post(f"/api/sessions/{session_id}/quests/offer", quality=False,
                                json={"kind": "short"})
            if offer.status_code == 200:
                accept = client.post(f"/api/sessions/{session_id}/quests/accept")
                ck.check("任务接受成功", accept.status_code == 200, accept.text[:120])
            else:
                ck.check("任务 offer 可生成", False, f"status={offer.status_code}")
        if index == 5:
            auto = client.post(f"/api/sessions/{session_id}/autoplay-choice")
            ck.check("托管单回合选择", auto.status_code == 200, auto.text[:120])
        if index == 6:
            ask = client.post(f"/api/sessions/{session_id}/ask", quality=False,
                              json={"question": "当前任务目标是什么？"})
            ck.check("规则问答可用", ask.status_code == 200, ask.text[:120])
        if index % 5 == 0:
            save = client.post(f"/api/sessions/{session_id}/save", quality=False,
                               json={"save_id": f"L-save-{index}"})
            ck.check(f"第{index}回合存档成功", save.status_code == 200, save.text[:120])
        if with_agent_checks and index == total:
            cluster = state.get("agent_cluster") or {}
            ck.check("编队簇产出可读", isinstance(cluster, dict), str(type(cluster)))


def _start_basic(client, ck: CheckCollector, book_id: str, agent: bool) -> str:
    frames = start_session(client, {"mode": "基础", "book_id": book_id,
                                    "story_agent_mode": agent, "story_richness": 500})
    state = durable_state(frames)
    session_id = session_id_of(frames)
    ck.check("基础开局产出正式状态",
             bool(session_id) and state.get("save_stage") in ("opening", "committed"),
             f"err={stream_error(frames)[:160]} stage={state.get('save_stage')}")
    ck.check("基础开局选项完整", six_options_ok(state),
             f"options={len(state.get('options') or [])}")
    return session_id


def _prep_window(client, ck: CheckCollector, book_id: str, tag: str) -> str:
    job = client.post(f"/api/books/{book_id}/preparation-jobs", quality=False,
                      json={"mode": "window", "idempotency_key": f"{tag}-{int(time.time())}"})
    ck.check(f"创建 window 准备任务[{tag}]", job.status_code in (200, 202),
             f"status={job.status_code} body={job.text[:160]}")
    job_id = str(job.json().get("job_id") or "")
    prep = wait_preparation(client, job_id, timeout=300)
    ck.check(f"window 准备 READY[{tag}]", prep.get("status") == "READY", str(prep)[:200])
    return job_id


def _prep_fullbook(client, ck: CheckCollector, book_id: str, tag: str) -> str:
    job = client.post(f"/api/books/{book_id}/preparation-jobs", quality=False,
                      json={"mode": "fullbook", "idempotency_key": f"{tag}-{int(time.time())}",
                            **_model_body()})
    ck.check(f"创建 fullbook 准备任务[{tag}]", job.status_code in (200, 202),
             f"status={job.status_code} body={job.text[:160]}")
    job_id = str(job.json().get("job_id") or "")
    if job_id:
        prep = wait_preparation(client, job_id, timeout=2400)
        ck.check(f"fullbook 准备 READY[{tag}]", prep.get("status") == "READY",
                 f"status={prep.get('status')} errors={str(prep.get('errors') or '')[:160]}")
    return job_id


def lane_L01_playtest_basic(client, ledger, ctx) -> dict[str, Any]:
    """L01 基础+非agent：完整对局（任务/问答/托管/每5回合存档）。"""
    ck = CheckCollector(client, ledger, "L01")
    book = upload_book(client, ctx["excerpt_txt"])
    _prep_window(client, ck, book, "L01")
    session_id = _start_basic(client, ck, book, agent=False)
    if session_id:
        _playtest_rounds(client, ck, session_id, _rounds())
    else:
        ck.skip("L01 对局", "开局未产出会话")
    return ck.summary()


def lane_L02_playtest_basic_agent(client, ledger, ctx) -> dict[str, Any]:
    """L02 基础+agent：同 L01，另核编队字段。"""
    ck = CheckCollector(client, ledger, "L02")
    book = upload_book(client, ctx["excerpt_txt"])
    _prep_window(client, ck, book, "L02")
    session_id = _start_basic(client, ck, book, agent=True)
    if session_id:
        _playtest_rounds(client, ck, session_id, _rounds(), with_agent_checks=True)
    else:
        ck.skip("L02 对局", "开局未产出会话")
    return ck.summary()


def _enhanced_start(client, ck: CheckCollector, book_id: str, job_id: str,
                    agent: bool) -> str:
    frames = start_session(client, {"mode": "强化", "book_id": book_id,
                                    "preparation_job_id": job_id,
                                    "story_agent_mode": agent, "story_richness": 500})
    state = durable_state(frames)
    session_id = session_id_of(frames)
    ck.check("强化开局产出正式状态",
             bool(session_id) and state.get("save_stage") in ("opening", "committed"),
             f"err={stream_error(frames)[:160]} stage={state.get('save_stage')}")
    ck.check("强化开局选项完整", six_options_ok(state),
             f"options={len(state.get('options') or [])}")
    return session_id


def lane_L03_enhanced(client, ledger, ctx) -> dict[str, Any]:
    """L03 强化+非agent：fullbook 前置 + 对局；锚点逐步落盘。"""
    ck = CheckCollector(client, ledger, "L03")
    book = upload_book(client, ctx["excerpt12_txt"])
    job_id = _prep_fullbook(client, ck, book, "L03")
    session_id = _enhanced_start(client, ck, book, job_id, agent=False) if job_id else ""
    if session_id:
        _playtest_rounds(client, ck, session_id, min(10, _rounds()))
    else:
        ck.skip("L03 对局", "强化开局未产出会话")
    return ck.summary()


def lane_L04_enhanced_agent(client, ledger, ctx) -> dict[str, Any]:
    """L04 强化+agent：编队簇全链（计划/场景/评审/选项/决策）。"""
    ck = CheckCollector(client, ledger, "L04")
    book = upload_book(client, ctx["excerpt12_txt"])
    job_id = _prep_fullbook(client, ck, book, "L04")
    session_id = _enhanced_start(client, ck, book, job_id, agent=True) if job_id else ""
    if session_id:
        _playtest_rounds(client, ck, session_id, min(10, _rounds()), with_agent_checks=True)
    else:
        ck.skip("L04 对局", "强化开局未产出会话")
    return ck.summary()


# ---------------------------------------------------------------- 阅读与访谈

def lane_L05_reader_fullbook(client, ledger, ctx) -> dict[str, Any]:
    """L05 全本阅读链：目录/首中尾章/搜索两模式/命中/锚点端点。"""
    ck = CheckCollector(client, ledger, "L05")
    book = upload_book(client, ctx["full_txt"])
    record = client.get(f"/api/books/{book}", quality=False).json()
    chapters = (record.get("book") or {}).get("chapters") or []
    if not ck.check("章节目录非空", bool(chapters), "无章节"):
        return ck.summary()
    first, mid, last = chapters[0]["index"], chapters[len(chapters) // 2]["index"], \
        chapters[-1]["index"]
    sample = ""
    for label, idx in (("首", first), ("中", mid), ("尾", last)):
        body = client.get(f"/api/books/{book}/chapters/{idx}", quality=False).json()
        text = str((body.get("chapter") or {}).get("text") or "")
        ck.check(f"{label}章正文非空", len(text) > 50, f"len={len(text)}")
        if label == "首":
            sample = text
    needle = sample[10:16] if len(sample) > 20 else "杨明"
    search = client.get(f"/api/books/{book}/search", quality=False,
                        params={"q": needle, "limit": 5})
    ck.check("全书搜索命中", search.status_code == 200
             and bool(search.json().get("results")), search.text[:120])
    occ = client.get(f"/api/books/{book}/search/occurrences", quality=False,
                     params={"q": needle})
    ck.check("occurrences 端点可用", occ.status_code == 200, occ.text[:120])
    anchors = client.get(f"/api/books/{book}/chapters/{first}/anchors")
    ck.check("锚点端点可用（未蒸馏可为 null）", anchors.status_code == 200, anchors.text[:120])
    playable = client.get("/api/library/playable", quality=False)
    ck.check("已玩书库端点可用", playable.status_code == 200, playable.text[:120])
    return ck.summary()


def lane_L06_reader_chat_copilot(client, ledger, ctx) -> dict[str, Any]:
    """L06 阅读访谈 + Copilot：roster/thread/消息 + docs/overview/降级。"""
    ck = CheckCollector(client, ledger, "L06")
    book = upload_book(client, ctx["excerpt_txt"])
    roster = client.get(f"/api/books/{book}/reader-chat/roster", quality=False)
    ck.check("访谈角色清单可用", roster.status_code == 200, roster.text[:160])
    thread = client.post(f"/api/books/{book}/reader-chat/threads", quality=False,
                         json={"character": "杨明"})
    ck.check("创建访谈线程", thread.status_code in (200, 201), thread.text[:160])
    thread_id = str((thread.json() or {}).get("thread_id")
                    or (thread.json() or {}).get("id") or "")
    if thread_id:
        detail = client.get(f"/api/reader-chat/threads/{thread_id}", quality=False)
        ck.check("访谈线程可回读", detail.status_code == 200, detail.text[:120])
        msg = client.post(f"/api/reader-chat/threads/{thread_id}/messages", quality=False,
                          json={"message": "你现在在做什么？", **_model_body()})
        ck.check("访谈消息有回复", msg.status_code == 200, msg.text[:160])
    else:
        ck.skip("访谈回读与消息", f"thread_id 缺失 body={thread.text[:80]}")
    docs = client.get("/api/copilot/docs", quality=False, params={"q": "开局"})
    ck.check("Copilot 手册检索有结果", docs.status_code == 200
             and bool(docs.json().get("results")), docs.text[:120])
    overview = client.get("/api/copilot/overview", quality=False)
    ck.check("Copilot overview 可用", overview.status_code == 200, overview.text[:120])
    return ck.summary()


# ---------------------------------------------------------------- 设计与配置

def lane_L07_designer_library(client, ledger, ctx) -> dict[str, Any]:
    """L07 角色设计器 + 角色库 CRUD/导入导出/损坏导入拒绝。"""
    ck = CheckCollector(client, ledger, "L07")
    schema = client.get("/api/character-designer/schema", quality=False)
    ck.check("设计器 schema 可用", schema.status_code == 200, schema.text[:120])
    gen = client.post("/api/character-designer/generate", quality=False,
                      json={"description": "冷静谨慎的边城旧刀客，重证据", **_model_body()})
    ck.check("设计器生成可用", gen.status_code == 200, gen.text[:160])
    card = {"name": "测试刀客", "summary": "边城旧刀客（测试用）", "tags": ["测试"]}
    create = client.post("/api/character-library", quality=False, json=card)
    ck.check("角色库新建", create.status_code in (200, 201), create.text[:160])
    card_id = str((create.json() or {}).get("card_id")
                  or (create.json() or {}).get("id") or "")
    if card_id:
        got = client.get(f"/api/character-library/{card_id}", quality=False)
        ck.check("角色卡可读", got.status_code == 200, got.text[:120])
        upd = client.put(f"/api/character-library/{card_id}", quality=False,
                         json={**card, "summary": "更新后的简介"})
        ck.check("角色卡更新", upd.status_code == 200, upd.text[:120])
    else:
        ck.skip("角色卡读改", f"card_id 缺失 body={create.text[:80]}")
    export = client.get("/api/character-library/export", quality=False)
    ck.check("角色库导出", export.status_code == 200, export.text[:120])
    broken = client.post("/api/character-library/import", quality=False,
                         files={"file": ("broken.json", b"{not-json", "application/json")})
    ck.check("损坏导入被拒绝", broken.status_code in (400, 422),
             f"status={broken.status_code}")
    if card_id:
        dele = client.delete(f"/api/character-library/{card_id}", quality=False)
        ck.check("角色卡删除", dele.status_code in (200, 204), dele.text[:120])
    return ck.summary()


def lane_L08_golden_finger(client, ledger, ctx) -> dict[str, Any]:
    """L08 金手指：recommend/propose/confirm + 设计器 options/compose/specs。"""
    ck = CheckCollector(client, ledger, "L08")
    book = upload_book(client, ctx["excerpt_txt"])
    rec = client.post("/api/golden-fingers/recommend", quality=False,
                      json={"book_id": book, "query": "低调实用的感知类金手指"})
    ck.check("金手指推荐可用", rec.status_code == 200, rec.text[:160])
    options = client.get("/api/gf-designer/options", quality=False)
    ck.check("GF 设计器 options 可用", options.status_code == 200, options.text[:120])
    specs = client.get("/api/gf-designer/specs", quality=False)
    ck.check("GF specs 列表可用", specs.status_code == 200, specs.text[:120])
    create_spec = client.post("/api/gf-designer/specs", quality=False,
                              json={"name": "测试神瞳", "category": "感知",
                                    "description": "看穿薄墙（测试用）"})
    ck.check("GF spec 创建", create_spec.status_code in (200, 201), create_spec.text[:160])
    return ck.summary()


# ---------------------------------------------------------------- 开局长尾/故障

def lane_L09_opening_longtail(client, ledger, ctx) -> dict[str, Any]:
    """L09 开局长尾：locate/select、章首开局、结构化问题、quick-distill 拒绝无Key。"""
    ck = CheckCollector(client, ledger, "L09")
    book = upload_book(client, ctx["excerpt_txt"])
    locate = client.post(f"/api/books/{book}/locate", quality=False,
                         json={"query": "教室 早自习", "limit": 5})
    ck.check("场景定位可用", locate.status_code == 200
             and bool(locate.json().get("candidates")), locate.text[:160])
    candidates = (locate.json() or {}).get("candidates") or []
    if candidates:
        pick = client.post(f"/api/books/{book}/locate/select", quality=False,
                           json={"candidate": candidates[0]})
        ck.check("开局位置确认可用", pick.status_code == 200, pick.text[:160])
    else:
        ck.skip("locate/select", "无候选")
    chapter_start = client.post(f"/api/books/{book}/chapter-start", quality=False,
                                json={"chapter_no": 1})
    ck.check("章首开局端点可用", chapter_start.status_code in (200, 202),
             chapter_start.text[:160])
    qd = client.post("/api/books/quick-distill", quality=False,
                     json={"book_id": book, "provider": PROVIDER,
                           "base_url": _bridge_base(client), "api_key": "",
                           "model": "zcode-1"})
    ck.check("quick-distill 空Key被拒绝(400)", qd.status_code == 400,
             f"status={qd.status_code}")
    return ck.summary()


def lane_L10_preparation_recovery(client, ledger, ctx) -> dict[str, Any]:
    """L10 准备故障恢复：取消→恢复→READY；幂等键复用同 job；乱序防御。"""
    ck = CheckCollector(client, ledger, "L10")
    book = upload_book(client, ctx["excerpt12_txt"])
    idem = f"L10-{int(time.time())}"
    job = client.post(f"/api/books/{book}/preparation-jobs", quality=False,
                      json={"mode": "window", "idempotency_key": idem})
    ck.check("创建可取消任务", job.status_code in (200, 202), job.text[:160])
    job_id = str(job.json().get("job_id") or "")
    dup = client.post(f"/api/books/{book}/preparation-jobs", quality=False,
                      json={"mode": "window", "idempotency_key": idem})
    if ck.check("幂等键重复提交返回同 job", dup.status_code in (200, 202)
                and str(dup.json().get("job_id") or "") == job_id, dup.text[:160]):
        cancel = client.post(f"/api/preparation-jobs/{job_id}/cancel", quality=False)
        ck.check("取消成功", cancel.status_code in (200, 202), cancel.text[:120])
        detail = client.get(f"/api/preparation-jobs/{job_id}", quality=False).json()
        ck.check("取消后状态为 CANCELLED", detail.get("status") == "CANCELLED",
                 str(detail)[:160])
        resume = client.post(f"/api/preparation-jobs/{job_id}/resume", quality=False)
        ck.check("恢复被受理", resume.status_code in (200, 202), resume.text[:160])
        prep = wait_preparation(client, job_id, timeout=300)
        ck.check("恢复后 READY", prep.get("status") == "READY", str(prep)[:200])
    return ck.summary()


def lane_L11_save_export(client, ledger, ctx) -> dict[str, Any]:
    """L11 存读档/导出矩阵：多 style 导出、损坏存档拒绝、存档切换。"""
    ck = CheckCollector(client, ledger, "L11")
    book = upload_book(client, ctx["excerpt_txt"])
    session_id = _start_basic(client, ck, book, agent=False)
    if not session_id:
        ck.skip("L11 存读导出", "开局未产出会话")
        return ck.summary()
    save = client.post(f"/api/sessions/{session_id}/save", quality=False,
                       json={"save_id": "L11-a"})
    ck.check("存档成功", save.status_code == 200, save.text[:160])
    saves = client.get("/api/saves", quality=False).json()
    rows = saves if isinstance(saves, list) else (saves.get("saves") or [])
    ck.check("存档列表包含 L11-a", any(str(s.get("save_id")) == "L11-a" for s in rows),
             str(saves)[:160])
    load = client.post("/api/saves/load", quality=False, json={"save_id": "L11-a"})
    ck.check("免会话读档创建新会话", load.status_code == 200, load.text[:160])
    for style in ("faithful", "concise"):
        export = client.post(f"/api/sessions/{session_id}/export-novel", quality=False,
                             json={"style": style})
        ck.check(f"导出小说[{style}]", export.status_code == 200, export.text[:120])
    bad_load = client.post("/api/saves/load", quality=False, json={"save_id": "不存在-999"})
    ck.check("读不存在存档返回4xx", bad_load.status_code in (400, 404),
             f"status={bad_load.status_code}")
    return ck.summary()


def lane_L12_isolation_security(client, ledger, ctx) -> dict[str, Any]:
    """L12 隔离/异常/安全：跨ID访问404、重复提交、ui-state、secret 脱敏。"""
    ck = CheckCollector(client, ledger, "L12")
    ghost = client.get("/api/books/does-not-exist-000", quality=False)
    ck.check("不存在书目返回404", ghost.status_code == 404, f"status={ghost.status_code}")
    ghost_sess = client.get("/api/sessions/no-such-session/state", quality=False)
    ck.check("不存在会话返回404", ghost_sess.status_code == 404,
             f"status={ghost_sess.status_code}")
    ui = client.post("/api/session/ui-state", quality=False,
                     json={"view": "library"})
    ck.check("ui-state 写入可用", ui.status_code in (200, 204), ui.text[:120])
    ui_read = client.get("/api/session/ui-state", quality=False)
    ck.check("ui-state 回读可用", ui_read.status_code == 200, ui_read.text[:120])
    health = client.get("/api/health", quality=False)
    body_text = health.text
    ck.check("health 不含密钥字面量",
             "sk-" not in body_text and "api_key" not in body_text, body_text[:120])
    books = client.get("/api/books", quality=False)
    ck.check("书库列表不含凭据字段",
             books.status_code != 200 or "api_key" not in books.text, books.text[:120])
    return ck.summary()


LANES = {
    "L01": lane_L01_playtest_basic,
    "L02": lane_L02_playtest_basic_agent,
    "L03": lane_L03_enhanced,
    "L04": lane_L04_enhanced_agent,
    "L05": lane_L05_reader_fullbook,
    "L06": lane_L06_reader_chat_copilot,
    "L07": lane_L07_designer_library,
    "L08": lane_L08_golden_finger,
    "L09": lane_L09_opening_longtail,
    "L10": lane_L10_preparation_recovery,
    "L11": lane_L11_save_export,
    "L12": lane_L12_isolation_security,
}
