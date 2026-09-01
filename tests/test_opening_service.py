# -*- coding: utf-8 -*-
"""services.opening_service 中台门面单测（重构 M1）。

FakeClient 走 distill_model 的真实四级阶梯（chat.completions.create），
路由逻辑复用 test_opening_distill 的 FakeModel 规则；库路径与入库通道
全部临时目录/闭包注入——绝不能写真实 assets/rules/work_library.md 与角色库。

覆盖：state 保留式回写（不覆盖既有 plot_summary 等）、400/502 领域异常、
真实作品库未被触碰。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import engine  # noqa: E402
from core.services import opening_service  # noqa: E402
from tests.test_opening_distill import (  # noqa: E402
    FULL_RULES, FakeModel, make_book_dir, make_library_path)

_REAL_LIBRARY = Path(engine.__file__).resolve().parents[2] / "assets" / "rules" / "work_library.md"


class _FakeCompletions:
    def __init__(self, model: FakeModel):
        self._model = model

    def create(self, **kwargs):
        prompt = str((kwargs.get("messages") or [{}])[0].get("content") or "")
        content = self._model(prompt)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """OpenAI 兼容假客户端：把 distill_model 的请求转给 FakeModel 路由。"""

    def __init__(self, model: FakeModel):
        self.chat = SimpleNamespace(completions=_FakeCompletions(model))


def _make_state(book_dir) -> dict:
    return {
        "distill_key": str(book_dir),
        "novel_name": "城门风雪",
        "provider": "deepseek",
        "request_kwargs": {},
        "mode": "强化模式",
        "distill": {"plot_summary": "旧摘要不得被清空", "custom_key": "保留字段",
                    "selected_chapters": ["旧样本"]},
        "work_distill": {"old_flag": True},
    }


class TestOpeningService(unittest.TestCase):
    def setUp(self):
        self.book_dir = make_book_dir()
        self.library_path = make_library_path()
        self.saved_cards: list = []
        self.real_library_before = (
            _REAL_LIBRARY.read_text(encoding="utf-8") if _REAL_LIBRARY.is_file() else None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.book_dir.parent, ignore_errors=True)
        shutil.rmtree(self.library_path.parent, ignore_errors=True)
        # 铁律：真实作品库一个字节都不能动。
        if self.real_library_before is not None:
            self.assertEqual(self.real_library_before,
                             _REAL_LIBRARY.read_text(encoding="utf-8"))

    def _saver(self, cards):
        self.saved_cards.extend(cards)
        return [{"id": "user-svc-%d" % index, "name": card.get("name")}
                for index, card in enumerate(cards)]

    def test_run_for_state_preserving_writeback(self):
        state = _make_state(self.book_dir)
        model = FakeModel(rules=FULL_RULES)
        result = opening_service.run_for_state(
            state, FakeClient(model), "test-model",
            chapters_ahead=3, library_path=str(self.library_path),
            save_characters_fn=self._saver)
        # 摘要可 JSON 化且字段齐全。
        json.dumps(result, ensure_ascii=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["plot_ready"])
        self.assertEqual(result["work_id"], "W01")
        self.assertEqual(result["work_action"], "added")
        self.assertEqual(result["character_saved"], 1)
        self.assertEqual(result["character_dropped"], 1)
        # 保留式回写：既有字段不被清空/覆盖无关键。
        distill = state["distill"]
        self.assertEqual(distill["custom_key"], "保留字段")
        self.assertEqual(distill["plot_summary"]["genre"], "东方玄幻（复仇流）")  # 新值替换
        self.assertIsInstance(distill["selected_chapters"], list)
        self.assertEqual(distill["opening_status"]["chapters_total"], 3)
        self.assertEqual(len(distill["opening_anchors"]), 3)
        self.assertEqual(state["work_distill"]["old_flag"], True)
        self.assertEqual(state["work_distill"]["character_count"], 1)
        self.assertEqual(state["opening_distill"]["status"], "done")
        self.assertIn("开局蒸馏完成", state["distill_status"])
        # 入库走注入通道（磁盘隔离），playable 卡 1 张。
        self.assertEqual([card["name"] for card in self.saved_cards], ["沈砚"])
        # 临时库落盘，而非真实库。
        text = self.library_path.read_text(encoding="utf-8")
        self.assertIn("### W01 · 《城门风雪》", text)

    def test_client_error_when_book_dir_missing(self):
        state = {"provider": "deepseek"}  # 无 distill_key / chapter_index
        with self.assertRaises(opening_service.OpeningClientError):
            opening_service.run_for_state(state, FakeClient(FakeModel()), "m")

    def test_upstream_error_when_nothing_produced(self):
        empty = self.book_dir.parent / "empty-book"
        (empty / "chapters").mkdir(parents=True)
        state = _make_state(empty)
        with self.assertRaises(opening_service.OpeningUpstreamError):
            opening_service.run_for_state(
                state, FakeClient(FakeModel(default="{}")), "m",
                library_path=str(self.library_path), save_characters_fn=self._saver)
        # 502 路径不回写 state（端点会返回错误而非半成品）。
        self.assertNotIn("opening_distill", state)


if __name__ == "__main__":
    unittest.main()
