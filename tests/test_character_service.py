# -*- coding: utf-8 -*-
"""角色 patch 中台门面测试（M4）。"""
from __future__ import annotations

import json
import unittest

from core.memory import blank_state
from core.services import character_service

NARRATIVE = "苏叶接过北墙旧册，看向李青说道：这份证据足够了。"


def good_json():
    return json.dumps({"patches": [{
        "name": "苏叶", "evidence": "苏叶接过北墙旧册",
        "relationship_delta": "升温", "summary": "因托付证据而增加信任",
    }]}, ensure_ascii=False)


class TestCharacterService(unittest.TestCase):
    def test_good_patch_committed(self):
        state = {"mode": "强化模式", "state_memory": blank_state("强化模式", "")}
        out = character_service.generate_patch(
            state, None, "m", narrative=NARRATIVE,
            active_members=[{"name": "苏叶"}], round_no=4,
            model_fn=lambda p: good_json())
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["valid"]), 1)
        rows = state["state_memory"]["relationships"]["characters"]
        self.assertEqual(rows[0]["name"], "苏叶")
        self.assertEqual(rows[0]["relationship_delta"], "升温")
        self.assertEqual(state["active_summaries"]["苏叶"], "因托付证据而增加信任")

    def test_bad_evidence_skipped(self):
        bad = json.dumps({"patches": [{
            "name": "苏叶", "evidence": "正文里不存在的证据",
            "relationship_delta": "升温", "summary": "无效",
        }]}, ensure_ascii=False)
        state = {"mode": "强化模式", "state_memory": blank_state("强化模式", "")}
        out = character_service.generate_patch(
            state, None, "m", narrative=NARRATIVE,
            active_members=[{"name": "苏叶"}], round_no=4,
            model_fn=lambda p: bad)
        self.assertTrue(out["ok"])
        self.assertEqual(out["valid"], [])
        self.assertTrue(out["rejected"])
        self.assertEqual(state["state_memory"]["relationships"]["characters"], [])

    def test_model_failure_does_not_raise(self):
        def boom(prompt):
            raise RuntimeError("upstream down")
        state = {"mode": "强化模式", "state_memory": blank_state("强化模式", "")}
        out = character_service.generate_patch(
            state, None, "m", narrative=NARRATIVE,
            active_members=[{"name": "苏叶"}], round_no=4, model_fn=boom)
        self.assertFalse(out["ok"])
        self.assertIn("upstream", out["error"])

    def test_no_active_members_is_noop(self):
        state = {"mode": "强化模式", "state_memory": blank_state("强化模式", "")}
        calls = []
        out = character_service.generate_patch(
            state, None, "m", narrative=NARRATIVE, active_members=[], round_no=4,
            model_fn=lambda p: calls.append(p) or good_json())
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("skipped"))
        self.assertEqual(calls, [])

    def test_existing_rows_preserved(self):
        memory = blank_state("强化模式", "")
        memory["relationships"]["characters"] = [{"name": "周桐", "note": "旧字段"}]
        state = {"mode": "强化模式", "state_memory": memory}
        out = character_service.generate_patch(
            state, None, "m", narrative=NARRATIVE,
            active_members=[{"name": "苏叶"}], round_no=4,
            model_fn=lambda p: good_json())
        self.assertTrue(out["ok"])
        rows = state["state_memory"]["relationships"]["characters"]
        self.assertEqual([r["name"] for r in rows], ["周桐", "苏叶"])
        self.assertEqual(rows[0]["note"], "旧字段")


if __name__ == "__main__":
    unittest.main()
