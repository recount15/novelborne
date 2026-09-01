"""世界书条目 schema 与加载器。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class LoreEntry:
    id: str
    title: str = ""
    keys: tuple[str, ...] = ()
    secondary_keys: tuple[str, ...] = ()
    content: str = ""
    category: str = "general"
    priority: int = 50
    constant: bool = False
    scan_depth: int = 6
    cooldown: int = 0
    max_chars: int = 800
    position: str = "before_turn"
    links: tuple[str, ...] = ()
    enabled: bool = True

    @classmethod
    def from_record(cls, raw: Mapping[str, Any], index: int = 0) -> "LoreEntry":
        def values(value: Any) -> tuple[str, ...]:
            if isinstance(value, str):
                return tuple(x.strip() for x in value.replace("，", ",").split(",") if x.strip())
            if isinstance(value, (list, tuple, set)):
                return tuple(str(x).strip() for x in value if str(x).strip())
            return ()
        return cls(
            id=str(raw.get("id") or f"lore-{index}"), title=str(raw.get("title") or raw.get("name") or ""),
            keys=values(raw.get("keys") or raw.get("keywords") or raw.get("key")),
            secondary_keys=values(raw.get("secondary_keys") or raw.get("aliases") or raw.get("secondary")),
            content=str(raw.get("content") or raw.get("text") or ""), category=str(raw.get("category") or "general"),
            priority=max(0, min(100, int(raw.get("priority", 50)))), constant=bool(raw.get("constant", False)),
            scan_depth=max(1, min(30, int(raw.get("scan_depth", 6)))), cooldown=max(0, int(raw.get("cooldown", 0))),
            max_chars=max(80, min(5000, int(raw.get("max_chars", 800)))), position=str(raw.get("position") or "before_turn"),
            links=values(raw.get("links")), enabled=bool(raw.get("enabled", True)),
        )


def load_entries(path: str | Path) -> list[LoreEntry]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, Mapping):
        raw = raw.get("entries", raw.get("items", []))
    if not isinstance(raw, list):
        raise ValueError("世界书文件必须是数组或 entries/items 对象")
    return [LoreEntry.from_record(item, i) for i, item in enumerate(raw) if isinstance(item, Mapping)]
