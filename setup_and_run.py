# -*- coding: utf-8 -*-
"""一键构建并启动书中织梦（Novelborne）。

用法：
    python setup_and_run.py                 # 检查环境 → 构建前端 → 启动
    python setup_and_run.py --build-only    # 只检查并构建，不启动
    python setup_and_run.py --port 21561 --no-browser --var D:/some/var

支持的参数（与 run_app.py 一致）：--host / --port / --var / --no-browser。
本脚本只做检查与提示，不自动安装任何依赖：
    - 缺少 Python 依赖时，请自行执行：pip install -r requirements.txt
    - 缺少 Node.js 18+ 时，请自行安装：https://nodejs.org/
"""
import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 21560

# requirements.txt 包名 → import 名（pillow/python-multipart 的导入名与包名不同）
REQUIRED = {
    "openai": "openai",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "python-multipart": "multipart",
    "qrcode": "qrcode",
    "pillow": "PIL",
    "pywebview": "webview",
}

USAGE = ("用法: python setup_and_run.py [--host H] [--port P] [--var DIR] "
         "[--no-browser] [--build-only] [--skip-build]")


def fail(msg: str) -> None:
    print(f"\n[错误] {msg}")
    sys.exit(1)


def check_python() -> None:
    if sys.version_info < (3, 10):
        fail(f"需要 Python 3.10+，当前是 {sys.version.split()[0]}。请前往 https://python.org 安装。")
    print(f"[1/4] Python {sys.version.split()[0]} OK")


def check_python_deps() -> None:
    missing = [pkg for pkg, mod in REQUIRED.items()
               if importlib.util.find_spec(mod) is None]
    if missing:
        print("\n[错误] 缺少以下 Python 依赖（见 requirements.txt）：")
        for pkg in missing:
            print(f"  - {pkg}")
        print("\n请先自行安装（本脚本不自动安装）：")
        print("    pip install -r requirements.txt")
        sys.exit(1)
    print("[2/4] Python 依赖完整（requirements.txt）OK")


def node_major() -> int:
    try:
        r = subprocess.run(["node", "--version"], capture_output=True,
                           text=True, timeout=15)
        return int(r.stdout.strip().lstrip("v").split(".")[0])
    except Exception:
        return -1


def check_node() -> None:
    node = node_major()
    if node < 18:
        shown = f"v{node}" if node >= 0 else "未安装"
        fail(f"构建前端需要 Node.js 18+，当前检测为 {shown}。请前往 https://nodejs.org/ 安装后重试。")
    print(f"[3/4] Node.js v{node} / npm OK")


def npm_install() -> int:
    if os.name == "nt":
        return subprocess.run(["npm.cmd", "ci"], cwd=FRONTEND).returncode
    return subprocess.run(["npm", "ci"], cwd=FRONTEND).returncode


def npm_build() -> int:
    if os.name == "nt":
        return subprocess.run(["npm.cmd", "run", "build"], cwd=FRONTEND).returncode
    return subprocess.run(["npm", "run", "build"], cwd=FRONTEND).returncode


def build_frontend() -> None:
    if not (FRONTEND / "node_modules").exists():
        print("首次构建：安装前端依赖（npm ci，约几分钟）……")
        if npm_install() != 0:
            fail("npm ci 失败。请检查网络后重试，或手动执行：cd frontend && npm ci")
    print("构建前端（npm run build）……")
    if npm_build() != 0:
        fail("前端构建失败。请手动执行排查：cd frontend && npm run build")
    if not (FRONTEND / "dist" / "index.html").exists():
        fail("构建结束但未生成 frontend/dist/index.html，请检查 frontend 目录。")
    print("[4/4] 前端构建完成 → frontend/dist")


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, wanted: int) -> int:
    if port_free(host, wanted):
        return wanted
    print(f"\n[注意] 端口 {wanted} 已被占用——可能有旧版本（如 FateEngine 便携版）正在运行。"
          f"\n        如需使用 {wanted}，请先退出占用它的程序；本次自动改用相邻端口。")
    for p in range(wanted + 1, wanted + 5):
        if port_free(host, p):
            print(f"[注意] 改用端口 {p}。")
            return p
    fail(f"{wanted}~{wanted + 4} 均被占用，请用 --port 指定一个空闲端口。")
    return -1  # unreachable


def parse_args(argv: list[str]) -> dict:
    opts = {"host": DEFAULT_HOST, "port": None, "var": None,
            "no_browser": False, "build_only": False, "skip_build": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--host" and i + 1 < len(argv):
            opts["host"] = argv[i + 1]; i += 2
        elif a == "--port" and i + 1 < len(argv):
            opts["port"] = int(argv[i + 1]); i += 2
        elif a == "--var" and i + 1 < len(argv):
            opts["var"] = argv[i + 1]; i += 2
        elif a == "--no-browser":
            opts["no_browser"] = True; i += 1
        elif a == "--build-only":
            opts["build_only"] = True; i += 1
        elif a == "--skip-build":
            opts["skip_build"] = True; i += 1
        else:
            fail(f"无法识别的参数：{a}\n{USAGE}")
    return opts


def main() -> None:
    opts = parse_args(sys.argv[1:])

    print("=" * 56)
    print(" 书中织梦 Novelborne · 一键构建启动")
    print("=" * 56)

    check_python()
    check_python_deps()
    check_node()

    if opts["skip_build"] and (FRONTEND / "dist" / "index.html").exists():
        print("[4/4] 跳过构建（--skip-build），使用现有 frontend/dist")
    else:
        build_frontend()

    if opts["build_only"]:
        print("\n构建完成（--build-only，未启动）。启动请执行：python setup_and_run.py")
        return

    host = opts["host"]
    port = opts["port"] if opts["port"] is not None else pick_port(host, DEFAULT_PORT)

    print(f"\n启动服务：http://{host}:{port}/ （Ctrl+C 停止）\n")

    # 进程内启动 run_app（参数已严格解析，不做 shell 命令拼接）
    run_argv = ["run_app.py", "--host", host, "--port", str(port)]
    if opts["var"]:
        run_argv += ["--var", opts["var"]]
    if opts["no_browser"]:
        run_argv += ["--no-browser"]
    sys.argv = run_argv
    sys.path.insert(0, str(ROOT))
    import run_app
    run_app.main()


if __name__ == "__main__":
    main()
