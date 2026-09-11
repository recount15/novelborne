# -*- coding: utf-8 -*-
"""C03 权威快照与来源传播：授权铁律进入回合快照（红测试先行）。

验收（计划 §C03）：
- 同一句话在原著证据 / 本局授权 / 无授权三态下判定不同；
- 角色不能因系统知道而知道（授权事实不自动成为他人知识）；
- 愿望效力不被原著校验取消（authorized_fact 无需 source 证据）；
- 原著阅读投影不读 session 授权（reader 通路结构性无 state 入参）；
- 授权只进 session 域：绝不进入 authorized_evidence（canon 域）。
"""
from __future__ import annotations

import inspect
import unittest

from core.engine import directives, fact_contract
from core.services import character_context_service as ccs
from core.services import generation_skills as gs


WISH_FACT = "北墙裂痕后有暗渠"
WISH_RAW = "我希望北墙的裂痕后面真的有一条暗渠"


def wish_state(status: str = "consumed", *, kind: str = "wish",
               origin: str = "wish", affected=(("北墙",)), fact: str = WISH_FACT) -> dict:
    """构造一条带完整授权记录的铁律账本（纯机制层，零网络）。"""
    state: dict = {}
    entry, _ = directives.parse_registration(
        {"fact_norm": fact, "scope": "world", "affected": list(affected), "conflicts": []},
        allowed=())
    record = fact_contract.WishAuthorizationRecord(
        authorization_id="auth-" + status + "-" + kind, request_id="req-" + status + "-" + kind,
        raw_text=WISH_RAW, authorized_origin=origin, status=status,
        accepted_interpretation=fact if status in ("active", "consumed") else "",
        target_ids=tuple(affected),
        receipt={} if affected else {"targets_unresolved": True})
    directives.register(state, entry, kind=kind, raw=WISH_RAW,
                        authorization=record.to_row_value())
    return state


class TestProjectAuthorizedDirectives(unittest.TestCase):
    def test_consumed_wish_projects_as_authorized(self):
        projected = gs.project_authorized_directives(wish_state("consumed"))
        self.assertEqual(set(projected), {"directive-1"})
        entry = projected["directive-1"]
        self.assertEqual(entry["origin"], "wish")
        self.assertEqual(entry["fact_norm"], WISH_FACT)
        self.assertEqual(entry["affected"], ["北墙"])
        self.assertEqual(entry["raw_text"], WISH_RAW)
        self.assertEqual(entry["facts"][0],
                         {"subject_ids": ["北墙"], "predicate": "authorized_directive",
                          "value": WISH_FACT})

    def test_active_relay_projects_with_relay_origin(self):
        projected = gs.project_authorized_directives(
            wish_state("active", kind="relay", origin="relay"))
        self.assertEqual(projected["directive-1"]["origin"], "relay")

    def test_draft_and_needs_clarification_not_projected(self):
        for status in ("draft", "needs_clarification"):
            self.assertEqual(gs.project_authorized_directives(wish_state(status)), {},
                             status + " 授权未生效，不得进入回合上下文")

    def test_legacy_row_without_receipt_not_projected(self):
        state: dict = {}
        entry, _ = directives.parse_registration(
            {"fact_norm": "旧档铁律", "scope": "world", "affected": ["北墙"], "conflicts": []},
            allowed=())
        directives.register(state, entry, kind="wish")
        self.assertEqual(gs.project_authorized_directives(state), {},
                         "无回执旧行是 unknown_legacy，绝不伪造授权进入回合上下文")

    def test_unresolved_targets_project_with_wildcard_level_fact(self):
        # 真实路径：模型给出的对象全部被白名单剔除 → targets_unresolved。
        state: dict = {}
        entry, _ = directives.parse_registration(
            {"fact_norm": WISH_FACT, "scope": "world", "affected": ["无名之城"],
             "conflicts": []}, allowed=["北墙"])
        record = fact_contract.WishAuthorizationRecord(
            authorization_id="auth-unresolved", request_id="req-unresolved",
            raw_text=WISH_RAW, authorized_origin="wish", status="consumed",
            accepted_interpretation=WISH_FACT, target_ids=(),
            receipt={"targets_unresolved": True})
        directives.register(state, entry, kind="wish", raw=WISH_RAW,
                            authorization=record.to_row_value())
        projected = gs.project_authorized_directives(state)
        self.assertEqual(projected["directive-1"]["affected"], [])
        self.assertTrue(projected["directive-1"]["targets_unresolved"])
        self.assertEqual(projected["directive-1"]["facts"][0]["subject_ids"], ["world"])


