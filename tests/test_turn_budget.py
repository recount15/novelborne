# -*- coding: utf-8 -*-
"""C04 非死锁基础设施：请求级预算、显式终态、取消与晚到结果丢弃（红测试先行）。

验收（计划 §4 / 卡 C04）：
- 持续无效模型/持续超时/修复同样失败都在预算内结束（有界时间，不挂死）；
- 取消为终态：取消后不再发起模型调用，晚到结果不写入；
- 硬错误（A 类）不得 keep-best 提交；仅软质量可 warning 提交；
- 预算配置与实际用量可见（agent_meta.request_budget），不静默超支；
- agent_cluster 预算经 RequestBudgetProfile 接线（不叠加第二套重试器）。
"""
from __future__ import annotations

import importlib
import json
import threading
import time
import unittest

from core.engine import papers, turn_blueprint
quality_gate = importlib.import_module("core.engine.quality_gate")
from core.engine.fact_contract import DEFAULT_REQUEST_BUDGET, RequestBudgetProfile
from core.services import turn_pipeline as tp
from core.services.agent_cluster_service import BudgetPolicy, ClusterError

ANCHOR = json.dumps({"chapter": 1, "title": "青崖问剑",
                     "summary": "守军换岗时发现北墙裂痕",
                     "events": ["换岗", "查证北墙"]}, ensure_ascii=False)

STATE = {
    "round": 3, "chapter_round": 1, "turn_budget": 3, "paper_tier": 3,
    "story_richness": 700, "persona": "谋士", "golden_finger": "先知",
    "anchor_shattered": False,
}


def director_json(paper, terms, names):
    seeds = [{"factor": "金手指", "direction": f"金手指路线{i}", "preview": "冷却代价"}
             for i in range(4)]
    seeds += [{"factor": "性格", "direction": f"性格路线{i}", "preview": "关系变化"}
              for i in range(2)]
    return json.dumps({
        "beat": "查验北墙", "goal": "拿到旧册", "conflict": "守军阻拦",
        "segments": [
            {"id": f"seg{i + 1}", "role": seg.role, "window": list(seg.window),
             "events": [f"独立事件{i + 1}"],
             "must_include": [], "must_mention": names[:1] if i == 0 else []}
            for i, seg in enumerate(paper.segments)
        ],
        "anchor_plan": {"stage": paper.stage, "action_terms": terms[:1],
                        "result_terms": terms[:1], "causal_phrase": "因此落定"},
        "ripple_resolution": "以代价收束",
        "world_beats": ["传闻扩散"],
        "cliffhanger": "马蹄声未散",
        "log_draft": {"player": "查验北墙", "golden_finger": "先知示警",
                      "world": "北墙裂痕", "beat": "推进"},
        "option_seeds": seeds,
    }, ensure_ascii=False)


def options_json():
    items = [
        "推演北墙裂痕下一步走势（后果：提前看清暗哨位置）",
        "回溯旧册水渍前的数目（后果：锁定军械批次）",
        "比对铜扣暗记的刻痕（后果：确认经手人身份）",
        "透支预知换撤离窗口（后果：金手指进入冷却）",
        "先撤回茶棚再图后计（后果：暂避锋芒）",
        "只抄录暗记不惊动守军（后果：证据留存）",
    ]
    return json.dumps({"options": items}, ensure_ascii=False)


def filler(low, high, name="苏叶"):
    base = f"{name}沿着北墙查验裂痕，因此守军的换岗节奏被彻底打乱。"
    text = base
    while len(text) < low:
        text += "风声压过火把的噼啪，砖缝里的旧痕愈发清楚，随行的人不敢多问。"
    return text[:high - 1] if len(text) >= high else text


class CountingModel:
    """按提示词特征分派合格答案并计数（线程安全）。"""

    def __init__(self, paper, terms, names, *, delay: float = 0.0):
        self._director = director_json(paper, terms, names)
        self._options = options_json()
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, prompt):
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if "option_seeds" in prompt:
            return self._director
        if "`options`" in prompt or '"options"' in prompt:
            return self._options
        low, high = 200, 320
        for line in prompt.splitlines():
            if "字" in line and "–" in line:
                nums = [int(x) for x in __import__("re").findall(r"\d+", line)[:2]]
                if len(nums) == 2:
                    low, high = nums
                break
        return filler(low, high)


