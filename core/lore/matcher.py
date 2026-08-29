"""世界书关键词匹配器。"""
from __future__ import annotations

from typing import Iterable, Mapping

from .schema import LoreEntry


def match_entries(entries: Iterable[LoreEntry], text: str, *, round_no: int = 0,
                  recent_rounds: Mapping[str, int] | None = None, depth: int = 6) -> list[tuple[LoreEntry, int]]:
    source = str(text or "").lower()
    recent_rounds = recent_rounds or {}
    result: list[tuple[LoreEntry, int]] = []
    for entry in entries:
        if not entry.enabled or entry.scan_depth > depth:
            continue
        if entry.cooldown and round_no - int(recent_rounds.get(entry.id, -10**9)) < entry.cooldown:
            continue
        hits = sum(1 for key in entry.keys if key.lower() in source)
        hits += sum(1 for key in entry.secondary_keys if key.lower() in source)
        if entry.constant:
            hits = max(hits, 1)
        if hits:
            result.append((entry, hits))
    result.sort(key=lambda pair: (pair[0].constant, pair[1], pair[0].priority), reverse=True)
    return result
