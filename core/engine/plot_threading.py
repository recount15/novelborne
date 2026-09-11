"""Internal plot-thread map and generation brief for long-sequence turns."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping
from hashlib import sha256
import json

#: §2.3 场景卡事件状态全集。迁移绑定已提交证据（story_ledger），反馈行
#: 只是回合质量回评，永远不构成完成依据。
EVENT_STATES = ("not_started", "active", "completed", "changed_by_player", "unavailable")

#: §3.2 初始活跃非玩家主动支线上限（规划软限制，可在 state 覆盖）。
SIDE_THREAD_LIMIT_DEFAULT = 1

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
    scene_card: dict[str, Any] | None = None
    event_states: dict[str, dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def brief(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "current_scene": self.scene,
            "scene_card": self.scene_card or {},
            "event_states": {eid: entry["state"]
                             for eid, entry in (self.event_states or {}).items()},
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


def _graph_events(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    graph = state.get("story_graph") if isinstance(state.get("story_graph"), Mapping) else {}
    return [dict(x) for x in (graph.get("events") or []) if isinstance(x, Mapping)]


def _event_title(row: Mapping[str, Any]) -> str:
    for key in ("title", "text", "name", "summary"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return str(row.get("event_id") or row.get("id") or "")


def committed_event_evidence(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """已提交台账行携带的事件 → 证据（turn_id/round/chapter）。

    只有 ``committed is True`` 的 story_ledger 行算证据：失败草稿、
    反馈回评、模型自述都不能证明事件完成。事件项允许字符串或含
    ``event_id`` 的对象（蓝图段事件是名字，图事件是 id）。
    """
    rows = state.get("story_ledger")
    evidence: dict[str, dict[str, Any]] = {}
    for row in (rows if isinstance(rows, list) else []):
        if not isinstance(row, Mapping) or row.get("committed") is not True:
            continue
        for item in (row.get("events") or []):
            if isinstance(item, Mapping):
                eid = str(item.get("event_id") or item.get("id") or "").strip()
            else:
                eid = str(item or "").strip()
            if eid and eid not in evidence:
                evidence[eid] = {"turn_id": str(row.get("turn_id") or ""),
                                 "round": row.get("round"),
                                 "chapter": row.get("chapter")}
    return evidence


def project_event_states(state: Mapping[str, Any], *,
                         evidence: Mapping[str, Mapping[str, Any]] | None = None,
                         ) -> dict[str, dict[str, Any]]:
    """story_graph 事件 → §2.3 状态投影（只读，不回写）。

    - completed：有已提交证据（evidence_kind=committed）；图内 resolved 但
      无证据 → completed + unknown_legacy（旧数据不伪造证据）；
    - changed_by_player：玩家明确阻止（player_prevented 或碎锚章）；
    - unavailable：前置被玩家改变/本身不可用（前置失效处理）；
    - active：状态 active，或前置全部完成解锁；
    - not_started：前置未满足或未知（保守不阻塞推进但也不冒进）。
    """
    rows = _graph_events(state)
    evidence = committed_event_evidence(state) if evidence is None else dict(evidence)
    broken: set[int] = set()
    for value in (state.get("broken_anchors") or []):
        try:
            broken.add(int(value))
        except (TypeError, ValueError):
            pass
    try:
        shatter_from = int(state.get("anchors_shattered_from") or 0)
    except (TypeError, ValueError):
        shatter_from = 0

    def _direct(row: Mapping[str, Any]) -> dict[str, Any] | None:
        eid = str(row.get("event_id") or row.get("id") or "").strip()
        if not eid:
            return None
        if eid in evidence:
            return {"state": "completed", "evidence_refs": [evidence[eid]["turn_id"]],
                    "evidence_kind": "committed", "reason": "ledger_committed"}
        status = str(row.get("status") or "planned")
        if status in ("resolved", "completed"):
            return {"state": "completed", "evidence_refs": [],
                    "evidence_kind": "unknown_legacy",
                    "reason": "graph_completed_without_committed_evidence"}
        try:
            chapter = int(row.get("chapter") or 0)
        except (TypeError, ValueError):
            chapter = 0
        if row.get("player_prevented") is True or (chapter and chapter in broken) \
                or (chapter and shatter_from and chapter >= shatter_from):
            return {"state": "changed_by_player", "evidence_refs": [],
                    "evidence_kind": "", "reason": "player_divergence"}
        if status in ("cancelled", "blocked"):
            return {"state": "unavailable", "evidence_refs": [],
                    "evidence_kind": "", "reason": "status_" + status}
        return None

    out: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    for row in rows:
        direct = _direct(row)
        eid = str(row.get("event_id") or row.get("id") or "").strip()
        if direct is not None:
            out[eid] = direct
        else:
            pending.append(row)
    # 前置失效传播到不动点（链式依赖一次性处理完，环由轮次上限截断）。
    for _ in range(len(pending) + 1):
        progressed = False
        remaining: list[dict[str, Any]] = []
        for row in pending:
            eid = str(row.get("event_id") or row.get("id") or "").strip()
            prereqs = [str(x) for x in (row.get("prerequisites") or [])]
            blocked = [p for p in prereqs
                       if out.get(p, {}).get("state") in ("changed_by_player", "unavailable")]
            if blocked:
                out[eid] = {"state": "unavailable", "evidence_refs": [],
                            "evidence_kind": "",
                            "reason": "prerequisite_invalidated:" + ",".join(blocked)}
                progressed = True
                continue
            known = [p for p in prereqs if p in out]
            if known and all(out[p]["state"] == "completed" for p in known):
                out[eid] = {"state": "active", "evidence_refs": [],
                            "evidence_kind": "", "reason": "prerequisites_satisfied"}
                progressed = True
                continue
            if str(row.get("status") or "planned") == "active" and not known:
                out[eid] = {"state": "active", "evidence_refs": [],
                            "evidence_kind": "", "reason": "status_active"}
                progressed = True
                continue
            remaining.append(row)
        pending = remaining
        if not pending or not progressed:
            break
    for row in pending:
        eid = str(row.get("event_id") or row.get("id") or "").strip()
        out[eid] = {"state": "not_started", "evidence_refs": [],
                    "evidence_kind": "", "reason": "prerequisite_pending"}
    return out


def scene_card(state: Mapping[str, Any], *, user_input: str = "",
               event_states: Mapping[str, Mapping[str, Any]] | None = None,
               ) -> dict[str, Any]:
    """§2.3 场景卡：单回合局部目标的确定性投影（只读）。

    authorized_deviations 只收录**已生效**授权（fact_contract 判定
    authorized）；draft/needs_clarification 愿望没有授权效力，不得进入。
    """
    from core.engine import directives as directives_engine
    from core.engine import fact_contract

    states = dict(project_event_states(state) if event_states is None else event_states)
    rows = _graph_events(state)
    by_id = {str(r.get("event_id") or r.get("id") or ""): r for r in rows}
    chapter = int(state.get("current_chapter") or 1)
    index = state.get("chapter_index") if isinstance(state.get("chapter_index"), Mapping) else {}
    chapters = index.get("chapters") if isinstance(index.get("chapters"), list) else []
    source = next((dict(x) for x in chapters
                   if isinstance(x, Mapping) and int(x.get("idx", 0) or 0) == chapter), {})
    objectives = [x for x in _items(state, "objectives")
                  if x.get("status") not in {"completed", "resolved", "cancelled"}]
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    scene = dict(memory.get("scene") or {})
    participants = [str(x) for x in (scene.get("participants") or []) if str(x).strip()]
    motives = [str(x) for x in (scene.get("motives") or []) if str(x).strip()]

    deviations: list[dict[str, Any]] = []
    try:
        for row in directives_engine.active_directives(state):
            if fact_contract.classify_directive_row(row).kind != fact_contract.KIND_AUTHORIZED:
                continue
            record = fact_contract.wish_authorization_of(row) or None
            targets = list(getattr(record, "target_ids", ()) or ()) or \
                [str(x) for x in (row.get("affected") or [])]
            deviations.append({
                "id": "directive-" + str(row.get("id") or ""),
                "text": str(row.get("fact_norm") or row.get("rule") or "")[:200],
                "targets": targets[:6],
                "origin": str(getattr(record, "authorized_origin", "wish") or "wish"),
            })
            if len(deviations) >= 6:
                break
    except Exception:  # noqa: BLE001 旧档账本形状异常时降级为空授权集，不阻断回合
        deviations = []

    prereqs = sorted({str(p) for row in rows for p in (row.get("prerequisites") or [])})
    forbidden = [_event_title(by_id[eid]) for eid, entry in states.items()
                 if entry["state"] == "not_started" and eid in by_id]
    try:
        side_limit = max(0, int(state.get("side_thread_limit", SIDE_THREAD_LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        side_limit = SIDE_THREAD_LIMIT_DEFAULT
    optional = [_event_title(by_id[eid]) for eid, entry in states.items()
                if entry["state"] == "active"
                and str((by_id.get(eid) or {}).get("optional")) == "True"][:side_limit]
    return {
        "schema": "scene-card-v1",
        "source_ref": {"chapter": chapter,
                       "title": str(source.get("title") or f"第{chapter}章"),
                       "origin": "chapter_index"},
        "current_objective": str((objectives[0].get("title") if objectives else "")
                                 or ((objectives[0].get("objective") if objectives else "")
                                     or "")),
        "participants": participants,
        "character_motives": motives,
        "prerequisites": prereqs[:8],
        "event_states": {eid: entry["state"] for eid, entry in states.items()},
        "authorized_deviations": deviations,
        "player_action": str(user_input or "").strip()[:160],
        "forbidden_early_reveals": forbidden[:6],
        "optional_threads": optional,
    }


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
    # C05（§3.2）：取消每轮必须有谜团/悬念/爽点/新伏笔的强制要求——
    # 按原书节奏推进，允许自然过渡与平静结束，防注意力漂向自造钩子。
    must_avoid.append("每轮强行制造新悬念或新伏笔（按原书节奏推进，允许平静收束）")
    # C05（§2.3 玩家分歧）：玩家明确阻止/碎锚改道的事件不强行重演。
    event_states = project_event_states(state)
    for eid, entry in event_states.items():
        if entry["state"] == "changed_by_player" and eid in {str(r.get("event_id") or r.get("id") or "") for r in events}:
            title = next((_event_title(r) for r in events
                          if str(r.get("event_id") or r.get("id") or "") == eid), eid)
            must_avoid.append(f"强行重演玩家已改变的事件「{title}」（真实分歧，不得回摆）")
    card = scene_card(state, user_input=user_input, event_states=event_states)
    if user_input.strip(): must_progress.append(f"响应玩家输入：{user_input.strip()[:160]}")
    knowledge = dict(memory.get("knowledge") or {})
    payload = {"scene": scene, "objectives": objectives, "events": events, "feedback": feedback[-8:], "user_input": user_input}
    return PlotThreadMap(1, int(state.get("round") or 0), scene, objectives, events[-40:], causal, unresolved[-24:], anchors[-20:], knowledge, list(dict.fromkeys(must_progress))[:20], list(dict.fromkeys(must_avoid))[:12], list(dict.fromkeys(must_progress))[-12:], _hash(payload), card, event_states)


def generation_brief(thread_map: PlotThreadMap) -> dict[str, Any]:
    return {"type": "generation_brief", "internal": True, "map_version": thread_map.version, **thread_map.brief()}
