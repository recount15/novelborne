"""Packaging contract tests without importing production core."""
import ast
import importlib.util
from pathlib import Path
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build'))
spec = importlib.util.spec_from_file_location('local_package_release', ROOT / 'build/package_release.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def test_lazy_services_and_isolation_before_collection():
    text = (ROOT / 'build/FateEngine.spec').read_text(encoding='utf-8')
    ast.parse(text)
    assert "'core.services'" in text
    assert text.index("os.environ['FATE_VAR_DIR'] =") < text.index('hiddenimports += collect_submodules')
    assert "_hook_root.relative_to(ROOT.resolve())" in text
    assert "(str(ROOT / 'assets')," not in text


def test_batch_never_kills_or_launches_or_enables_private_override():
    text = (ROOT / 'build/package_release.bat').read_text().lower()
    for forbidden in ('taskkill', 'start /wait', '--test', 'rmdir', 'set fateengine_allow_private_assets'):
        assert forbidden not in text
    assert 'package_release.py' in text


def test_public_packaging_rejects_populated_catalogue():
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        source = root / 'candidate'
        source.mkdir()
        (source / 'FateEngine.exe').write_bytes(b'synthetic executable')
        for relative in packager.PUBLIC_ASSET_FILES:
            asset = root / 'assets' / relative
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text('Synthetic preset work', encoding='utf-8')
        with mock.patch.object(packager, 'ROOT', root):
            with pytest.raises(SystemExit, match='hash mismatch'):
                packager.checked_files(source)


def test_public_packaging_rejects_incomplete_bundle():
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        source = root / 'candidate'
        source.mkdir()
        (source / 'FateEngine.exe').write_bytes(b'synthetic executable')
        for relative in packager.PUBLIC_ASSET_FILES:
            asset = root / 'assets' / relative
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(b'# Work Library\r\n\r\nNo preloaded works.\r\n'
                              if relative == 'rules/work_library.md' else b'x')
        with mock.patch.object(packager, 'ROOT', root):
            with pytest.raises(ValueError, match='inventory differs'):
                packager.checked_files(source)


def test_existing_output_never_overwritten():
    existing = ROOT / 'build/FateEngine.spec'
    before = existing.read_bytes()
    with mock.patch.object(sys, 'argv', ['package_release.py', '--source', str(ROOT / 'dist/FateEngine'), '--output', str(existing)]):
        with pytest.raises(ValueError, match='new .zip'):
            packager.main()
    assert existing.read_bytes() == before


def test_private_inventory_distinguishes_sdk_code_from_user_uploads():
    source = ROOT / 'dist/FateEngine'
    assert not packager.private_path(source / '_internal/openai/resources/uploads/parts.py', source)
    assert packager.private_path(source / 'var/uploads/novel.txt', source)
    assert packager.private_path(source / '_internal/openai/resources/uploads/private.txt', source)
    assert packager.private_path(source / '_internal/old-roles.db', source)
    assert packager.private_path(source / 'backups/save.json', source)
    assert packager.private_path(source / 'evidence/result.json', source)


def test_output_outside_workspace_rejected():
    with mock.patch.object(sys, 'argv', ['package_release.py', '--source', str(ROOT / 'dist/FateEngine'), '--output', str(ROOT.parent / 'forbidden.zip')]):
        with pytest.raises(ValueError):
            packager.main()
