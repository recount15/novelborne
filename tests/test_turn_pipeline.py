# -*- coding: utf-8 -*-
"""回合试卷管线门面测试（M3）：零网络，模型全部注入。

覆盖：legacy 信号、导演卷兜底、段卷批改重填、确定性兜底段、选项契约、
format_gate 回退、TurnResult 形状、Wave B 扁平并发（作业数 ≤ 硬上限）。
"""
from __future__ import annotations

import json
import threading
import unittest

from core.engine import papers, parallel, turn_blueprint
from core.services import turn_pipeline as tp

ANCHOR = json.dumps({"chapter": 1, "title": "城门风硬",
                     "summary": "守军换岗时发现北墙裂痕",
                     "events": ["换岗", "查证北墙"]}, ensure_ascii=False)

STATE = {
    "round": 3, "chapter_round": 1, "turn_budget": 3, "paper_tier": 3,
    "story_richness": 700, "persona": "谋士", "golden_finger": "先知",
    "anchor_shattered": False,
}


def director_json(paper, terms, names):
    seeds = [{"factor": "金手指", "direction": f"金手指路线{i}", "preview": "冷却代价"}
             for i in range(4)]
    seeds += [{"factor": "性格", "direction": f"性格路线{i}", "preview": "关系变化"}
              for i in range(2)]
    return json.dumps({
        "beat": "查验北墙", "goal": "拿到旧册", "conflict": "守军阻拦",
        "segments": [
            {"id": f"seg{i + 1}", "role": seg.role, "window": list(seg.window),
             "events": [f"独立事件{i + 1}"],
             "must_include": [], "must_mention": names[:1] if i == 0 else []}
            for i, seg in enumerate(paper.segments)
        ],
        "anchor_plan": {"stage": paper.stage, "action_terms": terms[:1],
                        "result_terms": terms[:1], "causal_phrase": "因此落定"},
        "ripple_resolution": "以代价收束",
        "world_beats": ["传闻扩散"],
        "cliffhanger": "马蹄声未散",
        "log_draft": {"player": "查验北墙", "golden_finger": "先知示警",
                      "world": "北墙裂痕", "beat": "推进"},
        "option_seeds": seeds,
    }, ensure_ascii=False)


def options_json():
    # 六条必须互不相同（difflib ≥0.8 判重）且 ≥8 字：这是选项空的批改契约。
    items = [
        "推演北墙裂痕下一步走势（后果：提前看清暗哨位置）",
        "回溯旧册水渍前的数目（后果：锁定军械批次）",
        "比对铜扣暗记的刻痕（后果：确认经手人身份）",
        "透支预知换撤离窗口（后果：金手指进入冷却）",
        "先撤回茶棚再图后计（后果：暂避锋芒）",
        "只抄录暗记不惊动守军（后果：证据留存）",
    ]
    return json.dumps({"options": items}, ensure_ascii=False)


def filler(low, high, name="阿岚"):
    """造一段落在窗口内、含点名与因果词的合格段落。"""
    base = f"{name}沿着北墙查验裂痕，因此守军的换岗节奏被彻底打乱。"
    text = base
    while len(text) < low:
        text += "风声压过火把的噼啪，砖缝里的旧痕愈发清楚，随行的人不敢多问。"
    return text[:high - 1] if len(text) >= high else text


class ScriptedModel:
    """按提示词特征分派答案的可编程模型（线程安全计数）。"""

    def __init__(self, paper, terms, names, *, director=None, segment=None,
                 options=None, fail_segments=False):
        self.paper = paper
        self._director = director if director is not None else director_json(paper, terms, names)
        self._segment = segment
        self._options = options if options is not None else options_json()
        self.fail_segments = fail_segments
        self.calls = []
        self.concurrent = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, prompt):
        with self._lock:
            self.calls.append(prompt)
            self.concurrent += 1
            self.peak = max(self.peak, self.concurrent)
        try:
            # 分派靠各卷独有标记：导演卷含 option_seeds、段卷含「段卷」、
            # 选项卷含 options 字段要求（导演卷也含「选项/后果」，不能用它分派）。
            if "option_seeds" in prompt:
                return self._director
            if "`options`" in prompt or '"options"' in prompt:
                return self._options
            if self.fail_segments:
                raise RuntimeError("segment transport down")
            if callable(self._segment):
                return self._segment(prompt)
            if self._segment is not None:
                return self._segment
            return self._default_segment(prompt)
        finally:
            with self._lock:
                self.concurrent -= 1

    def _default_segment(self, prompt):
        low, high = 200, 320
        for line in prompt.splitlines():
            if "字" in line and "–" in line:
                nums = [int(x) for x in __import__("re").findall(r"\d+", line)[:2]]
                if len(nums) == 2:
                    low, high = nums
                break
        return filler(low, high)


