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
import threading
import time


def _free_port(default: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", 0))
            return s.getsockname()[1]
        except OSError:
            return default


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
    parser = argparse.ArgumentParser(description="书中织梦 · 窗口版")
    parser.add_argument("--port", type=int, default=None, help="端口（默认自动挑选空闲口）")
    parser.add_argument("--no-lan", action="store_true", help="仅本机监听（默认 0.0.0.0，手机可扫码）")
    parser.add_argument("--var", default=None, help="运行数据目录（默认项目根 var/）")
    args = parser.parse_args()

    if args.var:
        os.environ["FATE_VAR_DIR"] = args.var

    port = args.port or _free_port(8300)
    host = "127.0.0.1" if args.no_lan else "0.0.0.0"
    # server 模块在 import 时读取 FATE_API_HOST 推断局域网监听状态
    os.environ["FATE_API_HOST"] = host
    os.environ["FATE_API_PORT"] = str(port)

    import uvicorn
    from core.server import app

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)

    import webview

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
    # 圆角在事件循环启动后由后台线程施加（FindWindow 需窗口已创建）
    threading.Thread(target=_apply_round_corners, daemon=True).start()
    webview.start()  # 阻塞至关窗
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
