from __future__ import annotations

import unittest

from core.services.golden_finger_service import (
    confirm_golden_finger, correct_golden_finger, deterministic_budget,
    make_game_ready, prepare_golden_finger, validate_spec,
)
from core.services.pre_game_service import PreGameService


class TestGoldenFingerService(unittest.TestCase):
    def _state(self):
        svc = PreGameService()
        svc.select_book({"title": "试读书"})
        svc.prepare_script({"script": "城门遭遇追杀，世界有灵气规则。", "world_evidence": ["灵气规则"], "stage_evidence": ["追杀"], "player_evidence": ["新手"]})
        return svc.derive_difficulty()

    def test_deterministic_budget_and_full_state_machine(self):
        state = self._state()
        budget1 = deterministic_budget(state["prepared_script"], state["difficulty"]["level"])
        budget2 = deterministic_budget(state["prepared_script"], state["difficulty"]["level"])
        self.assertEqual(budget1, budget2)
        state = prepare_golden_finger(state)
        self.assertEqual(state["stage"], "gf_draft_ready")
        state = correct_golden_finger(state, model_text="not json")
        self.assertEqual(state["stage"], "gf_corrected")
        state = confirm_golden_finger(state)
        self.assertEqual(state["stage"], "gf_confirmed")
        self.assertEqual(make_game_ready(state)["stage"], "game_ready")

    def test_mechanism_guard_rejects_overpowered_effect(self):
        spec = {"name": "坏能力", "effect": "获得全知", "scope": "世界", "cost": "精神负荷", "cooldown": "每日一次", "limits": "不得抹除既成事实、不得越过世界上限、必须可验证"}
        result = validate_spec(spec, "D4")
        self.assertFalse(result["ok"])
        self.assertTrue(any("机制越界" in item for item in result["issues"]))


if __name__ == "__main__":
    unittest.main()
