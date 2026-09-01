from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.services.book_prepare_service import BookPrepareService, prepare_book


class BookPrepareTests(unittest.TestCase):
    def test_prepare_writes_opening_ready_without_model(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book"
            (book / "chapters").mkdir(parents=True)
            (book / "chapters" / "0001.txt").write_text("第一章。主角醒来，发现一枚玉佩。", encoding="utf-8")
            (book / "chapter_index.json").write_text(json.dumps({"book_id": "x", "chapters": [{"idx": 1, "title": "第一章"}]}), encoding="utf-8")
            result = prepare_book(book, opening_chapters=1)
            self.assertTrue(result["extractive"])
            self.assertEqual(result["chapters"][0]["chapter_no"], 1)
            self.assertTrue((book / "opening_ready.json").is_file())
            self.assertNotIn("主角", json.dumps(json.loads((book / "book_manifest.json").read_text(encoding="utf-8")), ensure_ascii=False))

    def test_service_facade_reuses_prepare(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book"
            (book / "chapters").mkdir(parents=True)
            (book / "chapters" / "0001.txt").write_text("第一章。", encoding="utf-8")
            (book / "chapter_index.json").write_text(json.dumps({"book_id": "x", "chapters": [{"idx": 1}]}), encoding="utf-8")
            self.assertEqual(BookPrepareService().prepare(book)["book_id"], "x")


if __name__ == "__main__": unittest.main()
