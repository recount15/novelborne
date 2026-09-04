"""Durable save-state contract shared by the API and persistence layers.

A state can be shown while it is being generated, but only ``opening`` and
``committed`` states may be written as durable saves.  A committed state must
carry the complete, ordered A-F option set so a resumed game always has a
well-defined next action.
"""
from __future__ import annotations

from typing import Any, Mapping

SAVE_STAGES = ("opening", "streaming", "committed", "corrupt")
OPTION_KEYS = ("A", "B", "C", "D", "E", "F")


def _option_text(value: Any) -> str:
    return str(value or "").strip()


def valid_options(options: Any) -> bool:
    """Return whether *options* is exactly six unique non-empty A-F items."""
    if not isinstance(options, list) or len(options) != len(OPTION_KEYS):
        return False
    keys: list[str] = []
    for item in options:
        if not isinstance(item, Mapping):
            return False
        key = str(item.get("key") or "").strip().upper()
        if key not in OPTION_KEYS or not _option_text(item.get("text")):
            return False
        keys.append(key)
    return keys == list(OPTION_KEYS)


def option_error(options: Any) -> str:
    if not isinstance(options, list):
        return "options 缺失或不是列表"
    if len(options) != len(OPTION_KEYS):
        return f"options 必须完整包含 A-F 六项，当前为 {len(options)} 项"
    if not valid_options(options):
        return "options 必须按 A-F 顺序排列且每项文本非空"
    return ""


def _opening_flags(state: Mapping[str, Any]) -> tuple[bool, bool]:
    nested = state.get("opening_state")
    nested = nested if isinstance(nested, Mapping) else {}
    gf = state.get("gf_confirmed") is True or nested.get("gf_confirmed") is True
    opening = state.get("opening_confirmed") is True or nested.get("opening_confirmed") is True
    return gf, opening


def classify_state(state: Any) -> str:
    """Classify a state without mutating it.

    Explicit stage markers are authoritative for transient/opening frames. For
    legacy saves, enhanced states waiting for either confirmation are opening;
    any state with a system prompt and a complete option set is committed;
    other system-bearing states are corrupt because they cannot resume safely.
    """
    if not isinstance(state, Mapping):
        return "corrupt"
    explicit = str(state.get("save_stage") or "").strip().lower()
    if explicit in SAVE_STAGES:
        if explicit == "committed" and not valid_options(state.get("options")):
            return "corrupt"
        return explicit
    if not state.get("system"):
        return "opening"
    mode = str(state.get("mode") or "")
    gf_confirmed, opening_confirmed = _opening_flags(state)
    if mode.startswith("强化") and (not gf_confirmed or not opening_confirmed):
        return "opening"
    if valid_options(state.get("options")):
        return "committed"
    return "corrupt"


def is_durable_stage(stage: Any) -> bool:
    return str(stage or "") in ("opening", "committed")


def is_usable_state(state: Any) -> bool:
    """Whether a state may remain an active session (opening or committed)."""
    if not isinstance(state, Mapping) or not state.get("system"):
        return False
    return is_durable_stage(classify_state(state))


def validate_for_save(state: Any) -> str:
    """Validate and return the durable stage, raising on transient/corrupt data."""
    stage = classify_state(state)
    if stage == "streaming":
        raise ValueError("streaming 状态只能用于临时流，不允许写入正式存档")
    if stage == "corrupt":
        detail = option_error(state.get("options") if isinstance(state, Mapping) else None)
        raise ValueError(f"正式存档一致性错误：{detail or '状态不可恢复'}")
    if stage == "opening" and (not isinstance(state, Mapping) or not state.get("system")):
        raise ValueError("opening 状态缺少系统规则，不能写入正式存档")
    return stage


__all__ = [
    "SAVE_STAGES", "OPTION_KEYS", "valid_options", "option_error",
    "classify_state", "is_durable_stage", "is_usable_state", "validate_for_save",
]
