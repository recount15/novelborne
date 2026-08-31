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


if __name__ == "__main__":
    unittest.main()
