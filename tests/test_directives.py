# -*- coding: utf-8 -*-
"""铁律账本机制测试（M5）：纯函数、零网络。

覆盖：schema 校验、affected 白名单剔除、机制护栏、register 写账本（不污染
共享默认 cheat 字典）、superseded 仲裁、相关性选择（命中/未命中）、
注入块装配、migrate_legacy 幂等。
"""
from __future__ import annotations

import json
import unittest

from core.engine import directives, ledger as ledger_module


def good_payload(**over):
    payload = {
        "fact_norm": "北墙裂痕后方藏着一条通往旧水道的暗渠",
        "scope": "world",
        "affected": ["北墙", "旧水道"],
        "conflicts": [],
    }
    payload.update(over)
    return payload


class TestParseRegistration(unittest.TestCase):
    def test_good_payload_passes(self):
        entry, errors = directives.parse_registration(
            good_payload(), allowed=["北墙", "旧水道", "苏叶"])
        self.assertEqual(errors, [])
        self.assertIsNotNone(entry)
        self.assertEqual(entry["scope"], "world")
        self.assertEqual(entry["affected"], ["北墙", "旧水道"])

    def test_json_string_input(self):
        entry, errors = directives.parse_registration(
            json.dumps(good_payload(), ensure_ascii=False),
            allowed=["北墙", "旧水道"])
        self.assertEqual(errors, [])
        self.assertIsNotNone(entry)

    def test_fenced_json_input(self):
        raw = "```json\n" + json.dumps(good_payload(), ensure_ascii=False) + "\n```"
        entry, errors = directives.parse_registration(raw, allowed=["北墙", "旧水道"])
        self.assertEqual(errors, [])
        self.assertIsNotNone(entry)

    def test_scope_must_be_enum(self):
        entry, errors = directives.parse_registration(
            good_payload(scope="机制"), allowed=["北墙"])
        self.assertIsNone(entry)
        self.assertTrue(errors)

    def test_missing_fact_rejected(self):
        entry, errors = directives.parse_registration(
            {"scope": "world", "affected": ["北墙"]}, allowed=["北墙"])
        self.assertIsNone(entry)
        self.assertTrue(any("fact_norm" in e for e in errors))

    def test_affected_outside_whitelist_is_dropped(self):
        entry, errors = directives.parse_registration(
            good_payload(affected=["北墙", "不存在的势力"]),
            allowed=["北墙", "旧水道"])
        self.assertIsNotNone(entry)
        self.assertNotIn("不存在的势力", entry["affected"])
        self.assertIn("北墙", entry["affected"])

    def test_all_affected_dropped_falls_back_to_wildcard(self):
        entry, _ = directives.parse_registration(
            good_payload(affected=["幽灵城", "虚构国"]), allowed=["北墙"])
        self.assertIsNotNone(entry)
        self.assertEqual(entry["affected"], [directives.WILDCARD])

    def test_empty_allowed_skips_whitelist(self):
        entry, errors = directives.parse_registration(
            good_payload(affected=["任意名"]), allowed=())
        self.assertEqual(errors, [])
        self.assertEqual(entry["affected"], ["任意名"])

    def test_non_object_rejected(self):
        entry, errors = directives.parse_registration("不是 JSON", allowed=())
        self.assertIsNone(entry)
        self.assertTrue(errors)


class TestMechanismGuard(unittest.TestCase):
    def test_strips_mechanism_sentences(self):
        clean, rejected = directives.mechanism_guard(
            "北墙裂痕后有暗渠。把回合预算改成 99。")
        self.assertIn("北墙", clean)
        self.assertTrue(rejected)
        self.assertNotIn("回合", clean)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            directives.mechanism_guard("   ")


class TestRegisterAndLedger(unittest.TestCase):
    def test_register_writes_ledger_without_polluting_shared_default(self):
        before = json.dumps(ledger_module.new_ledger().get("cheat") or {},
                            ensure_ascii=False, sort_keys=True)
        state: dict = {"round": 4}
        entry, _ = directives.parse_registration(good_payload(), allowed=["北墙", "旧水道"])
        row = directives.register(state, entry, kind="wish", round_no=4, raw="原文")
        self.assertEqual(row["kind"], "wish")
        self.assertEqual(row["round"], 4)
        self.assertTrue(row["id"])
        rows = directives.directives(state)
        self.assertEqual(len(rows), 1)
        after = json.dumps(ledger_module.new_ledger().get("cheat") or {},
                           ensure_ascii=False, sort_keys=True)
        self.assertEqual(before, after, "绝不可污染 new_ledger 的共享 cheat 默认字典")

    def test_ids_are_unique(self):
        state: dict = {}
        entry, _ = directives.parse_registration(good_payload(), allowed=())
        ids = {directives.register(state, entry, kind="wish")["id"] for _ in range(4)}
        self.assertEqual(len(ids), 4)

    def test_mark_superseded_on_overlapping_affected(self):
        state: dict = {}
        first, _ = directives.parse_registration(
            good_payload(fact_norm="北墙由旧部把守"), allowed=())
        old = directives.register(state, first, kind="wish")
        second, _ = directives.parse_registration(
            good_payload(fact_norm="北墙已被彻底封死"), allowed=())
        # register 内部已做取代仲裁，这里直接验账本状态（不重复调用）。
        new = directives.register(state, second, kind="wish")
        rows = {row["id"]: row for row in directives.directives(state)}
        self.assertEqual(rows[old["id"]]["superseded_by"], new["id"])
        active = [row["id"] for row in directives.active_directives(state)]
        self.assertIn(new["id"], active)
        self.assertNotIn(old["id"], active)

    def test_non_overlapping_not_superseded(self):
        state: dict = {}
        a, _ = directives.parse_registration(
            good_payload(fact_norm="北墙有暗渠", affected=["北墙"]), allowed=())
        b, _ = directives.parse_registration(
            good_payload(fact_norm="药铺掌柜通医术", affected=["周桐"]), allowed=())
        directives.register(state, a, kind="wish")
        directives.register(state, b, kind="wish")
        # affected 不重叠 → 两条都留在生效集；重复仲裁应当幂等（无副作用）。
        self.assertEqual(len(directives.active_directives(state)), 2)
        rows = directives.directives(state)
        self.assertEqual(directives.mark_superseded(state, rows[-1]), [])
        self.assertEqual(len(directives.active_directives(state)), 2)


