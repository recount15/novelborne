# -*- coding: utf-8 -*-
"""测试工作包一键入口：按类别运行 fate-engine 全部本地检验。

用法：
  python tools/playtest_kit/run_tests.py              # 默认：单元回归（快）
  python tools/playtest_kit/run_tests.py all          # 单元回归 + 强化试玩
  python tools/playtest_kit/run_tests.py live         # 真实模型 HTTP 检验（需 API key）
  python tools/playtest_kit/run_tests.py pilot        # 类Agent模式对比实测（需 API key）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def run_unit() -> int:
    """项目全部 unittest 回归（排除需要真实 key 的项）。"""
    print("== [1/2] 单元回归 ==", flush=True)
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def run_strengthened() -> int:
    """强化模式端到端 mock 试玩（FakeClient 注入，不联网）。"""
    print("== [2/2] 强化试玩端到端 ==", flush=True)
    script = ROOT / "tools" / "run_strengthened_playtest.py"
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=str(ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT),
             "PYTHONIOENCODING": "utf-8"},
        text=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        print(proc.stdout[-3000:], proc.stderr[-2000:])
    else:
        print("(ok) 强化试玩通过")
    return proc.returncode


def run_live(rounds: int) -> int:
    """真实模型 HTTP 全功能检验；要求 api_server 已启动且有 FATE_API_KEY。"""
    from tools.playtest_kit import standalone
    argv = ["--rounds", str(rounds)]
    sys.argv = ["standalone"] + argv
    standalone.main()
    return 0


def run_pilot(rounds: int) -> int:
    """类Agent模式开/关对比实测。"""
    from tools.playtest_kit import agent_pilot
    return agent_pilot.main(["--rounds", str(rounds)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fate-engine 测试一键入口")
    parser.add_argument("suite", nargs="?", default="unit",
                        choices=["unit", "all", "live", "pilot"],
                        help="unit=单元回归 / all=unit+强化试玩 / live=真实HTTP / pilot=Agent模式")
    parser.add_argument("--rounds", type=int, default=10,
                        help="live/pilot 的回合数")
    args = parser.parse_args(argv)

    if args.suite == "unit":
        return run_unit()
    if args.suite == "all":
        rc = run_unit()
        rc |= run_strengthened()
        return rc
    if args.suite == "live":
        return run_live(args.rounds)
    if args.suite == "pilot":
        return run_pilot(args.rounds)
    parser.error(f"未知套件 {args.suite}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
