"""Precise, feasibility-aware task surfacing.

C06 TaskProjection：注册表任务统一投影为「主线/可选 × 可行性 + 依据」。
不可行的可选任务不再进入生成上下文（不阻断主线）；不可行的主线任务保留
但带 infeasible_reason，供规划改道而不是硬锁。可行性依据与 C05 事件投影
（project_event_states，含 player_prevented/碎锚）同源——玩家或愿望造成的
真实分歧会让挂在该锚点上的任务失效，而不是被模型反复重推。
"""
from __future__ import annotations
from typing import Any, Mapping

# 任务引用的原著锚点/事件字段名（生产端字段不统一，读取时逐一兼容）。
_EVENT_REF_KEYS = ("anchor", "anchor_ref", "event", "event_ref", "prerequisite")
_INVALID_STATES = ("changed_by_player", "unavailable")


def _event_ref(task: Mapping[str, Any]) -> str:
    for key in _EVENT_REF_KEYS:
        value = str(task.get(key) or "").strip()
        if value:
            return value
    return ""


def _ref_event_state(ref: str, state: Mapping[str, Any]) -> str:
    """任务引用的锚点/事件在 C05 事件投影下的状态；无引用或未登记返回空串。"""
    if not ref:
        return ""
    try:
        from core.engine.plot_threading import project_event_states
        states = project_event_states(state)
    except Exception:  # noqa: BLE001 投影失败按未知处理，不阻塞任务面
        states = {}
    entry = states.get(ref)
    if isinstance(entry, Mapping) and entry.get("state") in _INVALID_STATES:
        return str(entry["state"])
    graph = state.get("story_graph") if isinstance(state.get("story_graph"), Mapping) else {}
    for event in graph.get("events") or []:
        if isinstance(event, Mapping) and str(event.get("title") or "") == ref:
            projected = states.get(str(event.get("event_id") or event.get("id") or ""))
            if isinstance(projected, Mapping) and projected.get("state") in _INVALID_STATES:
                return str(projected["state"])
    return ""


def project_task(task: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """TaskProjection：保留原字段，附 optional/feasible/infeasible_reason/feasibility_basis。"""
    row = dict(task)
    optional = not bool(task.get("mainline") or task.get("required"))
    reason = ""
    basis: dict[str, Any] = {}
    ref = _event_ref(task)
    if ref:
        event_state = _ref_event_state(ref, state)
        basis["event_ref"] = ref
        if event_state:
            basis["event_state"] = event_state
            if event_state in _INVALID_STATES:
                reason = "prerequisite_invalidated"
    if not reason and task.get("feasibility") is not None:
        try:
            declared = float(task["feasibility"])
        except (TypeError, ValueError):
            declared = None
        if declared is not None and declared <= 0.0:
            reason = "declared_infeasible"
    row["optional"] = optional
    row["feasible"] = not reason
    row["infeasible_reason"] = reason or None
    row["feasibility_basis"] = basis
    return row


def score_task(task: Mapping[str, Any], state: Mapping[str, Any], chapter_plan: Mapping[str, Any] | None = None) -> float:
    score = 0.0
    text = str(task.get("title") or task.get("objective") or task.get("text") or "").lower()
    chapter = str((chapter_plan or {}).get("title") or "").lower()
    if text and chapter and any(token in text for token in chapter.split() if token): score += 0.2
    if task.get("chapter") in (None, state.get("current_chapter")): score += 0.2
    if task.get("location") in (None, (state.get("state_memory") or {}).get("location", {}).get("name")): score += 0.15
    if task.get("player_relevant", True): score += 0.2
    if task.get("repeat_count", 0): score -= min(0.3, 0.08 * int(task.get("repeat_count", 0)))
    if task.get("feasibility") is not None: score += 0.25 * float(task.get("feasibility") or 0)
    return max(0.0, min(1.0, score))


def rank_tasks(tasks, state: Mapping[str, Any], chapter_plan: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = []
    for task in tasks or []:
        if isinstance(task, Mapping):
            row = project_task(task, state); row["relevance_score"] = round(score_task(row, state, chapter_plan), 4); rows.append(row)
    return sorted(rows, key=lambda x: (-x["relevance_score"], str(x.get("id") or x.get("title") or "")))


def select_tasks(tasks, state: Mapping[str, Any], chapter_plan: Mapping[str, Any] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    ranked = rank_tasks(tasks, state, chapter_plan)
    surfaced = [row for row in ranked if row.get("feasible") or not row.get("optional")]
    return surfaced[:max(0, int(limit))]
