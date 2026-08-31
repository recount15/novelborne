# -*- coding: utf-8 -*-
"""agent_refill 逐空重填循环测试（重构 M4）。

核心断言：每空独立——任何一空的重填/失败/兜底绝不影响其他空；重填预算
上限生效；模型抛错按该空失败处理而非中断整批。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine import agent_refill
from core.engine.turn_grader import SegmentContract


class FakeModel:
    """可编程模型：按调用序返回预置答案，或抛异常。"""

    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.script:
            return ""
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _grade_len(contract, answer):
    """测试用批改：长度进窗口即通过，否则给中文错误。"""
    low, high = contract.window
    text = str(answer or "")
    if low <= len(text) <= high:
        return []
    return ["字数 %d 不在窗口 %d–%d" % (len(text), low, high)]


def _prompt(contract, errors):
    return "段%d 重填：%s" % (contract.index, "；".join(errors))


def _contract(index, low=4, high=10):
    return SegmentContract(index=index, role="推进", window=(low, high))


class TestRunRefillLoop(unittest.TestCase):
    def test_all_pass_no_model_call(self):
        model = FakeModel([])
        result = agent_refill.run_refill_loop(
            [_contract(1), _contract(2)], ["合格答案", "另一答案"],
            grade=_grade_len, refill_prompt=_prompt, model=model)
        self.assertEqual(model.prompts, [])
        self.assertTrue(all(row["ok"] for row in result["per_slot"]))
        self.assertEqual(result["stats"]["refill_rate"], 0.0)

    def test_single_slot_refill_does_not_touch_others(self):
        model = FakeModel(["补好的答案"])
        result = agent_refill.run_refill_loop(
            [_contract(1), _contract(2)], ["坏", "合格答案"],
            grade=_grade_len, refill_prompt=_prompt, model=model)
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(result["answers"][1], "合格答案")
        self.assertTrue(result["per_slot"][0]["ok"])
        self.assertEqual(result["per_slot"][0]["refills"], 1)
        self.assertEqual(result["per_slot"][1]["refills"], 0)

    def test_refill_budget_capped(self):
        model = FakeModel(["坏", "坏", "坏", "坏"])
        result = agent_refill.run_refill_loop(
            [_contract(1)], ["坏"], grade=_grade_len, refill_prompt=_prompt,
            model=model, attempts=2)
        self.assertEqual(len(model.prompts), 2)
        self.assertFalse(result["per_slot"][0]["ok"])
        self.assertEqual(result["per_slot"][0]["refills"], 2)

    def test_fallback_factory_used_when_budget_exhausted(self):
        model = FakeModel(["坏", "坏"])
        result = agent_refill.run_refill_loop(
            [_contract(1)], ["坏"], grade=_grade_len, refill_prompt=_prompt,
            model=model, attempts=2,
            fallback_factory=lambda contract: "兜底的答案")
        self.assertEqual(result["answers"][0], "兜底的答案")
        self.assertTrue(result["per_slot"][0]["fallback"])
        self.assertEqual(result["stats"]["fallback_rate"], 1.0)

    def test_model_exception_is_contained_per_slot(self):
        model = FakeModel([RuntimeError("transport down"), "补好的答案"])
        result = agent_refill.run_refill_loop(
            [_contract(1), _contract(2)], ["坏", "坏"],
            grade=_grade_len, refill_prompt=_prompt, model=model, attempts=1)
        self.assertFalse(result["per_slot"][0]["ok"])
        self.assertTrue(result["per_slot"][1]["ok"])
        self.assertEqual(result["answers"][1], "补好的答案")

    def test_missing_answers_treated_as_empty(self):
        model = FakeModel(["补好的答案"])
        result = agent_refill.run_refill_loop(
            [_contract(1)], [], grade=_grade_len, refill_prompt=_prompt,
            model=model)
        self.assertEqual(result["answers"][0], "补好的答案")

    def test_no_model_keeps_original_and_marks_failed(self):
        result = agent_refill.run_refill_loop(
            [_contract(1)], ["坏"], grade=_grade_len, refill_prompt=_prompt,
            model=None)
        self.assertFalse(result["per_slot"][0]["ok"])
        self.assertEqual(result["answers"][0], "坏")


class TestRefillBudgetMeta(unittest.TestCase):
    def test_meta_counts(self):
        meta = agent_refill.refill_budget_meta([
            {"index": 1, "ok": True, "refills": 0, "fallback": False, "errors": []},
            {"index": 2, "ok": True, "refills": 2, "fallback": False, "errors": []},
            {"index": 3, "ok": False, "refills": 2, "fallback": True, "errors": ["坏"]},
        ])
        self.assertEqual(meta["slots"], 3)
        self.assertEqual(meta["passed"], 2)
        # refilled 统计「发生过重填的空数」（不是总重填次数）
        self.assertEqual(meta["refilled"], 2)
        self.assertEqual(meta["fell_back"], 1)
        self.assertEqual(meta["max_refills"], 2)
        self.assertAlmostEqual(meta["refill_rate"], 2 / 3, places=3)
        self.assertAlmostEqual(meta["fallback_rate"], 1 / 3, places=3)

    def test_meta_empty(self):
        meta = agent_refill.refill_budget_meta([])
        self.assertEqual(meta["slots"], 0)
        self.assertEqual(meta["passed"], 0)
        self.assertEqual(meta["refilled"], 0)
        self.assertEqual(meta["fell_back"], 0)
        self.assertEqual(meta["refill_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
