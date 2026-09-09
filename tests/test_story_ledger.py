import unittest

from core.engine import story_ledger


class StoryLedgerTests(unittest.TestCase):
    def record(self, n):
        return {
            "turn_id": f"t-{n}", "round": n, "chapter": 1,
            "chapter_round": n, "narrative": f"第{n}回合正文",
            "options": [], "committed": True,
        }

    def test_append_is_idempotent_by_turn_id(self):
        rows = story_ledger.append_turn([], self.record(1))
        again = story_ledger.append_turn(rows, self.record(1))
        self.assertEqual(len(again), 1)

    def test_duplicate_round_is_rejected(self):
        rows = story_ledger.append_turn([], self.record(1))
        with self.assertRaises(ValueError):
            story_ledger.append_turn(rows, {**self.record(1), "turn_id": "other"})

    def test_validation_reports_gaps(self):
        result = story_ledger.validate_ledger([self.record(1), self.record(3)])
        self.assertTrue(result["ok"])
        self.assertEqual(result["source_gaps"], [2])

    def test_narrative_source_prefers_ledger_over_history(self):
        rows = [self.record(1), self.record(2)]
        source, kind, meta = story_ledger.narrative_source({
            "story_ledger": rows,
            "history": [{"role": "assistant", "content": "错误的压缩历史"}],
        })
        self.assertEqual(kind, "story_ledger")
        self.assertEqual([item["round"] for item in source], [1, 2])
        self.assertTrue(meta["ok"])

    def test_history_fallback_marks_uncertain(self):
        source, kind, _ = story_ledger.narrative_source({
            "history": [{"role": "assistant", "content": "旧正文"}],
        })
        self.assertEqual(kind, "history_fallback")
        self.assertTrue(source[0]["migration_uncertain"])


if __name__ == "__main__":
    unittest.main()
