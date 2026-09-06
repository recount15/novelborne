"""Chapter-level arc planning for long-sequence continuity."""
from __future__ import annotations
from typing import Any, Mapping
from hashlib import sha256
import json

def _hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]

def build_chapter_arc(state: Mapping[str, Any], plot_thread_map: Mapping[str, Any] | None = None) -> dict[str, Any]:
    chapter = int(state.get("current_chapter") or 1)
    index = state.get("chapter_index") if isinstance(state.get("chapter_index"), Mapping) else {}
    chapters = index.get("chapters") if isinstance(index.get("chapters"), list) else []
    source = next((dict(x) for x in chapters if isinstance(x, Mapping) and int(x.get("idx", 0) or 0) == chapter), {})
    title = str(source.get("title") or f"第{chapter}章")
    feedback = state.get("sequence_feedback") if isinstance(state.get("sequence_feedback"), list) else []
    current = [x for x in feedback if isinstance(x, Mapping) and int(x.get("chapter", chapter) or chapter) == chapter]
    turns = len(current)
    objectives = [dict(x) for x in (state.get("objectives") or []) if isinstance(x, Mapping)]
    active = [x for x in objectives if x.get("status") not in {"completed", "resolved", "cancelled"}]
    beats = ["建立章内局面", "推进核心冲突", "处理角色与机制后果", "形成阶段性收束"]
    completed_beats = min(len(beats), turns // 2)
    payload = {"chapter": chapter, "title": title, "turns": turns, "thread": plot_thread_map or {}, "active": active}
    return {"schema": "chapter-arc-v1", "version": 1, "chapter": chapter, "title": title, "source": source, "arc_goal": title, "start_state": {"round": max(0, int(state.get("round") or 0) - turns)}, "end_state": {"target": "形成阶段性收束"}, "beats": [{"index": i + 1, "title": beat, "status": "completed" if i < completed_beats else ("current" if i == completed_beats else "pending")} for i, beat in enumerate(beats)], "must_land": [x.get("title") or x.get("objective") for x in active[:6]], "optional_threads": [], "forbidden_leaks": ["未满足前置的后续事件", "角色尚未知晓的信息"], "mechanism_windows": {"tasks": True, "cheat": bool((state.get("ledger") or {}).get("cheat")), "conversation": True}, "character_arcs": [], "progress": {"turns": turns, "completed_beats": completed_beats, "total_beats": len(beats)}, "drift_score": 0.0, "source_hash": _hash(payload)}

def advance_chapter_arc(plan: Mapping[str, Any], committed_turn: Mapping[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(dict(plan), ensure_ascii=False, default=str))
    progress = result.setdefault("progress", {})
    progress["turns"] = int(progress.get("turns", 0)) + 1
    beats = result.get("beats") or []
    current = next((i for i, beat in enumerate(beats) if beat.get("status") == "current"), None)
    if current is not None:
        beats[current]["status"] = "completed"
        if current + 1 < len(beats): beats[current + 1]["status"] = "current"
    return result

def chapter_generation_brief(plan: Mapping[str, Any], turn_map: Mapping[str, Any] | None = None) -> dict[str, Any]:
    beats = plan.get("beats") or []
    current = next((x for x in beats if x.get("status") == "current"), beats[-1] if beats else {})
    return {"type": "chapter_generation_brief", "internal": True, "chapter": plan.get("chapter"), "chapter_goal": plan.get("arc_goal"), "current_beat": current, "must_land": list(plan.get("must_land") or []), "forbidden_leaks": list(plan.get("forbidden_leaks") or []), "mechanism_windows": dict(plan.get("mechanism_windows") or {}), "turn_brief": dict(turn_map or {})}
