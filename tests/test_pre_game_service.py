from __future__ import annotations

import unittest

from core.services.pre_game_service import PreGameError, PreGameService


class TestPreGameService(unittest.TestCase):
    def test_full_preparation_and_evidence(self):
        svc = PreGameService()
        svc.select_book({"title": "试读书"})
        svc.prepare_script({"script": "城门遭遇追杀，灵气与禁忌规则显现。", "world_evidence": ["灵气规则"], "stage_evidence": ["城门遭遇追杀"], "player_evidence": ["新手"]})
        state = svc.derive_difficulty()
        self.assertEqual(state["stage"], "difficulty_ready")
        self.assertEqual(state["prepared_script"]["context_scope"], "prepared_script_only")
        self.assertTrue(state["difficulty"]["evidence"]["world"])
        self.assertTrue(1 <= state["difficulty"]["level"] <= 9)

    def test_order_is_enforced(self):
        svc = PreGameService()
        with self.assertRaises(PreGameError):
            svc.derive_difficulty()


if __name__ == "__main__":
    unittest.main()
