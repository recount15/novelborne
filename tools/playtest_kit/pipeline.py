# -*- coding: utf-8 -*-
"""前端可见的真实检验管线：后台线程跑真实模型全流程，进度落内存池。

用户通过 /playtest-monitor 页面实时观看；事件同时写入 outputs/playtest_live.jsonl 留档。
Key 只经内存传递，不落盘（日志里也用掩码）。
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = ROOT / "outputs"
LIVE_LOG = OUT_DIR / "playtest_live.jsonl"

# 单例运行态：同一时刻只允许一场检验。
_LOCK = threading.Lock()
_RUN: dict[str, Any] | None = None


def current_run() -> dict[str, Any] | None:
    return _RUN


class _Reporter:
    """事件收集器：SSE 队列 + 内存快照 + 文件留档。"""

    def __init__(self) -> None:
        self.queues: list[queue.Queue] = []
        self.checks: list[dict[str, Any]] = []
        self.phases: list[dict[str, Any]] = []
        self.rounds: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.status = "pending"  # pending/running/done/error/stopped
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.error: str | None = None
        self.config: dict[str, Any] = {}
        self.session_id: str | None = None
        self.stop_flag = False

    # ---- 结构化事件 ----
    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        event = {"kind": kind, "t": time.strftime("%H:%M:%S"), **payload}
        LIVE_DIR = OUT_DIR
        LIVE_DIR.mkdir(exist_ok=True)
        with open(LIVE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        for q in list(self.queues):
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def phase(self, name: str, detail: str = "") -> None:
        item = {"name": name, "detail": detail, "t": time.strftime("%H:%M:%S")}
        self.phases.append(item)
        self.emit("phase", item)

    def check(self, step: str, ok: bool, detail: str = "") -> bool:
        item = {"step": step, "ok": bool(ok), "detail": str(detail)[:200], "t": time.strftime("%H:%M:%S")}
        self.checks.append(item)
        self.emit("check", item)
        return bool(ok)

    def note(self, text: str) -> None:
        item = {"text": text, "t": time.strftime("%H:%M:%S")}
        self.notes.append(item)
        self.emit("note", item)

    def round_update(self, data: dict[str, Any]) -> None:
        data["t"] = time.strftime("%H:%M:%S")
        self.rounds.append(data)
        self.emit("round", data)

    def snapshot(self) -> dict[str, Any]:
        passed = sum(1 for c in self.checks if c["ok"])
        return {
            "status": self.status,
            "config": self.config,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "phases": self.phases[-30:],
            "checks_tail": self.checks[-120:],
            "checks_total": len(self.checks),
            "passed_total": passed,
            "failed_total": len(self.checks) - passed,
            "failed_items": [c for c in self.checks if not c["ok"]][-40:],
            "rounds_tail": self.rounds[-12:],
            "rounds_total": len(self.rounds),
            "notes": self.notes[-20:],
        }

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        # 新订阅者先收到当前快照，再收增量。
        q.put({"kind": "snapshot", "data": self.snapshot()})
        self.queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        try:
            self.queues.remove(q)
        except ValueError:
            pass

    def should_stop(self) -> bool:
        return self.stop_flag


def _masked(key: str) -> str:
    return (key[:6] + "***" + key[-4:]) if key and len(key) > 12 else "***"


def start_run(config: dict[str, Any], runner: Callable[[Any, Callable[[], bool]], None],
              force_restart: bool = False) -> tuple[bool, str]:
    """启动一场检验；已有进行中的检验时拒绝重复启动（除非 force）。"""
    global _RUN
    with _LOCK:
        live = _RUN if isinstance(_RUN, dict) else None
        reporter = live.get("reporter") if live else None
        if reporter is not None and getattr(reporter, "status", "") == "running":
            if not force_restart:
                return False, "已有一场检验正在进行，请先在监控页停止或等待完成"
        rep = _Reporter()
        rep.status = "running"
        rep.started_at = time.time()
        rep.config = {k: v for k, v in config.items() if k != "api_key"}
        rep.config["api_key_masked"] = _masked(str(config.get("api_key") or ""))
        if LIVE_LOG.exists():
            LIVE_LOG.write_text("", encoding="utf-8")
        _RUN = {"reporter": rep}

        def worker() -> None:
            try:
                runner(rep, rep.should_stop)
                rep.status = "stopped" if rep.stop_flag and not rep.error else "done"
            except Exception as exc:  # noqa: BLE001
                rep.error = f"{type(exc).__name__}: {exc}"
                rep.status = "error"
                rep.emit("fatal", {"error": rep.error})
                rep.note("检验异常中断：" + traceback.format_exc()[-600:])
            finally:
                rep.ended_at = time.time()
                rep.status = "stopped" if (rep.stop_flag and not rep.error) else rep.status
                rep.emit("end", {"status": rep.status,
                                 "elapsed_sec": round((rep.ended_at or 0) - (rep.started_at or 0), 1),
                                 "passed": sum(1 for c in rep.checks if c['ok']),
                                 "failed": sum(1 for c in rep.checks if not c['ok'])})

        threading.Thread(target=worker, daemon=True, name="fate-playtest").start()
        return True, "检验已启动"


def stop_run() -> bool:
    """请求停止：回合间隙与长超时处都会检查标志位。"""
    live = _RUN if isinstance(_RUN, dict) else None
    rep = live.get("reporter") if live else None
    if rep is None or rep.status != "running":
        return False
    rep.stop_flag = True
    rep.note("已请求停止：本回合完成后终止")
    return True


def wait_for_stop(seconds: float, should_stop: Callable[[], bool]) -> None:
    """分段 sleep 并响应停止标志（监控页停止按钮立即生效）。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if should_stop():
            return
        time.sleep(0.5)


__all__ = ["current_run", "start_run", "stop_run", "wait_for_stop"]
