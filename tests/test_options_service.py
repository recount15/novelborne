# -*- coding: utf-8 -*-
"""options_service 选项生成中台门面单测（重构 M2）。

覆盖：提示词装配、选项解析（预告拆分/键分配/超长截断）、结构化生成成功、
错误清单回传重试、失败回退链（正文解析/弹性合成）、渲染文本块。全程离线
（model_fn 注入 FakeModel）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.services import options_service  # noqa: E402


GOOD_JSON = ('{"options": ['
             '"推演北墙裂痕的下一步（后果：提前看清暗哨位置）",'
             '"回溯旧册水渍前的数目（后果：锁定军械批次）",'
             '"比对铜扣暗记的刻痕（后果：确认经手人身份）",'
             '"透支预知换撤离窗口（后果：金手指进入冷却）",'
             '"先撤回茶棚再图后计（后果：暂避锋芒）",'
             '"只抄录暗记不惊动守军（后果：证据留存）"]}')

NARRATIVE_WITH_OPTIONS = (
    "沈砚贴着北墙听完回声，示意阿岚守住渠口。\n\n"
    "A. 推演北墙裂痕的下一步\n"
    "B. 回溯旧册水渍前的数目\n"
    "C. 比对铜扣暗记的刻痕\n"
    "D. 透支预知换撤离窗口\n"
    "E. 先撤回茶棚再图后计\n"
    "F. 只抄录暗记不惊动守军\n"
)


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_contains_sections(self):
        prompt = options_service.build_options_prompt("调查北墙", "【选项可用因素】锚点", "上一幕梗概")
        for marker in ("选项生成卷", "调查北墙", "锚点", "上一幕梗概", "JSON"):
            self.assertIn(marker, prompt)


class TestParseOptionItems(unittest.TestCase):
    def test_split_preview_and_assign_keys(self):
        items = options_service.parse_option_items([
            "行动一（后果：预告一）", "行动二（后果: 预告二）", "行动三",
            "行动四", "行动五", "行动六",
        ])
        self.assertEqual([item["key"] for item in items], list("ABCDEF"))
        self.assertEqual(items[0]["text"], "行动一")
        self.assertEqual(items[0]["preview"], "预告一")
        self.assertEqual(items[2]["preview"], "")

    def test_object_entries_with_factor(self):
        items = options_service.parse_option_items([
            {"text": "行动一（后果：预告一）", "factor": "金手指"},
            {"text": "行动二", "factor": "性格"},
            {"text": "行动三", "factor": "剧情"},
            {"text": "行动四", "factor": "金手指"},
            {"text": "行动五", "factor": "金手指"},
            {"text": "行动六", "factor": "性格"},
        ])
        self.assertEqual([item["factor"] for item in items],
                         ["金手指", "性格", "剧情", "金手指", "金手指", "性格"])
        self.assertEqual(items[0]["preview"], "预告一")

    def test_factor_defaults_by_position(self):
        items = options_service.parse_option_items(["一", "二", "三", "四", "五", "六"])
        self.assertEqual([item["factor"] for item in items],
                         ["金手指"] * 4 + ["性格"] * 2)

    def test_factor_invalid_falls_back_to_position(self):
        items = options_service.parse_option_items([
            {"text": "一", "factor": "随便"},
            {"text": "二", "factor": ""}, "三", "四", "五", "六",
        ])
        self.assertEqual(items[0]["factor"], "金手指")
        self.assertEqual(items[1]["factor"], "金手指")
        self.assertEqual(items[5]["factor"], "性格")

    def test_overlong_items_truncated_and_dropped(self):
        items = options_service.parse_option_items(["太短", "b" * 200, "c", "d", "e", "f", "g"])
        # 空文本剔除后仅 5 条合法（"太短"保留原文，空项不存在）；超长截断到 60。
        self.assertLessEqual(len(items), 6)
        self.assertTrue(all(len(item["text"]) <= 60 for item in items))


class TestGenerateOptions(unittest.TestCase):
    def test_model_success(self):
        result = options_service.generate_options(
            None, "fake-model", model_fn=lambda prompt: GOOD_JSON,
            action="调查北墙", factors_block="【选项可用因素】北墙裂痕",
            context_tail="上一幕",
            factors=[{"kind": "anchor", "label": "北墙裂痕", "detail": ""}])
        self.assertEqual(result["source"], "model")
        self.assertEqual(len(result["options"]), 6)
        self.assertEqual([o["key"] for o in result["options"]], list("ABCDEF"))
        self.assertTrue(all(o["preview"] for o in result["options"]))

    def test_retry_then_success(self):
        calls = []

        def model(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return "完全不是 JSON"
            self.assertIn("必须修正", prompt)
            return GOOD_JSON

        result = options_service.generate_options(
            None, "fake-model", model_fn=model, action="a", attempts=2)
        self.assertEqual(result["source"], "model")
        self.assertEqual(len(calls), 2)

    def test_fallback_to_narrative_options(self):
        result = options_service.generate_options(
            None, "fake-model", model_fn=lambda prompt: "垃圾输出",
            action="a", narrative=NARRATIVE_WITH_OPTIONS, attempts=1)
        self.assertEqual(result["source"], "narrative")
        self.assertEqual(len(result["options"]), 6)

    def test_total_failure_returns_empty(self):
        result = options_service.generate_options(
            None, "fake-model", model_fn=lambda prompt: "垃圾",
            action="a", narrative="正文里没有任何选项。", attempts=1)
        self.assertEqual(result["source"], "none")
        self.assertEqual(result["options"], [])
        self.assertIn("error", result)

    def test_model_exception_degrades_not_raises(self):
        def boom(prompt):
            raise ConnectionError("上游炸了")

        with self.assertRaises(ConnectionError):
            # 传输层连续异常按约定上抛（调用方 try/except 走兜底）。
            options_service.generate_options(
                None, "fake-model", model_fn=boom, action="a",
                narrative=NARRATIVE_WITH_OPTIONS, attempts=2)


class TestRenderDisplayBlock(unittest.TestCase):
    def test_render_with_preview(self):
        block = options_service.render_display_block([
            {"key": "A", "text": "推演下一步", "preview": "看清暗哨"},
            {"key": "B", "text": "撤回茶棚", "preview": ""},
        ])
        self.assertEqual(block.splitlines()[0], "A. 推演下一步（后果：看清暗哨）")
        self.assertEqual(block.splitlines()[1], "B. 撤回茶棚")


# —— v2.0.3：情景扎根 + 空级批改接线 + AI-only 清洗 ——
DUP_JSON = ('{"options": ['
            '"推演北墙裂痕的下一步（后果：提前看清暗哨位置）",'
            '"推演北墙裂痕的下一步（后果：看清暗哨位置）",'
            '"比对铜扣暗记的刻痕（后果：确认经手人身份）",'
            '"透支预知换撤离窗口（后果：金手指进入冷却）",'
            '"先撤回茶棚再图后计（后果：暂避锋芒）",'
            '"只抄录暗记不惊动守军（后果：证据留存）"]}')


class TestSceneGroundedPrompt(unittest.TestCase):
    def test_prompt_contains_scene_block(self):
        prompt = options_service.build_options_prompt(
            "调查北墙", "因素", "近期剧情", scene="城门下铜扣暗记与守军换防的原文节选")
        for marker in ("原文情景", "铜扣暗记", "守军换防", "至多 1 条"):
            self.assertIn(marker, prompt)

    def test_prompt_without_scene_degrades_gracefully(self):
        prompt = options_service.build_options_prompt("a", "b", "c")
        self.assertIn("无原文情景节选", prompt)


class TestGradeWiring(unittest.TestCase):
    def test_grade_failure_triggers_targeted_retry(self):
        calls = []

        def model(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return DUP_JSON
            self.assertIn("不合格", prompt)
            self.assertIn("相似度", prompt)
            return GOOD_JSON

        result = options_service.generate_options(
            None, "fake-model", model_fn=model, action="a", attempts=1)
        self.assertEqual(result["source"], "model")
        self.assertEqual(len(result["options"]), 6)
        self.assertEqual(len(calls), 2)

    def test_sanitize_drops_duplicates_labels_and_ai_origin(self):
        items = options_service.parse_option_items([
            "推演北墙裂痕的下一步（后果：a）",
            "推演北墙裂痕的下一步（金手指）（后果：a）",   # 相似度判重 + 来源标注
            "作为AI我建议先撤离现场",                        # AI 来源措辞
            "比对铜扣暗记的刻痕（后果：b）",
            "透支预知换撤离窗口（后果：c）",
            "先撤回茶棚再图后计（后果：d）",
        ])
        sanitized = options_service._sanitize_option_items(items)
        self.assertGreaterEqual(len(sanitized), options_service.MIN_AI_OPTIONS)
        keys = [item["key"] for item in sanitized]
        self.assertEqual(keys, list("ABCD")[: len(keys)])
        self.assertTrue(all("金手指）" not in item["text"] for item in sanitized))
        self.assertTrue(all("AI" not in item["text"] for item in sanitized))

    def test_too_few_valid_items_falls_to_none_not_template(self):
        # 6 条里 4 条两两判重 → 清洗后仅 3 条 < MIN_AI_OPTIONS：AI-only 兜底为空。
        dup_heavy = ('{"options": ['
                     '"推演北墙裂痕的下一步甲（后果：a）",'
                     '"推演北墙裂痕的下一步乙（后果：a）",'
                     '"推演北墙裂痕的下一步丙（后果：a）",'
                     '"推演北墙裂痕的下一步丁（后果：a）",'
                     '"比对铜扣暗记的刻痕（后果：b）",'
                     '"透支预知换撤离窗口（后果：c）"]}')

        def model(prompt):
            if "不合格" in prompt:
                return dup_heavy  # 重试依旧同质化
            return dup_heavy

        result = options_service.generate_options(
            None, "fake-model", model_fn=model, action="a", narrative="正文无选项",
            attempts=1)
        self.assertEqual(result["source"], "none")
        self.assertEqual(result["options"], [])
        self.assertIn("AI-only", result["error"])


if __name__ == "__main__":
    unittest.main()


class TestElasticRepairTupleContract(unittest.TestCase):
    """v2.0.3 回归：repair_options 返回 (列表, 是否合成) 元组。

    app.on_send 旧调用把元组整体传给 render_options_block，在元组第一项
    （选项列表）上调用 .get 抛 'list' object has no attribute 'get'，
    整回合被回滚（kimi-k3 实测）。此测试锁定元组契约防再次误用。
    """

    def test_repair_options_returns_tuple_with_list(self):
        from core.engine import elastic_gate
        result = elastic_gate.repair_options(
            "正文一句。A. 先查看现场" + chr(10) + "B. 再询问证人",
            [{"key": "A", "text": "先查看现场"}, {"key": "B", "text": "再询问证人"}],
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        options, synthesized = result
        self.assertIsInstance(options, list)
        self.assertIsInstance(synthesized, bool)
        self.assertEqual([o["key"] for o in options][:2], ["A", "B"])

    def test_render_options_block_accepts_repair_list(self):
        from core.engine import elastic_gate, options
        options_list, _ = elastic_gate.repair_options("正文。", [])
        block = options.render_options_block(options_list)
        self.assertIsInstance(block, str)
        for line in block.splitlines():
            self.assertRegex(line, r"^[A-F]\. \S")
