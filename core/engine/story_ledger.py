"""Immutable committed narrative records, independent of compressed context."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

SCHEMA = "fate-engine.story-ledger"
VERSION = 1


def new_ledger() -> list[dict[str, Any]]:
    return []


def _round(value: Any) -> int | None:
    # Do not truncate floats, accept booleans, or turn corrupt values into round 0.
    return value if type(value) is int and value >= 0 else None


def validate_ledger(rows: Sequence[Any] | None, *, expected_round: int | None = None) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(rows, (list, tuple)):
        errors.append("ledger 必须为列表")
        items = []
    else:
        items = rows
    seen_ids: set[str] = set()
    rounds: list[int] = []
    uncertain = False
    for index, row in enumerate(items):
        if not isinstance(row, Mapping):
            errors.append(f"第 {index + 1} 条不是记录")
            continue
        turn_id = row.get("turn_id")
        round_no = _round(row.get("round"))
        if not isinstance(turn_id, str) or not turn_id.strip():
            errors.append(f"第 {index + 1} 条缺少 turn_id")
        elif turn_id in seen_ids:
            errors.append(f"重复 turn_id: {turn_id}")
        else:
            seen_ids.add(turn_id)
        if round_no is None:
            errors.append(f"第 {index + 1} 条 round 无效")
        else:
            if round_no in rounds:
                errors.append(f"重复 round: {round_no}")
            rounds.append(round_no)
        if row.get("committed") is not True:
            errors.append(f"第 {index + 1} 条不是 committed")
        if not isinstance(row.get("narrative"), str) or not row["narrative"].strip():
            errors.append(f"第 {index + 1} 条缺少 narrative")
        uncertain = uncertain or bool(row.get("migration_uncertain"))
    if rounds != sorted(rounds):
        errors.append("round 未按升序排列")
    if expected_round is not None and _round(expected_round) is None:
        errors.append("state round 无效")
        expected_round = None
    start = 0 if 0 in rounds or expected_round == 0 else 1
    end = max(rounds, default=start - 1) if expected_round is None else expected_round
    present = set(rounds)
    gaps = [n for n in range(start, end + 1) if n not in present]
    if any(n > end for n in rounds):
        errors.append("ledger round 超出 state round")
    return {"ok": not errors, "errors": errors, "count": len(items),
            "rounds": rounds, "source_gaps": gaps,
            "expected_round": expected_round, "migration_uncertain": uncertain,
            "complete": bool(items) and not errors and not gaps and not uncertain}


def append_turn(rows: Sequence[Any] | None, record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Only an identical retry is idempotent; conflicting payloads are rejected."""
    current = deepcopy([] if rows is None else rows)
    check = validate_ledger(current)
    if not check["ok"]:
        raise ValueError("ledger 校验失败: " + "; ".join(check["errors"]))
    item = deepcopy(dict(record))
    check = validate_ledger([item])
    if not check["ok"]:
        raise ValueError("ledger 校验失败: " + "; ".join(check["errors"]))
    for row in current:
        if row["turn_id"] == item["turn_id"]:
            comparison = dict(row)
            if "created_at" not in item:
                comparison.pop("created_at", None)
            if comparison != item:
                raise ValueError(f"ledger turn_id payload conflict: {item['turn_id']}")
            return current
        if row["round"] == item["round"]:
            raise ValueError(f"ledger 已存在 round {item['round']}")
    item.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    current = list(current) + [item]
    current.sort(key=lambda row: row["round"])
    return current


def from_history(history: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Extract legacy text, never invent committed turns or ordinal round IDs."""
    result = []
    for index, entry in enumerate(history or ()):
        if not isinstance(entry, Mapping) or entry.get("role") != "assistant":
            continue
        text = entry.get("content")
        if isinstance(text, str) and text.lstrip().startswith("[接手摘要]"):
            continue
        result.append({"turn_id": f"legacy-history-{index + 1}",
                       "round": _round(entry.get("round")), "idx": index,
                       "narrative": text, "committed": False,
                       "migration_uncertain": True})
    return result


def narrative_source(state: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    rows = state.get("story_ledger")
    expected = state.get("round")
    # Empty ledgers are normal for legacy saves and a new game's round-0 opening.
    # Malformed nonempty ledgers must never silently fall back to history.
    if rows is not None and rows != []:
        check = validate_ledger(rows, expected_round=expected)
        clean = deepcopy(rows) if isinstance(rows, list) else []
        return clean, "story_ledger", check
    history = state.get("history", [])
    migrated = from_history(history if isinstance(history, list) else [])
    rounds = [row["round"] for row in migrated if row["round"] is not None]
    gaps = [n for n in range(1, expected + 1) if n not in rounds] if _round(expected) is not None else []
    errors = []
    if not isinstance(history, list) or any(not isinstance(row, Mapping) for row in history):
        errors.append("history 包含无效记录")
    if any(not isinstance(row["narrative"], str) or not row["narrative"].strip() for row in migrated):
        errors.append("history 包含空或无效正文")
    return migrated, "history_fallback", {
        "ok": not errors, "errors": errors, "count": len(migrated), "rounds": rounds,
        "source_gaps": gaps, "expected_round": expected,
        "migration_uncertain": True, "complete": False,
    }


__all__ = ["SCHEMA", "VERSION", "new_ledger", "validate_ledger", "append_turn", "from_history", "narrative_source"]
