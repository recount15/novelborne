# -*- coding: utf-8 -*-
"""Windows/source entry: serve the Vue workbench and open it in a browser.

多实例并发：同一台机器可同时运行多份本程序——
  - 端口：--port / FATE_API_PORT（默认 8000），每实例必须不同端口；
  - 数据目录：--var / FATE_VAR_DIR（默认项目根 var/），每实例必须不同目录，
    config / 存档 / 会话 / 上传 / 日志 / SQLite 全部随之隔离；
  - 集群实例可加 --no-browser 避免弹出浏览器窗口。

局域网访问：默认监听 0.0.0.0，手机连同一 Wi-Fi 后可扫码远程使用
（启动时终端打印地址与二维码；仅本机使用可 --host 127.0.0.1）。

示例（开三个实例并行测试）：
  python run_app.py --port 8000                      # 主实例（默认 var/）
  python run_app.py --port 8010 --var var/cluster/a --no-browser
  python run_app.py --port 8020 --var var/cluster/b --no-browser
"""
from __future__ import annotations

import argparse
import os


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="命运引擎 API 服务")
    parser.add_argument("--host", default=None,
                        help="监听地址（默认 FATE_API_HOST 或 0.0.0.0，支持局域网/手机扫码；仅本机可 127.0.0.1）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认 FATE_API_PORT 或 8000）")
    parser.add_argument("--var", default=None,
                        help="运行数据目录（默认 FATE_VAR_DIR 或项目根 var/）；多实例务必各用独立目录")
    parser.add_argument("--no-browser", action="store_true",
                        help="不自动打开浏览器（集群/后台实例用）")
    return parser.parse_args()


def _print_lan_banner(port: int) -> None:
    """启动横幅：局域网地址 + 终端 ASCII 二维码（手机扫码远程使用）。"""
    try:
        from core.server import _lan_addresses
        addresses = _lan_addresses()
    except Exception:  # noqa: BLE001  横幅失败不影响启动
        addresses = []
    if not addresses:
        print("[LAN] 未检测到局域网地址（仅本机可访问）")
        return
    url = f"http://{addresses[0]}:{port}"
    print("=" * 56)
    print(f"[LAN] 手机与电脑连同一 Wi-Fi，扫码或输入以下地址远程使用：")
    print(f"[LAN]   {url}")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001  无 qrcode 库时只打印地址
        print("[LAN] （终端二维码不可用：pip install qrcode；页面内二维码不受影响）")
    print("=" * 56)


def main() -> None:
    args = _parse_args()
    # --var 必须在导入 core 之前写入环境变量：WRITABLE_DIR 在 import 时固化。
    if args.var:
        os.environ["FATE_VAR_DIR"] = args.var

    host = args.host or os.getenv("FATE_API_HOST", "0.0.0.0")
    port = args.port or int(os.getenv("FATE_API_PORT", "8000"))
    # server 模块在 import 时读取 FATE_API_HOST 推断局域网监听状态，先写环境再导入。
    os.environ["FATE_API_HOST"] = host

    import threading
    import webbrowser

    import uvicorn

    from core.server import app

    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    if not args.no_browser and not os.getenv("FATE_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{display_host}:{port}")).start()
    if host in {"0.0.0.0", "::"}:
        _print_lan_banner(port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
