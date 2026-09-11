# -*- coding: utf-8 -*-
"""C05 场景与章节推进（红测试先行）。

验收（计划 §6 C05 / §2.3 / §3.2）：
- 事件状态迁移绑定**已提交证据**（story_ledger committed 行），sequence_feedback
  反馈行不再作为完成依据；
- 玩家明确阻止（碎锚章/player_prevented）→ changed_by_player，不强行重演；
  依赖它的事件 → unavailable（前置失效处理）；
- 每轮不再强制悬念/伏笔：允许自然过渡与平静收束，按原书节奏；
- 提交后推进：有事件证据才推进节拍；玩家停留允许延缓；不因回合数强推章节。
"""
from __future__ import annotations

import unittest

from core.engine.chapter_arc import (
    advance_chapter_arc, build_chapter_arc, chapter_generation_brief)
from core.engine.plot_threading import (
    committed_event_evidence, generation_brief, prepare, project_event_states,
    scene_card)


def _wish_row(i, text, status="active", affected=("北墙",)):
    return {"id": f"d{i}", "fact_norm": text, "rule": text, "scope": "session",
            "affected": list(affected),
            "authorization": {"authorization_id": f"a{i}", "request_id": f"req{i}",
                              "raw_text": text, "authorized_origin": "wish",
                              "status": status,
                              "accepted_interpretation": text,
                              "target_ids": list(affected)}}


def base_state(**over):
    state = {
        "round": 5, "current_chapter": 2,
        "chapter_index": {"chapters": [
            {"idx": 1, "title": "第一回"}, {"idx": 2, "title": "青崖问剑"}]},
        "objectives": [{"title": "拿到旧册", "status": "active"},
                       {"title": "撤离北墙", "status": "completed"}],
        "state_memory": {"scene": {"name": "北墙", "participants": ["苏叶", "周桐"],
                                   "motives": ["查证北墙"]},
                         "knowledge": {}},
        "story_graph": {"events": [
            {"event_id": "evt-a", "status": "resolved", "round": 2, "title": "发现裂痕"},
            {"event_id": "evt-b", "status": "planned", "prerequisites": ["evt-a"],
             "title": "取得旧册"},
            {"event_id": "evt-c", "status": "planned", "prerequisites": ["evt-x"],
             "title": "暗渠现身"},
        ], "completed": []},
        "story_ledger": [
            {"turn_id": "round-2", "round": 2, "chapter": 2, "committed": True,
             "narrative": "正文", "events": ["evt-a"]},
        ],
        # 反馈行很多但它们是回合质量回评，不是已提交事件证据。
        "sequence_feedback": [{"chapter": 2, "round": n} for n in range(1, 6)],
        "ledger": {"cheat": {"directives": [
            _wish_row(1, "苏叶获得夜视能力"),
            _wish_row(2, "草稿愿望尚未授权", status="draft"),
        ]}},
    }
    state.update(over)
    return state


class TestCommittedEventEvidence(unittest.TestCase):
    def test_only_committed_rows_count_as_evidence(self):
        state = base_state(story_ledger=[
            {"turn_id": "round-1", "round": 1, "chapter": 2, "committed": False,
             "narrative": "失败草稿", "events": ["evt-z"]},
            {"turn_id": "round-2", "round": 2, "chapter": 2, "committed": True,
             "narrative": "正文", "events": ["evt-a"]},
        ])
        evidence = committed_event_evidence(state)
        self.assertIn("evt-a", evidence)
        self.assertNotIn("evt-z", evidence, "未提交草稿的事件不得作为证据")
        self.assertEqual("round-2", evidence["evt-a"]["turn_id"])


class TestProjectEventStates(unittest.TestCase):
    def test_completed_binds_committed_evidence(self):
        states = project_event_states(base_state())
        entry = states["evt-a"]
        self.assertEqual("completed", entry["state"])
        self.assertEqual(["round-2"], entry["evidence_refs"])
        self.assertEqual("committed", entry["evidence_kind"])

    def test_graph_completed_without_evidence_is_unknown_legacy(self):
        state = base_state(story_ledger=[])
        states = project_event_states(state)
        entry = states["evt-a"]
        self.assertEqual("completed", entry["state"])
        self.assertEqual([], entry["evidence_refs"], "不得伪造已提交证据")
        self.assertEqual("unknown_legacy", entry["evidence_kind"])

    def test_satisfied_prerequisite_unlocks_active(self):
        states = project_event_states(base_state())
        self.assertEqual("active", states["evt-b"]["state"])

    def test_pending_or_unknown_prerequisite_is_not_started(self):
        states = project_event_states(base_state())
        self.assertEqual("not_started", states["evt-c"]["state"])

    def test_broken_chapter_event_is_changed_by_player(self):
        state = base_state(broken_anchors=[2])
        # 挂在被碎锚章上的事件属于玩家分歧，不属于待完成。
        state["story_graph"]["events"].append(
            {"event_id": "evt-d", "chapter": 2, "status": "planned", "title": "原书合围"})
        states = project_event_states(state)
        self.assertEqual("changed_by_player", states["evt-d"]["state"])

    def test_player_prevented_flag_without_shatter(self):
        state = base_state()
        state["story_graph"]["events"].append(
            {"event_id": "evt-p", "status": "planned", "player_prevented": True,
             "title": "码头截杀"})
        states = project_event_states(state)
        self.assertEqual("changed_by_player", states["evt-p"]["state"])

    def test_dependent_of_divergence_is_unavailable(self):
        state = base_state()
        state["story_graph"]["events"].append(
            {"event_id": "evt-p", "status": "planned", "player_prevented": True,
             "title": "码头截杀"})
        state["story_graph"]["events"].append(
            {"event_id": "evt-dep", "status": "planned", "prerequisites": ["evt-p"],
             "title": "截杀后续"})
        states = project_event_states(state)
        self.assertEqual("unavailable", states["evt-dep"]["state"])
        self.assertIn("prerequisite", states["evt-dep"]["reason"])


