# -*- coding: utf-8 -*-
"""quality_gate 的纯本地测试：覆盖规则维度、裁判证据与有界修订安全阀。"""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine.quality_gate import (  # noqa: E402
    DIMENSION_WEIGHTS,
    ScoreCard,
    bounded_refine,
    has_scaffold,
    score_turn,
)


GOOD_OPTIONS = tuple({"key": key, "text": text} for key, text in zip(
    "ABCDEF", (
        "趁夜色摸向渡口查验船期", "先登船控制舵手改变航向",
        "以听风耳辨认岸上埋伏", "放走信使追踪幕后主使",
        "稳住沈青伤势再决定", "与守门人当面对质谈判",
    )))
CONTEXT = {
    "world_terms": ["北墙", "守军"], "anchor_terms": ["北门失守"],
    "character_names": ["沈青", "阿岚"],
    "previous_narrative": "沈青在北墙发现守军换岗的裂痕。",
}
CONTRACTS = {"required_terms": ["玄铁令"], "forbidden_terms": ["系统提示"]}
GOOD = "沈青握紧玄铁令，沿着北墙听见守军换岗的脚步。阿岚指向城门，低声道：‘北门失守。’雨幕中火把骤然熄灭，众人随即转身避开追兵。"


class QualityGateTests(unittest.TestCase):
    def test_comprehensive_dimensions_and_clean_score(self):
        card = score_turn(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS)
        self.assertEqual(set(card.dimensions), set(DIMENSION_WEIGHTS))
        self.assertTrue(card.passed)
        self.assertGreaterEqual(card.total, 70)
        self.assertEqual(card.audit["judge"]["status"], "not_requested")

    def test_format_residues_are_hard_failures(self):
        card = score_turn(GOOD + "\n```json\n{}\n```\n【系统自检】ok", GOOD_OPTIONS, CONTEXT, CONTRACTS)
        codes = {issue.code for issue in card.issues}
        self.assertFalse(card.passed)
        self.assertIn("code_fence", codes)
        self.assertIn("system_residue", codes)

    def test_empty_and_repeated_filler_are_rejected(self):
        empty = score_turn(" ", GOOD_OPTIONS, CONTEXT, CONTRACTS)
        self.assertFalse(empty.passed)
        repeated = "雨声敲窗，灯影摇动。" * 8
        card = score_turn(repeated, GOOD_OPTIONS, {}, {})
        self.assertIn("repeated_sentence", {issue.code for issue in card.issues})
        self.assertIn("repeated_filler", {issue.code for issue in card.issues})

    def test_option_ai_origin_and_duplication(self):
        options = list(GOOD_OPTIONS)
        options[0] = {"text": "作为AI，我建议趁夜色摸向渡口"}
        options[1] = {"text": options[2]["text"]}
        card = score_turn(GOOD, options, CONTEXT, CONTRACTS)
        codes = {issue.code for issue in card.issues}
        self.assertIn("ai_origin", codes)
        self.assertIn("duplicate_options", codes)

    def test_required_forbidden_context_anchor_and_character_terms(self):
        card = score_turn("阿岚沿着南街离开，系统提示：安全。", GOOD_OPTIONS, CONTEXT, CONTRACTS)
        codes = {issue.code for issue in card.issues}
        self.assertIn("missing_required_terms", codes)
        self.assertIn("forbidden_terms", codes)
        self.assertIn("partial_coverage", codes)
        self.assertIn("missing_anchor", codes)
        self.assertNotIn("missing_character", codes)  # 阿岚 命中，至少一名角色即可

    def test_judge_requires_supported_evidence(self):
        def supported(payload):
            return {"scores": {"style": 95}, "evidence": {"style": ["沈青握紧玄铁令"]}}

        accepted = score_turn(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS, judge_fn=supported)
        self.assertTrue(accepted.audit["judge"]["used"])
        self.assertTrue(any(issue.code == "judge_evidence" for issue in accepted.issues))

        def unsupported(payload):
            return {"scores": {"style": 100}, "evidence": {"style": ["模型认为文笔极佳"]}}

        rejected = score_turn(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS, judge_fn=unsupported)
        self.assertFalse(rejected.audit["judge"]["used"])
        self.assertTrue(any(issue.code == "unsupported_judge_evidence" for issue in rejected.issues))

    def test_baseline_audit_has_dimension_deltas(self):
        baseline = score_turn(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS)
        card = score_turn(GOOD + " 雨落。", GOOD_OPTIONS, CONTEXT, CONTRACTS, baseline=baseline)
        self.assertEqual(set(card.audit["baseline_delta"]), set(DIMENSION_WEIGHTS))

    def test_empty_options_is_neutral_free_input_mode(self):
        # v2.0.3 AI-only：模型不可用时本回合合法无选项，不得因此判负。
        card = score_turn(GOOD, [], CONTEXT, CONTRACTS)
        self.assertEqual(card.dimensions["options"], 100.0)
        empty = [i for i in card.issues if i.code == "empty_options"]
        self.assertTrue(empty and empty[0].severity == "info")
        self.assertTrue(card.passed)

    def test_narrative_only_candidate_accepted_for_free_input_turn(self):
        weak = GOOD + "总而言之，空气中弥漫着不安的气息。仿佛在诉说着什么，一切似乎未完待续。"

        def model(request):
            return GOOD  # 纯正文修订：自由输入回合选项为空也合法

        result = bounded_refine(weak, (), CONTEXT, CONTRACTS, model, max_rounds=2)
        self.assertEqual(result.narrative, GOOD)
        self.assertEqual(result.options, ())
        self.assertTrue(any(event.accepted for event in result.events))

    def test_early_pass_skips_refinement_entirely(self):
        calls = []

        def model(request):  # pragma: no cover - 不应被调用
            calls.append(1)
            return GOOD

        initial = score_turn(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS)
        result = bounded_refine(
            GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS, model,
            early_pass_score=initial.total - 0.01)
        self.assertEqual(result.stop_reason, "early_pass")
        self.assertEqual(result.rounds, 0)
        self.assertEqual(calls, [])
        self.assertEqual(result.narrative, GOOD)