class TestLegacySignal(unittest.TestCase):
    def test_free_stage_paper_exists(self):
        # v2.0.4: free 阶段现在放行，tier 3 有 free 试卷
        # 但由于模型返回空字符串，会触发段卷失败
        state = dict(STATE, anchors_shattered_from=1)
        # 模型返回空字符串会导致段卷全线失败，抛出 TurnUpstreamError
        with self.assertRaises(tp.TurnUpstreamError):
            tp.run_turn(state, None, "m", model_fn=lambda p: "",
                       message="行动", anchor_text=ANCHOR)

    def test_missing_paper_returns_legacy(self):
        state = dict(STATE, paper_tier=99)
        result = tp.run_turn(state, None, "m", model_fn=lambda p: "",
                             message="行动", anchor_text=ANCHOR, tier=99)
        self.assertEqual(result, tp.LEGACY)


class TestRunTurn(unittest.TestCase):
    def setUp(self):
        self.paper = papers.get_paper(3, "setup")
        self.terms = turn_blueprint.extract_anchor_terms(ANCHOR)
        self.names = ["阿岚", "林秋"]

    def _run(self, model, **kw):
        return tp.run_turn(
            STATE, None, "m", model_fn=model, message="查验北墙裂痕",
            context_blocks="前文略", active_members=[{"name": n} for n in self.names],
            anchor_text=ANCHOR, **kw)

    def test_happy_path_shape(self):
        model = ScriptedModel(self.paper, self.terms, self.names)
        result = self._run(model)
        self.assertIsInstance(result, tp.TurnResult)
        self.assertTrue(result.narrative)
        self.assertEqual(len(result.options), 6)
        self.assertTrue(result.log_line)
        self.assertEqual(result.paper_key, f"{self.paper.family}_l3_setup")
        self.assertEqual(result.blueprint.origin, "model")
        for key in ("length", "interaction", "anchor"):
            self.assertIn(key, result.scene_validation)

    def test_option_contract_has_factor_and_preview(self):
        model = ScriptedModel(self.paper, self.terms, self.names)
        result = self._run(model)
        factors = [o.get("factor") for o in result.options]
        self.assertEqual(sum(1 for f in factors if f == "金手指"), 4)
        self.assertEqual(sum(1 for f in factors if f == "性格"), 2)
        self.assertTrue(all(o.get("preview") for o in result.options))
        self.assertEqual([o["key"] for o in result.options], list("ABCDEF"))

    def test_director_failure_falls_back_to_synthesized(self):
        model = ScriptedModel(self.paper, self.terms, self.names,
                              director="这不是 JSON")
        result = self._run(model)
        self.assertIsInstance(result, tp.TurnResult)
        self.assertEqual(result.blueprint.origin, "synthesized")
        self.assertTrue(result.narrative)

    def test_bad_segments_degrade_to_legacy_never_ship_scaffold(self):
        # v2.0.3（kimi-k3 实测）：段卷批改重填耗尽落兜底段后正文是蓝图
        # 脚手架（“X就地应对/Y随之显形”）；润色与质量门都没能重写成自然
        # 正文时，整回合必须降级 LEGACY 单卷路径——脚手架绝不出厂。
        model = ScriptedModel(self.paper, self.terms, self.names, segment="太短")
        result = self._run(model)
        self.assertEqual(result, tp.LEGACY)

    def test_scaffold_narrative_never_ships_even_after_postprocess(self):
        # 终检防线：组装正文混入脚手架短语时，即便后处理未救回也不得
        # 以 TurnResult 出厂（返回 LEGACY 交由单卷路径重写）。
        model = ScriptedModel(self.paper, self.terms, self.names,
                              segment="（推进与转折）阿岚就地应对。北墙随之显形。")
        result = self._run(model)
        self.assertEqual(result, tp.LEGACY)

    def test_segment_transport_failure_raises(self):
        model = ScriptedModel(self.paper, self.terms, self.names, fail_segments=True)
        with self.assertRaises(tp.TurnUpstreamError):
            self._run(model)

    def test_options_failure_yields_no_template_options(self):
        # v2.0.3 AI-only 硬性原则：选项卷全线失败时不出模板/种子选项，
        # options 为空保留自由输入，绝不展示「变体 N」类机械文案。
        model = ScriptedModel(self.paper, self.terms, self.names, options="坏答卷")
        result = self._run(model)
        self.assertEqual(len(result.options), 0)
        self.assertEqual(result.options_source, "none")
        joined = "\n".join(str(o.get("text") or "") for o in [])
        self.assertEqual(joined, "")
        self.assertIn("postprocess", result.agent_meta)

    def test_context_injection_reaches_director_and_segments(self):
        model = ScriptedModel(self.paper, self.terms, self.names)
        history = [{"role": "user", "content": "上一回合玩家行动"},
                   {"role": "assistant", "content": "上一回合叙事结果。"}]
        result = self._run(model, system_prompt="本书世界观：北境戍城，无任何科幻元素")
        # 直接驱动一次带历史的运行以验证近期摘要注入。
        model2 = ScriptedModel(self.paper, self.terms, self.names)
        tp.run_turn(dict(STATE, history=history), None, "m", model_fn=model2,
                    message="查验北墙裂痕", system_prompt="本书世界观：北境戍城",
                    context_blocks="前文略",
                    active_members=[{"name": n} for n in self.names],
                    anchor_text=ANCHOR)
        director_calls = [p for p in model2.calls if "option_seeds" in p]
        segment_calls = [p for p in model2.calls if "段卷" in p]
        self.assertTrue(director_calls)
        self.assertTrue(any("北境戍城" in p for p in director_calls),
                        "导演卷必须携带作品设定摘要")
        self.assertTrue(any("上一回合叙事结果" in p for p in segment_calls),
                        "段卷必须携带近期回合摘要")
        self.assertIsInstance(result, tp.TurnResult)

    def test_scene_excerpt_reaches_options_prompt(self):
        model = ScriptedModel(self.paper, self.terms, self.names)
        self._run(model, scene_excerpt="城门下的守军换岗与铜扣暗记的原文节选")
        option_calls = [p for p in model.calls if "选项生成卷" in p]
        self.assertTrue(option_calls)
        self.assertTrue(any("铜扣暗记" in p for p in option_calls),
                        "选项卷必须携带原文情景节选")

    def test_quality_gate_audit_recorded(self):
        model = ScriptedModel(self.paper, self.terms, self.names)
        result = self._run(model)
        post = result.agent_meta.get("postprocess") or {}
        self.assertEqual(post.get("version"), tp.QUALITY_GATE_VERSION)
        self.assertTrue(post.get("enabled"))
        self.assertIn("initial", post)
        self.assertIn("final", post)
        self.assertIn("stop_reason", post)

    def test_wave_b_is_flat_and_within_hard_limit(self):
        model = ScriptedModel(self.paper, self.terms, self.names)
        result = self._run(model)
        self.assertIsInstance(result, tp.TurnResult)
        self.assertLessEqual(model.peak, parallel.HARD_LIMIT,
                             "Wave B 必须单层扁平，并发不得超过硬上限")

    def test_climax_stage_uses_climax_paper(self):
        state = dict(STATE, chapter_round=3, turn_budget=3)
        paper = papers.get_paper(3, "climax")
        model = ScriptedModel(paper, self.terms, self.names,
                              director=director_json(paper, self.terms, self.names))
        result = tp.run_turn(
            state, None, "m", model_fn=model, message="收束",
            active_members=[{"name": n} for n in self.names], anchor_text=ANCHOR)
        self.assertIsInstance(result, tp.TurnResult)
        self.assertEqual(result.paper_key, f"{paper.family}_l3_climax")


if __name__ == "__main__":
    unittest.main()