class TestSceneCard(unittest.TestCase):
    def test_card_shape_and_domains(self):
        card = scene_card(base_state(), user_input="查验北墙")
        self.assertEqual("scene-card-v1", card["schema"])
        self.assertEqual(2, card["source_ref"]["chapter"])
        self.assertEqual("青崖问剑", card["source_ref"]["title"])
        self.assertEqual("拿到旧册", card["current_objective"])
        self.assertEqual(["苏叶", "周桐"], card["participants"])
        self.assertEqual("查验北墙", card["player_action"])
        self.assertEqual({"evt-a": "completed", "evt-b": "active",
                          "evt-c": "not_started"}, card["event_states"])

    def test_authorized_deviations_only_active_wishes(self):
        card = scene_card(base_state())
        texts = [d["text"] for d in card["authorized_deviations"]]
        self.assertIn("苏叶获得夜视能力", texts)
        self.assertNotIn("草稿愿望尚未授权", texts, "draft 愿望无授权效力")

    def test_optional_threads_capped_by_default(self):
        state = base_state()
        for i in range(3):
            state["story_graph"]["events"].append(
                {"event_id": f"evt-opt{i}", "status": "active", "optional": True,
                 "title": f"支线{i}"})
        card = scene_card(state)
        self.assertLessEqual(len(card["optional_threads"]), 1,
                             "初始最多 1 条活跃非玩家主动支线")

    def test_forbidden_early_reveals_pending_prereq_events(self):
        card = scene_card(base_state())
        self.assertIn("暗渠现身", card["forbidden_early_reveals"])
        self.assertNotIn("发现裂痕", card["forbidden_early_reveals"],
                         "已完成事件可谈，不属于提前泄露")


class TestArcEvidenceBasis(unittest.TestCase):
    def test_feedback_rows_do_not_advance_beats(self):
        # 5 条反馈行 + 仅 1 条已提交台账 → 完成节拍 0（旧实现按反馈数得 2）。
        plan = build_chapter_arc(base_state(), {})
        self.assertEqual(0, plan["progress"]["completed_beats"])

    def test_committed_turns_advance_beats(self):
        state = base_state(sequence_feedback=[],
                           story_ledger=[
                               {"turn_id": f"round-{n}", "round": n, "chapter": 2,
                                "committed": True, "narrative": "正文", "events": []}
                               for n in range(1, 6)])
        plan = build_chapter_arc(state, {})
        self.assertEqual(2, plan["progress"]["completed_beats"])

    def test_pacing_policy_removes_forced_hook(self):
        plan = build_chapter_arc(base_state(), {})
        policy = plan["pacing_policy"]
        self.assertFalse(policy["forced_hook_per_turn"])
        self.assertIn("平静", policy["allow"])
        brief = chapter_generation_brief(plan, {})
        self.assertEqual(policy, brief["pacing_policy"])

    def test_advance_completes_beat_only_with_event_evidence(self):
        plan = build_chapter_arc(base_state(), {})
        advanced = advance_chapter_arc(plan, {
            "turn_id": "round-6", "round": 6, "committed": True,
            "events": ["evt-b"], "narrative": "正文"})
        self.assertEqual("completed", advanced["beats"][0]["status"])
        self.assertEqual("round-6", advanced["progress"]["beat_evidence"]["turn_id"])
        self.assertIn("evt-b", advanced["progress"]["beat_evidence"]["events"])

    def test_advance_holds_beat_without_evidence(self):
        # 玩家明确停留/日常回合：无事件证据 → 节拍延缓，回合数只作记录。
        plan = build_chapter_arc(base_state(), {})
        advanced = advance_chapter_arc(plan, {
            "turn_id": "round-7", "round": 7, "committed": True,
            "events": [], "narrative": "平静的一天"})
        self.assertEqual("current", advanced["beats"][0]["status"])
        self.assertEqual(plan["progress"]["turns"] + 1,
                         advanced["progress"]["turns"])
        self.assertNotIn("beat_evidence", advanced["progress"])

    def test_advance_never_bumps_chapter(self):
        plan = build_chapter_arc(base_state(), {})
        advanced = advance_chapter_arc(plan, {
            "turn_id": "round-8", "round": 8, "committed": True,
            "events": ["evt-b"], "narrative": "正文"})
        self.assertEqual(plan["chapter"], advanced["chapter"],
                         "章节推进不因回合数强推")


class TestBriefPropagation(unittest.TestCase):
    def test_brief_carries_scene_card_and_divergence(self):
        state = base_state()
        state["story_graph"]["events"].append(
            {"event_id": "evt-p", "status": "planned", "player_prevented": True,
             "title": "码头截杀"})
        thread = prepare(state, user_input="查验北墙")
        brief = generation_brief(thread)
        self.assertEqual("scene-card-v1", brief["scene_card"]["schema"])
        self.assertTrue(any("码头截杀" in x and "重演" in x
                            for x in thread.must_avoid),
                        "玩家明确阻止的事件必须进入 must_avoid 防重演")
        self.assertIn("changed_by_player", json_states(thread)["evt-p"])

    def test_brief_pacing_allows_calm_ending(self):
        thread = prepare(base_state(), user_input="查验北墙")
        self.assertTrue(any("悬念" in x for x in thread.must_avoid),
                        "不得每轮强行制造新悬念/伏笔")


def json_states(thread):
    return {eid: entry["state"] for eid, entry in thread.event_states.items()}


if __name__ == "__main__":
    unittest.main()