class TestSnapshotPropagation(unittest.TestCase):
    SOURCE = {"book_id": "book", "source_hash": "s1",
              "texts": {1: "已知场景：北墙下换岗的哨兵正在打盹。" * 8}}

    def state_with(self, wish=None, **over):
        state = {"agent_mode": True, "distill_key": "host-book",
                 "knowledge_cutoff": {"chapter_no": 1, "offset": 24},
                 "state_memory": {"revision": 4}, "round": 3}
        if wish is not None:
            state["ledger"] = wish["ledger"]
        state.update(over)
        return state

    def snapshot(self, wish=None):
        source = gs.clone(self.SOURCE)
        return gs.build_turn_snapshot(
            self.state_with(wish), "推门", source_reader=lambda *_: source)

    def test_snapshot_carries_authorized_directives_for_all_roles(self):
        snap = self.snapshot(wish_state("consumed"))
        data = snap.read()
        self.assertIn("directive-1", data["authorized_directives"])
        for skill in gs.DEPENDENCIES:
            projection = data["role_projections"][skill]
            self.assertEqual(projection["authorized_directives"], ["directive-1"],
                             "所有策略经同一快照接口看到授权域")
        context = gs.role_context(snap, "story.evidence")
        self.assertIn("directive-1", context["authorized_directives"])
        readable = context["data"]["authorized_directives"]
        self.assertEqual(readable["directives"][0]["fact"], WISH_FACT)
        self.assertIn("authorized", readable["directives"][0]["provenance"])

    def test_directives_never_enter_canon_evidence_domain(self):
        data = self.snapshot(wish_state("consumed")).read()
        self.assertNotIn("directive-1", data["authorized_evidence"],
                         "愿望改变本局，不产生原著证据")
        self.assertNotIn("directive-1", data["authorized_branch_events"],
                         "授权是独立来源域，不冒充已提交事件")
        entry = data["authorized_directives"]["directive-1"]
        self.assertNotIn("source_hash", entry)

    def test_context_hash_binds_authorization(self):
        bare = self.snapshot().snapshot_hash
        granted = self.snapshot(wish_state("consumed")).snapshot_hash
        self.assertNotEqual(bare, granted, "授权集变化必须改变快照哈希")

    def test_no_wishes_yields_empty_authorized_domain(self):
        data = self.snapshot().read()
        self.assertEqual(data["authorized_directives"], {})


