# -*- coding: utf-8 -*-
"""回合蓝图机制测试（M3 Wave A）：解析校验、交叉校验与机械兜底。"""
from __future__ import annotations

import json
import unittest

from core.engine import turn_blueprint as tb

ANCHOR = json.dumps({
    "chapter": 1,
    "title": "城门风硬",
    "summary": "守军换岗时发现北墙裂痕",
    "events": ["换岗", "查证北墙"],
    "quotes": ["城门的风很硬"],
}, ensure_ascii=False)


def good_blueprint(terms, names, segments=2):
    seeds = [{"factor": "金手指", "direction": f"用金手指方案{i}", "preview": "付出冷却"}
             for i in range(4)]
    seeds += [{"factor": "性格", "direction": f"依性格行事{i}", "preview": "关系变化"}
              for i in range(2)]
    return {
        "beat": "查验北墙",
        "goal": "拿到旧册",
        "conflict": "守军阻拦",
        "segments": [
            {"id": f"seg{i + 1}", "role": f"第{i + 1}段", "window": [200, 300],
             "events": [f"事件{i + 1}"], "must_include": [terms[0]] if i == 0 else [],
             "must_mention": names[:1] if i == 0 else []}
            for i in range(segments)
        ],
        "anchor_plan": {"stage": "setup", "action_terms": [terms[0]],
                        "result_terms": [terms[0]], "causal_phrase": "因此落定"},
        "ripple_resolution": "以代价收束",
        "world_beats": ["城中传闻扩散"],
        "cliffhanger": "远处马蹄未散",
        "log_draft": {"player": "查验", "golden_finger": "先知", "world": "北墙",
                      "beat": "推进"},
        "option_seeds": seeds,
    }


class TestAnchorTerms(unittest.TestCase):
    def test_extract_terms_from_anchor_json(self):
        terms = tb.extract_anchor_terms(ANCHOR)
        self.assertTrue(terms)
        self.assertTrue(all(len(t) >= 2 for t in terms))

    def test_empty_anchor_gives_empty_terms(self):
        self.assertEqual(tb.extract_anchor_terms(""), [])


class TestParseBlueprint(unittest.TestCase):
    def setUp(self):
        self.terms = tb.extract_anchor_terms(ANCHOR)
        self.names = ["阿岚", "林秋"]

    def test_good_blueprint_passes(self):
        plan, errors = tb.parse_blueprint(
            good_blueprint(self.terms, self.names), segment_count=2,
            active_names=self.names, anchor_terms=self.terms)
        self.assertEqual(errors, [])

    def test_single_char_ripple_and_cliffhanger_accepted(self):
        # v2.0.3：min_len 2→1——「无」是提示词钦定的合法涟漪收束写法。
        data = good_blueprint(self.terms, self.names)
        data["ripple_resolution"] = "无"
        data["cliffhanger"] = "风"
        plan, errors = tb.parse_blueprint(
            data, segment_count=2, active_names=self.names,
            anchor_terms=self.terms)
        self.assertEqual(errors, [])
        self.assertEqual(plan.ripple_resolution, "无")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.origin, "model")
        self.assertEqual(len(plan.segments), 2)
        self.assertEqual(len(plan.option_seeds), 6)

    def test_non_object_rejected(self):
        plan, errors = tb.parse_blueprint("不是对象")
        self.assertIsNone(plan)
        self.assertTrue(errors)

    def test_missing_required_field(self):
        data = good_blueprint(self.terms, self.names)
        del data["cliffhanger"]
        plan, errors = tb.parse_blueprint(data, segment_count=2)
        self.assertIsNone(plan)
        self.assertTrue(any("cliffhanger" in e for e in errors))

    def test_segment_count_mismatch(self):
        plan, errors = tb.parse_blueprint(
            good_blueprint(self.terms, self.names, segments=2), segment_count=3,
            active_names=self.names, anchor_terms=self.terms)
        self.assertIsNone(plan)
        self.assertTrue(any("段数" in e for e in errors))

    def test_event_overlap_rejected(self):
        data = good_blueprint(self.terms, self.names)
        data["segments"][1]["events"] = data["segments"][0]["events"]
        plan, errors = tb.parse_blueprint(
            data, segment_count=2, active_names=self.names, anchor_terms=self.terms)
        self.assertIsNone(plan)
        self.assertTrue(any("互斥" in e for e in errors))

    def test_anchor_terms_outside_allowlist_rejected(self):
        data = good_blueprint(self.terms, self.names)
        data["anchor_plan"]["action_terms"] = ["凭空编造的词"]
        plan, errors = tb.parse_blueprint(
            data, segment_count=2, active_names=self.names, anchor_terms=self.terms)
        self.assertIsNone(plan)
        self.assertTrue(any("锚点文本之外" in e for e in errors))

    def test_unknown_member_name_rejected(self):
        data = good_blueprint(self.terms, self.names)
        data["segments"][0]["must_mention"] = ["路人甲"]
        plan, errors = tb.parse_blueprint(
            data, segment_count=2, active_names=self.names, anchor_terms=self.terms)
        self.assertIsNone(plan)
        self.assertTrue(any("未传入的角色名" in e for e in errors))

    def test_option_seed_count_and_split(self):
        data = good_blueprint(self.terms, self.names)
        data["option_seeds"] = data["option_seeds"][:5]
        plan, errors = tb.parse_blueprint(
            data, segment_count=2, active_names=self.names, anchor_terms=self.terms)
        self.assertIsNone(plan)
        self.assertTrue(any("6" in e for e in errors))

    def test_option_seed_distribution_enforced(self):
        data = good_blueprint(self.terms, self.names)
        data["option_seeds"][5]["factor"] = "金手指"
        plan, errors = tb.parse_blueprint(
            data, segment_count=2, active_names=self.names, anchor_terms=self.terms)
        self.assertIsNone(plan)
        self.assertTrue(any("分布" in e for e in errors))

    def test_empty_events_rejected(self):
        data = good_blueprint(self.terms, self.names)
        data["segments"][0]["events"] = []
        plan, errors = tb.parse_blueprint(
            data, segment_count=2, active_names=self.names, anchor_terms=self.terms)
        self.assertIsNone(plan)
        self.assertTrue(any("events" in e for e in errors))


