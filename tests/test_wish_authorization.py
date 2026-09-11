# -*- coding: utf-8 -*-
"""施工卡 C02：三愿码授权与事务——范围不扩大、独立愿望共存、幂等、原子回滚。

红测试先行：以下用例针对既有缺陷行为——
- parse_registration 白名单过滤后 affected 清空时静默扩为 [WILDCARD] 全局铁律；
- fallback_entry（模型解析失败兜底）无条件 affected=[WILDCARD]、scope=world；
- mark_superseded 仅凭 affected 重叠就顶掉旧愿望（同对象独立愿望丢失）；
- 登记行没有授权记录（原话与模型解释混在 fact_norm 里，无幂等键）。
"""
from __future__ import annotations

import json
import unittest

from core.engine import cheat_code, directives
from core.engine.fact_contract import ContractError, WishAuthorizationRecord
from core.services import directives_service as ds


def base_state(**over):
    state = {
        "round": 5,
        "companions": [{"name": "苏叶"}, {"name": "周桐"}],
        "lore_hits": ["北墙"],
        "state_memory": {"location": {"name": "城门", "region": "北境"}},
        "ledger": {"cheat": {}},
    }
    state.update(over)
    return state


def register_json(**over):
    payload = {"fact_norm": "苏叶其实是旧部统领的遗孤",
               "scope": "character", "affected": ["苏叶"], "conflicts": []}
    payload.update(over)
    return json.dumps(payload, ensure_ascii=False)


class TestNoSilentGlobalWidening(unittest.TestCase):
    def test_parse_all_targets_unresolved_keeps_them_not_wildcard(self):
        """白名单外对象全部保留为未解析，绝不静默授予全局。"""
        entry, notes = directives.parse_registration(
            register_json(affected=["幽灵城", "虚构国"]), allowed=["北墙"])
        self.assertIsNotNone(entry)
        self.assertEqual(entry["affected"], [])
        self.assertTrue(entry["targets_unresolved"])
        self.assertEqual(set(entry["removed_affected"]), {"幽灵城", "虚构国"})
        self.assertTrue(any("未" in n or "全局" in n for n in notes))

    def test_parse_model_claimed_global_is_not_player_global(self):
        """模型自己声称『全局』不构成玩家全局授权——降为未解析。"""
        entry, _ = directives.parse_registration(
            register_json(affected=["全局"]), allowed=())
        self.assertIsNotNone(entry)
        self.assertNotIn(directives.WILDCARD, entry["affected"])
        self.assertTrue(entry["targets_unresolved"])

    def test_fallback_matches_known_names_deterministically(self):
        entry = directives.fallback_entry("让苏叶获得御剑术",
                                          known=["苏叶", "周桐"])
        self.assertEqual(entry["affected"], ["苏叶"])
        self.assertFalse(entry["targets_unresolved"])
        self.assertNotIn(directives.WILDCARD, entry["affected"])

    def test_fallback_without_known_names_marks_unresolved(self):
        entry = directives.fallback_entry("大海尽头有一座会移动的岛")
        self.assertEqual(entry["affected"], [])
        self.assertTrue(entry["targets_unresolved"])
        self.assertNotIn(directives.WILDCARD, entry["affected"],
                         "解析失败不得扩大为全局授权")

    def test_register_keeps_empty_affected_only_with_unresolved_flag(self):
        """带未解析标记的空 affected 原样落账；无标记的空 affected 走旧兼容通配。"""
        state: dict = {}
        entry, _ = directives.parse_registration(
            register_json(affected=["无名之城"]), allowed=["北墙"])
        row = directives.register(state, entry, kind="wish")
        self.assertEqual(row["affected"], [])
        self.assertTrue(row["targets_unresolved"])
        legacy = directives.register(state, {"fact_norm": "旧调用方", "scope": "world"},
                                     kind="wish")
        self.assertEqual(legacy["affected"], [directives.WILDCARD])


class TestIndependentWishesCoexist(unittest.TestCase):
    def test_same_target_different_facts_both_active(self):
        state: dict = {}
        a, _ = directives.parse_registration(
            register_json(fact_norm="苏叶会御剑"), allowed=())
        b, _ = directives.parse_registration(
            register_json(fact_norm="苏叶是旧部统领的遗孤"), allowed=())
        directives.register(state, a, kind="wish")
        directives.register(state, b, kind="wish")
        active = {row["fact_norm"] for row in directives.active_directives(state)}
        self.assertEqual(active, {"苏叶会御剑", "苏叶是旧部统领的遗孤"},
                         "对象重叠不构成取代理由——独立愿望必须共存")

    def test_supersede_requires_explicit_conflict_reference(self):
        state: dict = {}
        old = directives.register(state, directives.parse_registration(
            register_json(fact_norm="北墙由旧部把守"), allowed=())[0], kind="wish")
        newer, _ = directives.parse_registration(
            register_json(fact_norm="北墙守军已换防", conflicts=["北墙由旧部把守"]),
            allowed=())
        new = directives.register(state, newer, kind="wish")
        rows = {row["id"]: row for row in directives.directives(state)}
        self.assertEqual(rows[old["id"]]["superseded_by"], new["id"])
        self.assertNotIn(old["id"], [r["id"] for r in directives.active_directives(state)])