class TestAuthorizedFactClaims(unittest.TestCase):
    def context(self, *, evidence=None, directives_records=None,
                knowers=("hero", "player", "北墙守卫", "北墙")):
        return {
            "boundary": {"scope": "game", "cutoff": 3},
            "evidence": evidence if evidence is not None else {
                "e1": {"facts": [{"subject_ids": ["北墙"], "predicate": "北墙状况",
                                  "value": "裂痕后有暗渠"}]}},
            "branch_events": {},
            "knowledge_holders": list(knowers),
            "authorized_directives": directives_records if directives_records is not None else {
                "directive-1": {"origin": "wish", "fact_norm": WISH_FACT,
                                "affected": ["北墙"], "targets_unresolved": False,
                                "facts": [{"subject_ids": ["北墙"],
                                           "predicate": "authorized_directive",
                                           "value": WISH_FACT}]}},
        }

    def claim(self, **over):
        raw = {"claim_id": "c1", "kind": "authorized_fact", "subject_ids": ["北墙"],
               "predicate": "北墙状况", "value": "裂痕后有暗渠",
               "evidence_ids": [], "branch_event_ids": [], "assumption_ids": [],
               "valid_boundary": {"scope": "game", "cutoff": 3},
               "knowledge_holders": ["player"], "directive_ids": ["directive-1"]}
        raw.update(over)
        return raw

    def assert_code(self, code: str, **over):
        with self.assertRaises(gs.GateError) as caught:
            gs.GroundedClaim.validate(self.claim(**over), self.context())
        self.assertEqual(code, caught.exception.code)

    def test_same_sentence_three_verdicts(self):
        # 同一句话：「北墙裂痕后有暗渠」
        # 1) 原著证据命中 → source_fact 成立。
        source_claim = self.claim(kind="source_fact", evidence_ids=["e1"], directive_ids=[])
        gs.GroundedClaim.validate(source_claim, self.context())
        # 2) 无证据无授权 → source_fact 被拒；无引用 inference 被拒；
        #    只剩 proposed_invention（如实标注为发明，不得冒充事实）。
        empty = self.context(evidence={}, directives_records={})
        with self.assertRaises(gs.GateError) as caught:
            gs.GroundedClaim.validate(self.claim(kind="source_fact", evidence_ids=["e1"],
                                                 directive_ids=[]), empty)
        self.assertEqual("unauthorized_evidence", caught.exception.code)
        with self.assertRaises(gs.GateError) as caught:
            gs.GroundedClaim.validate(self.claim(kind="inference", directive_ids=[]), empty)
        self.assertEqual("ungrounded_inference", caught.exception.code)
        gs.GroundedClaim.validate(self.claim(kind="proposed_invention", directive_ids=[]), empty)
        # 3) 玩家授权 → authorized_fact 成立：愿望效力不被原著校验取消。
        no_source = self.context(evidence={})
        gs.GroundedClaim.validate(self.claim(), no_source)

    def test_authorized_fact_requires_directive_ref(self):
        self.assert_code("missing_directive_ref", directive_ids=[])

    def test_unknown_directive_ref_rejected(self):
        self.assert_code("unauthorized_directive", directive_ids=["directive-9"])

    def test_other_kinds_cannot_cite_directives(self):
        self.assert_code("directive_ref_wrong_kind",
                         kind="inference", directive_ids=["directive-1"],
                         assumption_ids=["a1"])

    def test_subject_outside_targets_rejected(self):
        self.assert_code("directive_subject_outside_targets",
                         subject_ids=["南海商队"])

    def test_knowledge_not_propagagated_to_bystanders(self):
        # 北墙守卫在 knowledge_holders 白名单里，但不在愿望目标内：
        # 系统知道 ≠ 角色知道。
        self.assert_code("directive_knowledge_not_propagagated",
                         knowledge_holders=["北墙守卫"])

    def test_knowledge_allowed_for_player_and_targets(self):
        gs.GroundedClaim.validate(self.claim(knowledge_holders=["player", "北墙"]),
                                  self.context())
        # 未解析目标的授权：知情面收紧为玩家本人。
        wildcard = self.context(directives_records={
            "directive-1": {"origin": "wish", "fact_norm": WISH_FACT, "affected": [],
                            "targets_unresolved": True,
                            "facts": [{"subject_ids": ["world"],
                                       "predicate": "authorized_directive",
                                       "value": WISH_FACT}]}})
        gs.GroundedClaim.validate(self.claim(subject_ids=["world"],
                                             knowledge_holders=["player"]), wildcard)
        with self.assertRaises(gs.GateError) as caught:
            gs.GroundedClaim.validate(
                self.claim(subject_ids=["world"], knowledge_holders=["北墙守卫"]), wildcard)
        self.assertEqual("directive_knowledge_not_propagagated", caught.exception.code)

    def test_authorized_fact_cannot_mix_source_or_event_refs(self):
        self.assert_code("authorized_fact_single_domain", evidence_ids=["e1"])


class TestReaderDomainSeparation(unittest.TestCase):
    """原著阅读投影不读 session 授权：结构性钉死 reader 通路无 state 入参。"""

    def test_reader_context_signature_has_no_session_state(self):
        for func in (ccs.build_reader_context, ccs.read_reader_source):
            params = inspect.signature(func).parameters
            self.assertLessEqual(
                set(params),
                {"book_dir", "character_id", "chapter_no", "card_revision",
                 "source_hash", "card_provider"},
                func.__name__ + " 不得引入 state/session 授权入参（原著域与会话域分离）")


if __name__ == "__main__":
    unittest.main()
