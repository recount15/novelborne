# -*- coding: utf-8 -*-
"""答卷整合润色服务测试（非阻断后处理阶段）。"""
from __future__ import annotations

import unittest

from core.services import answer_polish_service as aps

DRAFT = "阿岚接过旧册。沈砚看向北墙，随后将铜扣收进袖中。"


class Blueprint:
    beat = "线索显形"
    goal = "查清暗渠"
    conflict = "守军阻拦"
    world_beats = ["城中传闻扩散"]
    cliffhanger = "远处马蹄声再次响起"


class TestPrompt(unittest.TestCase):
    def test_process_prompt_contains_four_stage_material(self):
        prompt = aps.build_polish_prompt(
            DRAFT, blueprint=Blueprint(), anchor_terms=["北墙", "暗渠"],
            active_members=[{"name": "阿岚", "character_card": {
                "goal": "守住北门", "fear": "城破", "speech_style": "短句",
                "unacceptable_behaviors": ["背叛同伴"],
            }}], quest_break="任务目标：拿到旧册；碎锚阶段：查证暗记",
            window=(200, 300))
        for marker in ("第一步：事实锁定", "第二步：接缝整合", "第三步：文学润色",
                       "第四步：输出整理", "线索显形", "北墙", "阿岚", "拿到旧册",
                       "200–300", DRAFT):
            self.assertIn(marker, prompt)
        self.assertNotIn("@@", prompt)


class TestPolishAnswer(unittest.TestCase):
    def test_successful_polish_replaces_draft(self):
        out = aps.polish_answer(
            DRAFT, model_fn=lambda prompt: "阿岚接过旧册，沈砚看向北墙，铜扣在袖中发出轻响。",
            window=(20, 300))
        self.assertTrue(out.used)
        self.assertIn("发出轻响", out.text)
        self.assertEqual(out.reason, "ok")

    def test_model_error_is_non_blocking(self):
        def boom(prompt):
            raise RuntimeError("temporary upstream error")
        out = aps.polish_answer(DRAFT, model_fn=boom, window=(20, 300))
        self.assertFalse(out.used)
        self.assertEqual(out.text, DRAFT)
        self.assertEqual(out.reason, "model_error")

    def test_empty_result_is_non_blocking(self):
        out = aps.polish_answer(DRAFT, model_fn=lambda prompt: "", window=(20, 300))
        self.assertFalse(out.used)
        self.assertEqual(out.text, DRAFT)
        self.assertEqual(out.reason, "empty_result")

    def test_json_or_fence_result_falls_back(self):
        for raw in ("```json\n{\"x\": 1}\n```", "```\n正文\n```"):
            out = aps.polish_answer(DRAFT, model_fn=lambda prompt, value=raw: value,
                                    window=(20, 300))
            self.assertFalse(out.used)
            self.assertEqual(out.text, DRAFT)
            self.assertEqual(out.reason, "format_residue")

    def test_result_outside_richness_window_falls_back(self):
        out = aps.polish_answer(DRAFT, model_fn=lambda prompt: "一句话。", window=(100, 300))
        self.assertFalse(out.used)
        self.assertEqual(out.reason, "window_drift")

    def test_overlong_result_outside_window_falls_back(self):
        out = aps.polish_answer(DRAFT, model_fn=lambda prompt: "长" * 80, window=(20, 60))
        self.assertFalse(out.used)
        self.assertEqual(out.reason, "window_drift")

    def test_short_scene_can_be_polished(self):
        out = aps.polish_answer(
            "短场景。", model_fn=lambda prompt: "短场景被自然地衔接起来。",
            window=(0, 40))
        self.assertTrue(out.used)


if __name__ == "__main__":
    unittest.main()
