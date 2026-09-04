# -*- mode: python ; coding: utf-8 -*-
"""Windowed PyInstaller recipe for FateEngineWindowed.exe."""
import hashlib
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), os.pardir)))  # noqa: F821

_DATA_FILES = (
    'layered_corpus.json',
    'skills_catalog.json',
    'tropes_biz.json',
    'tropes_combat.json',
    'tropes_life.json',
    'tropes_manifest.json',
    'tropes_mystery.json',
    'tropes_romance.json',
)
_PROMPT_FILES = (
    'agent_revise.md', 'agent_self_check.md', 'answer_polish.md',
    'character_patch.md', 'directives_register.md', 'eval_archive.md',
    'nemesis_block.md', 'opening_anchor_merge.md', 'opening_anchor_verify.md',
    'opening_archive.md', 'opening_characters.md', 'opening_check.md',
    'opening_nemesis_note.md', 'opening_plot_merge.md', 'opening_plot_sample.md',
    'opening_settings.md', 'option_repair.md', 'options_gen.md', 'pacing_hint.md',
    'paper_director.md', 'paper_polish.md', 'paper_segment.md', 'quality_judge.md',
    'quality_rewrite.md', 'rounds_rule.md', 'rounds_rule_enhanced.md',
    'rounds_rule_fragment.md', 'segment_refill.md', 'structured_question.md',
    'system_header.md', 'uploaded_work.md', 'work_archive_distill.md',
)
_RULE_FILES = (
    'enhanced.md', 'golden_finger.md', 'runtime.md', 'state_memory.md',
    'work_library.md', 'worldbook.md',
)
_PAPER_FILES = tuple(
    f'{size}_l{level}_{stage}.json'
    for size, levels in (('small', range(1, 4)), ('large', range(4, 7)))
    for level in levels
    for stage in ('setup', 'climax', 'free')
)
_PUBLIC_ASSET_FILES = (
    *(f'data/{name}' for name in _DATA_FILES),
    'lore/default_worldbook.json',
    *(f'papers/{name}' for name in _PAPER_FILES),
    *(f'prompts/{name}' for name in _PROMPT_FILES),
    *(f'rules/{name}' for name in _RULE_FILES),
)
_APPROVED_SHA256 = {
    'rules/work_library.md': 'f4961e8b6669488c599cfec62bc0a64f7421dba59e5c983bd037a4712467f308',
}
_FRONTEND_SUFFIXES = {
    '.css', '.html', '.ico', '.jpeg', '.jpg', '.js', '.json', '.png',
    '.svg', '.ttf', '.webp', '.woff', '.woff2',
}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _public_asset_datas():
    result = []
    asset_root = ROOT / 'assets'
    for relative_name in _PUBLIC_ASSET_FILES:
        source = asset_root / relative_name
        if not source.is_file() or source.is_symlink():
            raise SystemExit(f'Required public asset is missing or unsafe: assets/{relative_name}')
        expected = _APPROVED_SHA256.get(relative_name)
        if expected and _sha256(source) != expected:
            raise SystemExit(
                f'Public asset hash mismatch: assets/{relative_name}. '
                'Restore or explicitly review and update the approved hash before release.'
            )
        target = (Path('assets') / Path(relative_name).parent).as_posix()
        result.append((str(source), target))
    return result


def _frontend_datas():
    dist_root = ROOT / 'frontend' / 'dist'
    index = dist_root / 'index.html'
    if not index.is_file():
        raise SystemExit('frontend/dist/index.html is missing; run a clean Vite build first')
    result = []
    for source in sorted(dist_root.rglob('*')):
        if source.is_symlink():
            raise SystemExit(f'Symlink is not allowed in frontend output: {source.relative_to(ROOT)}')
        if not source.is_file():
            continue
        if source.suffix.lower() not in _FRONTEND_SUFFIXES:
            raise SystemExit(f'Unexpected frontend output type: {source.relative_to(ROOT)}')
        target = (Path('frontend/dist') / source.relative_to(dist_root).parent).as_posix()
        result.append((str(source), target))
    return result


datas = _public_asset_datas() + _frontend_datas() + [(str(ROOT / 'LICENSE'), 'LICENSE')]
binaries = []
hiddenimports = [
    'core', 'core.server', 'core.app', 'core.fate_engine',
    'tools.private_recovery', 'webview', 'clr_loader', 'pythonnet',
]
for _pkg in ('core.engine', 'core.api', 'core.ui', 'core.memory', 'core.lore', 'core.prompts'):
    hiddenimports += collect_submodules(_pkg)
for _pkg in ('openai', 'fastapi', 'uvicorn', 'multipart',
             'httpx', 'pydantic', 'qrcode', 'PIL'):
    tmp = collect_all(_pkg)
    datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]
HEAVY_EXCLUDES = [
    'gradio', 'gradio_client', 'safehttpx', 'groovy',
    'torch', 'torchvision', 'torchaudio', 'transformers', 'tensorflow',
    'cv2', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'plotly',
]
# pywebview packages vary between module and package layouts by platform.
for _pkg in ('pywebview', 'pythonnet', 'clr_loader'):
    try:
        tmp = collect_all(_pkg)
        datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]
    except Exception:
        try:
            hiddenimports += collect_submodules(_pkg)
        except Exception:
            pass

a = Analysis(
    [str(ROOT / 'run_windowed.py')],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=HEAVY_EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FateEngineWindowed',
    icon=str(ROOT / 'build' / 'icon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_authority=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FateEngineWindowed',
)
