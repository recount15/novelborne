# -*- coding: utf-8 -*-
"""D12 公开资产门禁：公开基线主键 + 私有覆盖显式开关（可独立干跑）。"""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from unittest import mock

from build import asset_gate

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_asset_tree(tmp: Path, *, work_library: str = "公开基线内容") -> Path:
    """按 PUBLIC_ASSET_FILES 建一棵最小资产树；work_library 内容可注入。"""
    for relative in asset_gate.PUBLIC_ASSET_FILES:
        target = tmp / 'assets' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(work_library if relative == 'rules/work_library.md' else 'x',
                          encoding='utf-8')
    return tmp


class AssetGateTest(unittest.TestCase):
    def test_empty_catalogue_is_pinned_without_private_override(self):
        self.assertEqual(asset_gate.PUBLIC_BASELINE_SHA256['rules/work_library.md'],
                         '921371c3a5eb70235c2e7e11b5616e2f36163bc73c2942b69c8f7ecbb32564b7')
        self.assertNotIn('rules/work_library.md', asset_gate.PRIVATE_LOCAL_SHA256)

    def test_populated_catalogue_fails_with_either_switch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_asset_tree(Path(tmpdir), work_library='Synthetic preset work')
            for allow_private in (False, True):
                with self.subTest(allow_private=allow_private):
                    with self.assertRaises(SystemExit) as ctx:
                        asset_gate.public_asset_datas(root, allow_private=allow_private)
                    self.assertIn('hash mismatch', str(ctx.exception))

    def test_empty_workspace_passes_with_either_switch(self):
        for allow_private in (False, True):
            with self.subTest(allow_private=allow_private):
                datas = asset_gate.public_asset_datas(PROJECT_ROOT, allow_private=allow_private)
                self.assertEqual(len(datas), len(asset_gate.PUBLIC_ASSET_FILES))
                self.assertIn(
                    (str(PROJECT_ROOT / 'assets' / 'rules' / 'work_library.md'), 'assets/rules'),
                    datas,
                )

    def test_env_switch_parser(self):
        self.assertFalse(asset_gate.private_override_enabled({}))
        self.assertFalse(asset_gate.private_override_enabled(
            {asset_gate.PRIVATE_ASSETS_ENV: '0'}))
        self.assertTrue(asset_gate.private_override_enabled(
            {asset_gate.PRIVATE_ASSETS_ENV: '1'}))

    def test_public_mode_passes_public_baseline_content(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_asset_tree(Path(tmpdir), work_library='公开基线内容')
            digest = hashlib.sha256(b'\xe5\x85\xac\xe5\xbc\x80\xe5\x9f\xba\xe7\xba\xbf\xe5\x86\x85\xe5\xae\xb9').hexdigest()
            with mock.patch.object(asset_gate, 'PUBLIC_BASELINE_SHA256',
                                   {'rules/work_library.md': digest}):
                datas = asset_gate.public_asset_datas(root, allow_private=False)
            self.assertEqual(len(datas), len(asset_gate.PUBLIC_ASSET_FILES))

    def test_missing_asset_aborts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_asset_tree(Path(tmpdir))
            (root / 'assets' / 'rules' / 'work_library.md').unlink()
            with self.assertRaises(SystemExit) as ctx:
                asset_gate.public_asset_datas(root, allow_private=False)
            self.assertIn('missing or unsafe', str(ctx.exception))

    def test_tampered_public_asset_aborts_even_with_private_switch(self):
        """开关只覆盖登记过的私有文件；其余文件篡改在私有模式下同样中止。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _make_asset_tree(Path(tmpdir))
            digest = hashlib.sha256(b'ok').hexdigest()
            with mock.patch.object(asset_gate, 'PUBLIC_BASELINE_SHA256',
                                   {'prompts/system_header.md': digest}):
                with self.assertRaises(SystemExit) as ctx:
                    asset_gate.public_asset_datas(root, allow_private=True)
            self.assertIn('hash mismatch', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
