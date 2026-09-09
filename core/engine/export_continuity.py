"""Deterministic continuity checks on raw canonical records (not display segments)."""
from __future__ import annotations
from collections import Counter
from typing import Any, Mapping, Sequence
from .story_ledger import validate_ledger


def report(rows: Sequence[Mapping[str, Any]], *, expected_round: int | None = None,
           validation: dict[str, Any] | None = None) -> dict[str, Any]:
    check = validation if validation is not None else validate_ledger(rows, expected_round=expected_round)
    rounds = check["rounds"]
    empty = [row.get("round") for row in rows if isinstance(row, Mapping)
             and (not isinstance(row.get("narrative"), str) or not row["narrative"].strip())]
    return {"ok": check["complete"], "round_min": min(rounds, default=None),
            "round_max": max(rounds, default=None), "turn_count": check["count"],
            "gaps": check["source_gaps"],
            "duplicates": sorted(n for n, count in Counter(rounds).items() if count > 1),
            "empty_narrative": empty, "errors": check["errors"],
            "expected_round": check["expected_round"],
            "migration_uncertain": check["migration_uncertain"],
            "complete": check["complete"]}
