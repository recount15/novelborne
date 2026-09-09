"""Bounded role prompts over the authoritative game-context projection.

Only explicit character_id mappings select rich context. Names are never joined
against the database. Callers supply committed state and exact source coordinates.
"""
from __future__ import annotations

import json
from typing import Mapping

from core.services.character_context_service import build_game_context, ReaderContextError


def role_members(state, members=None):
    if members:
        return list(members) if isinstance(members, (list, tuple)) else []
    for key in ("active_members", "active_characters"):
        if state.get(key):
            value = state[key]
            return list(value) if isinstance(value, (list, tuple)) else []
    return [item for key in ("companions", "heroines")
            for item in (state.get(key) or [])
            if isinstance(state.get(key), (list, tuple))]


def _resolve_member(state, member):
    if isinstance(member, str):
        matches = [row for key in ("companions", "heroines")
                   for row in (state.get(key) or [])
                   if isinstance(row, Mapping) and row.get("name") == member
                   and isinstance(row.get("character_id"), str) and row["character_id"].strip()]
        if len({row["character_id"] for row in matches}) == 1:
            return matches[0]
    return member


def explicit_role_ids(state, members=None):
    return list(dict.fromkeys(item["character_id"] for item in
                (_resolve_member(state, row) for row in role_members(state, members))
                if isinstance(item, Mapping) and isinstance(item.get("character_id"), str)
                and item["character_id"].strip()))


def project_role_context(state, members=None, *, card_provider=None, cutoff=None, budget=2100):
    """Return {block, ok, rich, omissions, target_ids}; errors never become success.

    Source facts remain reference-only; only branch knowledge is known_facts.
    No raw cards, evidence quotes, history or private state is rendered.
    """
    state = state if isinstance(state, Mapping) else {}
    rows, omissions, targets = [], [], []
    ok, rich = True, False
    for member in role_members(state, members):
        # Active selectors can be labels, but only an explicit roster ID mapping
        # (never a database name search) may resolve them. Ambiguity stays legacy.
        member = _resolve_member(state, member)
        cid = member.get("character_id") if isinstance(member, Mapping) else None
        if not isinstance(cid, str) or not cid.strip():
            label = member.get("name", member.get("character", "")) if isinstance(member, Mapping) else member
            if not isinstance(label, str) or not label.strip():
                continue
            # Compatibility prose is explicitly not evidence; never serialize cards.
            legacy = {"legacy_label": label[:80], "grounding": "unverified_legacy_label"}
            if isinstance(member, Mapping):
                card = member.get("character_card")
                goal = card.get("goal") if isinstance(card, Mapping) else member.get("goal")
                if isinstance(goal, str):
                    legacy["unverified_goal"] = goal[:160]
            summaries = state.get("active_summaries")
            summary = summaries.get(label) if isinstance(summaries, Mapping) else None
            if isinstance(summary, str):
                legacy["unverified_summary"] = summary[:160]
            rows.append(legacy)
            omissions.append("legacy_label_not_evidence")
            continue
        if cid in targets:
            continue
        rich = True
        targets.append(cid)
        try:
            scoped_state = dict(state)
            if isinstance(member, Mapping) and "card_revision" in member:
                scoped_state["card_revision"] = member["card_revision"]
            context = build_game_context(scoped_state, card_provider=card_provider,
                cutoff=cutoff if cutoff is not None else state.get("knowledge_cutoff"),
                character_id=cid).to_dict()
        except Exception as exc:
            # Return only a diagnostic code, never exception text (paths/resources).
            code = exc.code if isinstance(exc, ReaderContextError) else "projection_error"
            omissions.append(code)
            ok = False
            continue
        omissions.extend(context.get("omissions", []))
        rows.append({key: context.get(key) for key in (
            "character_id", "identity", "stable_core", "known_facts",
            "effective_state", "effective_relationships", "active_goals", "source_status")})
    header = ("【角色安全投影】仅 known_facts 是角色已知分支事实；核心仅作动机参考。"
              "未知不等于否定，不得用未来知识、原著结局或未验证标签补足行动前提。\n")
    # Whole records only: never truncate JSON into misleading partial assertions.
    selected = []
    for row in rows:
        candidate = json.dumps(selected + [row], ensure_ascii=False, separators=(",", ":"))
        if len(header) + len(candidate) > max(0, budget - 300):
            omissions.append("role_budget_omitted")
            continue
        selected.append(row)
    trace = list(dict.fromkeys(omissions))
    status = "partial" if ok else "projection_failed"
    block = header + json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
    block += "\nstatus=" + status + "; omissions=" + ",".join(trace)[:240]
    return {"block": block, "ok": ok, "rich": rich, "omissions": trace,
            "target_ids": targets}
