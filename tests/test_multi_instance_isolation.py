# -*- coding: utf-8 -*-
"""多实例并行隔离回归：operations 日志与 playtest 输出必须跟随 FATE_VAR_DIR。

模块级路径常量在进程内只求值一次，故用子进程按环境变量分别求值验证。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PROBE = (
    "import core.server as s\n"
    "from tools.playtest_kit import pipeline, runner\n"
    "print(s.operation_journal.path)\n"
    "print(pipeline.OUT_DIR)\n"
    "print(runner.REPORT_PATH)\n"
)


class MultiInstanceIsolationTests(unittest.TestCase):
    def _probe_paths(self, var_dir: str | None) -> list[str]:
        env = dict(os.environ)
        env.pop("FATE_VAR_DIR", None)
        if var_dir is not None:
            env["FATE_VAR_DIR"] = var_dir
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE], env=env, cwd=str(ROOT),
            capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def test_paths_follow_fate_var_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal, live, report = self._probe_paths(tmp)
            self.assertTrue(Path(journal).is_relative_to(Path(tmp)),
                            f"operations journal escaped var dir: {journal}")
            self.assertTrue(Path(live).is_relative_to(Path(tmp)),
                            f"playtest live log escaped var dir: {live}")
            self.assertTrue(Path(report).is_relative_to(Path(tmp)),
                            f"playtest report escaped var dir: {report}")

    def test_default_paths_stay_under_repo_var(self):
        journal, live, report = self._probe_paths(None)
        for path in (journal, live, report):
            self.assertTrue(Path(path).is_relative_to(ROOT / "var"),
                            f"default path escaped repo var: {path}")


if __name__ == "__main__":
    unittest.main()
