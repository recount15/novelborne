# -*- coding: utf-8 -*-
"""窗口化入口：pywebview 无边框窗口 + 现有 FastAPI 后端。

形态（2026-08-30 设计）：
- 后端：core.server 原样在线程里起 uvicorn，**绑 0.0.0.0**——手机扫码
  远程使用与 Web 版一致可用（窗口只是本机的一个"浏览器"）；
- 窗口：pywebview frameless 无边框，加载 http://127.0.0.1:<port>/；
  标题栏由前端渲染（App.vue TitleBar），拖拽走 .pywebview-drag-region，
  最小化/最大化/关闭经 js_api 调用本类方法；
- 圆角：Windows 11 经 DWM 设 DWMWA_WINDOW_CORNER_PREFERENCE=ROUND
  （macOS 式弧形边框）；Win10 无此 API 则保持直角，不报错；
- 关窗即退出整个进程（含后端线程）。

用法：python run_windowed.py [--port 8300] [--no-lan]
打包：build/FateEngineWindowed.spec（console=False 隐藏控制台）。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path


# Keep fallback streams alive for the entire process. Windowed PyInstaller
# builds may start with ``sys.stdout``/``sys.stderr`` set to None.
_devnull_streams: list[object] = []


def _ensure_stdio() -> None:
    """Install text streams for missing standard handles."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            stream = open(os.devnull, "w", encoding="utf-8", errors="ignore")
            setattr(sys, name, stream)
            _devnull_streams.append(stream)


def _startup_log(message: str) -> None:
    """Write diagnostics for windowed builds where no console is available."""
    try:
        path = _default_var_dir() / "logs" / "windowed-startup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def _install_startup_exception_hook() -> None:
    previous = sys.excepthook
    def hook(exc_type, exc_value, exc_traceback):
        import traceback
        _startup_log("UNHANDLED\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
        previous(exc_type, exc_value, exc_traceback)
    sys.excepthook = hook


def _default_var_dir() -> Path:
    """源码默认项目根 var；PyInstaller 默认 EXE 同级 var。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "var"
    return Path(__file__).resolve().parent / "var"


def _free_port(default: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", 0))
            return s.getsockname()[1]
        except OSError:
            return default


def _apply_window_icon(icon_path, title_keyword: str = "书中织梦") -> None:
    """Win32 WM_SETICON 设置窗口/任务栏品牌图标（pywebview 无 icon 形参）。

    与圆角同线程调用；窗口可能尚未创建，按标题轮询查找。
    """
    if not icon_path:
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        hicon = shell32.ExtractIconW(None, icon_path, 0)
        if not hicon:
            return
        WM_SETICON, ICON_BIG, ICON_SMALL = 0x0080, 1, 0

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _):
            nonlocal target
            n = user32.GetWindowTextLengthW(hwnd)
            if n and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if title_keyword in buf.value:
                    target = hwnd
                    return False
            return True

        for _ in range(50):
            target = None
            user32.EnumWindows(_enum, 0)
            if target is not None:
                user32.SendMessageW(wintypes.HWND(target), WM_SETICON, ICON_BIG, hicon)
                user32.SendMessageW(wintypes.HWND(target), WM_SETICON, ICON_SMALL, hicon)
                return
            time.sleep(0.1)
    except Exception:  # noqa: BLE001  图标是美化项，失败不影响功能
        pass


def _apply_round_corners(title_keyword: str = "书中织梦") -> None:
    """Win11 DWM 圆角（弧形边框）：按窗口标题找 HWND 后设置。

    frameless 窗口默认直角；DWMWCP_ROUND(2) 启用系统级圆角与投影，
    效果与 macOS 弧形边框一致。Win10 无此属性，调用静默失败即可。
    在 pywebview 事件循环起来后由后台线程调用（窗口已可见）。
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd, _lparam):
            nonlocal found
            length = user32.GetWindowTextLengthW(hwnd)
            if length and user32.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if title_keyword in title.value:
                    found = hwnd
                    return False  # 找到即停
            return True

        dwmapi = ctypes.windll.dwmapi
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        # 幂等重试：pywebview 建窗过程中可能重置窗口属性，单次设置会被
        # 覆盖（实测窗口创建后立即设置会被吞）。等窗口稳定后设，并在
        # 回读确认前每 0.5s 补设一次，最多 10 次。
        time.sleep(1.0)
        for _ in range(10):
            found = None
            user32.EnumWindows(_enum_proc, 0)
            if found is None:
                time.sleep(0.5)
                continue
            pref = ctypes.c_int(DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(found), DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref), ctypes.sizeof(pref))
            got = ctypes.c_int(0)
            dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(found), DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(got), ctypes.sizeof(got))
            if got.value == DWMWCP_ROUND:
                return
            time.sleep(0.5)
    except Exception:  # noqa: BLE001  圆角是美化项，失败不影响功能
        pass


