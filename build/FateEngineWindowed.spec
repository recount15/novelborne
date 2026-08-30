# -*- mode: python ; coding: utf-8 -*-
"""窗口版（无边框桌面应用）打包 spec：FateEngineWindowed.exe。

与 FateEngine.spec（Web 版）的差异：
- 入口 run_windowed.py（pywebview 无边框窗口 + 0.0.0.0 后端线程）；
- console=False：双击无黑框，纯窗口应用；
- 收编 pywebview/pythonnet/clr_loader（WebView2 壳运行时）；
- gradio 为 core.app 的遗留软依赖仍需收编（collect-all 保证 import 不炸）。

产物：dist\FateEngineWindowed\FateEngineWindowed.exe
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# spec 位于 build/ 下，数据与入口都锚定项目根（SPECPATH 的上一级）。
ROOT = os.path.dirname(SPECPATH)  # noqa: F821  SPECPATH 由 PyInstaller 注入

datas = [(os.path.join(ROOT, 'assets'), 'assets'),
         (os.path.join(ROOT, 'frontend', 'dist'), 'frontend/dist')]
binaries = []
hiddenimports = ['core', 'core.server', 'core.app', 'core.fate_engine',
                 'pywebview', 'webview', 'clr_loader', 'pythonnet']
for _pkg in ('core.engine', 'core.api', 'core.ui', 'core.memory', 'core.lore', 'core.prompts'):
    hiddenimports += collect_submodules(_pkg)
for _pkg in ('gradio', 'gradio_client', 'openai', 'fastapi', 'uvicorn', 'multipart',
             'httpx', 'pydantic', 'safehttpx', 'groovy', 'qrcode', 'PIL'):
    tmp = collect_all(_pkg)
    datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]
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
    excludes=[],
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
