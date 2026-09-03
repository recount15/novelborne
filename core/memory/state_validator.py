"""状态快照的确定性校验。"""
from __future__ import annotations

from typing import Any, Mapping

from .schema import CATEGORIES, STATE_SCHEMA, STATE_VERSION, blank_state


def validate_state(value: Mapping[str, Any] | None, *, fill: bool = True) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("状态快照必须是 JSON 对象")
    state = dict(value)
    if state.get("schema", STATE_SCHEMA) != STATE_SCHEMA:
        raise ValueError("状态快照 schema 无效")
    version = int(state.get("version", STATE_VERSION))
    if version != STATE_VERSION:
        raise ValueError("状态快照版本不受支持")
    if fill:
        defaults = blank_state(state.get("mode", ""), state.get("source", ""))
        for category in CATEGORIES:
            current = state.get(category)
            if not isinstance(current, dict):
                current = {}
            merged = dict(defaults.get(category) or {})
            merged.update(current)
            state[category] = merged
        if not isinstance(state.get("history"), list):
            state["history"] = []
    else:
        missing = [key for key in CATEGORIES if key not in state]
        if missing:
            raise ValueError("状态快照缺少字段: %s" % ", ".join(missing))
    return state


def validate_patch(patch: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        raise ValueError("状态变更提案必须是 JSON 对象")
    result = dict(patch)
    for key in result:
        if key not in CATEGORIES:
            raise ValueError("状态变更包含未知分类: %s" % key)
        if not isinstance(result[key], Mapping):
            raise ValueError("状态变更分类必须是对象: %s" % key)
    return result
