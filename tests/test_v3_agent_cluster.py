"""Standalone stdlib tests: python tests/test_v3_agent_cluster.py (no app imports)."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import unittest

# Direct-load owned modules, bypassing package initializers and pytest conftest.
ROOT = Path(__file__).resolve().parents[1]
for name in ("generation_skills", "agent_cluster_service"):
    qualified = "core.services." + name
    if qualified not in sys.modules:
        spec = importlib.util.spec_from_file_location(qualified, ROOT / "core" / "services" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
gs = sys.modules["core.services.generation_skills"]
cluster = sys.modules["core.services.agent_cluster_service"]


def snapshot_data():
    return {"book_id": "book", "source_hash": "source", "scope": "game", "context_hash": "context", "intent_hash": "intent", "base_revision": 4, "cutoff": 3, "strategy": "agent_cluster", "authorized_evidence": {"e1": {"facts": [{"subject_ids": ["hero"], "predicate": "present", "value": True}], "text": "Hero is here."}, "future-secret": {"text": "SECRET_NOT_AUTHORIZED"}}, "authorized_branch_events": {}, "authorized_directives": {"directive-1": {"origin": "wish", "provenance": "authorized", "fact_norm": "The wall hides a passage.", "scope": "world", "affected": ["wall"], "targets_unresolved": False, "raw_text": "player raw wish", "facts": [{"subject_ids": ["wall"], "predicate": "authorized_directive", "value": "The wall hides a passage."}]}}, "role_projections": {key: {"data": {"public": ["here"]}, "evidence_ids": ["e1"], "branch_event_ids": [], "knowledge_holders": ["hero"], "authorized_directives": ["directive-1"]} for key in gs.DEPENDENCIES}}


def claim():
    return {"claim_id": "c1", "kind": "source_fact", "subject_ids": ["hero"], "predicate": "present", "value": True, "evidence_ids": ["e1"], "branch_event_ids": [], "assumption_ids": [], "valid_boundary": {"scope": "game", "cutoff": 3}, "knowledge_holders": ["hero"], "directive_ids": []}


def output(key):
    schema = gs.default_registry()[key].output_schema
    if schema == "plan":
        return {"summary": "Continue from the player's choice.", "claims": [claim()]}
    if schema == "scene":
        return {"narrative": "The hero pauses at the gate.", "claims": [claim()]}
    if schema == "critic":
        return {"approved": True, "issues": []}
    if schema == "decision":
        return {"decision": "approve", "reasons": ["No unresolved issues."]}
    return {"claims": [claim()], "options": [{"option_id": "action-" + label, "label": label, "text": "Action " + label, "base_revision": 4, "preconditions": [], "effects": [], "knowledge_evidence_ids": ["e1"]} for label in "ABCDEF"]}


def reply(request, artifact=None):
    return gs.ModelReply(output(request.skill_id) if artifact is None else artifact, request.snapshot_hash, "fixed", "test", "1")


def callbacks(override=None):
    result = {key: reply for key in gs.DEPENDENCIES}
    result.update(override or {})
    return result


class ClusterTests(unittest.TestCase):
    def setUp(self):
        self.data = snapshot_data()
        self.snapshot = gs.GenerationSnapshot.freeze(self.data)

    def generate(self, workers=None, **kwargs):
        return cluster.AgentClusterService(workers or callbacks()).generate(self.snapshot, **kwargs)

    def blocked(self, code, workers=None, **kwargs):
        with self.assertRaises(cluster.ClusterError) as caught:
            self.generate(workers, **kwargs)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual("agent_cluster", caught.exception.effective_strategy)
        return caught.exception

    def test_snapshot_immutable_json_hash(self):
        self.data["role_projections"]["story.evidence"]["data"]["public"].append("changed")
        view = self.snapshot.read()
        view["source_hash"] = "changed"
        self.assertEqual("source", self.snapshot.read()["source_hash"])
        self.assertEqual(["here"], self.snapshot.read()["role_projections"]["story.evidence"]["data"]["public"])
        with self.assertRaises(gs.GateError):
            replace(self.snapshot, snapshot_hash="stale").read()

    def test_parallel_barrier_order_context_and_detachment(self):
        barrier = threading.Barrier(3)
        variable = ContextVar("worker-local", default="none")
        variable.set("parent")
        completed = set()
        lock = threading.Lock()
        def worker(req):
            self.assertEqual("parent", variable.get())
            variable.set(req.skill_id)
            self.assertNotIn("SECRET_NOT_AUTHORIZED", req.prompt)
            self.assertNotIn("future-secret", req.context["evidence"])
            with lock:
                self.assertTrue(set(gs.DEPENDENCIES[req.skill_id]) <= completed)
            if req.skill_id in gs.FIRST_WAVE:
                barrier.wait(timeout=2)
                self.assertEqual(req.skill_id, variable.get())
            self.assertEqual(["here"], req.context["data"]["public"])
            req.context["data"]["public"].append("mutated")
            for upstream in req.artifacts.values():
                upstream.clear()
            with lock:
                completed.add(req.skill_id)
            return reply(req)
        result = self.generate({key: worker for key in gs.DEPENDENCIES})
        self.assertEqual(10, len(result.layers))
        self.assertEqual("validated_candidate", result.status)
        self.assertEqual("parent", variable.get())
        self.assertEqual(set(gs.DEPENDENCIES), completed)
        self.assertTrue(all(layer.status == "ok" for layer in result.layers))
        self.assertEqual(["here"], self.data["role_projections"]["story.evidence"]["data"]["public"])
        self.assertIsNone(json.loads(result.layers[0].usage_json)["input_tokens"])
        self.assertFalse(json.loads(result.layers[0].usage_json)["measured"])
        result.options[0]["text"] = "changed"
        self.assertEqual("Action A", result.options[0]["text"])

    def test_completion_order_deterministic(self):
        def run(reverse):
            def worker(req):
                if req.skill_id in gs.FIRST_WAVE:
                    index = gs.FIRST_WAVE.index(req.skill_id)
                    time.sleep((2 - index if reverse else index) * .005)
                return reply(req)
            return self.generate({key: worker for key in gs.DEPENDENCIES})
        a, b = run(False), run(True)
        self.assertEqual(a.candidate_hash, b.candidate_hash)
        self.assertEqual(a.checks_digest, b.checks_digest)
        self.assertEqual(a.options, b.options)

    def test_missing_worker_and_node_no_fallback(self):
        workers = callbacks()
        del workers["story.motivation"]
        self.blocked("missing_worker", workers)
        registry = gs.default_registry()
        del registry["options.critic"]
        with self.assertRaises(cluster.ClusterError) as caught:
            cluster.AgentClusterService(callbacks(), registry=registry).generate(self.snapshot)
        self.assertEqual("missing_or_unknown_node", caught.exception.code)

    def test_explicit_strategy(self):
        self.data["strategy"] = "simple"
        with self.assertRaises(gs.GateError):
            gs.GenerationSnapshot.freeze(self.data)

    def test_atomic_budget_reservations(self):
        ledger = cluster.Budget(cluster.BudgetPolicy(max_calls=2))
        successes = []
        barrier = threading.Barrier(10)
        def reserve():
            barrier.wait()
            try:
                ledger.reserve(100, 100, 1)
                successes.append(True)
            except gs.GateError:
                pass
        threads = [threading.Thread(target=reserve) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(2, len(successes))
        self.assertEqual(2, ledger.calls)
        self.blocked("budget_exhausted", budget=cluster.BudgetPolicy(max_calls=2))

    def test_budget_reconciles_only_reported_usage(self):
        ledger = cluster.Budget(cluster.BudgetPolicy())
        ledger.reserve(100, 200, 1.0)
        ledger.reconcile(200, 50, 1.0, None)
        self.assertEqual(50, ledger.output_bytes)
        self.assertEqual(1.0, ledger.cost)
        ledger.reserve(100, 200, 1.0)
        ledger.reconcile(200, 60, 1.0, .25)
        self.assertEqual(110, ledger.output_bytes)
        self.assertEqual(1.25, ledger.cost)

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        self.blocked("cancelled", cancel=cancel)

    def test_cancel_and_late_result_discard(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        late_done = threading.Event()
        called = []
        def slow(req):
            called.append(req.skill_id)
            if req.skill_id == "story.evidence":
                entered.set()
                release.wait(2)
                late_done.set()
            return reply(req)
        errors = []
        def generate():
            try:
                self.generate({key: slow for key in gs.DEPENDENCIES}, cancel=cancel)
            except cluster.ClusterError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=generate)
        thread.start()
        self.assertTrue(entered.wait(1))
        cancel.set()
        thread.join(1)
        try:
            self.assertFalse(thread.is_alive())
            self.assertEqual(["cancelled"], errors)
            self.assertNotIn("story.plan", called)
        finally:
            release.set()
        self.assertTrue(late_done.wait(1))
        self.assertNotIn("story.plan", called)

    def test_deadline_discards_late_result(self):
        release = threading.Event()
        def slow(req):
            release.wait(1)
            return reply(req)
        try:
            self.blocked("deadline_exceeded", callbacks({"story.evidence": slow}), budget=cluster.BudgetPolicy(deadline_seconds=.03))
        finally:
            release.set()

    def test_job_deadline(self):
        registry = gs.default_registry()
        registry["story.evidence"] = replace(registry["story.evidence"], timeout=.02)
        release = threading.Event()
        def slow(req):
            release.wait(1)
            return reply(req)
        try:
            with self.assertRaises(cluster.ClusterError) as caught:
                cluster.AgentClusterService(callbacks({"story.evidence": slow}), registry=registry).generate(self.snapshot)
            self.assertEqual("job_deadline_exceeded", caught.exception.code)
        finally:
            release.set()

    def test_stale_snapshot_and_reply(self):
        self.blocked("stale_snapshot", current_snapshot_hash=lambda: "changed")
        self.blocked("stale_snapshot", callbacks({"story.evidence": lambda req: replace(reply(req), snapshot_hash="changed")}))

    def test_invalid_critic_blocks_options(self):
        called = []
        def critic(req):
            return reply(req, {"approved": "true", "issues": []})
        def options(req):
            called.append(True)
            return reply(req)
        self.blocked("critic_schema", callbacks({"story.continuity_critic": critic, "options.candidates": options}))
        self.assertEqual([], called)
        self.blocked("critic_blocked", callbacks({"options.critic": lambda req: reply(req, {"approved": True, "issues": [{"issue_id": "i1", "severity": "error", "message": "Unsafe"}]})}))

    def test_six_options_labels_effects_narrative(self):
        for mutation, code in ((lambda a: a["options"].pop(), "options_six"), (lambda a: a["options"][0].update(label="B"), "option_labels"), (lambda a: a["options"][0].update(effects=[{"gold": 3}]), "unsupported_option_effect"), (lambda a: a["options"][0].update(base_revision=5), "option_revision")):
            def worker(req):
                artifact = output(req.skill_id)
                mutation(artifact)
                return reply(req, artifact)
            self.blocked(code, callbacks({"options.candidates": worker}))
        self.blocked("empty_narrative", callbacks({"story.scene_draft": lambda req: reply(req, {"narrative": " ", "claims": []})}))

    def test_fabricated_evidence_and_claims_blocked(self):
        for patch, code in (({"evidence_ids": ["invented"]}, "unauthorized_evidence"), ({"predicate": "owns_all_gold"}, "unsupported_fact"), ({"kind": "arbitrary_patch"}, "claim_kind"), ({"knowledge_holders": ["villain"]}, "unauthorized_knowledge_holder")):
            def worker(req):
                artifact = output(req.skill_id)
                artifact["claims"][0].update(patch)
                req.context["evidence"]["invented"] = {"facts": [artifact["claims"][0]]}
                return reply(req, artifact)
            self.blocked(code, callbacks({"story.evidence": worker}))

    def test_projection_does_not_expand_upstream_authority(self):
        self.data["role_projections"]["story.plan"]["evidence_ids"] = []
        self.snapshot = gs.GenerationSnapshot.freeze(self.data)
        self.blocked("unauthorized_evidence")

    def test_polish_and_arbiter_cannot_bypass_gates(self):
        self.blocked("polish_changed_claims", callbacks({"story.polish": lambda req: reply(req, {"narrative": "Nice prose", "claims": []})}))
        self.blocked("arbiter_blocked", callbacks({"final_commit_arbiter": lambda req: reply(req, {"decision": "reject", "reasons": ["No"]})}))
        self.blocked("artifact_schema", callbacks({"story.plan": lambda req: reply(req, {**output(req.skill_id), "state_patch": {"gold": 5}})}))

    def test_bounded_transient_retries(self):
        attempts = []
        def transient(req):
            attempts.append(req.attempt_no)
            if req.attempt_no == 1:
                raise ConnectionError("secret diagnostic must not escape")
            return reply(req)
        result = self.generate(callbacks({"story.evidence": transient}))
        self.assertEqual([1, 2], attempts)
        self.assertEqual(2, len(json.loads(result.layers[0].attempts_json)))
        self.assertNotIn("secret diagnostic", repr(result.layers))
        def failure(req):
            raise ConnectionError("unavailable")
        error = self.blocked("worker_failed", callbacks({"story.evidence": failure}))
        layer = next(x for x in error.layers if x.skill_id == "story.evidence")
        self.assertEqual(3, len(json.loads(layer.attempts_json)))
        self.assertIsNone(layer.artifact)

    def test_validation_failure_retries_with_feedback(self):
        """校验类 GateError 不再一击即溃：第二次尝试提示词携带上次错误码（反馈修复）。

        真实模型（kimi-k3 实测）会漏 claim 键/多顶层键，同一提示词重试必然
        复现同一偏离——不带反馈的重试等于死锁（开局闸门正确拒绝但永不通过）。
        """
        attempts = []
        prompts = []
        def flaky(req):
            attempts.append(req.attempt_no)
            prompts.append(req.prompt)
            if req.attempt_no == 1:
                return reply(req, {**output(req.skill_id), "state_patch": {"gold": 5}})
            return reply(req)
        result = self.generate(callbacks({"story.evidence": flaky}))
        self.assertEqual([1, 2], attempts)
        self.assertEqual(2, len(json.loads(result.layers[0].attempts_json)))
        self.assertIn("artifact_schema", prompts[1])

    def test_persistent_schema_violation_still_blocked(self):
        """反馈重试不放松门禁：三次都违约仍然 blocked，且不产生 artifact。"""
        def bad(req):
            return reply(req, {**output(req.skill_id), "state_patch": {"gold": 5}})
        error = self.blocked("artifact_schema", callbacks({"story.evidence": bad}))
        layer = next(x for x in error.layers if x.skill_id == "story.evidence")
        self.assertEqual(3, len(json.loads(layer.attempts_json)))
        self.assertIsNone(layer.artifact)


class PipelineClusterTests(unittest.TestCase):
    def test_real_pipeline_flags_execute_cluster_even_free_missing_paper(self):
        from core.services import turn_pipeline
        from unittest.mock import patch
        source = {"book_id": "book", "source_hash": "source", "texts": {1: "Known scene.SECRET_AFTER_CUTOFF"}}
        for flag in ("agent_mode", "story_agent_mode", "generation_strategy"):
            state = {flag: "agent_cluster" if flag == "generation_strategy" else True,
                     "distill_key": "host-book", "knowledge_cutoff": {"chapter_no": 1, "offset": 12},
                     "paper_tier": 999, "anchor_shattered": True, "state_memory": {"revision": 4},
                     "secret": "SECRET_STATE", "history": [{"role": "system", "content": "SECRET_SYSTEM"}]}
            before = gs.clone(state)
            barrier = threading.Barrier(3)
            calls, completed = [], set()
            lock = threading.Lock()
            def model(prompt):
                payload = json.loads(prompt.split("\n", 1)[1])
                key = payload["skill_id"]
                self.assertNotIn("SECRET", prompt)
                self.assertIn("output_contract", payload)
                with lock:
                    self.assertTrue(set(gs.DEPENDENCIES[key]) <= completed)
                    calls.append(key)
                if key in gs.FIRST_WAVE:
                    barrier.wait(2)
                artifact = output(key)
                if "claims" in artifact:
                    artifact["claims"] = []
                if "options" in artifact:
                    for option in artifact["options"]:
                        option["knowledge_evidence_ids"] = []
                with lock:
                    completed.add(key)
                return json.dumps(artifact)
            with patch.object(turn_pipeline.papers, "get_paper", side_effect=AssertionError("agent must bypass paper lookup")):
                result = turn_pipeline.run_turn(state, None, "fixed", model_fn=model,
                    cluster_source_reader=lambda *_: gs.clone(source), message="Wait.",
                    system_prompt="SECRET_SYSTEM", scene_excerpt="SECRET_FUTURE")
            self.assertIsInstance(result, turn_pipeline.TurnResult)
            self.assertEqual(10, len(calls))
            self.assertEqual(list("ABCDEF"), [o["key"] for o in result.options])
            self.assertEqual("agent_cluster", result.agent_meta["effective_strategy"])
            self.assertEqual("validated_candidate", result.agent_meta["status"])
            self.assertEqual(before, state)

    def test_story_agent_rejects_legacy_and_preserves_state(self):
        from core.services import turn_pipeline
        from core.services.story_agent import StoryAgent
        from unittest.mock import patch
        state = {"round": 1}
        with patch.object(turn_pipeline, "run_turn", return_value=turn_pipeline.LEGACY):
            with self.assertRaises(gs.GateError):
                StoryAgent(mode="agent").run_turn(state)
        self.assertEqual({"round": 1}, state)

    def test_persisted_start_params_select_cluster(self):
        self.assertEqual("agent_cluster", gs.selected_strategy({"start_params": {"story_agent_mode": True}}))
        self.assertEqual("simple", gs.selected_strategy({}))

    def test_real_source_reader_uses_temp_book_and_rejects_tampering(self):
        import tempfile
        from core.engine.book_index import build_book_index
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "chapters").mkdir()
            chapter = root / "chapters" / "0001.txt"
            chapter.write_text("Known scene.SECRET_FUTURE", encoding="utf-8")
            (root / "chapter_index.json").write_text(json.dumps({"book_id": "book", "chapters": [{"idx": 1, "title": "One", "start_char": 0}]}), encoding="utf-8")
            build_book_index(root)
            state = {"distill_key": folder, "knowledge_cutoff": {"chapter_no": 1, "offset": 12}, "state_memory": {"revision": 4}}
            snapshot = gs.build_turn_snapshot(state, "Wait")
            self.assertNotIn("SECRET_FUTURE", gs.role_context(snapshot, "story.evidence")["evidence"].values().__repr__())
            chapter.write_text("Tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                gs.build_turn_snapshot(state, "Wait")

    def test_game_character_ids_committed_continuity_and_unknown_source(self):
        from unittest.mock import patch
        from core.services import character_context_service as cc
        source = {"book_id": "book", "source_hash": "hash", "texts": {1: "SECRET_FUTURE"}}
        reader_patch = patch.object(cc, "read_reader_source", side_effect=lambda *_: {**source, "cutoff": {"chapter_no": 1, "offset": 13, "source_hash": "hash"}})
        card_patch = patch.object(cc, "_repository_card", return_value=None)
        reader_patch.start()
        card_patch.start()
        self.addCleanup(reader_patch.stop)
        self.addCleanup(card_patch.stop)
        fact = {"id": "alive1", "key": "alive", "value": True, "provenance": ["event1"], "confidence": 1.0, "knowledge_holder_id": "cid1"}
        state = {"round": 0, "distill_key": "host-book", "state_revision": 2, "branch_id": "branch1",
                 "start_params": {"persona": "Patient"},
                 "active_members": [{"character_id": "cid1", "name": "Shared"}, {"name": "Shared"}],
                 "character_states": {"cid1": {"assertions": [fact]}, "Shared": {"assertions": [{**fact, "value": "SECRET_NAME_JOIN"}]}},
                 "history": [{"role": "assistant", "content": "SECRET_DRAFT"}]}
        snapshot = gs.build_turn_snapshot(state, "Begin", source_reader=lambda *_: source)
        context = gs.role_context(snapshot, "story.continuity")
        character = context["data"]["characters"]["cid1"]
        self.assertEqual("unknown", character["source_status"])
        self.assertEqual(True, character["known_facts"][0]["value"])
        self.assertEqual({"cid1"}, set(context["data"]["characters"]))
        self.assertNotIn("SECRET", gs.canonical(context))
        self.assertTrue(any(row["facts"] and row["facts"][0]["subject_ids"] == ["cid1"] for row in context["branch_events"].values()))
        state.update(round=2, knowledge_cutoff={"chapter_no": 1, "offset": 1}, story_ledger=[
            {"status": "committed", "revision": 2, "branch_id": "branch1", "narrative": "Bridge saved", "public_consequences": ["Route remains open"]},
            {"status": "draft", "revision": 2, "narrative": "SECRET_DRAFT"},
            {"status": "committed", "revision": 2, "branch_id": "other", "narrative": "SECRET_OTHER_BRANCH"}])
        context = gs.role_context(gs.build_turn_snapshot(state, "Continue", source_reader=lambda *_: source), "story.continuity")
        self.assertEqual([{"narrative": "Bridge saved", "revision": 2, "consequences": ["Route remains open"]}], context["data"]["recent_visible_turns"])
        self.assertNotIn("SECRET_DRAFT", gs.canonical(context))
        self.assertNotIn("SECRET_OTHER_BRANCH", gs.canonical(context))
        state["story_ledger"] = [{"committed": True, "turn_id": "turn-1", "round": 1, "narrative": "The gate stayed open"}]
        context = gs.role_context(gs.build_turn_snapshot(state, "Continue", source_reader=lambda *_: source), "story.continuity")
        self.assertEqual("The gate stayed open", context["data"]["recent_visible_turns"][0]["narrative"])
        self.assertIsNone(context["data"]["recent_visible_turns"][0]["revision"])

    def test_game_projection_exact_midchapter_evidence(self):
        from unittest.mock import patch
        from core.services import character_context_service as cc
        source = {"book_id": "book", "source_hash": "hash", "texts": {1: "Hero waits. SECRET_FUTURE"}, "cutoff": {"chapter_no": 1, "offset": 25, "source_hash": "hash"}}
        def evidence(eid, start, end):
            return {"evidence_id": eid, "book_id": "book", "source_hash": "hash", "chapter_no": 1, "start": start, "end": end, "quote": source["texts"][1][start:end]}
        card = {"schema_version": 2, "character_id": "cid1", "revision": 1, "name": "Hero", "source": {"book_id": "book", "source_hash": "hash"}, "quality": {"state": "ready"},
                "evidence": [evidence("before", 0, 11), evidence("after", 12, 25)],
                "facts": [{"fact_id": "f1", "subject_id": "cid1", "knowledge_holder_id": "cid1", "predicate": "action", "value": "Hero waits.", "evidence_ids": ["before"], "status": "confirmed"},
                          {"fact_id": "f2", "subject_id": "cid1", "knowledge_holder_id": "cid1", "predicate": "secret", "value": "SECRET_FUTURE", "evidence_ids": ["after"], "status": "confirmed"}]}
        state = {"branch_id": "branch", "active_members": [{"character_id": "cid1"}]}
        with patch.object(cc, "read_reader_source", side_effect=lambda *_: {**source}), patch.object(cc, "_repository_card", return_value=card):
            result = gs.game_character_projections(state, source, {"chapter_no": 1, "offset": 11}, "host-book", 2)["cid1"]
            self.assertEqual("scoped_reference_only", result["source_status"])
            self.assertEqual(11, result["cutoff"]["offset"])
            self.assertEqual(["f1"], [fact["fact_id"] for fact in result["source_facts"]])
            self.assertNotIn("SECRET_FUTURE", gs.canonical(result))
            zero = gs.game_character_projections(state, source, {"chapter_no": 1, "offset": 0}, "host-book", 2)["cid1"]
            self.assertEqual("unknown", zero["source_status"])
            self.assertEqual([], zero["provenance"])

    def test_zero_cutoff_authored_opening_has_no_source_grant(self):
        source = {"book_id": "book", "source_hash": "hash", "texts": {1: "SECRET_FUTURE"}}
        state = {"round": 0, "distill_key": "host-book", "start_params": {"persona": "Patient observer"}, "history": [{"role": "assistant", "content": "SECRET_UNVERIFIED_IDENTITY"}]}
        snap = gs.build_turn_snapshot(state, "Begin", source_reader=lambda *_: source)
        context = gs.role_context(snap, "story.evidence")
        self.assertEqual({}, context["evidence"])
        self.assertNotIn("SECRET_FUTURE", gs.canonical(context))
        self.assertNotIn("SECRET_UNVERIFIED_IDENTITY", gs.canonical(context))
        self.assertEqual("proposed_invention", context["data"]["authored_opening"]["persona"]["kind"])
        self.assertEqual(0, snap.read()["cutoff"]["offset"])
        state.pop("distill_key")
        with self.assertRaises(gs.GateError) as caught:
            gs.build_turn_snapshot(state, "Begin", source_reader=lambda *_: self.fail("must not resolve a work label"))
        self.assertEqual("preparation_required", caught.exception.code)

    def test_actual_basic_on_start_uses_candidate_without_reparse(self):
        from core import app
        from core.services import turn_pipeline
        from contextlib import ExitStack
        from unittest.mock import patch
        import inspect
        import tempfile
        good = turn_pipeline.TurnResult(narrative="Opening scene", options=[{"key": k, "text": "Action " + k} for k in "ABCDEF"], agent_meta={"status": "validated_candidate", "effective_strategy": "agent_cluster"}, options_source="agent_cluster")
        kwargs = {name: None for name, param in inspect.signature(app.on_start).parameters.items() if param.default is inspect.Parameter.empty and param.kind != inspect.Parameter.VAR_KEYWORD}
        kwargs.update(provider="openai", base_url="https://example.invalid", api_key="test-only", model="fixed", mode="普通", work="Book", role="Observer", persona_preset="Patient", gf="无", story_agent_mode=True, remember=False, difficulty="一般", distill_enabled=False)
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            stack.enter_context(patch.object(app.fe, "WRITABLE_DIR", root))
            stack.enter_context(patch.object(app.fe, "make_client", return_value=object()))
            prestart = stack.enter_context(patch.object(app, "_distill_model", side_effect=AssertionError("no unbudgeted identity call")))
            stack.enter_context(patch.object(app.game_setup, "resolve_work_source", return_value=(None, "", "", "Book")))
            stack.enter_context(patch.object(app, "_new_session_log", return_value=str(Path(root) / "log.txt")))
            stack.enter_context(patch.object(app, "_append_log"))
            stack.enter_context(patch.object(app, "_out_start", side_effect=lambda chat, state, status, **_: {"chat": chat, "state": app.copy.deepcopy(state), "status": status}))
            runner = stack.enter_context(patch.object(turn_pipeline, "run_turn", return_value=good))
            stream = stack.enter_context(patch.object(app.fe, "stream_reply_with_retry", side_effect=AssertionError("no legacy model")))
            parse = stack.enter_context(patch.object(app, "_finalize_options", side_effect=AssertionError("no reparse")))
            save = stack.enter_context(patch.object(app.engine.persistence, "save_state"))
            outputs = list(app.on_start(**kwargs))
            self.assertTrue(runner.called, repr(outputs[-1]))
            self.assertEqual(1, runner.call_count)
            prestart.assert_not_called()
            self.assertTrue(runner.call_args.args[0]["agent_mode"])
            stream.assert_not_called()
            parse.assert_not_called()
            save.assert_called_once()
            self.assertEqual(good.options, outputs[-1]["state"]["options"])
            self.assertEqual("committed", outputs[-1]["state"]["save_stage"])
            runner.side_effect = cluster.ClusterError("preparation_required")
            save.reset_mock()
            outputs = list(app.on_start(**kwargs))
            save.assert_not_called()
            stream.assert_not_called()
            parse.assert_not_called()
            self.assertIn("preparation_required", repr(outputs[-1]))

    def test_pipeline_missing_cutoff_blocks_without_model_or_legacy(self):
        from core.services import turn_pipeline
        with self.assertRaises(cluster.ClusterError) as caught:
            turn_pipeline.run_turn({"agent_mode": True}, None, "fixed", model_fn=lambda _: self.fail("must not call model"))
        self.assertEqual("authorized_cutoff_required", caught.exception.code)

    def test_pipeline_rejects_source_change_after_model(self):
        from core.services import turn_pipeline
        source = {"book_id": "book", "source_hash": "before", "texts": {1: "Known scene."}}
        state = {"agent_mode": True, "distill_key": "host-book", "knowledge_cutoff": {"chapter_no": 1, "offset": 12}, "state_memory": {"revision": 4}}
        def model(prompt):
            key = json.loads(prompt.split("\n", 1)[1])["skill_id"]
            artifact = output(key)
            if "claims" in artifact:
                artifact["claims"] = []
            if "options" in artifact:
                for option in artifact["options"]:
                    option["knowledge_evidence_ids"] = []
            if key == "final_commit_arbiter":
                source["source_hash"] = "after"
            return artifact
        with self.assertRaises(cluster.ClusterError) as caught:
            turn_pipeline.run_turn(state, None, "fixed", model_fn=model, cluster_source_reader=lambda *_: gs.clone(source))
        self.assertEqual("stale_snapshot", caught.exception.code)

    def test_actual_client_adapter_has_fresh_messages_and_caps(self):
        from types import SimpleNamespace
        captured = []
        def create(**kwargs):
            captured.append(kwargs)
            payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            return SimpleNamespace(model="actual-model", choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output(payload["skill_id"]))))], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        snap = gs.GenerationSnapshot.freeze(snapshot_data())
        result = cluster.AgentClusterService(gs.model_callbacks(client, "requested", "openai", request_kwargs={"tools": ["forbidden"], "max_tokens": 999999})).generate(snap)
        self.assertEqual(10, len(captured))
        self.assertEqual(10, len({id(call["messages"]) for call in captured}))
        self.assertTrue(all(call["max_tokens"] == 4096 and "tools" not in call and call["timeout"] > 0 for call in captured))
        self.assertEqual("actual-model", json.loads(result.layers[0].provenance_json)["model_id"])

    def test_adapter_fills_missing_claim_directive_ids(self):
        """实测弱模型（kimi-k3）系统性漏 claim.directive_ids：适配器补保守空
        列表后工件原样过硬闸门；其余键缺失仍由 validate_artifact 拦截。"""
        from types import SimpleNamespace
        seen_claims = []
        def create(**kwargs):
            payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            artifact = output(payload["skill_id"])
            for claim in artifact.get("claims") or []:
                claim.pop("directive_ids")
                seen_claims.append(claim["claim_id"])
            return SimpleNamespace(model="m", choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(artifact)))], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        snap = gs.GenerationSnapshot.freeze(snapshot_data())
        result = cluster.AgentClusterService(gs.model_callbacks(client, "requested", "openai")).generate(snap)
        self.assertTrue(seen_claims)
        for layer in result.layers:
            artifact = json.loads(layer.artifact_json)
            for claim in artifact.get("claims") or []:
                self.assertEqual([], claim["directive_ids"])

    def test_adapter_fills_missing_claim_reference_keys(self):
        """实测分布（kimi-k3 一波 9 样本）：缺失全部是列表引用键——7/9 仅
        directive_ids，2/9 还缺 assumption_ids/branch_event_ids。适配器对全部
        引用键做保守空列表补全（指向空集）；标量键不造，语义门禁原样生效。"""
        from types import SimpleNamespace
        seen_claims = []
        def create(**kwargs):
            payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            artifact = output(payload["skill_id"])
            for claim in artifact.get("claims") or []:
                claim.pop("directive_ids", None)
                claim.pop("assumption_ids", None)
                if seen_claims:  # 第二个 claim 起再缺 branch_event_ids（实测 2/9）
                    claim.pop("branch_event_ids", None)
                seen_claims.append(claim["claim_id"])
            return SimpleNamespace(model="m", choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(artifact)))], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        snap = gs.GenerationSnapshot.freeze(snapshot_data())
        result = cluster.AgentClusterService(gs.model_callbacks(client, "requested", "openai")).generate(snap)
        self.assertTrue(seen_claims)
        for layer in result.layers:
            self.assertEqual("ok", layer.status)
            artifact = json.loads(layer.artifact_json)
            for claim in artifact.get("claims") or []:
                self.assertEqual([], claim["directive_ids"])
                self.assertEqual([], claim["assumption_ids"])
                self.assertEqual([], claim["branch_event_ids"])

    def test_adapter_repairs_stringified_boundary_but_not_wrong_dict(self):
        """真机实测（kimi-k3）：valid_boundary 被序列化成字符串。缺省/非 dict
        时适配器用上下文授权边界补齐（模型本被要求逐字复制）；主动给出错误
        dict 是语义越界，claim_boundary 照常拒绝。"""
        from types import SimpleNamespace
        def create(**kwargs):
            payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            artifact = output(payload["skill_id"])
            for claim in artifact.get("claims") or []:
                claim["valid_boundary"] = "chapter_no:1, offset:1192"
            return SimpleNamespace(model="m", choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(artifact)))], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        snap = gs.GenerationSnapshot.freeze(snapshot_data())
        result = cluster.AgentClusterService(gs.model_callbacks(client, "requested", "openai")).generate(snap)
        for layer in result.layers:
            self.assertEqual("ok", layer.status)
            for claim in json.loads(layer.artifact_json).get("claims") or []:
                self.assertEqual({"scope": "game", "cutoff": 3}, claim["valid_boundary"])
        def wrong(**kwargs):
            payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            artifact = output(payload["skill_id"])
            for claim in artifact.get("claims") or []:
                claim["valid_boundary"] = {"scope": "game", "cutoff": 99}
            return SimpleNamespace(model="m", choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(artifact)))], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=wrong)))
        snap = gs.GenerationSnapshot.freeze(snapshot_data())
        with self.assertRaises(cluster.ClusterError) as caught:
            cluster.AgentClusterService(gs.model_callbacks(client, "requested", "openai")).generate(snap)
        self.assertEqual("claim_boundary", caught.exception.code)

    def test_boundary_equivalence_accepts_flattened_only(self):
        """拍平形式（cutoff 字段一致）视为同一边界；cutoff 不同必须不等价。"""
        authorized = {"scope": "game", "cutoff": {"chapter_no": 1, "offset": 1192}}
        self.assertTrue(gs._boundary_equivalent(authorized, {"chapter_no": 1, "offset": 1192, "source_hash": "x"}))
        self.assertTrue(gs._boundary_equivalent(authorized, {"cutoff": {"chapter_no": 1, "offset": 1192}}))
        self.assertFalse(gs._boundary_equivalent(authorized, {"chapter_no": 2, "offset": 1192}))
        self.assertFalse(gs._boundary_equivalent(authorized, "chapter_no:1, offset:1192"))
        self.assertFalse(gs._boundary_equivalent({"scope": "game", "cutoff": 3}, {"chapter_no": 1, "offset": 1192}))

    def test_adapter_unparseable_body_retries_as_transient(self):
        """实测网关（kimi-k3）会返回 HTTP 200 但 body 非 JSON（顺序调用 1/6，
        高负载近半）：json.loads 失败与传输超时同类，走剩余尝试；探测原文
        不得进任何 layer 记录。"""
        from types import SimpleNamespace
        calls = []
        def create(**kwargs):
            payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            calls.append(payload["skill_id"])
            body = "<!doctype html>upstream overloaded" if len(calls) == 1 else json.dumps(output(payload["skill_id"]))
            return SimpleNamespace(model="m", choices=[SimpleNamespace(message=SimpleNamespace(content=body))], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        snap = gs.GenerationSnapshot.freeze(snapshot_data())
        result = cluster.AgentClusterService(gs.model_callbacks(client, "requested", "openai")).generate(snap)
        self.assertEqual(11, len(calls))
        self.assertNotIn("upstream overloaded", repr(result.layers))
        attempts = json.loads(result.layers[0].attempts_json)
        self.assertEqual(["worker_failed", "ok"], [entry["status"] if entry["status"] == "ok" else entry["error_code"] for entry in attempts])

    def test_adapter_persistent_garbage_still_blocked(self):
        """持续返回非 JSON 体：三次尝试全部 worker_failed 后 blocked，不产出工件。"""
        from types import SimpleNamespace
        def create(**kwargs):
            return SimpleNamespace(model="m", choices=[SimpleNamespace(message=SimpleNamespace(content="not json at all"))], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        snap = gs.GenerationSnapshot.freeze(snapshot_data())
        with self.assertRaises(cluster.ClusterError) as caught:
            cluster.AgentClusterService(gs.model_callbacks(client, "requested", "openai")).generate(snap)
        self.assertEqual("worker_failed", caught.exception.code)
        # 首波并发 + 预算耗尽先后不定：error.layers 携带哪几个技能随调度而变，
        # 断言「出现的层都烧满 3 次尝试且无工件」而非特定技能。
        self.assertTrue(caught.exception.layers)
        for layer in caught.exception.layers:
            attempts = json.loads(layer.attempts_json)
            self.assertEqual(3, len(attempts))
            self.assertTrue(all(entry["error_code"] == "worker_failed" for entry in attempts))
            self.assertIsNone(layer.artifact)


if __name__ == "__main__":
    # Direct standalone mode keeps package-free foundation coverage. Real pipeline
    # tests run under pytest's V00 collection guard, never unsafe app bootstrap.
    unittest.main(verbosity=2, defaultTest="ClusterTests")
