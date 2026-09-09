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


class TestWishTypedTransaction(unittest.TestCase):
    """P4：愿望 → 类型化角色状态事务（与次数原子提交）。"""

    def test_wish_writes_typed_character_state_and_effects(self):
        state = base_state()
        cheat_code.arm(state)
        result = ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                               model_fn=lambda p: register_json())
        self.assertEqual(result["characters_touched"], ["苏叶"])
        char_state = state["character_states"]["苏叶"]
        rows = [row for row in char_state["assertions"] if row["key"] == "wish_character"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "苏叶其实是旧部统领的遗孤")
        self.assertEqual(rows[0]["confidence"], 1.0)
        self.assertEqual(rows[0]["provenance"][0]["kind"], "wish")
        effects = state["wish_effects"]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["characters_touched"], ["苏叶"])
        self.assertEqual(effects[0]["scope"], "character")
        self.assertEqual(effects[0]["directive_id"], result["row"]["id"])

    def test_duplicate_wish_rejected_without_charge(self):
        state = base_state()
        cheat_code.arm(state)
        first = ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                              model_fn=lambda p: register_json())
        with self.assertRaises(ds.DirectiveClientError):
            ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                          model_fn=lambda p: register_json(fact_norm="让苏叶成为旧部统领的遗孤"))
        self.assertEqual(first["remaining"], cheat_code.remaining_wishes(state),
                         "重复愿望不得扣次数")
        self.assertEqual(len(directives.active_directives(state)), 1)
        self.assertEqual(len(state["wish_effects"]), 1)

    def test_effects_failure_rolls_back_ledger_and_does_not_charge(self):
        from unittest import mock
        from core.services import character_state_service
        state = base_state()
        cheat_code.arm(state)
        before = cheat_code.remaining_wishes(state)
        with mock.patch.object(character_state_service, "add_evidence",
                               side_effect=ValueError("boom")):
            with self.assertRaises(ds.DirectiveClientError):
                ds.grant_wish(state, "让苏叶成为旧部统领的遗孤",
                              model_fn=lambda p: register_json())
        self.assertEqual(cheat_code.remaining_wishes(state), before,
                         "状态兑现失败不得扣次数")
        self.assertEqual(directives.directives(state), [], "账本行须回滚")
        self.assertNotIn("wish_effects", state)
        self.assertNotIn("wish_facts", state)

    def test_partial_effects_failure_rolls_back_all_domains(self):
        """D10：多角色兑现中途失败——首个角色已写入的 character_states 也必须回滚。"""
        from unittest import mock
        from core.services import character_state_service
        state = base_state()
        cheat_code.arm(state)
        before = cheat_code.remaining_wishes(state)
        real_add = character_state_service.add_evidence
        calls = {"count": 0}

        def flaky(current, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return real_add(current, **kwargs)
            raise ValueError("第二个角色兑现失败")

        payload = register_json(affected=["苏叶", "周桐"])
        with mock.patch.object(character_state_service, "add_evidence",
                               side_effect=flaky):
            with self.assertRaises(ds.DirectiveClientError):
                ds.grant_wish(state, "苏叶与周桐身世揭晓",
                              model_fn=lambda p: payload)
        self.assertEqual(cheat_code.remaining_wishes(state), before,
                         "中途失败不得扣次数")
        self.assertEqual(directives.directives(state), [], "账本行须回滚")
        self.assertFalse(state.get("character_states"),
                         "首角色已写入的状态断言必须整体回滚")
        self.assertNotIn("wish_effects", state)
        self.assertNotIn("wish_facts", state)

    def test_relay_partial_failure_rolls_back_all_domains(self):
        """D10：增补通路同样受完整回滚保护（character_states/wish_effects）。"""
        from unittest import mock
        from core.services import character_state_service
        state = base_state()
        real_add = character_state_service.add_evidence
        calls = {"count": 0}

        def flaky(current, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return real_add(current, **kwargs)
            raise TypeError("第二角色类型错误")

        payload = register_json(fact_norm="苏叶与周桐身世揭晓",
                                affected=["苏叶", "周桐"])
        with mock.patch.object(character_state_service, "add_evidence",
                               side_effect=flaky):
            with self.assertRaises(ds.DirectiveClientError):
                ds.append_relay_fact(state, "苏叶与周桐身世揭晓",
                                     model_fn=lambda p: payload)
        self.assertEqual(directives.directives(state), [])
        self.assertFalse(state.get("character_states"))
        self.assertNotIn("wish_effects", state)


class TestAskPersistTrace(unittest.TestCase):
    """D10：ask 链路落盘失败不得静默——响应必须携带错误痕迹。"""

    def test_wish_persist_failure_surfaces_in_response(self):
        from unittest import mock
        from core.engine import persistence
        from core.services import ask_service
        state = base_state()
        cheat_code.arm(state)
        with mock.patch.object(ds, "distill_model",
                               return_value=register_json()), \
                mock.patch.object(persistence, "save_state",
                                  side_effect=OSError("disk full")):
            response = ask_service.handle_ask(
                state, "让苏叶成为旧部统领的遗孤",
                api_key="占位Key", session_id="占位会话")
        self.assertTrue(response.get("wish_granted"), "愿望本身仍应生效")
        self.assertIn("persist_error", response,
                      "落盘失败必须出现在响应字段中，不得静默吞掉")
        self.assertIn("disk full", str(response["persist_error"]))
        self.assertIn("落盘失败", response["answer"],
                      "答复文案必须可见落盘警告")

    def test_relay_also_applies_typed_effects(self):
        state = base_state()
        result = ds.append_relay_fact(state, "北墙从此坚不可摧",
                                      model_fn=lambda p: register_json(
                                          fact_norm="北墙从此坚不可摧",
                                          scope="location", affected=["北墙"]))
        self.assertEqual(result["characters_touched"], [], "北墙不在名册，只落账")
        effects = state["wish_effects"]
        self.assertEqual(effects[0]["kind"], "relay")
        self.assertEqual(effects[0]["scope"], "location")


if __name__ == "__main__":
    unittest.main()
