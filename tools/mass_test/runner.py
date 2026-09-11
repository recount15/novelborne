# -*- coding: utf-8 -*-
"""测试运行入口 CLI。

  python -m tools.mass_test.runner smoke            # 单实例全流程冒烟
  python -m tools.mass_test.runner phase2           # 12 实例并行矩阵

约定：
- 每次运行使用 var/mass_test/run_<ts>/，实例 var、队列、台账全部随 run 隔离；
- 实例由本进程拉起，进程登记（PID/创建时间/argv）落 processes.jsonl，退出回收；
- 内容生产由 ZCode 经 queue_cli 消费桥队列完成（队列目录随 run 隔离，
  见 FATE_MASS_QUEUE_DIR）；本脚本只负责编排；
- 退出码综合 lane 断言失败与台账 P0/P1 数：任一非零即失败。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .client import MassClient
from .instance import TestInstance
from .live_log import open_loggers
from .problems import ProblemLedger
from .throttle import GlobalThrottle

ROOT = Path(__file__).resolve().parents[2]

# 12 实例并行矩阵的固定 lane 顺序（runbook F 阶段 L01–L12）。
PHASE2_LANES = ["L01", "L02", "L03", "L04", "L05", "L06",
                "L07", "L08", "L09", "L10", "L11", "L12"]


def _run_dir(prefix: str) -> Path:
    run_dir = ROOT / "var" / "mass_test" / f"run_{prefix}_" + time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


class ProcessRegistry:
    """进程登记簿：启动/停止逐条落盘，供审计与安全停止核对。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, action: str, instance: TestInstance) -> None:
        entry: dict[str, Any] = {
            "action": action, "name": instance.name, "port": instance.port,
            "var": str(instance.var_dir), "ts": round(time.time(), 3),
        }
        if instance.process is not None:
            entry.update({
                "pid": instance.process.pid,
                "argv": instance.process.args if isinstance(instance.process.args, list)
                        else [instance.process.args],
                "create_time": instance.started_at[2] if instance.started_at else None,
            })
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _make_client(instance: TestInstance, run_dir: Path, lane: str,
                 throttle: GlobalThrottle) -> tuple[MassClient, ProblemLedger]:
    loggers = open_loggers(instance.var_dir)
    ledger = ProblemLedger(run_dir / f"problems_{lane}.jsonl")
    return MassClient(instance.base, loggers, ledger, throttle, lane=lane), ledger


def _corpus_ctx(inst_var: str = "") -> dict[str, Any]:
    corpus = Path(os.getenv("FATE_MASS_CORPUS_DIR",
                            str(ROOT / "var" / "mass_test" / "corpus")))
    meta_path = corpus / "baseline.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    return {"full_txt": str(corpus / "full.txt"),
            "excerpt_txt": str(corpus / "excerpt40.txt"),
            "excerpt12_txt": str(corpus / "excerpt12.txt"),
            "xiyou_txt": str(corpus / "xiyou.txt"),
            "baseline_full_chapters": int(meta.get("full_chapters", 0)),
            "app_full_chapters": int(meta.get("app_full_chapters", 0)),
            "app_full_first_last": list(meta.get("app_full_first_last") or []),
            "app_excerpt40_chapters": int(meta.get("app_excerpt40_chapters", 0)),
            "app_excerpt40_first_last": list(meta.get("app_excerpt40_first_last") or []),
            "inst_var": inst_var}


def _verdict(lane_summaries: list[dict[str, Any]],
             ledgers: list[ProblemLedger]) -> tuple[int, dict[str, Any]]:
    """退出码裁决：lane 断言失败 + 台账 P0/P1，任一非零即失败。"""
    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for ledger in ledgers:
        for level, n in ledger.counts_by_severity().items():
            counts[level] += n
    failed_checks = sum(s.get("failed", 0) for s in lane_summaries)
    lane_errors = sum(1 for s in lane_summaries if s.get("error"))
    code = 0 if (failed_checks == 0 and counts["P0"] == 0
                 and counts["P1"] == 0 and lane_errors == 0) else 1
    return code, {"failed_checks": failed_checks, "lane_errors": lane_errors,
                  "ledger": counts}


