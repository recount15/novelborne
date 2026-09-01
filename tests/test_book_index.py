from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.engine.book_index import build_book_index, deterministic_id, load_index, search_index


class BookIndexTests(unittest.TestCase):
    def make_book(self, root: Path) -> Path:
        book = root / "book"
        (book / "chapters").mkdir(parents=True)
        for i, text in enumerate(("小明来到长安，寻找玉佩。", "玉佩指向北山，敌人在等待。", "北山决战后小明归来。"), 1):
            (book / "chapters" / f"{i:04d}.txt").write_text(text, encoding="utf-8")
        (book / "chapter_index.json").write_text(json.dumps({"book_id": "demo", "chapters": [{"idx": i, "title": f"第{i}章"} for i in range(1, 4)]}, ensure_ascii=False), encoding="utf-8")
        return book

    def test_hierarchy_spans_and_search(self):
        with tempfile.TemporaryDirectory() as d:
            index = build_book_index(self.make_book(Path(d)), leaf_chars=8, arc_size=2)
            self.assertEqual(index["root"]["kind"], "root")
            self.assertEqual(len(index["arcs"]), 2)
            self.assertEqual(index["stats"]["chapters"], 3)
            self.assertTrue(all(x["source"]["checksum"] for x in index["leaves"]))
            self.assertTrue(search_index(index, "玉佩"))
            self.assertEqual(deterministic_id("chapter", "demo", 1), index["chapters"][0]["id"])

    def test_rebuild_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            book = self.make_book(Path(d))
            first = build_book_index(book, leaf_chars=20)
            second = build_book_index(book, leaf_chars=20)
            self.assertEqual(first["root_id"], second["root_id"])
            self.assertEqual(first["leaves"], second["leaves"])
            self.assertEqual(load_index(book)["book_id"], "demo")
            manifest = json.loads((book / "book_manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("小明", json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__": unittest.main()
