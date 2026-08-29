"""世界书选择、冷却和 token/字符预算注入。"""
from __future__ import annotations

from typing import Any, Iterable

from .matcher import match_entries
from .schema import LoreEntry


class LoreInjector:
    def __init__(self, entries: Iterable[LoreEntry] = (), *, budget_chars: int = 3000, depth: int = 6):
        self.entries = list(entries)
        self.budget_chars = max(200, int(budget_chars))
        self.depth = max(1, int(depth))
        self.recent_rounds: dict[str, int] = {}

    def inject(self, text: str, *, round_no: int = 0, budget_chars: int | None = None) -> dict[str, Any]:
        budget = max(200, int(budget_chars or self.budget_chars))
        selected: list[LoreEntry] = []
        chunks: list[str] = []
        used = 0
        for entry, _hits in match_entries(self.entries, text, round_no=round_no,
                                          recent_rounds=self.recent_rounds, depth=self.depth):
            content = entry.content[:entry.max_chars].strip()
            if not content or used + len(content) > budget:
                continue
            selected.append(entry)
            chunks.append(f"【{entry.title or entry.id}】\n{content}")
            used += len(content)
            self.recent_rounds[entry.id] = round_no
        injected = "\n\n".join(chunks)
        return {"text": injected, "chars": used, "ids": [item.id for item in selected],
                "entries": selected, "recent_rounds": dict(self.recent_rounds)}

    def snapshot(self) -> dict[str, Any]:
        return {"recent_rounds": dict(self.recent_rounds), "budget_chars": self.budget_chars, "depth": self.depth}

    def restore(self, snapshot: dict[str, Any] | None) -> None:
        if not isinstance(snapshot, dict):
            return
        self.recent_rounds = {str(k): int(v) for k, v in (snapshot.get("recent_rounds") or {}).items()}
