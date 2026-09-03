# -*- mode: python ; coding: utf-8 -*-
"""窗口版（无边框桌面应用）打包 spec：FateEngineWindowed.exe。

与 FateEngine.spec（Web 版）的差异：
- 入口 run_windowed.py（pywebview 无边框窗口 + 0.0.0.0 后端线程）；
- console=False：双击无黑框，纯窗口应用；
- 收编 pywebview/pythonnet/clr_loader（WebView2 壳运行时）；
- Gradio legacy UI is source-only and excluded from production bundles.

产物：dist\FateEngineWindowed\FateEngineWindowed.exe
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# spec 位于 build/ 下，数据与入口都锚定项目根（SPECPATH 的上一级）。
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), os.pardir))  # noqa: F821

datas = [(os.path.join(ROOT, 'assets'), 'assets'),
         (os.path.join(ROOT, 'frontend', 'dist'), 'frontend/dist'),
         (os.path.join(ROOT, 'frontend', 'public', 'favicon.ico'), '.'),
         (os.path.join(ROOT, 'LICENSE'), 'LICENSE')]
binaries = []
hiddenimports = ['core', 'core.server', 'core.app', 'core.fate_engine',
                 'tools.private_recovery', 'webview', 'clr_loader', 'pythonnet']
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
# pywebview 壳三件：collect 在部分环境报 not-a-package（单模块布局），
# 用 collect_submodules + 动态库钩子兜底，缺了由 hiddenimports 兜住。
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
    [os.path.join(ROOT, 'run_windowed.py')],
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
    icon=os.path.join(ROOT, 'build', 'icon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # 窗口应用：无控制台
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
