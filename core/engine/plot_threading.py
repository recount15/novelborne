"""Internal plot-thread map and generation brief for long-sequence turns."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping
from hashlib import sha256
import json

@dataclass(frozen=True)
class PlotThreadMap:
    version: int
    round: int
    scene: dict[str, Any]
    objectives: list[dict[str, Any]]
    events: list[dict[str, Any]]
    causal_chains: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    anchors: list[dict[str, Any]]
    knowledge: dict[str, Any]
    must_progress: list[str]
    must_avoid: list[str]
    repair_directives: list[str]
    source_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def brief(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "current_scene": self.scene,
            "must_progress": self.must_progress,
            "must_avoid": self.must_avoid,
            "active_objectives": self.objectives[:8],
            "causal_chains": self.causal_chains[:8],
            "unresolved": self.unresolved[:12],
            "knowledge": self.knowledge,
            "repair_directives": self.repair_directives[:12],
        }


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return sha256(raw).hexdigest()[:16]


def _items(state: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = state.get(key) or []
    return [dict(x) for x in value if isinstance(x, Mapping)] if isinstance(value, list) else []


def prepare(state: Mapping[str, Any], *, user_input: str = "") -> PlotThreadMap:
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    scene = dict(memory.get("scene") or {})
    scene.setdefault("chapter", state.get("current_chapter", 1))
    scene.setdefault("round", int(state.get("round") or 0))
    goals = dict(memory.get("goals") or {})
    objectives = _items(state, "objectives") + _items(state, "quest_items")
    objectives += [dict(x) for x in goals.get("current", []) if isinstance(x, Mapping)]
    ledger = _items(state, "story_ledger")
    feedback = _items(state, "sequence_feedback")
    events = _items(state, "story_events")
    graph = state.get("story_graph") if isinstance(state.get("story_graph"), Mapping) else {}
    events += [dict(x) for x in graph.get("events", []) if isinstance(x, Mapping)]
    unresolved = []
    for row in feedback[-8:]:
        unresolved.extend(dict(x) for x in (row.get("unresolved") or []) if isinstance(x, Mapping))
        unresolved.extend({"directive": str(x)} for x in (row.get("next_turn_directives") or []) if str(x).strip())
    anchors = _items(state, "anchors")
    anchors += [dict(x) for x in (ledger[-3:] and ledger[-3:] or []) if x.get("anchor_id")]
    causal = []
    for event in events[-20:]:
        causal.append({"event_id": event.get("event_id") or event.get("id"), "prerequisites": list(event.get("prerequisites") or []), "status": event.get("status", "planned"), "round": event.get("round")})
    must_progress = [str(x.get("title") or x.get("objective") or x.get("text")) for x in objectives if x.get("status") not in {"resolved", "completed", "cancelled"} and str(x.get("title") or x.get("objective") or x.get("text"))]
    must_progress += [str(x.get("directive")) for x in unresolved if x.get("directive")]
    must_avoid = ["引入当前角色未知的信息", "跳过未满足的事件前置", "重复已经解决的目标"]
    if user_input.strip(): must_progress.append(f"响应玩家输入：{user_input.strip()[:160]}")
    knowledge = dict(memory.get("knowledge") or {})
    payload = {"scene": scene, "objectives": objectives, "events": events, "feedback": feedback[-8:], "user_input": user_input}
    return PlotThreadMap(1, int(state.get("round") or 0), scene, objectives, events[-40:], causal, unresolved[-24:], anchors[-20:], knowledge, list(dict.fromkeys(must_progress))[:20], must_avoid, list(dict.fromkeys(must_progress))[-12:], _hash(payload))


def generation_brief(thread_map: PlotThreadMap) -> dict[str, Any]:
    return {"type": "generation_brief", "internal": True, "map_version": thread_map.version, **thread_map.brief()}
