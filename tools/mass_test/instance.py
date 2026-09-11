# -*- coding: utf-8 -*-
"""测试实例管理：独立端口 + 独立 FATE_VAR_DIR 拉起/健康检查/回收。

只终止自己启动并记录 PID 的进程；凭据经环境变量传入子进程，不落盘。
健康检查仅访问本机环回固定 host（127.0.0.1），端口经白名单校验，
opener 禁止重定向——不构成出站请求面。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# 端口白名单：只允许编排器约定的私有端口段。
_MIN_PORT, _MAX_PORT = 21561, 21999


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class TestInstance:
    """一个隔离的书中行源码实例（uvicorn + 独立 var）。"""

    def __init__(self, port: int, var_dir: str | Path, name: str = ""):
        if (not isinstance(port, int) or not (_MIN_PORT <= port <= _MAX_PORT)):
            raise ValueError(f"port {port!r} outside allowed range "
                             f"{_MIN_PORT}-{_MAX_PORT}")
        self.port = port
        self.var_dir = Path(var_dir).resolve()
        self.name = name or f"port{port}"
        self.process: subprocess.Popen | None = None
        self.started_at: tuple[str, int, float] | None = None  # (pname, pid, create_time)
        self.log_path = self.var_dir / "testlog" / "server_stdout.log"
        self.log_handle = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait_seconds: int = 120) -> None:
        if self.process is not None:
            raise RuntimeError(f"instance {self.name} already started")
        # 端口占用预检：已被占用就拒绝启动，绝不接管既有服务。
        if not _port_free(self.port):
            raise RuntimeError(f"port {self.port} already in use; refusing to start "
                               f"instance {self.name}")
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["FATE_VAR_DIR"] = str(self.var_dir)
        env["FATE_NO_BROWSER"] = "1"
        # 透传测试凭据（来源：编排器自己的环境变量）。
        for name in ("FATE_TEST_API_KEY", "FATE_TEST_BASE_URL", "FATE_TEST_MODEL"):
            if os.getenv(name):
                env[name] = os.environ[name]
        self.log_handle = self.log_path.open("ab")
        # 显式环回绑定：run_app 默认 0.0.0.0，测试实例绝不允许对外监听。
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "run_app.py"),
             "--host", "127.0.0.1",
             "--port", str(self.port), "--no-browser"],
            cwd=str(ROOT), env=env, stdout=self.log_handle, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        if not self.wait_healthy(wait_seconds):
            self.stop()
            raise RuntimeError(f"instance {self.name} not healthy after {wait_seconds}s; "
                               f"see {self.log_path}")
        try:
            import psutil
            ps = psutil.Process(self.process.pid)
            self.started_at = ps.name(), self.process.pid, ps.create_time()
        except ImportError:
            self.started_at = "", self.process.pid, 0.0

    def wait_healthy(self, wait_seconds: int) -> bool:
        deadline = time.monotonic() + wait_seconds
        # host 固定为环回字面量，端口已过白名单；URL 不含任何外部输入。
        url = f"http://127.0.0.1:{self.port}/api/health"
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                return False  # 进程提前退出
            try:
                with _OPENER.open(url, timeout=2) as response:
                    if response.status == 200:
                        return True
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                time.sleep(1.0)
        return False

    def create_time(self) -> float:
        if self.process is None:
            raise RuntimeError("instance not started")
        try:
            import psutil
            return psutil.Process(self.process.pid).create_time()
        except ImportError:
            return 0.0

    def stop(self) -> None:
        """先优雅终止，5 秒不退再 kill；只针对自己启动且身份未漂移的进程。"""
        if self.process is None:
            return
        if self.process.poll() is None:
            self._verify_identity()
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        self.started_at = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def _verify_identity(self) -> None:
        """停止前核验 PID 与创建时间仍然匹配；漂移说明句柄失效，拒绝误杀。"""
        import psutil
        proc = psutil.Process(self.process.pid)
        actual = (proc.name(), proc.pid, proc.create_time())
        if self.started_at is not None and actual[1] != self.started_at[1]:
            raise RuntimeError(f"identity drift on stop for {self.name}: "
                               f"recorded={self.started_at} actual={actual}")

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "port": self.port, "var": str(self.var_dir),
                "running": self.process is not None and self.process.poll() is None,
                "pid": self.process.pid if self.process else None,
                "create_time": self.started_at[2] if self.started_at else None}


def _port_free(port: int) -> bool:
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
