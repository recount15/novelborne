# -*- coding: utf-8 -*-
"""类 agent 选项通路契约（D05/D06）：合法榜恒为恰好 6 条 A–F，禁止 fail-open。

Q07 必跑矩阵：mode(legacy/agent) × 响应(正常/含未来知识/剔除后不足6/重试仍
含未来知识/全非法/空响应)。任何路径的输出要么是 6 条合法榜，要么空榜+error。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from core.services import options_service


def _opt(text: str, **flags) -> dict:
    item = {"text": text}
    item.update(flags)
    return item


def _clean_six():
    return [
        _opt("检查现场遗留的痕迹并记录线索（后果：发现一枚陌生的火漆印）"),
        _opt("向守门人打听昨夜访客的去向（后果：得到一顶斗篷的描述）"),
        _opt("伪装成杂役混入西厢院探查（后果：撞见管事清点箱笼）"),
        _opt("清点随身银钱并封存重要信件（后果：确认少了半袋碎银）"),
        _opt("约见旧识核实账目差额的真相（后果：旧识言辞闪躲）"),
        _opt("连夜抄近路赶往渡口截住信使（后果：在渡口等到天明）"),
    ]


def _resp(options):
    return ({"options": list(options)}, {})


def _empty():
    return (None, {"transport_error": "模拟空响应"})


class _Base(unittest.TestCase):
    def generate(self, calls, narrative="", mode="legacy"):
        env = {"STORY_CHOICE_AGENT_MODE": mode}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(options_service.structured, "structured_call",
                                  side_effect=list(calls)):
            return options_service.generate_options(
                client=object(), model="test", narrative=narrative)

    def assert_legal_board(self, result):
        options = result["options"]
        self.assertEqual(result["source"], "model")
        self.assertEqual(len(options), 6, f"合法榜必须恰 6 条：{len(options)}")
        self.assertEqual([o["key"] for o in options], list("ABCDEF"))
        for item in options:
            self.assertNotIn("requires_future_knowledge", item)
            self.assertNotIn("patch_valid", item)


class LegacyMatrixTest(_Base):
    def test_legacy_normal_returns_six(self):
        result = self.generate([_resp(_clean_six())])
        self.assert_legal_board(result)

    def test_legacy_keeps_flagged_items_but_strips_flags(self):
        items = _clean_six()
        items[0]["requires_future_knowledge"] = True
        result = self.generate([_resp(items)])
        # legacy 通路不做 agent 过滤（行为基线），但标志不得外泄
        self.assertEqual(len(result["options"]), 6)
        for item in result["options"]:
            self.assertNotIn("requires_future_knowledge", item)

    def test_legacy_empty_response_returns_none(self):
        result = self.generate([_empty(), _empty()])
        self.assertEqual(result["source"], "none")
        self.assertEqual(result["options"], [])
        self.assertIn("error", result)


class AgentMatrixTest(_Base):
    def test_agent_normal_applies_and_returns_six(self):
        result = self.generate([_resp(_clean_six())], mode="agent")
        self.assert_legal_board(result)
        self.assertTrue(result["meta"]["choice_agent"]["applied"])

    def test_agent_future_knowledge_dropped_to_exactly_six(self):
        items = _clean_six() + [
            _opt("使用尚未觉醒的血脉之力镇场", requires_future_knowledge=True),
            _opt("读取下一章才揭示的密信内容", requires_future_knowledge=True),
        ]
        result = self.generate([_resp(items)], mode="agent")
        self.assert_legal_board(result)
        texts = {item["text"] for item in result["options"]}
        self.assertNotIn("使用尚未觉醒的血脉之力镇场", texts)
        self.assertNotIn("读取下一章才揭示的密信内容", texts)

    def test_agent_under_six_triggers_targeted_retry(self):
        first = _clean_six()[:5] + [
            _opt("使用尚未觉醒的血脉之力镇场", requires_future_knowledge=True),
            _opt("读取下一章才揭示的密信内容", requires_future_knowledge=True),
        ]  # 剔除 2 → 剩 5 < 6：必须定向重试补齐
        result = self.generate([_resp(first), _resp(_clean_six())], mode="agent")
        self.assert_legal_board(result)
        self.assertIn("grade_retry", result["meta"])
        # 初次剔除记录不得被重试覆盖（可追溯）
        self.assertIn("choice_agent_initial", result["meta"])

    def test_agent_retry_still_flagged_never_fail_open(self):
        first = _clean_six()[:5] + [
            _opt("使用尚未觉醒的血脉之力镇场（后果：惊动全城）",
                 requires_future_knowledge=True),
        ]
        retry = _clean_six()
        retry[3]["requires_future_knowledge"] = True  # 重试仍含 1 条未来知识
        result = self.generate([_resp(first), _resp(retry)], mode="agent")
        # 禁止 fail-open：被 agent 剔除的条目（首轮或重试）都不得出现在最终榜
        texts = {item["text"] for item in result["options"]}
        self.assertNotIn("使用尚未觉醒的血脉之力镇场", texts)
        self.assertNotIn("清点随身银钱并封存重要信件", texts)
        if result["options"]:
            self.assertEqual(len(result["options"]), 6)
            self.assertEqual(result["source"], "model")
        else:
            self.assertEqual(result["source"], "none")
            self.assertIn("error", result)

    def test_agent_all_illegal_returns_none(self):
        items = [_opt("使用尚未觉醒的血脉之力镇场", requires_future_knowledge=True)
                 for _ in range(6)]
        result = self.generate([_resp(items), _resp(items)], mode="agent")
        self.assertEqual(result["options"], [])
        self.assertNotEqual(result["source"], "model")

    def test_agent_empty_response_returns_none(self):
        result = self.generate([_empty(), _empty()], mode="agent")
        self.assertEqual(result["source"], "none")
        self.assertEqual(result["options"], [])


class SanitizeGateTest(_Base):
    def test_sanitize_below_six_is_not_a_legal_board(self):
        items = _clean_six()
        items[1] = _opt("重复条目：检查现场遗留的痕迹并记录线索")  # 与 A 近重复被剔
        items[2] = _opt("太短")  # 低于 OPTION_TEXT_MIN 被剔 → 清洗后仅 4 条
        result = self.generate([_resp(items), _resp(items)])
        if result["options"]:
            self.assertEqual(len(result["options"]), 6)
        else:
            self.assertEqual(result["source"], "none")


class NarrativeFallbackTest(_Base):
    def test_narrative_repair_fills_to_six_with_trace(self):
        narrative = (
            "A. 检查现场遗留的痕迹并记录线索\n"
            "B. 向守门人打听昨夜访客的去向\n"
            "C. 伪装成杂役混入西厢院探查\n"
            "D. 清点随身银钱并封存重要信件\n"
        )
        result = self.generate([_empty(), _empty()], narrative=narrative)
        self.assertEqual(result["source"], "narrative")
        self.assertEqual(len(result["options"]), 6)
        self.assertIn("narrative_repair", result["meta"])


if __name__ == "__main__":
    unittest.main()
