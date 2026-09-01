from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.services.context_retrieval_service import ContextRetrievalService, retrieve_context
from core.services.book_prepare_service import prepare_book


class ContextRetrievalTests(unittest.TestCase):
    def setUpBook(self, root: Path) -> Path:
        book = root / "book"
        (book / "chapters").mkdir(parents=True)
        texts = ["第一章 小明获得玉佩，踏上旅程。", "第二章 敌人在北山等待玉佩。"]
        for i, text in enumerate(texts, 1):
            (book / "chapters" / f"{i:04d}.txt").write_text(text, encoding="utf-8")
        (book / "chapter_index.json").write_text(json.dumps({"book_id": "r", "chapters": [{"idx": i, "title": f"第{i}章"} for i in range(1, 3)]}, ensure_ascii=False), encoding="utf-8")
        prepare_book(book, leaf_chars=12)
        return book

    def test_chinese_search_and_bound(self):
        with tempfile.TemporaryDirectory() as d:
            result = retrieve_context(self.setUpBook(Path(d)), "玉佩", limit=5, max_chars=30)
            self.assertTrue(result["chunks"])
            self.assertLessEqual(result["chars"], 30)
            self.assertTrue(all("text" in item for item in result["chunks"]))

    def test_service_facade(self):
        with tempfile.TemporaryDirectory() as d:
            book = self.setUpBook(Path(d))
            result = ContextRetrievalService().retrieve(book, "北山")
            self.assertEqual(result["book_id"], "r")


if __name__ == "__main__": unittest.main()
