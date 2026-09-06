"""Deterministic, non-blocking review for committed story turns."""
from __future__ import annotations
from typing import Any, Mapping
from core.engine.sequence_feedback import new_feedback


def review_turn(*, state: Mapping[str, Any], action: str = "", narrative: str = "", options=None,
                events=None, degraded: bool = False, repairs=None) -> dict[str, Any]:
    rows = [dict(x) for x in (events or []) if isinstance(x, Mapping)]
    violations: list[dict[str, Any]] = []
    seen = set()
    for row in rows:
        eid = str(row.get("event_id") or row.get("id") or "")
        if eid and eid in seen: violations.append({"code": "duplicate_event", "event_id": eid})
        if eid: seen.add(eid)
        for req in row.get("prerequisites") or []:
            if str(req) == eid: violations.append({"code": "self_dependency", "event_id": eid})
    opts = [x for x in (options or []) if isinstance(x, Mapping)]
    if len(opts) != 6: violations.append({"code": "option_count", "count": len(opts)})
    if not str(narrative or "").strip(): violations.append({"code": "empty_narrative"})
    unresolved = []
    for objective in state.get("objectives") or []:
        if isinstance(objective, Mapping) and objective.get("status") not in {"resolved", "completed", "cancelled"}:
            title = objective.get("title") or objective.get("objective") or objective.get("text")
            if title: unresolved.append({"objective": str(title)[:160], "status": objective.get("status", "active")})
    directives = ["保持已提交回合的时间、地点和角色状态一致"]
    if violations: directives.append("优先修复上一回合的结构警告，不重复触发已解决事件")
    summary = (str(narrative).strip().replace("\n", " ")[:600] or "本回合无正文")
    return new_feedback(round=int(state.get("round") or 0), turn_id=f"round-{int(state.get('round') or 0)}", summary=summary, timeline_facts=[{"round": int(state.get("round") or 0), "chapter": state.get("current_chapter", 1)}], causal_facts=rows[-12:], quality_signals={"narrative_chars": len(str(narrative or "")), "options": len(opts)}, violations=violations, repairs=list(repairs or []), unresolved=unresolved, next_turn_directives=directives, degraded=bool(degraded or violations))
