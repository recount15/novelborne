# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os
from pathlib import Path
import tempfile
from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), os.pardir)))  # noqa: F821

# 公开资产门禁（D12）：单一事实来源在 build/asset_gate.py（可独立干跑测试）。
# 公开基线哈希永远生效；私有本地哈希仅在环境变量
# FATEENGINE_ALLOW_PRIVATE_ASSETS=1 显式开启时覆盖（公开打包脚本不得设置）。
_gate_spec = importlib.util.spec_from_file_location(
    'asset_gate', str(ROOT / 'build' / 'asset_gate.py'))
_gate = importlib.util.module_from_spec(_gate_spec)
_gate_spec.loader.exec_module(_gate)

_FRONTEND_SUFFIXES = {
    '.css', '.html', '.ico', '.jpeg', '.jpg', '.js', '.json', '.png',
    '.svg', '.ttf', '.webp', '.woff', '.woff2',
}


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


datas = _gate.public_asset_datas(
    ROOT, allow_private=_gate.private_override_enabled()) \
    + _frontend_datas() + [(str(ROOT / 'LICENSE'), 'LICENSE')] \
    + [(str(ROOT / 'docs' / 'USER_MANUAL.md'), 'docs')]
binaries = []
hiddenimports = ['core', 'core.server', 'core.app', 'core.fate_engine']
# Hooks may import core in isolated subprocesses. Never inherit the owner's DB.
# Keep this build-only runtime outside datas; the launcher selects its own at runtime.
_hook_root = Path(CONF['workpath']).resolve() / 'hook-runtimes'
_hook_root.relative_to(ROOT.resolve())  # Packaging side effects must stay in workspace.
_hook_root.mkdir(parents=True, exist_ok=True)
os.environ['FATE_VAR_DIR'] = tempfile.mkdtemp(prefix='empty-', dir=str(_hook_root))
# core.engine and related packages use lazy imports that static analysis misses.
for _pkg in ('core.engine', 'core.api', 'core.ui', 'core.memory', 'core.lore', 'core.prompts',
             'core.services'):
    hiddenimports += collect_submodules(_pkg)
# FastAPI + Vue production runtime. Gradio remains source-only.
for _pkg in ('openai', 'fastapi', 'uvicorn', 'multipart', 'httpx', 'pydantic',
             'qrcode', 'PIL'):
    tmp_ret = collect_all(_pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

HEAVY_EXCLUDES = [
    'gradio', 'gradio_client', 'safehttpx', 'groovy',
    'torch', 'torchvision', 'torchaudio', 'transformers', 'tensorflow',
    'cv2', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'plotly',
]


a = Analysis(
    [str(ROOT / 'run_app.py')],
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
    name='FateEngine',
    icon=str(ROOT / 'build' / 'icon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FateEngine',
)
