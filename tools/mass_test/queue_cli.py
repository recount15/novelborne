# -*- coding: utf-8 -*-
"""队列消费 CLI（ZCode 内容生产源使用）。

claim：把 pending 任务领到 running 并打印任务列表（供 ZCode 阅读提示词）。
answer：把 ZCode 写好的回答文本落成 done/<id>.json（桥会取走返回给应用）。
stats：打印队列计数。

用法：
  python -m tools.mass_test.queue_cli claim [--limit N] [--tag TAG] [--out FILE]
  python -m tools.mass_test.queue_cli answer --file ANSWERS.json
      ANSWERS.json 形如 [{"id": "...", "content": "..."}, ...]
  python -m tools.mass_test.queue_cli stats
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 与 bridge_server 同一 env 约定：FATE_MASS_QUEUE_DIR 指向本轮独立队列。
QUEUE_ROOT = Path(os.getenv(
    "FATE_MASS_QUEUE_DIR",
    str(Path(__file__).resolve().parents[2] / "var" / "mass_test" / "bridge_queue")))
PENDING = QUEUE_ROOT / "pending"
RUNNING = QUEUE_ROOT / "running"
DONE = QUEUE_ROOT / "done"
for directory in (PENDING, RUNNING, DONE):
    directory.mkdir(parents=True, exist_ok=True)


def cmd_claim(limit: int, tag: str | None, out: str | None) -> None:
    tasks = []
    for path in sorted(PENDING.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if len(tasks) >= limit:
            break
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if tag and task.get("tag") != tag:
            continue
        path.rename(RUNNING / path.name)
        task["status"] = "running"
        tasks.append(task)
    payload = json.dumps(tasks, ensure_ascii=False, indent=1)
    if out:
        Path(out).write_text(payload, encoding="utf-8")
        print(f"claimed {len(tasks)} tasks -> {out}")
    else:
        print(payload)


def cmd_answer(path: str) -> None:
    answers = json.loads(Path(path).read_text(encoding="utf-8"))
    answered = 0
    for item in answers:
        task_id = str(item.get("id") or "")
        content = str(item.get("content") or "")
        source = RUNNING / f"{task_id}.json"
        if not source.is_file():
            print(f"skip {task_id}: not in running/")
            continue
        (DONE / f"{task_id}.json").write_text(
            json.dumps({"id": task_id, "content": content, "by": "zcode",
                        "answered_ts": time.time()}, ensure_ascii=False),
            encoding="utf-8")
        source.unlink()
        answered += 1
    print(f"answered {answered}/{len(answers)}")


def cmd_stats() -> None:
    def count(directory: Path) -> int:
        return sum(1 for _ in directory.glob("*.json"))
    print(json.dumps({"pending": count(PENDING), "running": count(RUNNING),
                      "done": count(DONE)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    claim = sub.add_parser("claim")
    claim.add_argument("--limit", type=int, default=8)
    claim.add_argument("--tag", default=None)
    claim.add_argument("--out", default=None)
    answer = sub.add_parser("answer")
    answer.add_argument("--file", required=True)
    sub.add_parser("stats")
    args = parser.parse_args()
    if args.cmd == "claim":
        cmd_claim(args.limit, args.tag, args.out)
    elif args.cmd == "answer":
        cmd_answer(args.file)
    elif args.cmd == "stats":
        cmd_stats()


if __name__ == "__main__":
    sys.exit(main())
