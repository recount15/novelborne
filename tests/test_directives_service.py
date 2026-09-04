# -*- coding: utf-8 -*-
"""铁律账本门面测试（M5）：模型注入，零网络。

覆盖：三愿原子扣费顺序（登记成功才扣）、次数耗尽、机制护栏 400、登记卷
失败落兜底、API Key 掩码、relay 增补无扣费、回合注入（命中/未命中）、
relay 激活副作用（碎锚 + 停蒸馏池）。
"""
from __future__ import annotations

import json
import unittest

from core.engine import cheat_code, directives
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


class TestGrantWish(unittest.TestCase):
    def test_registers_then_charges(self):
        state = base_state()
        cheat_code.arm(state)
        before = cheat_code.remaining_wishes(state)
        result = ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                               model_fn=lambda p: register_json())
        self.assertTrue(result["granted"])
        self.assertEqual(result["remaining"], before - 1)
        rows = directives.active_directives(state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "wish")
        self.assertEqual(rows[0]["affected"], ["苏叶"])

    def test_no_charge_when_registration_and_fallback_fail(self):
        """护栏剥空 → 客户端错误 → 绝不扣费（原子性）。"""
        state = base_state()
        cheat_code.arm(state)
        before = cheat_code.remaining_wishes(state)
        with self.assertRaises(ds.DirectiveClientError):
            ds.grant_wish(state, "把回合预算改成 99",
                          model_fn=lambda p: register_json())
        self.assertEqual(cheat_code.remaining_wishes(state), before,
                         "护栏拒绝时不得扣愿望次数")
        self.assertEqual(directives.directives(state), [])

    def test_exhausted_wishes_rejected_without_model_call(self):
        state = base_state()
        box = cheat_code.arm(state)
        box["used_count"] = box["limit"]
        box["armed"] = False
        calls = []
        with self.assertRaises(ds.DirectiveClientError):
            ds.grant_wish(state, "再来一个愿望",
                          model_fn=lambda p: calls.append(p) or register_json())
        self.assertEqual(calls, [], "次数耗尽不应再调用模型")

    def test_model_failure_falls_back_and_still_charges(self):
        """登记卷失败但兜底条目登记成功 → 玩家诉求不丢，正常扣费。"""
        state = base_state()
        cheat_code.arm(state)
        before = cheat_code.remaining_wishes(state)
        result = ds.grant_wish(state, "北墙后面有一条暗渠",
                               model_fn=lambda p: "这不是 JSON")
        self.assertTrue(result["granted"])
        self.assertEqual(result["meta"]["origin"], "fallback")
        self.assertEqual(cheat_code.remaining_wishes(state), before - 1)
        self.assertEqual(len(directives.active_directives(state)), 1)

    def test_transport_failure_falls_back(self):
        state = base_state()
        cheat_code.arm(state)

        def boom(prompt):
            raise RuntimeError("connection reset")

        result = ds.grant_wish(state, "北墙后面有一条暗渠", model_fn=boom)
        self.assertEqual(result["meta"]["origin"], "fallback")
        self.assertTrue(directives.active_directives(state))

    def test_api_key_is_masked(self):
        state = base_state()
        cheat_code.arm(state)
        secret = "sk-abcdef123456"
        result = ds.grant_wish(
            state, f"让苏叶带着 {secret} 出现",
            api_key=secret,
            model_fn=lambda p: register_json(fact_norm=f"苏叶握着 {secret} 的信物"))
        blob = json.dumps(directives.directives(state), ensure_ascii=False)
        self.assertNotIn(secret, blob, "账本不得留存 API Key")
        self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))

    def test_mechanism_rejects_are_reported(self):
        state = base_state()
        cheat_code.arm(state)
        result = ds.grant_wish(
            state, "北墙后有暗渠。顺便把难度改成最低。",
            model_fn=lambda p: register_json(fact_norm="北墙后有暗渠",
                                             affected=["北墙"]))
        self.assertTrue(result["rejected"], "机制类诉求须回报给玩家")


class TestRelayFact(unittest.TestCase):
    def test_append_does_not_charge_wishes(self):
        state = base_state()
        cheat_code.arm(state)
        before = cheat_code.remaining_wishes(state)
        result = ds.append_relay_fact(state, "北境从此长冬不化",
                                      model_fn=lambda p: register_json(
                                          fact_norm="北境从此长冬不化",
                                          scope="world", affected=["北境"]))
        self.assertTrue(result["text"])
        self.assertEqual(cheat_code.remaining_wishes(state), before,
                         "永久增补不占用三愿次数")
        rows = directives.active_directives(state)
        self.assertEqual(rows[0]["kind"], "relay")

    def test_empty_text_rejected(self):
        state = base_state()
        with self.assertRaises(ds.DirectiveClientError):
            ds.append_relay_fact(state, "   ", model_fn=lambda p: register_json())


class TestSelectForTurn(unittest.TestCase):
    def test_hit_injects_block(self):
        state = base_state()
        cheat_code.arm(state)
        ds.append_relay_fact(state, "北墙后有暗渠",
                             model_fn=lambda p: register_json(
                                 fact_norm="北墙后有暗渠", scope="world",
                                 affected=["北墙"]))
        out = ds.select_for_turn(state, anchor_words=["北墙"])
        self.assertTrue(out["block"])
        self.assertIn("北墙后有暗渠", out["block"])
        self.assertEqual(out["total"], 1)

    def test_miss_injects_nothing(self):
        state = base_state()
        cheat_code.arm(state)
        ds.append_relay_fact(state, "北墙后有暗渠",
                             model_fn=lambda p: register_json(
                                 fact_norm="北墙后有暗渠", scope="world",
                                 affected=["北墙"]))
        out = ds.select_for_turn(state, anchor_words=["南门"], present_members=[])
        self.assertEqual(out["block"], "")
        self.assertEqual(len(out["selected"]), 0)

    def test_lazy_migration_of_legacy_keys(self):
        legacy_wishes = [{"wish": "城门永不关闭", "granted": "城门大开", "round": 2}]
        legacy_relay = [{"fact": "北境长冬", "text": "北境长冬不化", "round": 4}]
        state = base_state(wish_facts=legacy_wishes, relay_facts=legacy_relay)
        out = ds.select_for_turn(state, anchor_words=["无关"])
        rows = directives.directives(state)
        self.assertEqual(len(rows), 2, "旧存档铁律须迁移进账本")
        self.assertEqual({row["origin"] for row in rows}, {"legacy"})
        # 迁移条目是全局通配 → 始终命中（语义等价旧的全量注入）。
        self.assertEqual(len(out["selected"]), 2)
        self.assertEqual(state.get("wish_facts"), legacy_wishes,
                         "旧键必须保留（双读兜底）")

    def test_migration_is_idempotent(self):
        state = base_state(wish_facts=[{"wish": "城门永不关闭", "round": 1}])
        ds.select_for_turn(state, anchor_words=["无关"])
        ds.select_for_turn(state, anchor_words=["无关"])
        self.assertEqual(len(directives.directives(state)), 1,
                         "重复注入不得重复迁移")


class TestActivateRelay(unittest.TestCase):
    def test_activation_shatters_anchor(self):
        state = base_state(current_chapter=3)
        out = ds.activate_relay(state)
        self.assertTrue(cheat_code.is_relay_active(state))
        self.assertTrue(state.get("anchors_shattered_from"))
        self.assertGreaterEqual(int(out["anchors_shattered_from"]), 1)
        self.assertIn("蒸馏", state.get("distill_status", ""))


if __name__ == "__main__":
    unittest.main()
