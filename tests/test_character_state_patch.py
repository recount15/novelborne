# -*- coding: utf-8 -*-
"""角色状态 patch 严格校验与关系行合并测试（M4，零模型零网络）。"""
from __future__ import annotations

import json
import unittest

from core.engine import character_state_patch as csp
from core.memory import blank_state
from core.memory.state_store import apply_turn

NARRATIVE = (
    "苏叶接过北墙旧册，低声说这是最后一份存档。"
    "周桐守住退路，因此失踪军械的去向曝光，三处暗哨随后被撤换。"
)


def payload(*entries):
    return {"patches": list(entries)}


def entry(name="苏叶", evidence="苏叶接过北墙旧册", delta="升温", summary="因托付旧册而增加信任"):
    return {"name": name, "evidence": evidence, "relationship_delta": delta, "summary": summary}


class TestParsePayload(unittest.TestCase):
    def test_good_payload(self):
        valid, rejected = csp.parse_patch_payload(
            payload(entry()), ["苏叶", "周桐"], NARRATIVE)
        self.assertEqual(rejected, [])
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["name"], "苏叶")
        self.assertEqual(valid[0]["relationship_delta"], "升温")

    def test_json_string_and_fenced_input(self):
        raw = "```json\n" + json.dumps(payload(entry()), ensure_ascii=False) + "\n```"
        valid, _ = csp.parse_patch_payload(raw, ["苏叶"], NARRATIVE)
        self.assertEqual(len(valid), 1)

    def test_name_not_in_active_members_rejected(self):
        valid, rejected = csp.parse_patch_payload(
            payload(entry(name="李青")), ["苏叶", "周桐"], NARRATIVE)
        self.assertEqual(valid, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("李青", rejected[0][1] + json.dumps(rejected[0][0], ensure_ascii=False))

    def test_evidence_must_be_exact_substring(self):
        valid, rejected = csp.parse_patch_payload(
            payload(entry(evidence="苏叶交出了北墙旧册")), ["苏叶"], NARRATIVE)
        self.assertEqual(valid, [])
        self.assertTrue(any("原文" in reason or "子串" in reason for _, reason in rejected))

    def test_evidence_with_newline_rejected(self):
        valid, _ = csp.parse_patch_payload(
            payload(entry(evidence="苏叶接过\n北墙旧册")), ["苏叶"], NARRATIVE)
        self.assertEqual(valid, [])

    def test_invalid_delta_rejected(self):
        valid, rejected = csp.parse_patch_payload(
            payload(entry(delta="暴涨")), ["苏叶"], NARRATIVE)
        self.assertEqual(valid, [])
        self.assertTrue(rejected)

    def test_unknown_field_rejected(self):
        bad = entry()
        bad["mood"] = "开心"
        valid, rejected = csp.parse_patch_payload(payload(bad), ["苏叶"], NARRATIVE)
        self.assertEqual(valid, [])
        self.assertTrue(rejected)

    def test_duplicate_name_rejects_whole_payload(self):
        valid, rejected = csp.parse_patch_payload(
            payload(entry(), entry(summary="第二条")), ["苏叶"], NARRATIVE)
        self.assertEqual(valid, [], "重复角色名必须整包拒收")
        self.assertTrue(rejected)

    def test_partial_valid_keeps_good_entries(self):
        valid, rejected = csp.parse_patch_payload(
            payload(entry(), entry(name="周桐", evidence="周桐守住退路", summary="并肩防守")),
            ["苏叶", "周桐"], NARRATIVE)
        self.assertEqual(len(valid), 2)
        self.assertEqual(rejected, [])

    def test_bad_envelope_rejected(self):
        for bad in ([], {"items": []}, {"patches": "x"}, "不是 JSON", {"patches": [1, 2]}):
            valid, rejected = csp.parse_patch_payload(bad, ["苏叶"], NARRATIVE)
            self.assertEqual(valid, [], f"{bad!r} 应被拒收")
            self.assertTrue(rejected, f"{bad!r} 应给出拒收原因")

    def test_summary_overlong_rejected(self):
        valid, _ = csp.parse_patch_payload(
            payload(entry(summary="长" * (csp.SUMMARY_MAX + 5))), ["苏叶"], NARRATIVE)
        self.assertEqual(valid, [])

    def test_empty_patches_is_valid_noop(self):
        valid, rejected = csp.parse_patch_payload(
            {"patches": []}, ["苏叶"], NARRATIVE)
        self.assertEqual(valid, [])
        # 空数组要么按合法空操作（无 rejected），要么给出明确原因；两者都不得抛错
        self.assertIsInstance(rejected, list)


class TestRelationshipRows(unittest.TestCase):
    def test_upsert_preserves_unknown_fields_and_order(self):
        existing = [
            {"name": "李青", "note": "旧字段", "relationship_delta": "稳定"},
            {"name": "苏叶", "note": "保留我", "relationship_delta": "稳定", "last_round": 1},
        ]
        rows = csp.build_relationship_rows([entry()], existing, round_no=7)
        self.assertEqual([r["name"] for r in rows], ["李青", "苏叶"], "既有顺序不变")
        self.assertEqual(rows[0]["note"], "旧字段", "未命中行原样保留")
        hit = rows[1]
        self.assertEqual(hit["note"], "保留我", "命中行的未知字段也要保留")
        self.assertEqual(hit["relationship_delta"], "升温")
        self.assertEqual(hit["last_round"], 7)
        self.assertEqual(hit["source"], csp.SOURCE)
        self.assertEqual(hit["last_evidence"], "苏叶接过北墙旧册")

    def test_new_name_appended(self):
        rows = csp.build_relationship_rows([entry(name="周桐", evidence="周桐守住退路")],
                                           [{"name": "苏叶"}], round_no=3)
        self.assertEqual([r["name"] for r in rows], ["苏叶", "周桐"])

    def test_to_memory_patch_shape(self):
        rows = csp.build_relationship_rows([entry()], [], round_no=2)
        patch = csp.to_memory_patch(rows)
        self.assertEqual(list(patch.keys()), ["relationships"])
        self.assertEqual(patch["relationships"]["characters"], rows)

    def test_apply_turn_merges_and_keeps_others(self):
        """与真实 apply_turn 联跑：分类级替换下未命中角色不得丢失。"""
        state = blank_state("强化模式", "")
        state["relationships"]["characters"] = [{"name": "李青", "relationship_delta": "稳定"}]
        rows = csp.build_relationship_rows(
            [entry()], state["relationships"]["characters"], round_no=5)
        updated, _changes = apply_turn(state, csp.to_memory_patch(rows),
                                       round_no=5, source=csp.SOURCE)
        names = [r["name"] for r in updated["relationships"]["characters"]]
        self.assertIn("李青", names, "未命中角色必须仍在")
        self.assertIn("苏叶", names)
        self.assertTrue(updated.get("history"), "apply_turn 应留下 history 轨迹")


class TestPatchPrompt(unittest.TestCase):
    def test_prompt_accepts_dicts_and_strings(self):
        text_a = csp.build_patch_prompt(["苏叶", "周桐"], NARRATIVE)
        text_b = csp.build_patch_prompt([{"name": "苏叶"}, {"name": "周桐"}], NARRATIVE)
        for text in (text_a, text_b):
            self.assertIn("苏叶", text)
            self.assertIn("周桐", text)
        self.assertIn("苏叶", text_b)

    def test_prompt_contains_narrative_and_delta_vocab(self):
        text = csp.build_patch_prompt(["苏叶"], NARRATIVE)
        self.assertIn("升温", text)
        self.assertIn("北墙旧册", text)


if __name__ == "__main__":
    unittest.main()