class TestSynthesizeBlueprint(unittest.TestCase):
    def test_fallback_shape_is_valid(self):
        terms = tb.extract_anchor_terms(ANCHOR)
        plan = tb.synthesize_blueprint(
            segment_roles=[("开场", (200, 300)), ("收束", (300, 420))],
            action="查验北墙裂痕", anchor_terms=terms,
            active_names=["阿岚"], gf_hint="先知", persona_hint="苟道")
        self.assertEqual(plan.origin, "synthesized")
        self.assertEqual(len(plan.segments), 2)
        self.assertEqual(len(plan.option_seeds), 6)
        self.assertEqual(sum(1 for s in plan.option_seeds if s["factor"] == "金手指"), 4)
        self.assertEqual(sum(1 for s in plan.option_seeds if s["factor"] == "性格"), 2)
        # 段间事件互斥（兜底也必须满足自身规则）
        events = [e for seg in plan.segments for e in seg.events]
        self.assertEqual(len(events), len(set(events)))

    def test_fallback_without_anchor(self):
        plan = tb.synthesize_blueprint(
            segment_roles=[("开场", (200, 300))], action="推进")
        self.assertEqual(len(plan.segments), 1)
        self.assertTrue(plan.beat)
        self.assertTrue(plan.log_draft.get("player"))

    def test_segment_lookup(self):
        plan = tb.synthesize_blueprint(
            segment_roles=[("A", (100, 200)), ("B", (100, 200))], action="x")
        self.assertIsNotNone(plan.segment("seg1"))
        self.assertIsNone(plan.segment("nope"))


class TestDirectorPrompt(unittest.TestCase):
    def test_prompt_renders_all_placeholders(self):
        prompt = tb.build_director_prompt(
            paper_label="标准", stage="climax", segment_count=3, target_chars=950,
            action="查验北墙", context_tail="前文", active_names=["阿岚"],
            anchor_text=ANCHOR, ripple_hint="L1", gf_hint="先知", persona_hint="苟道")
        self.assertNotIn("@@", prompt)
        self.assertIn("阿岚", prompt)
        self.assertIn("950", prompt)


if __name__ == "__main__":
    unittest.main()
