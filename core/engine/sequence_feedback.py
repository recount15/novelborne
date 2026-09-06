"""Durable, redacted feedback records for long-sequence self-improvement."""
from __future__ import annotations
from typing import Any, Mapping
from hashlib import sha256
import json


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def new_feedback(*, round: int, turn_id: str, summary: str = "", timeline_facts=None,
                  causal_facts=None, quality_signals=None, violations=None, repairs=None,
                  unresolved=None, next_turn_directives=None, degraded: bool = False) -> dict[str, Any]:
    return {
        "schema": "sequence-feedback-v1", "round": int(round), "turn_id": _text(turn_id, 120),
        "summary": _text(summary, 1200), "timeline_facts": list(timeline_facts or []),
        "causal_facts": list(causal_facts or []), "quality_signals": dict(quality_signals or {}),
        "violations": list(violations or []), "repairs": list(repairs or []),
        "unresolved": list(unresolved or []), "next_turn_directives": [_text(x, 300) for x in (next_turn_directives or []) if _text(x)],
        "degraded": bool(degraded), "feedback_status": "degraded" if degraded else "ok"
    }


def validate_feedback(row: Mapping[str, Any]) -> list[str]:
    errors = []
    try: round_no = int(row.get("round"))
    except (TypeError, ValueError): round_no = 0
    if round_no < 0: errors.append("invalid_round")
    if not _text(row.get("turn_id")): errors.append("missing_turn_id")
    if row.get("schema") not in (None, "sequence-feedback-v1"): errors.append("invalid_schema")
    return errors


def append_feedback(existing: list[Mapping[str, Any]] | None, row: Mapping[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
    result = [dict(x) for x in (existing or []) if isinstance(x, Mapping) and not validate_feedback(x)]
    if validate_feedback(row): return result
    result = [x for x in result if x.get("turn_id") != row.get("turn_id") and int(x.get("round", -1)) != int(row.get("round", -2))]
    result.append(dict(row)); result.sort(key=lambda x: (int(x.get("round", 0)), str(x.get("turn_id", ""))))
    return result[-limit:]


def recent_feedback(existing: list[Mapping[str, Any]] | None, current_round: int, limit: int = 5) -> list[dict[str, Any]]:
    rows = [dict(x) for x in (existing or []) if isinstance(x, Mapping)]
    rows.sort(key=lambda x: (abs(int(x.get("round", 0)) - int(current_round)), -int(x.get("round", 0))))
    return rows[:limit]


def digest(rows: list[Mapping[str, Any]] | None) -> dict[str, Any]:
    rows = [x for x in (rows or []) if isinstance(x, Mapping)]
    unresolved, directives, warnings = [], [], []
    for row in rows:
        unresolved.extend(row.get("unresolved") or [])
        directives.extend(row.get("next_turn_directives") or [])
        warnings.extend(row.get("violations") or [])
    return {"rounds": [int(x.get("round", 0)) for x in rows], "unresolved": unresolved[-24:], "directives": directives[-24:], "warnings": warnings[-24:], "hash": sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]}