class WindowApi:
    """前端 window.pywebview.api.* 的窗口控制。"""

    def __init__(self, window_holder: dict):
        self._holder = window_holder

    def minimize(self) -> None:
        win = self._holder.get("win")
        if win is not None:
            win.minimize()

    def toggle_maximize(self) -> None:
        win = self._holder.get("win")
        if win is None:
            return
        maximized = self._holder.get("maximized", False)
        self._holder["maximized"] = not maximized
        try:
            if maximized:
                win.restore()
            else:
                win.maximize()
        except Exception:  # noqa: BLE001  平台差异兜底
            pass

    def close(self) -> None:
        win = self._holder.get("win")
        if win is not None:
            win.destroy()


def main() -> None:
    _ensure_stdio()
    _install_startup_exception_hook()
    _startup_log("启动窗口版")
    parser = argparse.ArgumentParser(description="书中织梦 · 窗口版")
    parser.add_argument("--port", type=int, default=None, help="端口（默认自动挑选空闲口）")
    parser.add_argument("--no-lan", action="store_true", help="仅本机监听（默认 0.0.0.0，手机可扫码）")
    parser.add_argument("--var", default=None, help="运行数据目录（默认项目根 var/）")
    parser.add_argument("--restore-private", default=None, help="仅本机：校验并恢复私有 sidecar ZIP")
    parser.add_argument("--restore-target", default=None, help="私有恢复目标目录（默认应用根）")
    parser.add_argument("--restore-components", default="data,personas,rules", help="私有恢复组件，逗号分隔")
    parser.add_argument("--restore-overwrite", action="store_true", help="私有恢复时允许覆盖已有文件")
    args = parser.parse_args()

    if args.restore_private:
        from pathlib import Path
        from tools.private_recovery import RecoveryError, restore

        target = Path(args.restore_target) if args.restore_target else Path(__file__).resolve().parent
        components = {item.strip() for item in args.restore_components.split(",") if item.strip()}
        try:
            report = restore(Path(args.restore_private), target, components, overwrite=args.restore_overwrite, dry_run=False)
            print(f"私有恢复完成：{len(report['restored'])} 个文件，跳过 {len(report['skipped'])} 个。")
            return
        except RecoveryError as exc:
            print(f"私有恢复失败：{exc}", file=sys.stderr)
            raise SystemExit(2)

    # 必须在导入 core.server 之前确定运行数据目录；否则 character_db 会把
    # 默认 SQLite 写进 PyInstaller 的 _internal/var。
    if args.var:
        os.environ["FATE_VAR_DIR"] = str(Path(args.var).resolve())
    elif not os.getenv("FATE_VAR_DIR"):
        os.environ["FATE_VAR_DIR"] = str(_default_var_dir())

    port = args.port or _free_port(8300)
    host = "127.0.0.1" if args.no_lan else "0.0.0.0"
    _startup_log(f"配置 host={host} port={port} var={os.environ.get('FATE_VAR_DIR', '')}")
    # server 模块在 import 时读取 FATE_API_HOST 推断局域网监听状态
    os.environ["FATE_API_HOST"] = host
    os.environ["FATE_API_PORT"] = str(port)

    import uvicorn
    from core.server import app

    server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, log_level="warning", use_colors=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)

    import webview

    def _icon_path():
        """品牌图标（火漆徽标）：源码运行取 frontend/public，打包后取
        PyInstaller 捆绑目录；缺失时返回 None 用默认图标。"""
        for base in (os.path.dirname(os.path.abspath(__file__)),
                     getattr(sys, "_MEIPASS", "")):
            if not base:
                continue
            for rel in (os.path.join("frontend", "public", "favicon.ico"), "favicon.ico"):
                p = os.path.join(base, rel)
                if os.path.isfile(p):
                    return p
        return None

    holder: dict = {}
    api = WindowApi(holder)
    window = webview.create_window(
        "书中织梦 · Novelborne",
        f"http://127.0.0.1:{port}/",
        width=1440, height=900, min_size=(1024, 680),
        frameless=True,  # 无边框：标题栏由前端 TitleBar 渲染
        js_api=api,
    )
    holder["win"] = window
    # 圆角 + 品牌图标在事件循环启动后由后台线程施加（需窗口已创建）
    _icon = _icon_path()
    threading.Thread(target=_apply_round_corners, daemon=True).start()
    if _icon:
        threading.Thread(target=_apply_window_icon, args=(_icon,), daemon=True).start()

    def _poke_first_paint():
        # WebView2 偶发「首帧不绘制」：页面已加载但画面停在黑底，手动刷新
        # 才恢复（实测）。loaded 事件后 evaluate 一次 DOM 触发重绘即可。
        time.sleep(2.0)
        try:
            window.evaluate("void document.body.offsetWidth")
        except Exception:  # noqa: BLE001  窗口可能已被关掉
            pass

    webview.start(func=_poke_first_paint)  # 阻塞至关窗
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
