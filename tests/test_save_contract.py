"""Durable save-stage and complete-option contract tests."""
from __future__ import annotations

import pytest

from core.api.save_contract import (
    classify_state,
    is_usable_state,
    valid_options,
    validate_for_save,
)
from core.engine import persistence


def options():
    return [{"key": key, "text": f"行动 {key}"} for key in "ABCDEF"]


def test_valid_options_requires_ordered_non_empty_af():
    assert valid_options(options())
    assert not valid_options(options()[:-1])
    assert not valid_options([{"key": "A", "text": "x"}] * 6)
    assert not valid_options([{"key": "A", "text": ""}] + options()[1:])


def test_legacy_states_are_classified_without_mutation():
    opening = {"system": "rules", "mode": "强化模式", "options": [],
               "gf_confirmed": False, "opening_confirmed": False}
    committed = {"system": "rules", "mode": "基础模式", "options": options()}
    corrupt = {"system": "rules", "mode": "基础模式", "options": []}
    assert classify_state(opening) == "opening"
    assert classify_state(committed) == "committed"
    assert classify_state(corrupt) == "corrupt"
    assert opening["options"] == []


def test_persistence_rejects_streaming_and_corrupt(tmp_path):
    base = {"system": "rules", "mode": "基础模式", "options": options(), "history": []}
    streaming = {**base, "save_stage": "streaming"}
    corrupt = {**base, "options": []}
    with pytest.raises(ValueError, match="streaming"):
        persistence.save_state(streaming, root=tmp_path, session_id="s1")
    with pytest.raises(ValueError, match="A-F"):
        persistence.save_state(corrupt, root=tmp_path, session_id="s1")
    assert not list((tmp_path / "saves").glob("*.json"))


def test_committed_save_round_trip_preserves_stage_and_options(tmp_path):
    state = {"system": "rules", "mode": "基础模式", "options": options(), "history": []}
    path = persistence.save_state(state, root=tmp_path, session_id="s1")
    restored = persistence.load_state_strict("latest", root=tmp_path, session_id="s1")
    assert path.endswith("latest-s1.json")
    assert restored is not None
    assert restored["save_stage"] == "committed"
    assert valid_options(restored["options"])
    assert is_usable_state(restored)


def test_opening_save_is_usable_but_not_committed(tmp_path):
    state = {
        "system": "rules", "mode": "强化模式", "options": [],
        "gf_confirmed": False, "opening_confirmed": False,
        "save_stage": "opening", "history": [],
    }
    persistence.save_state(state, root=tmp_path, session_id="s2")
    restored = persistence.load_state_strict("latest", root=tmp_path, session_id="s2")
    assert restored is not None
    assert validate_for_save(restored) == "opening"
    assert is_usable_state(restored)