class TestSelectRelevant(unittest.TestCase):
    def setUp(self):
        self.state: dict = {}
        wall, _ = directives.parse_registration(
            good_payload(fact_norm="北墙裂痕后有暗渠", affected=["北墙"]), allowed=())
        person, _ = directives.parse_registration(
            good_payload(fact_norm="苏叶私藏一枚铜扣", scope="character",
                         affected=["苏叶"]), allowed=())
        directives.register(self.state, wall, kind="wish")
        directives.register(self.state, person, kind="relay")

    def test_anchor_word_hit(self):
        hits = directives.select_relevant(self.state, anchor_words=["北墙", "换岗"])
        self.assertEqual([row["fact_norm"] for row in hits], ["北墙裂痕后有暗渠"])

    def test_member_hit(self):
        hits = directives.select_relevant(
            self.state, present_members=[{"name": "苏叶"}])
        self.assertEqual([row["fact_norm"] for row in hits], ["苏叶私藏一枚铜扣"])

    def test_no_hit_returns_empty(self):
        hits = directives.select_relevant(self.state, anchor_words=["南门"])
        self.assertEqual(hits, [], "未命中不得注入（解决 relay 无限累积）")

    def test_wildcard_always_hits(self):
        entry = directives.fallback_entry("世界规则整体改写", kind="wish")
        directives.register(self.state, entry, kind="wish")
        hits = directives.select_relevant(self.state, anchor_words=["无关词"])
        self.assertTrue(any(row["fact_norm"] == "世界规则整体改写" for row in hits))

    def test_superseded_excluded_from_selection(self):
        # 登记一条 affected 重叠的新铁律 → 旧条目被取代 → 注入时只出现新条目。
        newer, _ = directives.parse_registration(
            good_payload(fact_norm="北墙暗渠已被彻底封死", affected=["北墙"]),
            allowed=())
        directives.register(self.state, newer, kind="wish")
        facts = [row["fact_norm"]
                 for row in directives.select_relevant(self.state, anchor_words=["北墙"])]
        self.assertIn("北墙暗渠已被彻底封死", facts)
        self.assertNotIn("北墙裂痕后有暗渠", facts, "被取代的铁律不得再注入")

    def test_block_shape(self):
        hits = directives.select_relevant(self.state, anchor_words=["北墙"])
        block = directives.build_directives_block(hits)
        self.assertIn("北墙裂痕后有暗渠", block)
        self.assertIn("铁律", block)
        self.assertEqual(directives.build_directives_block([]), "")


class TestMigrateLegacy(unittest.TestCase):
    def test_migrates_old_keys_and_is_idempotent(self):
        state: dict = {
            "wish_facts": [{"wish": "北墙有暗渠", "granted": "北墙确有暗渠", "round": 2}],
            "relay_facts": [{"fact": "苏叶有铜扣", "text": "苏叶确有铜扣", "round": 3}],
        }
        first = directives.migrate_legacy(state)
        self.assertEqual(first["migrated"], 2)
        rows = directives.directives(state)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["origin"] == "legacy" for row in rows))
        self.assertIn("wish_facts", state, "旧键必须保留（双读期）")
        second = directives.migrate_legacy(state)
        self.assertEqual(second["migrated"], 0)
        self.assertEqual(len(directives.directives(state)), 2)

    def test_no_legacy_keys_is_noop(self):
        state: dict = {}
        result = directives.migrate_legacy(state)
        self.assertEqual(result["migrated"], 0)
        self.assertEqual(directives.directives(state), [])


class TestPrompt(unittest.TestCase):
    def test_prompt_contains_text_and_format(self):
        prompt = directives.build_registration_prompt(
            "北墙裂痕后有暗渠", kind="wish", roster=["苏叶"], worldbook=["北墙"])
        self.assertIn("北墙裂痕后有暗渠", prompt)
        self.assertIn("fact_norm", prompt)
        self.assertNotIn("@@", prompt)


if __name__ == "__main__":
    unittest.main()
