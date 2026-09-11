# -*- coding: utf-8 -*-
"""B05 编排自测：注入假 lane/假实例，验证矩阵调度、隔离、失败聚合与回收。

不启动真实服务；TestInstance 的安全语义（端口占用拒绝/身份核验）另测。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.mass_test.problems import ProblemLedger  # noqa: E402
from tools.mass_test.runner import ProcessRegistry, run_matrix  # noqa: E402


class FakeInstance:
    """记录生命周期调用的假实例；process=None 使 registry 跳过 PID 记录。"""

    def __init__(self, lane_id: str, port: int, var: Path):
        self.name = lane_id
        self.port = port
        self.var_dir = var
        self.base = f"http://127.0.0.1:{port}"
        self.process = None
        self.events: list[str] = []
        self.start_sleep = 0.0

    def start(self, wait_seconds: int = 120) -> None:
        if self.start_sleep:
            threading.Event().wait(self.start_sleep)
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


def _factory(instances: dict[str, FakeInstance]):
    def make(lane_id: str, port: int, var: Path) -> FakeInstance:
        inst = FakeInstance(lane_id, port, var)
        instances[lane_id] = inst
        return inst
    return make


def _lane_ok(client, ledger, ctx):
    ledger.report("P3", "normal note")  # P3 不影响门禁
    return {"lane": "x", "passed": 2, "failed": 0, "skipped": 0}


def _lane_fail(client, ledger, ctx):
    ledger.report("P1", "inject failure")
    return {"lane": "x", "passed": 0, "failed": 3, "skipped": 0}


def _lane_boom(client, ledger, ctx):
    raise RuntimeError("lane exploded")


def test_matrix_parallel_isolation_and_cleanup(tmp_path):
    instances: dict[str, FakeInstance] = {}
    started = threading.Event()

    def slow_lane(client, ledger, ctx):
        started.set()
        import time
        time.sleep(0.2)  # 让两条 lane 重叠，验证并行而非串行
        return {"lane": "x", "passed": 1, "failed": 0, "skipped": 0}

    specs = [("A", 21651, slow_lane), ("B", 21652, slow_lane)]
    code, payload = run_matrix(specs, tmp_path / "matrix", concurrency=2,
                               instance_factory=_factory(instances),
                               ctx_factory=lambda var: {})
    assert code == 0
    assert started.is_set()
    assert set(instances) == {"A", "B"}
    # 隔离：每 lane 独立 var 目录
    assert (tmp_path / "matrix" / "instances" / "A").is_dir()
    assert (tmp_path / "matrix" / "instances" / "B").is_dir()
    # 回收：异常与否，start/stop 成对
    for inst in instances.values():
        assert inst.events.count("start") == 1
        assert inst.events.count("stop") == 1
    summary = json.loads((tmp_path / "matrix" / "phase2_summary.json").read_text("utf-8"))
    assert summary["verdict"]["failed_checks"] == 0


def test_matrix_aggregates_failures_and_boom(tmp_path):
    instances: dict[str, FakeInstance] = {}
    specs = [("A", 21651, _lane_ok), ("B", 21652, _lane_fail),
             ("C", 21653, _lane_boom), ("D", 21654, _lane_ok)]
    code, payload = run_matrix(specs, tmp_path / "matrix", concurrency=4,
                               instance_factory=_factory(instances),
                               ctx_factory=lambda var: {})
    assert code == 1  # P1 台账 + failed checks + lane error
    verdict = payload["verdict"]
    assert verdict["failed_checks"] == 3
    assert verdict["lane_errors"] == 1
    assert verdict["ledger"]["P1"] == 1
    lanes = {item["lane"]: item for item in payload["lanes"]}
    assert lanes["B"]["failed"] == 3
    assert "lane exploded" in lanes["C"]["error"]
    # 异常 lane 也被回收
    assert instances["C"].events == ["start", "stop"]
    # 成功 lane 不被拖累
    assert lanes["A"]["passed"] == 2 and lanes["D"]["passed"] == 2


def test_process_registry_records_entries(tmp_path):
    registry = ProcessRegistry(tmp_path / "processes.jsonl")
    inst = FakeInstance("L01", 21655, tmp_path / "v")
    registry.record("start", inst)
    registry.record("stop", inst)
    lines = (tmp_path / "processes.jsonl").read_text("utf-8").strip().splitlines()
    entries = [json.loads(line) for line in lines]
    assert [e["action"] for e in entries] == ["start", "stop"]
    assert entries[0]["name"] == "L01" and entries[0]["port"] == 21655
    assert "pid" not in entries[0]  # 假实例无 process，不写 PID


def test_ledger_severity_counts(tmp_path):
    ledger = ProblemLedger(tmp_path / "p.jsonl")
    ledger.report("P0", "x")
    ledger.report("P2", "y")
    ledger.report("P2", "z")
    counts = ledger.counts_by_severity()
    assert counts == {"P0": 1, "P1": 0, "P2": 2, "P3": 0}
