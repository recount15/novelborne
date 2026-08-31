# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), os.pardir))  # noqa: F821

datas = [(os.path.join(ROOT, 'assets'), 'assets'),
         (os.path.join(ROOT, 'frontend', 'dist'), 'frontend/dist'),
         (os.path.join(ROOT, 'LICENSE'), 'LICENSE')]
binaries = []
hiddenimports = ['core', 'core.server', 'core.app', 'core.fate_engine']
# core.engine 等子包使用 __getattr__ 惰性导入，静态分析追踪不到，
# 必须用 collect_submodules 全量收编，否则运行到对应机制才报缺模块。
for _pkg in ('core.engine', 'core.api', 'core.ui', 'core.memory', 'core.lore', 'core.prompts'):
    hiddenimports += collect_submodules(_pkg)
# FastAPI + Vue production runtime. Gradio is a source-only optional legacy UI;
# do not collect it or its heavy optional ecosystem into Release builds.
for _pkg in ('openai', 'fastapi', 'uvicorn', 'multipart', 'httpx', 'pydantic',
             'qrcode', 'PIL'):
    tmp_ret = collect_all(_pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Explicitly exclude unused optional ML/data stacks that PyInstaller hooks may
# discover from the developer environment. The application imports none of them.
HEAVY_EXCLUDES = [
    'gradio', 'gradio_client', 'safehttpx', 'groovy',
    'torch', 'torchvision', 'torchaudio', 'transformers', 'tensorflow',
    'cv2', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'plotly',
]


a = Analysis(
    [os.path.join(ROOT, 'run_app.py')],
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
    icon=os.path.join(ROOT, 'build', 'icon.ico'),
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
