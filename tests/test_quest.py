# -*- coding: utf-8 -*-
"""任务机制 v2.0.4 测试：上下文注入三处单源、奖励事务化发放+补发、判定证据制。

零网络：app 层用 mock 替身注入 _distill_model；管线层复用 ScriptedModel 模式。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from core.engine import options, papers, quest, turn_blueprint
from core.engine.quest import (
    compute_reward,
    new_offer,
    accept,
    quest_context_block,
    requirement_hits,
)
from core.memory import blank_state
from core.services import turn_pipeline as tp


def active_quest_state(round_no=5, deadline=8, **extra):
    """构造带 active 任务的 state（走真实状态机，不走手搓 dict）。"""
    state = {"mode": "强化模式", "round": round_no, "quest": None}
    offer = {"title": "查证北墙裂痕", "goal": "拿到守军换岗的破绽证据",
             "requirements": ["查证北墙裂痕", "拿到换岗破绽"], "plot_hook": ""}
    reward = compute_reward("short", 4, 4)
    new_offer(state, offer, "short", 4, reward, round_no - 1)
    accept(state)
    # accept 会按 span 重算 deadline；测试需显式覆盖时在 accept 之后设置。
    state["quest"]["deadline_round"] = deadline
    state.update(extra)
    return state


class TestQuestContextBlock(unittest.TestCase):
    def test_active_block_contains_essentials(self):
        state = active_quest_state()
        block = quest_context_block(state)
        self.assertIn("【进行中任务】查证北墙裂痕", block)
        self.assertIn("拿到守军换岗的破绽证据", block)
        self.assertIn("剩余 3 回合", block)
        self.assertIn("由系统", block)  # 奖励只预览，注明系统发放

    def test_overdue_urgency(self):
        state = active_quest_state(round_no=9, deadline=8)
        self.assertIn("已到或越过期限", quest_context_block(state))

    def test_non_active_returns_empty(self):
        state = active_quest_state()
        state["quest"]["status"] = "offered"
        self.assertEqual(quest_context_block(state), "")
        self.assertEqual(quest_context_block({}), "")


class TestRequirementHits(unittest.TestCase):
    BOX = {"requirements": ["查证北墙裂痕", "拿到换岗破绽"],
           "goal": "取得守军罪证"}

    def test_hit_and_miss(self):
        text = "主角深夜查证北墙裂痕，砖缝里的旧痕愈发清楚。"
        self.assertEqual(requirement_hits(self.BOX, text), 1)
        self.assertEqual(requirement_hits(self.BOX, "毫不相干的一天"), 0)

    def test_short_phrase_whole_match(self):
        box = {"requirements": ["救人"], "goal": ""}
        self.assertEqual(requirement_hits(box, "他出手救人一命"), 1)

    def test_punctuation_normalized(self):
        self.assertEqual(
            requirement_hits(self.BOX, "换岗、破绽！两个词都出现了吗"),
            requirement_hits(self.BOX, "换岗破绽两个词都出现了吗"))


class TestOptionFactor(unittest.TestCase):
    def test_active_quest_factor(self):
        state = active_quest_state()
        factors = options.collect_option_factors(state)
        quest_factors = [f for f in factors if f.get("kind") == "quest"]
        self.assertEqual(len(quest_factors), 1)
        self.assertEqual(quest_factors[0]["label"], "查证北墙裂痕")
        self.assertIn("剩 3 回合", quest_factors[0]["detail"])

    def test_inactive_no_factor(self):
        state = active_quest_state()
        state["quest"]["status"] = "completed"
        self.assertFalse([f for f in options.collect_option_factors(state)
                          if f.get("kind") == "quest"])


class TestDirectorPrompt(unittest.TestCase):
    def test_quest_hint_rendered(self):
        prompt = turn_blueprint.build_director_prompt(
            paper_label="卷", stage="setup", segment_count=2, target_chars=600,
            action="行动", context_tail="前情", active_names=["苏叶"],
            quest_hint="【进行中任务】查证北墙裂痕：目标｜剩余 3 回合")
        self.assertIn("【进行中任务】查证北墙裂痕", prompt)
        self.assertNotIn("无进行中任务", prompt)

    def test_default_placeholder(self):
        prompt = turn_blueprint.build_director_prompt(
            paper_label="卷", stage="setup", segment_count=2, target_chars=600,
            action="行动", context_tail="前情")
        self.assertIn("无进行中任务", prompt)


# —— 管线注入（复用 test_turn_pipeline 的 ScriptedModel 形状，本地精简版）——
ANCHOR = json.dumps({"chapter": 1, "title": "青崖问剑", "summary": "守军换岗时发现北墙裂痕",
                     "events": ["换岗", "查证北墙"]}, ensure_ascii=False)
STATE = {
    "round": 3, "chapter_round": 1, "turn_budget": 3, "paper_tier": 3,
    "story_richness": 700, "persona": "谋士", "golden_finger": "先知",
    "anchor_shattered": False,
}


class RecordingModel:
    """把所有 prompt 记下来；按特征分派答卷（导演/选项/其余按窗口填段）。"""

    def __init__(self, paper, names):
        self.calls = []
        self.paper = paper
        self.names = names

    def __call__(self, prompt):
        self.calls.append(prompt)
        if "option_seeds" in prompt:
            seeds = [{"factor": "金手指", "direction": f"路线{i}", "preview": "后果"}
                     for i in range(4)]
            seeds += [{"factor": "性格", "direction": f"性向{i}", "preview": "关系"}
                      for i in range(2)]
            return json.dumps({
                "beat": "查验北墙", "goal": "拿到旧册", "conflict": "守军阻拦",
                "segments": [
                    {"id": f"seg{i + 1}", "role": seg.role, "window": list(seg.window),
                     "events": [f"独立事件{i + 1}"], "must_include": [],
                     "must_mention": self.names[:1] if i == 0 else []}
                    for i, seg in enumerate(self.paper.segments)],
                "anchor_plan": {"stage": self.paper.stage, "action_terms": ["换岗"],
                                "result_terms": ["裂痕"], "causal_phrase": "因此"},
                "ripple_resolution": "代价收束", "world_beats": ["传闻"],
                "cliffhanger": "马蹄声", "log_draft": {"player": "查", "golden_finger": "先知",
                                                       "world": "北墙", "beat": "推进"},
                "option_seeds": seeds}, ensure_ascii=False)
        if '"options"' in prompt or "`options`" in prompt:
            return json.dumps({"options": [
                "推演北墙裂痕下一步走势（后果：看清暗哨）",
                "回溯旧册水渍前的数目（后果：锁定批次）",
                "比对铜扣暗记的刻痕（后果：确认经手人）",
                "透支预知换撤离窗口（后果：金手指冷却）",
                "先撤回茶棚再图后计（后果：暂避锋芒）",
                "只抄录暗记不惊动守军（后果：证据留存）"]}, ensure_ascii=False)
        low, high = 200, 320
        import re
        for line in prompt.splitlines():
            if "字" in line and "–" in line:
                nums = [int(x) for x in re.findall(r"\d+", line)[:2]]
                if len(nums) == 2:
                    low, high = nums
                break
        text = f"苏叶沿着北墙查验裂痕，因此守军的换岗节奏被彻底打乱。"
        while len(text) < low:
            text += "风声压过火把的噼啪，砖缝里的旧痕愈发清楚，随行的人不敢多问。"
        return text[:high - 1] if len(text) >= high else text


class TestPipelineInjection(unittest.TestCase):
    def _run(self, state):
        paper = papers.get_paper(3, "setup")
        model = RecordingModel(paper, ["苏叶"])
        result = tp.run_turn(
            state, None, "m", model_fn=model, message="查验北墙裂痕",
            context_blocks="前文略", active_members=[{"name": "苏叶"}],
            anchor_text=ANCHOR)
        return result, model

    def test_segment_and_director_prompts_contain_quest(self):
        state = dict(STATE, quest=active_quest_state()["quest"])
        result, model = self._run(state)
        self.assertIsInstance(result, tp.TurnResult)
        self.assertTrue(any("【进行中任务】查证北墙裂痕" in c for c in model.calls))
        # 段卷（含 WORLD 块）与导演卷（含任务行）都可见任务
        director_calls = [c for c in model.calls if "option_seeds" in c]
        self.assertTrue(director_calls and all("查证北墙裂痕" in c for c in director_calls))

    def test_context_audit_records_quest_block(self):
        state = dict(STATE, quest=active_quest_state()["quest"])
        result, _ = self._run(state)
        self.assertGreater(result.agent_meta["context_audit"]["quest_block_chars"], 20)

    def test_mechanism_context_completed_echo(self):
        snapshot = {"round": 7, "golden_finger": "先知", "last_ripple": {},
                    "scene_validation": {}, "state_memory": blank_state("强化模式", "")}
        paper = papers.get_paper(3, "setup")
        quest_box = {"status": "completed", "title": "查证北墙裂痕",
                     "last_settlement": {"round": 6, "granted": ["物资×1件"]}}
        text = tp._mechanism_context(snapshot, paper, quest_box, {}, snapshot["state_memory"])
        self.assertIn("任务已完成", text)
        self.assertIn("不得把该任务当作未完成", text)
        # 超过 2 回合不再回响
        stale = dict(quest_box, last_settlement={"round": 3, "granted": ["物资×1件"]})
        text2 = tp._mechanism_context(snapshot, paper, stale, {}, snapshot["state_memory"])
        self.assertNotIn("任务已完成（查证北墙裂痕）", text2)

    def test_mechanism_context_active_quest(self):
        snapshot = {"round": 5, "golden_finger": "先知", "last_ripple": {},
                    "scene_validation": {}, "state_memory": blank_state("强化模式", "")}
        paper = papers.get_paper(3, "setup")
        text = tp._mechanism_context(
            snapshot, paper, active_quest_state()["quest"], {}, snapshot["state_memory"])
        self.assertIn("任务 active", text)
        self.assertIn("可观察推进或明确受阻", text)


# —— app 层：奖励事务化 + 判定证据制 + 补发 ——
from core import app as core_app  # noqa: E402


def reward_payload(*items, relief=0.02):
    return {"kind": "short", "difficulty": 4, "player_difficulty": 4,
            "items": list(items), "convergence_relief": relief}


class TestGrantReward(unittest.TestCase):
    def _state(self):
        return {"mode": "强化模式", "round": 6,
                "state_memory": blank_state("强化模式", ""),
                "quest": active_quest_state(round_no=6)["quest"]}

    def test_grant_through_apply_turn_and_audit_consistent(self):
        state = self._state()
        before_items = list(state["state_memory"]["assets"]["items"])
        granted = core_app._grant_quest_reward(state, reward_payload(
            {"type": "物资", "amount": 2, "unit": "件"},
            {"type": "技能碎片", "amount": 1, "unit": "片"},
            {"type": "神秘力量", "amount": 9, "unit": "个"}, relief=0))
        memory = state["state_memory"]
        self.assertEqual(len(memory["assets"]["items"]), len(before_items) + 1)
        entry = memory["assets"]["items"][-1]
        self.assertEqual(entry["source"], "quest")
        self.assertEqual(entry["name"], "任务奖励物资×2")
        self.assertEqual(len(memory["abilities"]["skills"]), 1)
        # 走了 apply_turn：审计历史里有 quest_reward 来源记录
        self.assertTrue(any(h.get("source") == "quest_reward"
                            for h in memory.get("history", [])))
        # 审计与生效一致：未知类型不入审计
        audit = state["ledger"]["cheat"]["quest_rewards"]
        self.assertEqual([a["type"] for a in audit], ["物资", "技能碎片"])
        self.assertEqual(sorted(granted), sorted(["物资×2件", "技能碎片×1片"]))
        self.assertIn("状态记忆面板", state["state_panel"])

    def test_momentum_writes_ripple_not_memory(self):
        state = self._state()
        state["ripples"] = [{"level": "L1", "effective_total": 2}]
        core_app._grant_quest_reward(state, reward_payload(
            {"type": "积势", "amount": 2, "unit": "点"}))
        self.assertEqual(state["ripples"][-1]["effective_total"], 4)
        audit = state["ledger"]["cheat"]["quest_rewards"]
        self.assertEqual([a["type"] for a in audit], ["积势"])
        # 积势不动 state_memory 分类
        self.assertEqual(state["state_memory"]["assets"]["items"], [])

    def test_empty_and_invalid_items_noop(self):
        state = self._state()
        self.assertEqual(core_app._grant_quest_reward(state, reward_payload()), [])
        self.assertEqual(
            core_app._grant_quest_reward(state, reward_payload(
                {"type": "物资", "amount": 0, "unit": "件"})), [])
        self.assertNotIn("ledger", state)


class TestVerdictCheck(unittest.TestCase):
    BOX = {"requirements": ["查证北墙裂痕", "拿到换岗破绽"], "goal": "取得证据"}
    REPLY = "主角深夜潜到北墙下，亲手查证北墙裂痕，并从换岗记录里拿到换岗破绽，铁证如山。"
    UNRELATED = "主角在茶棚里喝了一碗粗茶，听老人讲了个与前事无关的故事。"

    def test_verbatim_evidence_accepted(self):
        verdict = {"completed": True, "evidence": "亲手查证北墙裂痕"}
        v, check = core_app._quest_verdict_check(self.BOX, verdict, self.REPLY)
        self.assertTrue(v["completed"])
        self.assertIsNone(check)

    def test_fabricated_evidence_rejected(self):
        # 正文与任务毫无重合：无引文、无关键词佐证 → 判定降级为未完成
        verdict = {"completed": True, "evidence": "主角一刀斩杀了守军统帅取得首级"}
        v, check = core_app._quest_verdict_check(self.BOX, verdict, self.UNRELATED)
        self.assertFalse(v["completed"])
        self.assertEqual(check, "evidence_rejected")

    def test_paraphrase_with_keyword_hits_corroborated(self):
        verdict = {"completed": True, "evidence": "主角完成了对城墙缺陷的查验任务"}
        v, check = core_app._quest_verdict_check(self.BOX, verdict, self.REPLY)
        self.assertTrue(v["completed"])
        self.assertEqual(check, "keyword_corroborated")

    def test_incomplete_verdict_untouched(self):
        verdict = {"completed": False, "evidence": "仍在推进"}
        v, check = core_app._quest_verdict_check(self.BOX, verdict, self.REPLY)
        self.assertFalse(v["completed"])
        self.assertIsNone(check)


class TestSettleQuest(unittest.TestCase):
    def _settle(self, state, verdict_json):
        with mock.patch.object(core_app, "_distill_model",
                               return_value=verdict_json):
            core_app._settle_quest(state, None, "m", None, "deepseek",
                                   "我查证北墙", self.REPLY, 6)

    REPLY = "主角深夜潜到北墙下，亲手查证北墙裂痕，并从换岗记录里拿到换岗破绽，铁证如山。"

    def test_full_chain_complete_and_grant(self):
        state = {"mode": "强化模式", "round": 6,
                 "state_memory": blank_state("强化模式", ""),
                 "quest": active_quest_state(round_no=6)["quest"]}
        self._settle(state, json.dumps(
            {"completed": True, "evidence": "亲手查证北墙裂痕"}, ensure_ascii=False))
        box = state["quest"]
        self.assertEqual(box["status"], "completed")
        self.assertEqual(len(state["state_memory"]["assets"]["items"])
                         + len(state["state_memory"]["abilities"]["skills"]) >= 1, True)
        self.assertTrue(box["last_settlement"]["granted"])
        self.assertNotIn("reward_pending", box)

    def test_hallucinated_completion_stays_active(self):
        state = {"mode": "强化模式", "round": 6,
                 "state_memory": blank_state("强化模式", ""),
                 "quest": active_quest_state(round_no=6)["quest"]}
        unrelated = "主角在茶棚里喝了一碗粗茶，听老人讲了个与前事无关的故事。"
        with mock.patch.object(core_app, "_distill_model", return_value=json.dumps(
                {"completed": True, "evidence": "主角已经飞升成仙完成任务"},
                ensure_ascii=False)):
            core_app._settle_quest(state, None, "m", None, "deepseek",
                                   "我查证北墙", unrelated, 6)
        box = state["quest"]
        self.assertEqual(box["status"], "active")
        self.assertEqual(box["last_settlement"]["evidence_check"], "evidence_rejected")

    def test_grant_failure_pends_then_retries(self):
        state = {"mode": "强化模式", "round": 6,
                 "state_memory": blank_state("强化模式", ""),
                 "quest": active_quest_state(round_no=6)["quest"]}
        verdict = json.dumps(
            {"completed": True, "evidence": "亲手查证北墙裂痕"}, ensure_ascii=False)
        with mock.patch.object(core_app, "_distill_model", return_value=verdict), \
                mock.patch.object(core_app, "apply_turn",
                                  side_effect=ValueError("校验拒绝")):
            core_app._settle_quest(state, None, "m", None, "deepseek",
                                   "我查证北墙", self.REPLY, 6)
        box = state["quest"]
        self.assertEqual(box["status"], "completed")
        self.assertTrue(box.get("reward_pending"))
        self.assertEqual(state["state_memory"]["assets"]["items"], [])
        # 下一回合：补发成功，挂起清除
        self._settle(state, verdict)
        box = state["quest"]
        self.assertNotIn("reward_pending", box)
        self.assertEqual(len(state["state_memory"]["assets"]["items"])
                         + len(state["state_memory"]["abilities"]["skills"]) >= 1, True)


class TestVerdictPrompt(unittest.TestCase):
    def test_prompt_requires_verbatim_quote(self):
        prompt = core_app._quest_verdict_prompt(
            {"goal": "g", "requirements": ["r1"]}, "行动", "正文")
        self.assertIn("逐字摘录", prompt)
        self.assertIn("不得编造", prompt)


if __name__ == "__main__":
    unittest.main()
