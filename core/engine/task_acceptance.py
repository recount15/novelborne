"""Flexible, non-blocking task acceptance."""
from __future__ import annotations
from typing import Any, Mapping

def evaluate(task: Mapping[str, Any], *, action: str = "", narrative: str = "", state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    state = state or {}; text = (str(action) + " " + str(narrative)).lower()
    hard = [str(x).lower() for x in (task.get("hard_facts") or [])]
    soft = [str(x).lower() for x in (task.get("soft_signals") or [])]
    hard_hit = sum(1 for x in hard if x and x in text)
    soft_hit = sum(1 for x in soft if x and x in text)
    total = len(hard) or 1
    if hard_hit == len(hard) and (hard or soft_hit): status = "complete"
    elif hard_hit or soft_hit: status = "partial"
    else: status = "pending"
    progress = {"hard_hits": hard_hit, "hard_total": len(hard), "soft_hits": soft_hit, "soft_total": len(soft)}
    return {"status": status, "progress": progress, "evidence": {"action": str(action)[:240], "narrative": str(narrative)[:400]}, "repair": [] if status == "complete" else [{"action": "continue_or_redirect", "reason": "insufficient evidence"}], "degraded": status != "complete"}
