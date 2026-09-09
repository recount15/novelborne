"""Shared role projection and actual prompt boundaries; V00 isolates collection."""
import copy
import json

import pytest

from core.engine import modular_context, quest
from core.services import directives_service, options_service
from core.services.role_context_projection import project_role_context


def state_for(case):
    row = {"id": "f1", "key": "location", "value": "SAFE_BRANCH_MARKER",
           "confidence": 1.0, "provenance": {"source": "committed"},
           "knowledge_holder_id": "c1", "state_revision": 2}
    if case == "contradiction":
        row["contradictions"] = ["conflict"]
    if case == "future":
        row["state_revision"] = 3
    return {"branch_id": "b1", "state_revision": 2,
            "active_members": [{"character_id": "c1", "name": "Display",
                                "character_card": {"private": "PRIVATE_CARD"}}],
            "character_states": {"c1": {"assertions": [] if case == "missing" else [row]}},
            "nemesis_private": {"secret": "PRIVATE_RESOURCE"}}


@pytest.mark.parametrize("case", ["normal", "missing", "contradiction", "future"])
@pytest.mark.parametrize("consumer", ["helper", "modular", "options", "quest", "directives"])
def test_actual_role_consumer_boundaries(case, consumer):
    state = state_for(case)
    before = copy.deepcopy(state)
    if consumer == "helper":
        output = project_role_context(state)["block"]
    elif consumer == "modular":
        output = modular_context.build_modular_context(state)
    elif consumer == "options":
        output = options_service.build_options_prompt("act", "", "", state=state)
    elif consumer == "quest":
        output = quest.quest_offer_prompt(quest.build_quest_context(state, "short", 4))
    else:
        prompts = []
        result = directives_service.append_relay_fact(state, "A visible change", model_fn=lambda p:
            prompts.append(p) or json.dumps({"fact_norm": "A visible change", "scope": "character",
                                           "affected": ["c1"], "conflicts": []}))
        assert result["characters_touched"] == ["c1"]
        assert "Display" not in state["character_states"]
        output = prompts[0]
    assert ("SAFE_BRANCH_MARKER" in output) == (case == "normal")
    assert "PRIVATE_CARD" not in output
    assert "PRIVATE_RESOURCE" not in output
    assert "角色安全投影" in output
    if consumer != "directives":
        assert state == before


def test_projection_errors_block_success_and_do_not_call_model():
    state = state_for("normal")
    state.pop("state_revision")
    projected = project_role_context(state)
    assert not projected["ok"]
    assert "invalid_revision" in projected["omissions"]
    assert "projection_failed" in modular_context.build_modular_context(state)
    calls = []
    result = options_service.generate_options(None, "", state=state, model_fn=lambda p: calls.append(p))
    assert result["source"] == "none"
    assert not calls
    with pytest.raises(ValueError, match="role_projection_failed"):
        quest.build_quest_context(state, "short", 4)
    with pytest.raises(directives_service.DirectiveClientError, match="role_projection_failed"):
        directives_service.append_relay_fact(state, "visible change", model_fn=lambda p: calls.append(p))
    assert not calls
    assert "ledger" not in state


def test_legacy_names_never_lookup_cards_and_are_not_evidence():
    result = project_role_context({"active_members": ["Legacy"]},
                                  card_provider=lambda *args: pytest.fail("name join"))
    assert result["ok"] and not result["rich"]
    assert "Legacy" in result["block"]
    assert "legacy_label_not_evidence" in result["omissions"]


def test_full_provider_and_cutoff_are_forwarded(monkeypatch):
    from core.services import character_context_service as context
    source = {"book_id": "book", "source_hash": "hash", "texts": {1: "C knows SAFE."},
              "cutoff": {"chapter_no": 1, "offset": 13, "source_hash": "hash"}}
    monkeypatch.setattr(context, "read_reader_source", lambda *args: copy.deepcopy(source))
    card = {"schema_version": 2, "character_id": "c1", "revision": 4, "name": "C",
            "source": {"book_id": "book", "source_hash": "hash"}, "quality": {"state": "ready"},
            "evidence": [{"evidence_id": "e1", "book_id": "book", "source_hash": "hash",
                          "chapter_no": 1, "start": 0, "end": 13, "quote": "C knows SAFE."}],
            "facts": [{"fact_id": "f", "value": "SAFE", "status": "known", "evidence_ids": ["e1"],
                       "knowledge_holder_id": "c1"}],
            "semantic": {"mind_model": [{"value": "SAFE", "evidence_ids": ["e1"]}]}}
    # Exact end is 13 code points; future evidence cannot enter an earlier prefix.
    source["texts"][1] = "C knows SAFE."
    card["evidence"][0]["end"] = len(source["texts"][1])
    state = state_for("missing")
    state.update(book_id="book", book_dir="isolated", knowledge_cutoff={
        "chapter_no": 1, "offset": len(source["texts"][1]), "source_hash": "hash"})
    state["active_members"][0]["card_revision"] = 4
    calls = []
    monkeypatch.setattr(context, "_repository_card", lambda cid, rev: calls.append((cid, rev)) or card)
    assert '"value":"SAFE"' in project_role_context(state)["block"]
    assert calls == [("c1", 4)]
    state["knowledge_cutoff"]["offset"] = 0
    assert '"value":"SAFE"' not in project_role_context(state)["block"]


def test_roster_preserves_explicit_selected_identity():
    from core.services.game_setup import assemble_roster
    rows = assemble_roster([{"name": "Display", "character_card": {
        "character_id": "c1", "revision": 4}}], 1, "partner", "Partner")
    assert rows[0]["character_id"] == "c1"
    assert rows[0]["card_revision"] == 4
    state = state_for("normal")
    state.update(active_members=["Display"], companions=rows)
    assert "SAFE_BRANCH_MARKER" in project_role_context(state)["block"]
