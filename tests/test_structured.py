# -*- coding: utf-8 -*-
"""engine.structured 结构化填空基础件单测（重构 M0）。

覆盖：extract_json 阶梯、FieldSpec 校验各维度、spec_prompt 渲染、
structured_call 错误清单回传重试/兜底返回/传输层连续异常上抛。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import structured  # noqa: E402


SPECS = (
    structured.FieldSpec("goal", "str", min_len=4, max_len=40, hint="本回合目标"),
    structured.FieldSpec("progress", "int"),
    structured.FieldSpec("tags", "list", min_items=2, max_items=4, item_max_len=10),
    structured.FieldSpec("level", "str", enum=("低", "高")),
    structured.FieldSpec("note", "str", required=False, default="-"),
)

GOOD = {"goal": "夺回矿镇", "progress": 3, "tags": ["锚点", "宿敌"], "level": "高"}


class TestExtractJson(unittest.TestCase):
    def test_dict_passthrough(self):
        self.assertEqual(structured.extract_json({"a": 1}), {"a": 1})

    def test_fenced(self):
        self.assertEqual(structured.extract_json("```json\n{\"a\": 1}\n```"), {"a": 1})

    def test_prose_wrapped(self):
        text = "以下是结果：{\"a\": 1} 希望采纳"
        self.assertEqual(structured.extract_json(text), {"a": 1})

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            structured.extract_json("完全不是 JSON")
        with self.assertRaises(ValueError):
            structured.extract_json("")
        with self.assertRaises(ValueError):
            structured.extract_json("[1,2,3]")


class TestValidate(unittest.TestCase):
    def test_good_passes(self):
        self.assertEqual(structured.validate(SPECS, GOOD), [])

    def test_missing_required(self):
        errors = structured.validate(SPECS, {"progress": 1})
        self.assertTrue(any("goal" in e for e in errors))

    def test_type_errors(self):
        bad = dict(GOOD, progress="三")
        self.assertTrue(any("progress" in e and ("整数" in e or "类型" in e) for e in structured.validate(SPECS, bad)))

    def test_length_bounds(self):
        bad = dict(GOOD, goal="短")
        self.assertTrue(any("过短" in e for e in structured.validate(SPECS, bad)))
        bad = dict(GOOD, goal="长" * 41)
        self.assertTrue(any("超长" in e for e in structured.validate(SPECS, bad)))

    def test_enum(self):
        bad = dict(GOOD, level="中")
        self.assertTrue(any("取值" in e for e in structured.validate(SPECS, bad)))

    def test_list_rules(self):
        bad = dict(GOOD, tags=["唯一"])
        self.assertTrue(any("项数不足" in e for e in structured.validate(SPECS, bad)))
        bad = dict(GOOD, tags=["a", "b" * 11])
        self.assertTrue(any("超长" in e for e in structured.validate(SPECS, bad)))

    def test_optional_default_applied(self):
        merged = structured.apply_defaults(SPECS, dict(GOOD))
        self.assertEqual(merged["note"], "-")
        self.assertNotIn("note", GOOD)

    def test_non_object(self):
        self.assertEqual(len(structured.validate(SPECS, [1, 2])), 1)


class TestSpecPrompt(unittest.TestCase):
    def test_renders_requirements(self):
        text = structured.spec_prompt(SPECS)
        for name in ("goal", "progress", "tags", "level", "note"):
            self.assertIn(name, text)
        self.assertIn("JSON", text)


class TestStructuredCall(unittest.TestCase):
    def test_retry_with_feedback_then_success(self):
        calls = []

        def model(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return "这不是JSON"
            self.assertIn("必须修正", prompt)
            return '{"goal": "夺回矿镇", "progress": 3, "tags": ["锚点", "宿敌"], "level": "高"}'

        data, meta = structured.structured_call(model, "题面", SPECS, attempts=2)
        self.assertEqual(data["goal"], "夺回矿镇")
        self.assertEqual(meta["attempts"], 2)
        self.assertEqual(data["note"], "-")

    def test_validation_failure_returns_none_with_errors(self):
        def model(prompt):
            return '{"goal": "短", "progress": 1, "tags": ["a"], "level": "中"}'

        data, meta = structured.structured_call(model, "题面", SPECS, attempts=2)
        self.assertIsNone(data)
        self.assertTrue(meta["errors"])
        self.assertEqual(meta["attempts"], 2)

    def test_transport_failure_raises_after_all_attempts(self):
        def model(prompt):
            raise ConnectionError("上游炸了")

        with self.assertRaises(ConnectionError):
            structured.structured_call(model, "题面", SPECS, attempts=2)

    def test_mixed_failure_returns_none(self):
        state = {"count": 0}

        def model(prompt):
            state["count"] += 1
            if state["count"] == 1:
                raise ConnectionError("first boom")
            return '{"goal": "还是短", "progress": 1, "tags": ["a"], "level": "中"}'

        data, meta = structured.structured_call(model, "题面", SPECS, attempts=2)
        self.assertIsNone(data)
        self.assertTrue(meta["raw_chars"] > 0)


if __name__ == "__main__":
    unittest.main()
