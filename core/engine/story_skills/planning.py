"""Deterministic planning skills used before prose generation."""
from __future__ import annotations
from typing import Any
from .contract import SkillContext, SkillResult

def thread_planning_skill(ctx: SkillContext) -> SkillResult:
    events = list(ctx.thread_map.get("events") or [])
    unresolved = list(ctx.thread_map.get("unresolved") or [])
    active = [e for e in events if str(e.get("status", "planned")) not in {"resolved", "cancelled"}]
    ordered = sorted(active, key=lambda e: (int(e.get("round") or 0), str(e.get("event_id") or e.get("id") or "")))
    proposal = {"active_events": ordered[-12:], "next_targets": unresolved[-8:], "must_progress": list(ctx.brief.get("must_progress") or [])[:12], "must_avoid": list(ctx.brief.get("must_avoid") or [])[:12]}
    warnings = []
    known = {str(e.get("event_id") or e.get("id") or "") for e in ordered}
    for event in ordered:
        for req in event.get("prerequisites") or []:
            if str(req) not in known and str(req) not in {str(x) for x in ctx.thread_map.get("completed") or []}:
                warnings.append({"code": "unresolved_prerequisite", "event_id": event.get("event_id"), "prerequisite": str(req)})
    return SkillResult("thread_planning", proposal, warnings=warnings, degraded=bool(warnings))

def scene_design_skill(ctx: SkillContext) -> SkillResult:
    targets = list(ctx.brief.get("must_progress") or [])[:5]
    avoid = list(ctx.brief.get("must_avoid") or [])[:5]
    return SkillResult("scene_design", {"scene_goal": targets[0] if targets else "推进当前场景", "beats": ["建立当前局面", "呈现行动与反应", "落下可追踪后果"], "must_progress": targets, "must_avoid": avoid}, checkpoint={"scene_ready": True})
