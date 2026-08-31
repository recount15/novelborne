"""结构化状态记忆 schema。

状态分为核心层、活跃层、归档层和索引层。状态值是当前已确认事实，
历史版本由 StateStore 追加保存，不能靠模型自然语言直接覆盖。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

STATE_SCHEMA = "fate-engine-state"
STATE_VERSION = 1

# 章节与回合统一由 scene 承载，不再单列 chapter 分类，避免 schema 与默认值不一致。
CATEGORIES = (
    "world", "time", "location", "body", "assets", "abilities", "relationships",
    "goals", "knowledge", "scene", "flags",
)


def blank_state(mode: str = "", source: str = "") -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "version": STATE_VERSION,
        "mode": mode,
        "source": source,
        "world": {"name": "", "rules": [], "conditions": []},
        "time": {"date": "", "clock": "", "timezone": "", "day_index": 0, "day_phase": ""},
        "location": {"name": "", "area": "", "coordinates": "", "relations": []},
        "body": {"condition": "正常", "injuries": [], "abnormalities": [], "fatigue": 0, "mental": "稳定"},
        "assets": {"currency": [], "equipment": [], "items": [], "debts": [], "income": []},
        "abilities": {"skills": [], "cultivation": "", "golden_finger": {"name": "无", "status": "inactive", "cooldown": 0, "costs": []}},
        "relationships": {"characters": [], "factions": []},
        "goals": {"current": [], "tasks": [], "foreshadowing": [], "promises": []},
        "knowledge": {"known": [], "unknown": [], "misconceptions": [], "sources": []},
        "scene": {"chapter": 1, "round": 0, "name": "", "anchor_ids": [], "pending": []},
        "flags": {"last_update": "", "last_worldbook": [], "conflicts": []},
        "history": [],
    }


def clone(value: Any) -> Any:
    return deepcopy(value)
