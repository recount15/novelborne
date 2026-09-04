# -*- coding: utf-8 -*-
"""角色池 work 过滤（v2.0.3 跨书防线）单测。

不依赖 FastAPI TestClient：直接调用端点函数，mock 合并池。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import server  # noqa: E402


def make_card(cid: str, name: str, work: str, *, archetype: str = "配角",
              original_position: str = "配角") -> SimpleNamespace:
    return SimpleNamespace(
        id=cid, name=name, gender="male", work=work, source_medium="",
        source_region="", archetype=archetype, original_position=original_position,
        slot_keys={"宿敌栏": ("通用",)}, protagonist_type=[], mainline_type=[],
        partner_type=[], nemesis_type=[], one_line="", background="",
        desire="", abilities=[])


CARDS = [
    make_card("w1", "角色甲", "原创示例一"),
    make_card("w1b", "角色乙", "《原创示例一》"),
    make_card("w2", "角色丙", "原创示例二"),
    make_card("w3", "角色丁", "机娘纪元"),
    make_card("g1", "原创甲", ""),
]


class TestSameWork(unittest.TestCase):
    def test_normalizes_brackets_and_extension(self):
        self.assertTrue(server._same_work("《青崖问剑》", "青崖问剑.txt"))
        self.assertTrue(server._same_work("青崖问剑", "青崖问剑"))
        self.assertFalse(server._same_work("青崖问剑", "机娘纪元"))
        self.assertFalse(server._same_work("", "青崖问剑"))
        self.assertFalse(server._same_work("青崖问剑", ""))


class TestPoolWorkFilter(unittest.TestCase):
    def _pool(self, work=None):
        with patch.object(server.engine.character_library,
                          "merged_pool_cached", return_value=(list(CARDS), [])):
            return server.characters_pool(slot="宿敌栏", work=work)

    def _names(self, payload):
        return sorted(
            card["name"]
            for group in payload["keys"]
            for sub in group["sub_groups"]
            for card in sub["cards"])

    def test_no_work_returns_all(self):
        self.assertEqual(
            self._names(self._pool()),
            sorted(["角色甲", "角色乙", "角色丙", "角色丁", "原创甲"]))

    def test_work_filter_keeps_same_book_and_generic(self):
        names = self._names(self._pool(work="原创示例一"))
        self.assertEqual(sorted(names), ["原创甲", "角色乙", "角色甲"])

    def test_work_filter_without_match_keeps_only_generic(self):
        # 宁缺毋滥：查无此书的卡不展示，只剩无出处（原创/通用）卡。
        names = self._names(self._pool(work="不存在的书"))
        self.assertEqual(names, ["原创甲"])


if __name__ == "__main__":
    unittest.main()