class TestAuthorizationRecord(unittest.TestCase):
    def test_grant_wish_attaches_record_separating_raw_from_interpretation(self):
        state = base_state()
        cheat_code.arm(state)
        result = ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                               model_fn=lambda p: register_json())
        row = result["row"]
        record = WishAuthorizationRecord.from_row_value(row["authorization"])
        self.assertIsNotNone(record)
        self.assertEqual(record.raw_text, "让苏叶成为旧部统领的遗孤")
        self.assertEqual(record.accepted_interpretation, "苏叶其实是旧部统领的遗孤")
        self.assertNotEqual(record.raw_text, record.accepted_interpretation,
                            "原话与模型解释必须分离")
        self.assertEqual(record.target_ids, ("苏叶",))
        self.assertEqual(record.status, "consumed")
        self.assertTrue(record.receipt.get("charged"))
        # 账本里的行同样带授权记录（不只是返回副本）。
        stored = [r for r in directives.directives(state) if r["id"] == row["id"]][0]
        self.assertIn("authorization", stored)

    def test_grant_request_id_stable_and_idempotent_after_superseded(self):
        state = base_state()
        cheat_code.arm(state)
        first = ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                              model_fn=lambda p: register_json())
        record = WishAuthorizationRecord.from_row_value(first["row"]["authorization"])
        # 用显式冲突把第一条愿望取代掉（合法路径）。
        conflict, _ = directives.parse_registration(
            register_json(fact_norm="苏叶身世另有隐情",
                          conflicts=["苏叶其实是旧部统领的遗孤"]), allowed=())
        directives.register(state, conflict, kind="wish")
        # 同文愿望再来：request_id 命中历史行 → 拒绝且不扣次数（幂等）。
        before = cheat_code.remaining_wishes(state)
        with self.assertRaises(ds.DirectiveClientError):
            ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                          model_fn=lambda p: register_json())
        self.assertEqual(cheat_code.remaining_wishes(state), before)
        again = WishAuthorizationRecord.from_row_value(first["row"]["authorization"])
        self.assertEqual(again.request_id, record.request_id)

    def test_fallback_grant_records_unresolved_targets(self):
        state = base_state()
        cheat_code.arm(state)
        result = ds.grant_wish(state, "让远方的无名岛浮出水面",
                               model_fn=lambda p: "这不是 JSON")
        record = WishAuthorizationRecord.from_row_value(result["row"]["authorization"])
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "consumed")
        self.assertTrue(record.receipt.get("targets_unresolved"))
        self.assertFalse(record.global_authority,
                         "解析失败绝不记为玩家授予全局")

    def test_effects_failure_rolls_back_authorization_too(self):
        state = base_state()
        cheat_code.arm(state)
        before = cheat_code.remaining_wishes(state)
        original = ds._apply_directive_effects

        def boom(*args, **kwargs):
            raise ValueError("效果落库失败")

        ds._apply_directive_effects = boom
        try:
            with self.assertRaises(ds.DirectiveClientError):
                ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                              model_fn=lambda p: register_json())
        finally:
            ds._apply_directive_effects = original
        self.assertEqual(cheat_code.remaining_wishes(state), before)
        for row in directives.directives(state):
            self.assertNotIn("authorization", row,
                             "回滚后账本不得残留授权记录")


class TestUnresolvedInjectionAndContract(unittest.TestCase):
    def test_unresolved_targets_still_injected_every_turn(self):
        """未解析愿望不得因对象缺失而死——与通配同级的始终注入。"""
        state: dict = {}
        entry = directives.fallback_entry("大海尽头有一座会移动的岛")
        directives.register(state, entry, kind="wish")
        hits = directives.select_relevant(state, anchor_words=["无关词"])
        self.assertTrue(any(row["fact_norm"] == "大海尽头有一座会移动的岛"
                            for row in hits))

    def test_contract_allows_unresolved_marker_but_not_silent_widening(self):
        receipt = {"targets_unresolved": True}
        record = WishAuthorizationRecord(
            authorization_id="a1", request_id="r1", raw_text="愿望",
            authorized_origin="wish", status="active",
            accepted_interpretation="解释", receipt=receipt)
        self.assertEqual(record.target_ids, ())
        with self.assertRaises(ContractError):
            WishAuthorizationRecord(
                authorization_id="a1", request_id="r1", raw_text="愿望",
                authorized_origin="wish", status="active",
                accepted_interpretation="解释", receipt={})

    def test_relay_grant_also_authorized_origin(self):
        state = base_state()
        result = ds.append_relay_fact(state, "北墙后面有一条暗渠",
                                      model_fn=lambda p: register_json(
                                          fact_norm="北墙后确有暗渠", affected=["北墙"]))
        record = WishAuthorizationRecord.from_row_value(result["row"]["authorization"])
        self.assertIsNotNone(record)
        self.assertEqual(record.authorized_origin, "relay")
        self.assertEqual(record.status, "active")
        self.assertFalse(record.receipt.get("charged"))


if __name__ == "__main__":
    unittest.main()
