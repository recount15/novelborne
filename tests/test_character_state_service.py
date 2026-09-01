from __future__ import annotations

import unittest

from core.services.character_state_service import (
    CharacterStateService,
    add_evidence,
    blank_character_state,
    core_personality_projection,
    decay_assertions,
    migrate_character_state,
    public_projection,
    retrieve_assertions,
)


class CharacterStateServiceTests(unittest.TestCase):
    def test_core_projection_is_immutable_and_prefers_core(self):
        character = {"core": {"traits": ["冷静"], "speech_style": "简短"}, "traits": ["错误"]}
        projection = core_personality_projection(character)
        projection["traits"].append("外部修改")
        self.assertEqual(character["core"]["traits"], ["冷静"])
        self.assertTrue(projection["core_locked"])

    def test_evidence_has_confidence_provenance_contradiction_and_decay(self):
        state = blank_character_state({"traits": ["谨慎"]})
        updated = add_evidence(state, "loyalty", "守诺", confidence=.8,
                               provenance={"source": "turn-2"}, contradiction="传闻",
                               round_no=2)
        self.assertEqual(state["assertions"], [])
        row = updated["assertions"][0]
        self.assertEqual(row["confidence"], .8)
        self.assertEqual(row["provenance"], [{"source": "turn-2"}])
        self.assertEqual(row["contradictions"], ["传闻"])
        self.assertEqual(row["last_round"], 2)
        decayed = decay_assertions(updated, steps=1)
        self.assertLess(decayed["assertions"][0]["confidence"], .8)

    def test_reinforcement_and_retrieval_threshold(self):
        state = add_evidence(None, "goal", "复仇", confidence=.3, provenance="a")
        state = add_evidence(state, "goal", "复仇", confidence=.7, provenance="b")
        row = retrieve_assertions(state, min_confidence=.6)[0]
        self.assertEqual(row["confidence"], .7)
        self.assertEqual(row["provenance"], ["a", "b"])
        self.assertEqual(retrieve_assertions(state, min_confidence=.8), [])

    def test_migration_legacy_shape_and_public_projection(self):
        legacy = {"evidence": [{"trait": "fear", "value": "火", "confidence": "0.6", "source": "scene"}],
                  "revision": "3"}
        state = migrate_character_state(legacy, {"personality": {"values": {"honor": 1}}})
        self.assertEqual(state["revision"], 3)
        self.assertEqual(state["assertions"][0]["key"], "fear")
        public = public_projection(state, min_confidence=.5)
        self.assertIn("core", public)
        self.assertNotIn("schema", public)
        public["core"]["values"]["honor"] = 0
        self.assertEqual(state["core"]["values"]["honor"], 1)

    def test_facade(self):
        service = CharacterStateService()
        state = service.add_evidence(None, "mood", "平静")
        self.assertEqual(len(service.retrieve(state)), 1)


if __name__ == "__main__":
    unittest.main()