class FailModel:
    """绝不应被调用的哨兵模型：一旦调用立即失败并记录。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        return ""


class SleepingInvalidModel:
    """导演即答；其余先睡够时长再返回空串：烧掉预算且不产任何有效稿。"""

    def __init__(self, paper, terms, names, *, delay: float = 1.2):
        self._director = director_json(paper, terms, names)
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, prompt):
        with self._lock:
            self.calls += 1
        if "option_seeds" in prompt:
            return self._director
        time.sleep(self.delay)
        return ""


class TestRequestBudgetResolution(unittest.TestCase):
    def test_default_profile_when_unset(self):
        self.assertIs(tp.resolve_request_budget({}), DEFAULT_REQUEST_BUDGET)

    def test_state_override_honored(self):
        profile = tp.resolve_request_budget({"request_budget": {"deadline_seconds": 33.0}})
        self.assertEqual(profile.deadline_seconds, 33.0)
        self.assertEqual(profile.max_repair_passes,
                         DEFAULT_REQUEST_BUDGET.max_repair_passes)

    def test_invalid_config_is_loud_not_silent(self):
        with self.assertRaises(tp.TurnBudgetError):
            tp.resolve_request_budget({"request_budget": {"deadline_seconds": 0}})

    def test_cluster_policy_uses_profile_with_structural_floor(self):
        profile = RequestBudgetProfile(deadline_seconds=45.0)
        policy = tp.cluster_policy_from_profile(profile)
        self.assertIsInstance(policy, BudgetPolicy)
        self.assertEqual(policy.deadline_seconds, 45.0)
        self.assertGreaterEqual(policy.max_calls, tp.CLUSTER_STRUCTURAL_MIN_CALLS,
                                "十节点 DAG 结构性调用下限不得被 6 次配置误杀")


class TestGateClassification(unittest.TestCase):
    def test_structural_codes_are_class_A(self):
        for code in ("unauthorized_evidence", "unauthorized_directive",
                     "stale_snapshot", "budget_exhausted", "deadline_exceeded",
                     "claim_schema", "critic_blocked"):
            self.assertEqual("A", quality_gate.classify_gate_code(code), code)

    def test_unknown_codes_fail_closed_to_A(self):
        self.assertEqual("A", quality_gate.classify_gate_code("something_new"))
        self.assertEqual("A", quality_gate.classify_gate_code(""))

    def test_soft_lane_reserved_not_silent(self):
        # 目前没有任何软质量以 GateError 形式出现；预留集合必须显式为空，
        # 未来加入即需测试锚定（禁止借软通道放行结构错误）。
        self.assertEqual(frozenset(), quality_gate.SOFT_GATE_CODES)

    def test_terminal_status_from_code(self):
        self.assertEqual("cancelled",
                         quality_gate.terminal_status_from_code("cancelled"))
        self.assertEqual("failed_recoverable",
                         quality_gate.terminal_status_from_code("unauthorized_evidence"))
        self.assertEqual("failed_recoverable",
                         quality_gate.terminal_status_from_code("cluster_failed"))


class TestClusterErrorTerminal(unittest.TestCase):
    def test_blocked_is_failed_recoverable(self):
        self.assertEqual("failed_recoverable", ClusterError("worker_failed").terminal_status)
        self.assertEqual("failed_recoverable",
                         ClusterError("unauthorized_evidence").terminal_status)

    def test_cancelled_terminal(self):
        self.assertEqual("cancelled", ClusterError("cancelled").terminal_status)

    def test_sequential_generations_after_failure_not_deadlocked(self):
        # 失败后的再次生成必须可用（预算/锁不残留）。
        from core.services import generation_skills as gs
        from core.services.agent_cluster_service import AgentClusterService
        data = {
            "book_id": "b", "source_hash": "s", "scope": "game",
            "context_hash": "c", "intent_hash": "i", "base_revision": 1,
            "cutoff": 3, "strategy": "agent_cluster",
            "authorized_evidence": {}, "authorized_branch_events": {},
            "authorized_directives": {},
            "role_projections": {key: {"data": {}, "evidence_ids": [],
                                       "branch_event_ids": [],
                                       "knowledge_holders": ["hero"],
                                       "authorized_directives": []}
                                 for key in gs.DEPENDENCIES},
        }
        snap = gs.GenerationSnapshot.freeze(data)
        bad = {key: (lambda req: (_ for _ in ()).throw(RuntimeError("down")))
               for key in gs.DEPENDENCIES}
        with self.assertRaises(ClusterError):
            AgentClusterService(bad).generate(snap)
        good = {key: (lambda req, key=key: gs.ModelReply(
            {"decision": "approve", "reasons": ["ok"]}
            if gs.default_registry()[key].output_schema == "decision" else
            {"summary": "计划摘要足够具体，因果链完整", "claims": []}
            if gs.default_registry()[key].output_schema == "plan" else
            {"narrative": "正文内容足够长，情节推进自然，因果衔接清楚，风格稳定，字数在窗口之内，角色在场互动。",
             "claims": []}
            if gs.default_registry()[key].output_schema == "scene" else
            {"approved": True, "issues": []}
            if gs.default_registry()[key].output_schema == "critic" else
            {"claims": [], "options": [
                {"option_id": "a" + label, "label": label,
                 "text": "行动选项" + label + "各不相同且足够长",
                 "base_revision": 1, "preconditions": [], "effects": [],
                 "knowledge_evidence_ids": []} for label in "ABCDEF"]},
            req.snapshot_hash, "m", "test", "1"))
            for key in gs.DEPENDENCIES}
        candidate = AgentClusterService(good).generate(snap)
        self.assertEqual("validated_candidate", candidate.status)


class TestSimplePathBudget(unittest.TestCase):
    def setUp(self):
        self.paper = papers.get_paper(3, "setup")
        self.terms = turn_blueprint.extract_anchor_terms(ANCHOR)
        self.names = ["苏叶", "周桐"]

    def _model(self, **kw):
        return CountingModel(self.paper, self.terms, self.names, **kw)

    def _run(self, model, state=None, **kw):
        return tp.run_turn(
            dict(STATE, **(state or {})), None, "m", model_fn=model,
            message="查验北墙裂痕", context_blocks="前文略",
            active_members=[{"name": n} for n in self.names],
            anchor_text=ANCHOR, **kw)

    def test_expired_budget_refuses_model_calls_and_fails_recoverable(self):
        model = FailModel()
        started = time.monotonic()
        with self.assertRaises(tp.TurnBudgetError) as caught:
            self._run(model, state={"request_budget": {"deadline_seconds": 0.001}})
        self.assertEqual("failed_recoverable", caught.exception.terminal_status)
        self.assertEqual(0, model.calls, "预算耗尽后不得再发起模型调用")
        self.assertLess(time.monotonic() - started, 5.0, "预算内必须结束")

    def test_cancel_before_run_is_terminal_and_makes_no_calls(self):
        model = FailModel()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(tp.TurnCancelledError) as caught:
            self._run(model, cancel=cancel)
        self.assertEqual("cancelled", caught.exception.terminal_status)
        self.assertEqual(0, model.calls)

    def test_slow_model_ends_within_budget(self):
        # 无效慢模型（睡而不产稿）：预算 11s，单调用门槛 10s——
        # 导演即时成功后，Wave B 一次慢调用即把剩余时间压到门槛以下，
        # 后续调用全部被拒 → 预算内以 failed_recoverable 终态结束。
        model = SleepingInvalidModel(self.paper, self.terms, self.names, delay=1.2)
        state = {"request_budget": {"deadline_seconds": 11.0}}
        started = time.monotonic()
        with self.assertRaises(tp.TurnBudgetError) as caught:
            self._run(model, state=state)
        self.assertEqual("failed_recoverable", caught.exception.terminal_status)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 8.0,
                        "总预算必须约束挂起模型： %.1fs 内应已终止" % elapsed)
        self.assertGreater(model.calls, 0, "预算窗口内的调用应已发生")

    def test_happy_path_commits_with_visible_budget_meta(self):
        model = self._model()
        result = self._run(model)
        self.assertIsInstance(result, tp.TurnResult)
        meta = result.agent_meta
        self.assertEqual("committed", meta["terminal_status"])
        budget = meta["request_budget"]
        self.assertGreater(budget["model_calls"], 0)
        self.assertEqual(DEFAULT_REQUEST_BUDGET.deadline_seconds,
                         budget["deadline_seconds"])
        self.assertFalse(budget["deadline_hit"])
        self.assertEqual([], budget["stage_skips"])

    def test_terminal_status_assignment_rules(self):
        # deadline 命中但有既得草稿 → warning 提交；干净通过 → committed。
        self.assertEqual("committed_with_warnings",
                         tp.terminal_status_for(deadline_hit=True,
                                                stage_skips=["polish"],
                                                format_fallback=False))
        self.assertEqual("committed",
                         tp.terminal_status_for(deadline_hit=False,
                                                stage_skips=[],
                                                format_fallback=False))
        self.assertEqual("committed_with_warnings",
                         tp.terminal_status_for(deadline_hit=False,
                                                stage_skips=[],
                                                format_fallback=True))


class TestClusterBudgetWiring(unittest.TestCase):
    SOURCE = {"book_id": "book", "source_hash": "s1",
              "texts": {1: "已知场景：北墙下换岗的哨兵正在打盹。" * 8}}

    def test_profile_deadline_reaches_cluster(self):
        # 模型延迟 50ms 后给出合格计划工件：若预算来自 RequestBudgetProfile
        # （0.001s），回包后必触发 deadline_exceeded；若沿用 420s 默认值，
        # 第一波会顺利通过——以合格工件排除「模型失败抢占结论」的干扰。
        state = {"agent_mode": True, "distill_key": "host-book",
                 "knowledge_cutoff": {"chapter_no": 1, "offset": 24},
                 "state_memory": {"revision": 4},
                 "request_budget": {"deadline_seconds": 0.001}}

        class SlowOkModel:
            def __init__(self):
                self.calls = 0

            def __call__(self, prompt):
                self.calls += 1
                time.sleep(0.05)
                return json.dumps({"summary": "计划摘要足够具体，因果链完整",
                                   "claims": []}, ensure_ascii=False)

        model = SlowOkModel()
        with self.assertRaises(ClusterError) as caught:
            tp.run_turn(state, None, "m", model_fn=model, message="推门",
                        cluster_source_reader=lambda *_: json.loads(json.dumps(self.SOURCE)))
        self.assertEqual("deadline_exceeded", caught.exception.code,
                         "集群预算必须来自 RequestBudgetProfile 而非 420s 默认值")
        self.assertEqual("failed_recoverable", caught.exception.terminal_status)
        self.assertLessEqual(model.calls, 3, "deadline 终止后不得继续派发新调用")


if __name__ == "__main__":
    unittest.main()
