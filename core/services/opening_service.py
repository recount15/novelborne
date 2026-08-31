# -*- coding: utf-8 -*-
"""开局蒸馏中台门面（重构 M1，REFACTOR_PLAN §0 第 9 条 / §10）。

对齐 ask_service 的门面风格：本模块不感知 FastAPI/HTTP，通过两个领域
异常上抛语义，由 server 端点映射状态码：
- ``OpeningClientError``   → HTTP 400（书籍目录缺失等请求侧问题）
- ``OpeningUpstreamError`` → HTTP 502（开局蒸馏全线失败）

职责：从 state 解析 book_dir / work_title / provider / request_kwargs，
用 engine.parallel.budget_model 以开局优先级（PRIORITY_OPENING）包装
engine.distill.distill_model 通道，调 engine.opening_distill.
run_opening_pipeline，再把结果**保留式**回写 state——不覆盖既有
plot_summary 等字段（语义对齐 app._merge_distill_status，本地实现同名
逻辑，不反向依赖 app）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from core import engine
from core import fate_engine as fe


class OpeningClientError(Exception):
    """请求侧错误：端点映射为 HTTP 400。"""


class OpeningUpstreamError(Exception):
    """模型/蒸馏侧错误：端点映射为 HTTP 502。"""


def _book_dir(state: Mapping[str, Any]) -> str:
    """书籍目录：state.distill_key 优先，否则按 chapter_index.book_id 推导
    （口径对齐 core.ui.common._book_dir，此处不 import ui 以免跨层）。"""
    key = str(state.get("distill_key") or "").strip()
    if key:
        return key
    index = state.get("chapter_index") if isinstance(state.get("chapter_index"), Mapping) else {}
    book_id = (index or {}).get("book_id")
    return os.path.join(fe.WRITABLE_DIR, "books", str(book_id)) if book_id else ""


def _work_title(state: Mapping[str, Any]) -> str:
    """作品名：novel_name > work > start_params 同名字段；缺省未命名作品。"""
    start = state.get("start_params") if isinstance(state.get("start_params"), Mapping) else {}
    for value in (state.get("novel_name"), state.get("work"),
                  (start or {}).get("novel_name"), (start or {}).get("work")):
        text = str(value or "").strip().strip("《》")
        if text:
            return text
    return "未命名作品"


def _merge_keep(old: Any, updates: Mapping[str, Any]) -> dict:
    """保留式 dict 合并：只覆盖非 None 的新值，其余既有字段原样保留。"""
    merged = dict(old) if isinstance(old, Mapping) else {}
    for key, value in updates.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def _apply_to_state(state: dict, report: Mapping[str, Any]) -> None:
    """把流水线报告回写 state（保留式，绝不整体覆盖 distill/work_distill）。"""
    plot = report.get("plot")
    anchors = report.get("anchors") or []
    distill_updates: dict = {}
    if plot:
        distill_updates["plot_summary"] = plot
        if report.get("selected_chapters"):
            distill_updates["selected_chapters"] = report["selected_chapters"]
    if anchors:
        distill_updates["opening_anchors"] = anchors
        distill_updates["opening_status"] = {
            "chapters_done": sum(1 for item in anchors if item.get("status") != "failed"),
            "chapters_total": len(anchors),
            "fallback": sum(1 for item in anchors if item.get("origin") == "fallback"),
        }
    if distill_updates:
        state["distill"] = _merge_keep(state.get("distill"), distill_updates)

    characters = report.get("characters") or []
    saved = [item for item in characters if isinstance(item, Mapping) and item.get("saved")]
    entry = report.get("work_entry") or {}
    work_updates: dict = {
        "character_count": len(saved),
        "opening_saved_characters": [item.get("name") for item in saved],
    }
    if entry.get("work_id"):
        work_updates.update({
            "work_id": entry.get("work_id"),
            "work_title": report.get("work_title"),
            "action": entry.get("action"),
            "anchors": entry.get("anchors_count"),
        })
    state["work_distill"] = _merge_keep(state.get("work_distill"), work_updates)
    state["opening_distill"] = {
        "status": "done",
        "ok": bool(report.get("ok")),
        "plot_ready": bool(plot),
        "plot_degraded": bool(report.get("plot_degraded")),
        "timings": report.get("timings") or {},
        "errors": report.get("errors") or [],
    }
    # 展示用蒸馏状态（中文）；仅在确有产出时覆盖，避免把旧失败提示冲掉。
    if plot or anchors or saved or entry.get("work_id"):
        fallback_count = sum(1 for item in anchors if item.get("origin") == "fallback")
        bits = []
        if plot:
            bits.append("剧情就绪")
        if anchors:
            bits.append("锚点 %d/%d 章%s" % (
                sum(1 for item in anchors if item.get("status") != "failed"), len(anchors),
                "（含兜底 %d 章）" % fallback_count if fallback_count else ""))
        if entry.get("work_id"):
            bits.append("作品库 %s" % entry.get("work_id"))
        if saved:
            bits.append("角色入库 %d 张" % len(saved))
        state["distill_status"] = "开局蒸馏完成：" + "、".join(bits)


def _summary(report: Mapping[str, Any]) -> dict:
    """可 JSON 化的对外摘要（不含大块正文）。"""
    characters = report.get("characters") or []
    return {
        "ok": bool(report.get("ok")),
        "work_title": report.get("work_title"),
        "plot_ready": bool(report.get("plot")),
        "plot_degraded": bool(report.get("plot_degraded")),
        "work_id": (report.get("work_entry") or {}).get("work_id"),
        "work_action": (report.get("work_entry") or {}).get("action"),
        "character_saved": sum(1 for item in characters if item.get("saved")),
        "character_dropped": sum(1 for item in characters if item.get("dropped_reason")),
        "characters": [
            {"name": item.get("name"),
             "level": (item.get("quality") or {}).get("level"),
             "score": (item.get("quality") or {}).get("score"),
             "saved": bool(item.get("saved")),
             "dropped_reason": item.get("dropped_reason")}
            for item in characters if isinstance(item, Mapping)
        ],
        "anchors": [
            {"chapter": item.get("chapter"), "status": item.get("status"),
             "origin": item.get("origin")}
            for item in (report.get("anchors") or []) if isinstance(item, Mapping)
        ],
        "timings": report.get("timings") or {},
        "errors": report.get("errors") or [],
    }


def run_for_state(state: dict,
                  client: Any,
                  model: str,
                  *,
                  chapters_ahead: int = 3,
                  library_path: Optional[str | Path] = None,
                  save_characters_fn: Optional[Callable] = None) -> dict:
    """开局蒸馏流水线对外门面：解析参数 → 开局优先级包装 → 流水线 → 回写 state。

    ``state`` 必须来自已持有会话锁的会话；``client`` 为 OpenAI 兼容客户端，
    ``model`` 为模型名。``library_path`` / ``save_characters_fn`` 可注入，
    供测试隔离磁盘真实库。返回可 JSON 化摘要（见 :func:`_summary`）。
    """
    book_dir = _book_dir(state)
    if not book_dir or not os.path.isdir(book_dir):
        raise OpeningClientError(
            "开局蒸馏需要有效的书籍目录（state 缺少 distill_key 或 chapter_index）")
    work_title = _work_title(state)
    provider = str(state.get("provider") or "deepseek")
    request_kwargs = state.get("request_kwargs") if isinstance(
        state.get("request_kwargs"), Mapping) else None

    def _call(prompt: str) -> str:
        return engine.distill.distill_model(client, model, prompt, request_kwargs, provider)

    # 开局优先级给满：预算包装幂等（流水线内部再包一次也无副作用）。
    budgeted = engine.parallel.budget_model(
        _call, default_priority=engine.parallel.PRIORITY_OPENING)
    report = engine.opening_distill.run_opening_pipeline(
        book_dir, work_title, budgeted,
        chapters_ahead=chapters_ahead,
        library_path=library_path,
        save_characters_fn=save_characters_fn)

    # 全线失败（无锚点、无剧情、无档案）才上抛 502；兜底锚点在场即放行开局。
    if (not report.get("ok") and not report.get("anchors")
            and not report.get("plot") and not report.get("work_entry")):
        detail = "；".join(report.get("errors") or []) or "未知错误"
        raise OpeningUpstreamError("开局蒸馏全线失败：%s" % detail[:300])
    _apply_to_state(state, report)
    return _summary(report)
