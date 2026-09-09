"""Bounded candidate-only DAG. Host owns authorization, model adapters and commit.

Cancellation discards results; Python cannot kill a running provider callback.
Adapters must honor deadline/cancel and enforce provider output/cost caps. Token
usage remains unknown when not reported; reservations are conservative UTF-8
byte ceilings (not fabricated provider token measurements).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import copy_context
from dataclasses import dataclass
import math
import threading
import time
import uuid
from typing import Callable, Mapping

from core.services.generation_skills import (
    DEPENDENCIES, FIRST_WAVE, SCHEMA_VERSION, GenerationSnapshot, GateError,
    GroundedClaim, LayerResult, ModelReply, SkillSpec, WorkerInput, canonical, clone, digest,
    default_registry, output_contract, require, role_context, text, validate_artifact,
)


@dataclass(frozen=True)
class BudgetPolicy:
    max_calls: int = 20
    max_input_bytes: int = 2_000_000
    max_output_bytes: int = 655_360
    max_cost: float = 20.0
    deadline_seconds: float = 120.0
    max_workers: int = 3
    version: str = "1"


class Budget:
    """One lock and one ledger per generation; unknown usage never refunds caps."""
    def __init__(self, policy: BudgetPolicy):
        require(all(type(x) is int and x > 0 for x in (policy.max_calls, policy.max_input_bytes, policy.max_output_bytes, policy.max_workers)), "budget_policy")
        require(math.isfinite(policy.deadline_seconds) and policy.deadline_seconds > 0 and math.isfinite(policy.max_cost) and policy.max_cost >= 0, "budget_policy")
        self.policy = policy
        self.lock = threading.Lock()
        self.calls = self.input_bytes = self.output_bytes = 0
        self.cost = 0.0

    def reserve(self, input_bytes: int, output_bytes: int, cost: float) -> None:
        with self.lock:
            p = self.policy
            require(self.calls + 1 <= p.max_calls and self.input_bytes + input_bytes <= p.max_input_bytes and self.output_bytes + output_bytes <= p.max_output_bytes and self.cost + cost <= p.max_cost, "budget_exhausted")
            self.calls += 1
            self.input_bytes += input_bytes
            self.output_bytes += output_bytes
            self.cost += cost

    def reconcile(self, reserved_output: int, actual_output: int,
                  reserved_cost: float, actual_cost: float | None) -> None:
        with self.lock:
            self.output_bytes -= reserved_output - actual_output
            if actual_cost is not None:
                self.cost -= reserved_cost - actual_cost


@dataclass(frozen=True)
class ValidatedCandidate:
    generation_id: str
    snapshot_id: str
    snapshot_hash: str
    base_revision: int
    candidate_hash: str
    checks_digest: str
    narrative: str
    options_json: str
    layers: tuple[LayerResult, ...]
    status: str = "validated_candidate"
    requested_strategy: str = "agent_cluster"
    effective_strategy: str = "agent_cluster"

    @property
    def options(self):
        import json
        return json.loads(self.options_json)


class ClusterError(GateError):
    def __init__(self, code: str, layers: tuple[LayerResult, ...] = ()):
        super().__init__(code)
        self.layers = layers
        self.requested_strategy = self.effective_strategy = "agent_cluster"
        self.status = "cancelled" if code == "cancelled" else "blocked"


class AgentClusterService:
    def __init__(self, callbacks: Mapping[str, Callable[[WorkerInput], ModelReply]], *, registry: Mapping[str, SkillSpec] | None = None):
        self.callbacks = dict(callbacks)
        self.registry = dict(default_registry() if registry is None else registry)

    def generate(self, snapshot: GenerationSnapshot, *, budget: BudgetPolicy | None = None,
                 cancel: threading.Event | None = None,
                 current_snapshot_hash: Callable[[], str] | None = None,
                 generation_id: str | None = None) -> ValidatedCandidate:
        """Return a validated proposal or raise ClusterError; never write/commit.

        current_snapshot_hash is a host freshness probe, NOT an atomic commit
        check. Host must recheck authoritative source/revision inside its existing
        transaction. No fallback, real network default, or automatic state patch.
        """
        policy = budget or BudgetPolicy()
        external_cancel = cancel if cancel is not None else threading.Event()
        stopped = threading.Event()
        generation_id = generation_id or uuid.uuid4().hex
        results: dict[str, LayerResult] = {}
        pool = None
        try:
            data = snapshot.read()
            ledger = Budget(policy)
            require(data["strategy"] == "agent_cluster", "unsupported_strategy")
            require(data["budget_policy_version"] == policy.version, "budget_policy_version")
            require(set(self.registry) == set(DEPENDENCIES), "missing_or_unknown_node")
            require(all(callable(self.callbacks.get(key)) for key in DEPENDENCIES), "missing_worker")
            contexts = {}
            for key, deps in DEPENDENCIES.items():
                spec = self.registry[key]
                require(spec.skill_id == key and spec.dependencies == deps and spec.schema_version == SCHEMA_VERSION and spec.skill_version == "1" and spec.input_schema == "WorkerInput/1" and spec.output_schema == default_registry()[key].output_schema, "registry_contract")
                require(spec.permissions == ("read_projection", "propose_artifact") and spec.model_policy == "injected" and data["scope"] in spec.allowed_read_scopes, "skill_permissions")
                require(type(spec.max_attempts) is int and 1 <= spec.max_attempts <= 3 and math.isfinite(spec.timeout) and spec.timeout > 0 and type(spec.max_output_bytes) is int and spec.max_output_bytes > 0 and math.isfinite(spec.max_cost) and spec.max_cost >= 0, "skill_limits")
                contexts[key] = role_context(snapshot, key)
            deadline = time.monotonic() + policy.deadline_seconds

            def check():
                require(not external_cancel.is_set() and not stopped.is_set(), "cancelled")
                require(time.monotonic() < deadline, "deadline_exceeded")
                if current_snapshot_hash is not None:
                    require(current_snapshot_hash() == snapshot.snapshot_hash, "stale_snapshot")

            def run(key, upstream):
                spec = self.registry[key]
                context = contexts[key]
                job_id = generation_id + "/" + key
                job_deadline = min(deadline, time.monotonic() + spec.timeout)
                attempts = []
                usage = {"calls": 0, "input_tokens": None, "output_tokens": None, "cost": None, "measured": False}
                reply = None
                artifact = None
                code = None
                for attempt in range(1, spec.max_attempts + 1):
                    start = time.monotonic()
                    try:
                        check()
                        require(start < job_deadline, "job_deadline_exceeded")
                        # Both the prompt and structured fields use separate detached data.
                        prompt = spec.prompt + "\n" + canonical({"skill_id": key, "snapshot_hash": snapshot.snapshot_hash, "context": context, "artifacts": upstream, "output_schema": spec.output_schema, "output_contract": output_contract(spec.output_schema), "rules": "Output exactly the artifact JSON object, no markdown. Claims may be empty when unknown; never fabricate source facts. Only copy source_fact triples from authorized evidence facts. Preserve branch facts over source expectations. No future secrets. Polish must preserve all draft claims exactly. Critics inspect candidate independently, reject unsupported knowledge or contradictions. Options have no executable effects or preconditions; six distinct proposed actions. No state patches."})
                        ledger.reserve(len(prompt.encode("utf-8")), spec.max_output_bytes, spec.max_cost)
                        usage["calls"] += 1
                        request = WorkerInput(key, generation_id, job_id, attempt, snapshot.snapshot_id, snapshot.snapshot_hash, clone(context), clone(upstream), prompt, job_deadline, lambda: stopped.is_set() or external_cancel.is_set() or time.monotonic() >= job_deadline)
                        reply = self.callbacks[key](request)
                        check()
                        require(time.monotonic() < job_deadline, "job_deadline_exceeded")
                        require(isinstance(reply, ModelReply), "reply_schema")
                        require(reply.snapshot_hash == snapshot.snapshot_hash, "stale_snapshot")
                        require(all(text(x) for x in (reply.model_id, reply.provider, reply.model_version)), "model_provenance")
                        for value in (reply.input_tokens, reply.output_tokens):
                            require(value is None or (type(value) is int and value >= 0), "usage_schema")
                        require(reply.cost is None or (type(reply.cost) in (int, float) and math.isfinite(reply.cost) and 0 <= reply.cost <= spec.max_cost), "cost_limit")
                        require(len(canonical(reply.artifact).encode("utf-8")) <= spec.max_output_bytes, "output_limit")
                        ledger.reconcile(spec.max_output_bytes, len(canonical(reply.artifact).encode("utf-8")), spec.max_cost, reply.cost)
                        # A failed earlier transport may have consumed unknown usage.
                        if usage["calls"] == 1:
                            usage.update(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, cost=reply.cost, measured=reply.input_tokens is not None and reply.output_tokens is not None)
                        artifact = validate_artifact(spec, reply.artifact, context, upstream)
                        code = None
                        attempts.append({"attempt_no": attempt, "status": "ok", "started_at": start, "ended_at": time.monotonic()})
                        break
                    except Exception as exc:
                        code = exc.code if isinstance(exc, GateError) else "worker_failed"
                        artifact = None
                        attempts.append({"attempt_no": attempt, "status": "failed", "error_code": code, "started_at": start, "ended_at": time.monotonic()})
                        # Only explicit transient transport failures are retried.
                        if not isinstance(exc, (TimeoutError, ConnectionError)) or attempt == spec.max_attempts:
                            break
                provenance = {"book_id": data["book_id"], "source_hash": data["source_hash"], "cutoff": data["cutoff"], "context_hash": data["context_hash"], "evidence_ids": sorted(context["evidence"]), "branch_event_ids": sorted(context["branch_events"]), "prompt_manifest": spec.prompt_manifest}
                if isinstance(reply, ModelReply):
                    provenance.update(model_id=reply.model_id, provider=reply.provider, model_version=reply.model_version)
                status = "ok" if artifact is not None else ("cancelled" if code == "cancelled" else "blocked")
                return LayerResult(SCHEMA_VERSION, key, spec.skill_version, generation_id, job_id, snapshot.snapshot_id, snapshot.snapshot_hash, tuple(generation_id + "/" + dep for dep in spec.dependencies), status, canonical(artifact) if artifact is not None else None, canonical(provenance), canonical(attempts), canonical(usage), (code,) if code else ())

            def wave(keys):
                check()
                pending = {}
                launched = time.monotonic()
                for key in keys:
                    upstream = {dep: results[dep].artifact for dep in self.registry[key].dependencies}
                    for artifact in upstream.values():
                        for claim in artifact.get("claims", []):
                            GroundedClaim.validate(claim, contexts[key])
                    # copy_context() must be called afresh for EACH submitted job.
                    future = pool.submit(copy_context().run, run, key, upstream)
                    pending[future] = key
                while pending:
                    check()
                    require(all(time.monotonic() - launched < self.registry[key].timeout for key in pending.values()), "job_deadline_exceeded")
                    done, _ = wait(pending, timeout=0.01, return_when=FIRST_COMPLETED)
                    for future in done:
                        key = pending.pop(future)
                        result = future.result()
                        check()
                        results[key] = result
                        require(result.status == "ok", result.issues[0] if result.issues else "worker_failed")

            check()
            pool = ThreadPoolExecutor(max_workers=min(3, policy.max_workers), thread_name_prefix="story-cluster")
            wave(FIRST_WAVE)
            for key in tuple(DEPENDENCIES)[3:]:
                wave((key,))
            check()
            # Revalidate final objects with authoritative (not worker-mutated) inputs.
            scene = validate_artifact(self.registry["story.polish"], results["story.polish"].artifact, contexts["story.polish"], {"story.scene_draft": results["story.scene_draft"].artifact})
            options = validate_artifact(self.registry["options.candidates"], results["options.candidates"].artifact, contexts["options.candidates"], {})
            candidate_hash = digest({"snapshot_hash": snapshot.snapshot_hash, "scene": scene, "options": options})
            checks_digest = digest({key: results[key].artifact for key in DEPENDENCIES})
            check()
            return ValidatedCandidate(generation_id, snapshot.snapshot_id, snapshot.snapshot_hash, data["base_revision"], candidate_hash, checks_digest, scene["narrative"], canonical(options["options"]), tuple(results[key] for key in DEPENDENCIES))
        except Exception as exc:
            stopped.set()
            code = exc.code if isinstance(exc, GateError) else "cluster_failed"
            raise ClusterError(code, tuple(results[key] for key in DEPENDENCIES if key in results)) from None
        finally:
            stopped.set()
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
