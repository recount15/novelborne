# -*- coding: utf-8 -*-
"""网页版启动器回归：打包版不得写入 _internal/var。"""
from __future__ import annotations
import unittest
from pathlib import Path
from unittest import mock
import run_app

class LauncherVarDirTest(unittest.TestCase):
    def test_source_defaults_to_project_var(self):
        self.assertEqual(run_app._default_var_dir(), Path(run_app.__file__).resolve().parent / "var")

    def test_frozen_defaults_to_exe_sibling_var(self):
        executable = Path(r"C:\Program Files\Novelborne\FateEngine.exe")
        with mock.patch.object(run_app.sys, "frozen", True, create=True), mock.patch.object(run_app.sys, "executable", str(executable)):
            self.assertEqual(run_app._default_var_dir(), executable.parent / "var")

if __name__ == "__main__":
    unittest.main()