def cmd_smoke(args: argparse.Namespace) -> int:
    from . import lanes
    run_dir = _run_dir("smoke")
    print(f"[runner] run dir: {run_dir}")
    instance = TestInstance(args.port, run_dir / "instances" / "smoke", name="smoke")
    registry = ProcessRegistry(run_dir / "processes.jsonl")
    throttle = GlobalThrottle(max_concurrency=args.concurrency)
    ctx = _corpus_ctx(str(instance.var_dir))
    summaries: list[dict[str, Any]] = []
    ledgers: list[ProblemLedger] = []
    started = time.monotonic()
    try:
        instance.start()
        registry.record("start", instance)
        print(f"[runner] instance up: {instance.base}")
        client, ledger = _make_client(instance, run_dir, "smoke", throttle)
        ledgers.append(ledger)
        stage1 = lanes.smoke_lane(client, ledger, ctx)
        print(f"[runner] stage1: {json.dumps(stage1, ensure_ascii=False)}")
        summaries.append(stage1)
        stage2 = lanes.smoke_loop_lane(client, ledger, ctx)
        print(f"[runner] stage2: {json.dumps(stage2, ensure_ascii=False)}")
        summaries.append(stage2)
    except BaseException:
        summaries.append({"lane": "smoke", "error": traceback.format_exc(limit=6)})
        raise
    finally:
        (run_dir / "smoke_summary.json").write_text(json.dumps(
            {"stage1": summaries[0] if summaries else {},
             "stage2": summaries[1] if len(summaries) > 1 else {},
             "ctx": {k: v for k, v in ctx.items() if not k.endswith("_txt")}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        if instance.process is not None:
            registry.record("stop", instance)
        instance.stop()
    code, verdict = _verdict(summaries, ledgers)
    print(f"[runner] smoke done in {round(time.monotonic() - started)}s; "
          f"verdict={json.dumps(verdict, ensure_ascii=False)}")
    return code


def _safe_stop(instance: Any, registry: ProcessRegistry) -> None:
    """幂等回收：同实例只记录/执行一次 stop，防 lane 与兜底双重回收。"""
    if getattr(instance, "_matrix_stopped", False):
        return
    instance._matrix_stopped = True
    if getattr(instance, "process", None) is not None:
        registry.record("stop", instance)
    instance.stop()


# ------------------------------------------------------------- phase2 编排

def run_matrix(lane_specs: list[tuple[str, int, Callable[..., dict[str, Any]]]],
               run_dir: Path, *, concurrency: int = 4, start_wait: int = 120,
               instance_factory: Callable[[str, int, Path], Any] | None = None,
               ctx_factory: Callable[[str], dict[str, Any]] | None = None
               ) -> tuple[int, dict[str, Any]]:
    """并行执行 lane 矩阵（lane/实例/ctx 均可注入，便于编排自测）。

    lane_specs: [(lane_id, port, lane_func)，func 签名 (client, ledger, ctx)->summary]
    隔离：每 lane 独立实例 var（run_dir/instances/<lane>）。
    任何 lane 抛异常：记录 error summary，不阻断其他 lane；finally 全部回收。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    registry = ProcessRegistry(run_dir / "processes.jsonl")
    throttle = GlobalThrottle(max_concurrency=concurrency)
    make_ctx = ctx_factory or _corpus_ctx
    factory = instance_factory or (lambda lane_id, port, var:
                                   TestInstance(port, var, name=lane_id))
    instances = {lane_id: factory(lane_id, port, run_dir / "instances" / lane_id)
                 for lane_id, port, _ in lane_specs}
    # 开线程前串行预建目录：Windows 并发 mkdir 共享父目录存在竞态。
    for instance in instances.values():
        try:
            Path(instance.var_dir).mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            pass
    summaries: list[dict[str, Any]] = []
    ledgers: list[ProblemLedger] = []
    started = time.monotonic()

    def _execute(lane_id: str, port: int, func) -> dict[str, Any]:
        instance = instances[lane_id]
        try:
            instance.start(wait_seconds=start_wait)
            registry.record("start", instance)
            Path(instance.var_dir).mkdir(parents=True, exist_ok=True)
            client, ledger = _make_client(instance, run_dir, lane_id, throttle)
            ledgers.append(ledger)
            ctx = make_ctx(str(instance.var_dir))
            summary = func(client, ledger, ctx)
            summary["lane"] = lane_id  # lane 名以矩阵为准，防 lane 自报错名
            return summary
        except BaseException:  # noqa: BLE001  单 lane 失败不拖垮矩阵
            return {"lane": lane_id, "error": traceback.format_exc(limit=6)}
        finally:
            _safe_stop(instance, registry)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(lane_specs)) as pool:
            futures = [pool.submit(_execute, lane_id, port, func)
                       for lane_id, port, func in lane_specs]
            for future in concurrent.futures.as_completed(futures):
                summaries.append(future.result())
    finally:
        for instance in instances.values():
            _safe_stop(instance, registry)  # 兜底：lane 线程未跑到的实例也要回收
        code, verdict = _verdict(summaries, ledgers)
        payload = {"lanes": summaries, "verdict": verdict,
                   "elapsed": round(time.monotonic() - started)}
        (run_dir / "phase2_summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return code, {"lanes": summaries, "verdict": verdict}


def cmd_phase2(args: argparse.Namespace) -> int:
    from .phase2_lanes import LANES
    count = max(1, min(len(PHASE2_LANES), args.instances))
    lane_ids = PHASE2_LANES[:count]
    run_dir = _run_dir("phase2")
    print(f"[runner] phase2 run dir: {run_dir}; lanes={lane_ids}")
    missing = [lane for lane in lane_ids if lane not in LANES]
    if missing:
        print(f"[runner] lane 未实现: {missing}")
        return 2
    specs = [(lane, args.port_base + index, LANES[lane])
             for index, lane in enumerate(lane_ids)]
    code, payload = run_matrix(specs, run_dir / "matrix", concurrency=args.concurrency)
    print(f"[runner] phase2 done: verdict={json.dumps(payload['verdict'], ensure_ascii=False)}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--port", type=int, default=21561)
    smoke.add_argument("--concurrency", type=int, default=4)
    phase2 = sub.add_parser("phase2")
    phase2.add_argument("--instances", type=int, default=12)
    phase2.add_argument("--port-base", type=int, default=21565)
    phase2.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if args.cmd == "smoke":
        return cmd_smoke(args)
    return cmd_phase2(args)


if __name__ == "__main__":
    sys.exit(main())
