# -*- coding: utf-8 -*-
"""独立命令行版全功能 HTTP 检验（不经 api_server 的 playtest 端点）。

与 pipeline.start_run 相同的 reporter 装配，改为前台运行并在结束时打印
快照。凭据只经环境变量或 CLI 传入，报告落盘前经 reporter.scrub 脱敏。

用法：
  FATE_API_KEY=sk-xxx python -m tools.playtest_kit.standalone --rounds 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from tools.playtest_kit import runner
    from tools.playtest_kit.pipeline import _Reporter

    parser = argparse.ArgumentParser(description="独立命令行真实模型全流程检验")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--provider", default=os.environ.get("FATE_PROVIDER", "deepseek"))
    parser.add_argument("--model", default=os.environ.get("FATE_MODEL", ""))
    parser.add_argument("--base-url", default=os.environ.get("FATE_BASE_URL", ""))
    parser.add_argument("--thinking-mode", default="auto")
    parser.add_argument("--story-richness", type=int, default=700)
    parser.add_argument("--report", default=str(ROOT / "outputs" / "standalone_report.json"))
    args = parser.parse_args()

    api_key = os.environ.get("FATE_API_KEY", "")
    if not api_key:
        parser.error("缺少 FATE_API_KEY 环境变量")
    if not args.model:
        parser.error("缺少 --model 或 FATE_MODEL")

    rep = _Reporter()
    rep.status = "running"
    rep.started_at = time.time()
    rep.config = {"base": os.environ.get("FATE_BASE", "http://127.0.0.1:21560"),
                  "provider": args.provider, "base_url": args.base_url,
                  "model": args.model, "thinking_mode": args.thinking_mode,
                  "rounds": args.rounds, "story_richness": args.story_richness,
                  "api_key_masked": (api_key[:6] + "…") if api_key else ""}
    rep.private_config = {"api_key": api_key}

    try:
        runner.run(rep, lambda: False)
        rep.status = "done"
    except Exception as exc:  # noqa: BLE001
        rep.error = f"{type(exc).__name__}: {exc}"
        rep.status = "error"
    finally:
        rep.ended_at = time.time()
        passed = sum(1 for c in rep.checks if c["ok"])
        failed = sum(1 for c in rep.checks if not c["ok"])
        rep.emit("end", {"status": rep.status, "elapsed_sec": round(rep.ended_at - rep.started_at, 1),
                         "passed": passed, "failed": failed})
        snapshot = rep.scrub(rep.snapshot())
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        print(json.dumps({"status": rep.status, "passed": passed, "failed": failed,
                          "report": args.report}, ensure_ascii=False))
    return 0 if rep.status == "done" and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