class BoundedRefinementTests(unittest.TestCase):
    def test_scaffold_text_is_hard_format_failure(self):
        # v2.0.3（kimi-k3 实测）：段卷兜底段的机械短语绝不能因命中必含词
        # /锚点词而拿高分——脚手架按格式硬伤处理，总分必须显著低于及格线。
        scaffold = ("（推进与转折）沈青就地应对。玄铁令随之显形。"
                    + "北门失守是否因此改变？。" * 10)
        self.assertTrue(has_scaffold(scaffold))
        self.assertFalse(has_scaffold(GOOD))
        card = score_turn(scaffold, GOOD_OPTIONS, CONTEXT, CONTRACTS)
        codes = {issue.code for issue in card.issues}
        self.assertIn("scaffold_apply", codes)
        self.assertIn("scaffold_materialize", codes)
        self.assertIn("scaffold_role_label", codes)
        self.assertFalse(card.passed)
        self.assertLess(card.total, 70.0)

    def test_amnesty_round_accepts_fix_against_gamed_baseline(self):
        # 初稿是机械分被污染的脚手架（逐字命中全部锚点词 anchors=高分），
        # 修订稿是自然正文但锚点覆盖低——旧分维无回退门会拒绝真正更好的
        # 修订（实测：垃圾稿 84.75 vs 好稿 87.25 被 regressed=[anchors] 拒）。
        # 特赦轮：初稿带 error 级硬伤时首个候选免受分维门约束。
        scaffold = ("（落点与钩子）沈青就地应对。玄铁令随之显形。"
                    + "北门失守。" * 12)

        def model(request):
            return {"narrative": GOOD, "options": GOOD_OPTIONS}

        result = bounded_refine(scaffold, GOOD_OPTIONS, CONTEXT, CONTRACTS, model,
                                max_rounds=1)
        self.assertEqual(result.narrative, GOOD)
        self.assertTrue(any(event.accepted and event.reason == "amnesty_accept"
                            for event in result.events))
        self.assertGreater(result.scorecard.total, result.initial_scorecard.total)

    def test_amnesty_does_not_apply_to_clean_baseline(self):
        # 干净基线照常受分维无回退门保护（防反作用机制不被特赦削弱）。
        def model(request):
            return {"narrative": "完全脱离上下文的空泛文字。" * 3,
                    "options": GOOD_OPTIONS}

        result = bounded_refine(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS, model,
                                max_rounds=1)
        self.assertEqual(result.narrative, GOOD)
        self.assertTrue(any(event.reason == "dimension_regression"
                            for event in result.events))

    def test_keep_best_rejects_dimension_regression(self):
        calls = []

        def model(request):
            calls.append(request.round_index)
            return {"narrative": "完全脱离上下文的空泛文字。" * 3, "options": GOOD_OPTIONS}

        result = bounded_refine(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS, model, max_rounds=3)
        self.assertEqual(result.narrative, GOOD)
        self.assertTrue(any(event.reason == "dimension_regression" for event in result.events))
        self.assertEqual(result.stop_reason, "two_no_improvement_rounds")

    def test_failed_repair_rolls_back_and_two_failures_stop(self):
        attempts = []

        def model(request):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("offline")
            return {"narrative": "", "options": GOOD_OPTIONS}

        result = bounded_refine(GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS, model, max_rounds=3)
        self.assertEqual(result.narrative, GOOD)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result.stop_reason, "two_no_improvement_rounds")
        self.assertTrue(all(not event.accepted for event in result.events))

    def test_accepts_only_real_improvement_and_honors_hard_bounds(self):
        better = GOOD + " 他回望北墙，雨水沿着甲胄流下，远处传来三声急促铜铃。"
        count = []

        def model(request):
            count.append(1)
            return {"narrative": better, "options": GOOD_OPTIONS}

        result = bounded_refine(
            GOOD, GOOD_OPTIONS, CONTEXT, CONTRACTS, model,
            max_rounds=99, max_calls=99, max_seconds=1800,
        )
        self.assertLessEqual(result.rounds, 3)
        self.assertLessEqual(result.calls, 8)
        self.assertLessEqual(len(count), 3)
        self.assertGreaterEqual(result.scorecard.total, result.initial_scorecard.total)


if __name__ == "__main__":
    unittest.main()
