"""Deterministic preflight skills for executable public choices."""
from __future__ import annotations
from typing import Any
from .contract import SkillContext, SkillResult

def choice_precondition_skill(ctx: SkillContext, candidates: list[dict[str, Any]] | None = None) -> SkillResult:
    accepted, rejected = [], []
    for item in candidates or []:
        text = str(item.get("text") or item.get("action") or "").strip()
        if not text or item.get("requires_future_knowledge") or item.get("patch_valid") is False:
            rejected.append({"candidate": text[:120], "code": "invalid_precondition"})
        else:
            accepted.append(dict(item))
    return SkillResult("choice_precondition", {"accepted": accepted, "rejected": rejected}, warnings=rejected, degraded=bool(rejected))

def choice_coverage_skill(ctx: SkillContext, candidates: list[dict[str, Any]] | None = None) -> SkillResult:
    rows = candidates or []
    buckets = {"mainline": 0, "relationship": 0, "information": 0, "risk": 0, "resource": 0, "personality": 0}
    for item in rows:
        tags = set(str(x).lower() for x in (item.get("tags") or item.get("coverage") or []))
        for key in buckets:
            if key in tags: buckets[key] += 1
    missing = [key for key, value in buckets.items() if value == 0]
    return SkillResult("choice_coverage", {"coverage": buckets, "missing": missing}, warnings=[{"code": "coverage_missing", "dimension": x} for x in missing], degraded=bool(missing))
