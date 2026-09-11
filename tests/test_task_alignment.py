# -*- coding: utf-8 -*-
"""C06 任务对齐与一次结算：否定/计划/尝试排除、已提交结果验证、
TaskProjection 可行性投影、奖励幂等发放。

零网络：app 层只测 _grant_quest_reward/_quest_verdict_check/_settle_quest
的本地规则与状态机（判定模型以 mock 替身注入）。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from core.engine.task_acceptance import evaluate
from core.engine.task_director import project_task, select_tasks
from core.engine.quest import compute_reward, requirement_hits
from core import app as core_app
from core.memory import blank_state


def quest_box(**extra):
    box = {"status": "active", "kind": "short", "difficulty": 4,
           "title": "查证北墙裂痕", "goal": "取得守军罪证",
           "requirements": ["查证北墙裂痕", "拿到换岗破绽"],
           "plot_hook": "", "accepted_round": 5, "deadline_round": 8,
           "progress": [], "reward": compute_reward("short", 4, 4)}
    box.update(extra)
    return box


def grant_state(round_no=6):
    return {"mode": "强化模式", "round": round_no,
            "state_memory": blank_state("强化模式", ""),
            "quest": quest_box()}


def reward_payload(*items, relief=0.0):
    return {"kind": "short", "difficulty": 4, "player_difficulty": 4,
            "items": list(items), "convergence_relief": relief}


# —— task_acceptance：否定/计划/尝试不算事实 ——

class TestNegationPlanAttempt(unittest.TestCase):
    TASK = {"hard_facts": ["找到钥匙"], "soft_signals": ["询问"]}

    def test_plan_does_not_complete(self):
        result = evaluate(self.TASK, action="我准备找到钥匙，再顺手拿走")
        self.assertEqual(result["status"], "pending")

    def test_negation_does_not_complete(self):
        result = evaluate(self.TASK, action="没有找到钥匙")
        self.assertEqual(result["status"], "pending")

    def test_attempt_failure_does_not_complete(self):
        result = evaluate(self.TASK, action="他试图找到钥匙，但锁孔灌了铁水")
        self.assertEqual(result["status"], "pending")

    def test_question_form_does_not_complete(self):
        result = evaluate(self.TASK, action="找到钥匙了吗")
        self.assertEqual(result["status"], "pending")

    def test_real_acquisition_completes(self):
        result = evaluate(self.TASK,
                          narrative="他探进墙缝摸索半晌，终于找到钥匙，攥进手心。")
        self.assertEqual(result["status"], "complete")

    def test_negated_then_real_counts_the_real_one(self):
        result = evaluate(
            self.TASK,
            narrative="起初没有找到钥匙，翻到第三层架子，他找到钥匙，冰凉的铜齿硌着手心。")
        self.assertEqual(result["status"], "complete")


# —— task_acceptance：完成需落在已提交回合 ——

class TestCommittedVerification(unittest.TestCase):
    TASK = {"hard_facts": ["拿到令牌"], "soft_signals": []}
    STORY = "他掰断锁扣，拿到令牌，塞进怀里。"

    @staticmethod
    def _row(narrative, committed=True):
        return {"turn_id": "round-1", "round": 1, "chapter": 1, "action": "",
                "narrative": narrative, "events": [], "committed": committed}

    def test_committed_evidence_completes(self):
        state = {"story_ledger": [self._row(self.STORY)]}
        result = evaluate(self.TASK, action="拿到令牌", narrative=self.STORY, state=state)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["progress"]["committed_verified"])

    def test_uncommitted_row_does_not_complete(self):
        state = {"story_ledger": [self._row(self.STORY, committed=False)]}
        result = evaluate(self.TASK, action="拿到令牌", narrative=self.STORY, state=state)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["progress"]["committed_verified"])

    def test_committed_rows_without_fact_caps_at_partial(self):
        state = {"story_ledger": [self._row("他在茶棚歇脚。")]}
        result = evaluate(self.TASK, action="拿到令牌", narrative=self.STORY, state=state)
        self.assertEqual(result["status"], "partial")

    def test_no_ledger_keeps_legacy_complete(self):
        result = evaluate(self.TASK, action="拿到令牌", narrative=self.STORY)
        self.assertEqual(result["status"], "complete")


# —— task_director：TaskProjection 可行性投影（与 C05 事件投影同源） ——

class TestTaskProjection(unittest.TestCase):
    @staticmethod
    def _state(events=None):
        return {"current_chapter": 2, "round": 6,
                "state_memory": {"location": {"name": "城门"}},
                "story_graph": {"events": events if events is not None else [
                    {"id": "evt-a", "title": "北墙密道", "chapter": 2,
                     "status": "pending", "player_prevented": True},
                    {"id": "evt-b", "title": "换岗破绽", "chapter": 2,
                     "status": "active"}]},
                "story_ledger": []}

    def test_player_prevented_anchor_marks_task_infeasible(self):
        row = project_task({"id": "t1", "title": "穿越北墙密道", "anchor": "北墙密道"},
                           self._state())
        self.assertFalse(row["feasible"])
        self.assertEqual(row["infeasible_reason"], "prerequisite_invalidated")
        self.assertTrue(row["optional"])

    def test_ref_by_event_id_also_resolves(self):
        row = project_task({"id": "t1", "title": "穿越密道", "event_ref": "evt-a"},
                           self._state())
        self.assertFalse(row["feasible"])

    def test_feasible_task_projects_clean(self):
        row = project_task({"id": "t2", "title": "套出换岗破绽", "anchor": "换岗破绽"},
                           self._state())
        self.assertTrue(row["feasible"])
        self.assertIsNone(row["infeasible_reason"])

    def test_declared_zero_feasibility_infeasible(self):
        row = project_task({"id": "t3", "title": "杂项", "feasibility": 0}, self._state())
        self.assertFalse(row["feasible"])
        self.assertEqual(row["infeasible_reason"], "declared_infeasible")

    def test_select_tasks_drops_infeasible_optional(self):
        tasks = [{"id": "t1", "title": "穿越北墙密道", "anchor": "北墙密道"},
                 {"id": "t2", "title": "套出换岗破绽", "anchor": "换岗破绽"}]
        surfaced = select_tasks(tasks, self._state())
        self.assertEqual([t["id"] for t in surfaced], ["t2"])

    def test_select_tasks_keeps_infeasible_mainline_marked(self):
        tasks = [{"id": "t1", "title": "穿越北墙密道", "anchor": "北墙密道", "mainline": True},
                 {"id": "t2", "title": "套出换岗破绽", "anchor": "换岗破绽"}]
        surfaced = select_tasks(tasks, self._state())
        self.assertIn("t1", [t["id"] for t in surfaced])
        t1 = next(t for t in surfaced if t["id"] == "t1")
        self.assertFalse(t1["feasible"])

    def test_broken_anchor_chapter_invalidates_wish_changed_task(self):
        # 愿望改世碎锚：第 2 章锚点碎裂后，挂在该锚点上的可选任务不再可行
        state = self._state(events=[
            {"id": "evt-a", "title": "北墙密道", "chapter": 2, "status": "pending"}])
        state["broken_anchors"] = [2]
        row = project_task({"id": "t1", "title": "穿越北墙密道", "anchor": "北墙密道"}, state)
        self.assertFalse(row["feasible"])
        self.assertEqual(row["infeasible_reason"], "prerequisite_invalidated")


# —— quest.requirement_hits：佐证核验同样排除否定/计划 ——

class TestRequirementHitsTaint(unittest.TestCase):
    BOX = {"requirements": ["找到钥匙"], "goal": ""}

    def test_negation_not_counted(self):
        self.assertEqual(requirement_hits(self.BOX, "没有找到钥匙"), 0)

    def test_plan_not_counted(self):
        self.assertEqual(requirement_hits(self.BOX, "我准备找到钥匙"), 0)

    def test_real_hit_counted(self):
        self.assertEqual(requirement_hits(self.BOX, "终于找到钥匙，攥进手心"), 1)


# —— app：判定核验排除「打算做/没做成」的口头证据 ——

class TestVerdictCheckTaint(unittest.TestCase):
    BOX = quest_box()

    def test_intent_speech_rejected(self):
        reply = "主角回到营帐，说：“我准备查证北墙裂痕，明天再动手。”"
        verdict = {"completed": True, "evidence": "我准备查证北墙裂痕"}
        v, check = core_app._quest_verdict_check(self.BOX, verdict, reply)
        self.assertFalse(v["completed"])
        self.assertEqual(check, "evidence_rejected")

    def test_negated_verbatim_rejected(self):
        reply = "主角绕着北墙走了一圈，没有查证北墙裂痕，也没有拿到换岗破绽。"
        verdict = {"completed": True, "evidence": "没有查证北墙裂痕"}
        v, check = core_app._quest_verdict_check(self.BOX, verdict, reply)
        self.assertFalse(v["completed"])
        self.assertEqual(check, "evidence_rejected")

    def test_genuine_verbatim_still_accepted(self):
        reply = "主角深夜潜到北墙下，亲手查证北墙裂痕，并从换岗记录里拿到换岗破绽，铁证如山。"
        verdict = {"completed": True, "evidence": "亲手查证北墙裂痕"}
        v, check = core_app._quest_verdict_check(self.BOX, verdict, reply)
        self.assertTrue(v["completed"])
        self.assertIsNone(check)


# —— app：奖励发放幂等（重复结算/部分失败补发都不双奖） ——

class TestRewardIdempotency(unittest.TestCase):
    def _reward(self):
        return reward_payload({"type": "物资", "amount": 2, "unit": "件"},
                              {"type": "积势", "amount": 2, "unit": "点"})

    def _asset_rows(self, state):
        return [r for r in state["state_memory"]["assets"]["items"]
                if r.get("name") == "任务奖励物资×2"]

    def test_double_grant_is_noop(self):
        state = grant_state()
        state["ripples"] = [{"level": "L1", "effective_total": 2}]
        first = core_app._grant_quest_reward(state, self._reward())
        self.assertEqual(len(first), 2)
        self.assertEqual(len(self._asset_rows(state)), 1)
        self.assertEqual(len(state["ledger"]["cheat"]["quest_rewards"]), 2)
        self.assertEqual(state["ripples"][-1]["effective_total"], 4)

        second = core_app._grant_quest_reward(state, self._reward())
        self.assertEqual(second, [])
        self.assertEqual(len(self._asset_rows(state)), 1)
        self.assertEqual(len(state["ledger"]["cheat"]["quest_rewards"]), 2)
        self.assertEqual(state["ripples"][-1]["effective_total"], 4)

    def test_partial_failure_retry_converges_single_reward(self):
        state = grant_state()
        state["ripples"] = [{"level": "L1", "effective_total": 2}]
        with mock.patch.object(core_app, "render_panel",
                               side_effect=RuntimeError("面板渲染失败")):
            with self.assertRaises(RuntimeError):
                core_app._grant_quest_reward(state, self._reward())
        # 阶段一已落账（apply_turn 成功），面板渲染失败前不重复入账
        self.assertEqual(len(self._asset_rows(state)), 1)

        granted = core_app._grant_quest_reward(state, self._reward())
        self.assertEqual(len(granted), 2)
        self.assertEqual(len(self._asset_rows(state)), 1)
        self.assertEqual(len(state["ledger"]["cheat"]["quest_rewards"]), 2)
        self.assertEqual(state["ripples"][-1]["effective_total"], 4)


class TestSettleOnceOnly(unittest.TestCase):
    REPLY = "主角深夜潜到北墙下，亲手查证北墙裂痕，并从换岗记录里拿到换岗破绽，铁证如山。"

    def test_resettle_after_completion_grants_nothing_more(self):
        state = grant_state(round_no=6)
        verdict = json.dumps({"completed": True, "evidence": "亲手查证北墙裂痕"},
                             ensure_ascii=False)
        with mock.patch.object(core_app, "_distill_model", return_value=verdict):
            core_app._settle_quest(state, None, "m", None, "deepseek",
                                   "我查证北墙", self.REPLY, 6)
        box = state["quest"]
        self.assertEqual(box["status"], "completed")
        counted = (len(state["ledger"]["cheat"]["quest_rewards"])
                   + len(state["state_memory"]["assets"]["items"])
                   + len(state["state_memory"]["abilities"]["skills"]))
        self.assertGreater(counted, 0)

        state["round"] = 7
        with mock.patch.object(core_app, "_distill_model", return_value=verdict):
            core_app._settle_quest(state, None, "m", None, "deepseek",
                                   "我查证北墙", self.REPLY, 7)
        self.assertEqual(
            len(state["ledger"]["cheat"]["quest_rewards"])
            + len(state["state_memory"]["assets"]["items"])
            + len(state["state_memory"]["abilities"]["skills"]), counted)
        self.assertNotIn("reward_pending", state["quest"])


if __name__ == "__main__":
    unittest.main()
