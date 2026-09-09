"""Synthetic stdlib tests; direct loading avoids application/package initialization.

Run directly with python -B <absolute path>; no pytest/conftest is imported.
All temporary sources are created beneath this workspace and removed afterward.
"""
import importlib.util
import json
from pathlib import Path
import random
import sys
import tempfile
import unicodedata
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("v14_search_under_test", ROOT / "core/services/book_search_service.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)


class V14SearchTests(unittest.TestCase):
    def setUp(self):
        # V00 owns tempfile's default directory before collection; never override
        # its runtime boundary. Direct stdlib execution still stays in workspace.
        guard = sys.modules.get("tests.v00_isolation")
        guarded = guard is not None and getattr(guard, "_STATE", None) is not None
        self.temp = tempfile.TemporaryDirectory(
            prefix="v14-synthetic-", **({} if guarded else {"dir": ROOT / "tests"}))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "chapters").mkdir()

    def book(self, chapters, indexed=True):
        for number, text in chapters.items():
            (self.root / "chapters" / f"{number:04d}.txt").write_text(text, encoding="utf-8")
        if indexed:
            (self.root / "chapter_index.json").write_text(json.dumps({
                "book_id": "synthetic", "chapters": [{"idx": no, "title": "same name"} for no in chapters]
            }), encoding="utf-8")

    def run_search(self, query, **options):
        return search.search_occurrences(self.root, query, **options)

    def test_v3_v14_cross_block_overlap_pagination_and_navigation(self):
        self.book({9: "aaa " * 30, 2: "x" * 1199 + "crossblock"})
        result = self.run_search("crossblock")
        self.assertEqual([(h["chapter_no"], h["start"], h["end"]) for h in result["hits"]], [(2, 1199, 1209)])
        pages = [self.run_search("aa", page=i, page_size=7) for i in range(1, 10)]
        self.assertEqual(pages[0]["total_hits"], 60)
        hits = [h for page in pages for h in page["hits"]]
        self.assertEqual(len(hits), 60)
        self.assertEqual(len({h["hit_id"] for h in hits}), 60)
        self.assertFalse(pages[-1]["has_more"])
        self.assertTrue(all(h["chapter_no"] == 9 for h in hits))
        self.assertEqual(self.run_search("aa")["hits"], hits[:20])

    def test_v3_v14_unicode_offsets_and_excerpt(self):
        text = "   heading\n😀Ａ， e\u0301  Straße 끝  "
        self.book({5: text})
        result = self.run_search("aéstrasse", ignore_punctuation=True, ignore_whitespace=True)
        hit = result["hits"][0]
        self.assertEqual(text[hit["start"]:hit["end"]], "Ａ， e\u0301  Straße")
        self.assertEqual(hit["excerpt"][hit["start"] - hit["excerpt_start"]:hit["end"] - hit["excerpt_start"]], text[hit["start"]:hit["end"]])
        emoji = self.run_search("😀")["hits"][0]
        self.assertEqual(emoji["end"] - emoji["start"], 1)
        self.assertEqual(len(text[:emoji["end"]].encode("utf-16-le")) // 2 - len(text[:emoji["start"]].encode("utf-16-le")) // 2, 2)
        self.assertEqual(self.run_search("s")["total_hits"], 2)  # S and expanded ß, deduplicated.

    def test_v3_v14_normalization_matches_stdlib(self):
        values = ["e\u0301", "\u1100\u1161\u11a8", "a\u0315\u0300", "ﬃＡß", "\u0344", "\u212b"]
        rng = random.Random(14)
        alphabet = "aＡßﬃ\u0300\u0315\u0301\u1100\u1161\u11a8😀"
        values.extend("".join(rng.choices(alphabet, k=30)) for _ in range(100))
        for value in values:
            normalized, spans = search.normalize_with_mapping(value)
            self.assertEqual(normalized, unicodedata.normalize("NFKC", value).casefold())
            self.assertEqual(len(normalized), len(spans))
            self.assertTrue(all(0 <= a < b <= len(value) for a, b in spans))

    def test_v3_v14_fuzzy_thresholds_and_stability(self):
        self.book({3: "abcd abxd abXYcd abcdefgh abXXefgh"})
        short = self.run_search("ＡＢ", mode="fuzzy")
        self.assertEqual(short["effective_mode"], "exact")
        self.assertEqual(short["warnings"], ["short_query_exact_only"])
        self.assertEqual(self.run_search("abc", mode="fuzzy")["edit_budget"], 0)
        one = self.run_search("abcd", mode="fuzzy")
        self.assertEqual(one["edit_budget"], 1)
        self.assertTrue(any(h["start"] == 5 and h["end"] == 9 and h["score"] == .75 for h in one["hits"]))
        self.assertFalse(any(h["start"] == 10 and h["end"] == 16 for h in one["hits"]))
        two = self.run_search("abcdefgh", mode="fuzzy")
        self.assertEqual(two["edit_budget"], 2)
        self.assertEqual(two, self.run_search("abcdefgh", mode="fuzzy"))
        self.assertTrue(any(h["score"] == .75 for h in two["hits"]))

    def test_v3_v14_candidate_filter_matches_exhaustive_oracle(self):
        def distance(a, b):
            row = list(range(len(b) + 1))
            for i, char in enumerate(a, 1):
                nxt = [i]
                for j, other in enumerate(b, 1):
                    nxt.append(min(row[j] + 1, nxt[-1] + 1, row[j - 1] + (char != other)))
                row = nxt
            return row[-1]
        rng = random.Random(1400)
        for budget, length in [(1, 4), (2, 8)]:
            for _ in range(100):
                query = "".join(rng.choices("abc", k=length))
                text = "".join(rng.choices("abc", k=20))
                expected = []
                for start in range(len(text)):
                    choices = []
                    for size in range(length - budget, length + budget + 1):
                        if start + size <= len(text):
                            d = distance(query, text[start:start + size])
                            self.assertEqual(min(search._distance(query, text[start:start + size], budget), budget + 1), min(d, budget + 1))
                            if d <= budget:
                                choices.append((d, abs(size - length), start + size))
                    if choices:
                        best = min(choices)
                        expected.append((start, best[2], best[0]))
                self.assertEqual(list(search._matches(text, query, budget)), expected)

    def test_v3_v14_reader_newlines_and_legacy_chapter_inventory(self):
        self.book({12: "placeholder"})
        (self.root / "chapter_index.json").write_text(json.dumps({
            "book_id": "synthetic", "chapters": [{"chapter": 12, "title": "heading"}]
        }), encoding="utf-8")
        path = self.root / "chapters/0012.txt"
        path.write_bytes("heading\r\n😀\r\nneedle\r\n".encode("utf-8"))
        reader_text = path.read_text(encoding="utf-8")
        hit = self.run_search("needle")["hits"][0]
        self.assertEqual(hit["chapter_no"], 12)
        self.assertEqual(hit["start"], reader_text.index("needle"))
        self.assertEqual(reader_text[hit["start"]:hit["end"]], "needle")

    def test_v3_v14_source_versions_and_no_writes(self):
        self.book({2: "needle"})
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        first = self.run_search("needle")
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        (self.root / "chapters/0002.txt").write_text("needle changed", encoding="utf-8")
        second = self.run_search("needle")
        for key in ("source_hash", "index_hash"):
            self.assertNotEqual(first[key], second[key])
        self.assertNotEqual(first["hits"][0]["hit_id"], second["hits"][0]["hit_id"])

    def test_v3_v14_invalid_queries_and_sources(self):
        for query, options in [("", {}), ("  ", {}), ("a" * 201, {}), ("，", {"ignore_punctuation": True}),
                               ("a", {"page": 0}), ("a", {"page_size": 101}), ("a", {"page": True}),
                               ("a", {"mode": "model"}), ("a", {"ignore_whitespace": "false"})]:
            with self.assertRaises(search.SearchQueryError):
                self.run_search(query, **options)
        with self.assertRaises(search.BookSourceError):
            self.run_search("a")
        self.book({2: "text"})
        with self.assertRaises(search.BookSourceError):
            self.run_search("a", book_id="wrong")
        (self.root / "chapters/0002.txt").unlink()
        with self.assertRaises(search.BookSourceError):
            self.run_search("a")
        (self.root / "chapter_index.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(search.BookSourceError):
            self.run_search("a")

    def test_v3_v14_filename_fallback_duplicate_and_unsafe_path(self):
        self.book({7: "needle", 19: "needle"}, indexed=False)
        self.assertEqual([h["chapter_no"] for h in self.run_search("needle")["hits"]], [7, 19])
        duplicate = self.root / "chapters/7.txt"
        duplicate.write_text("needle", encoding="utf-8")
        with self.assertRaises(search.BookSourceError):
            self.run_search("needle")
        duplicate.unlink()
        with self.assertRaises(search.BookSourceError):
            search._safe_path(self.root, self.root / "../outside.txt")
        (self.root / "chapters/0007.txt").write_bytes(b"\xff")
        with self.assertRaises(search.BookSourceError):
            self.run_search("needle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
