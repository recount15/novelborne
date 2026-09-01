"""API 会话生命周期、上传隔离与内存凭据。"""
from __future__ import annotations

import asyncio
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import normalize_filename


@dataclass
class ApiSession:
    session_id: str
    root: Path
    state: dict[str, Any] = field(default_factory=dict)
    api_key: str = ""
    uploads: dict[str, Path] = field(default_factory=dict)
    upload_display_names: dict[str, str] = field(default_factory=dict)
    ui_state: dict[str, Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def upload_dir(self) -> Path:
        # 上传目录跟随 fe.WRITABLE_DIR（FATE_VAR_DIR 可覆盖）：
        # 多实例并发时各实例的上传文件互不混杂。容错 str 赋值（测试 patch）。
        from core import fate_engine as _fe
        return Path(_fe.WRITABLE_DIR) / "uploads" / self.session_id


class SessionManager:
    """进程内会话存储；凭据只存在对象内存，不写入 state 或文件。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self._sessions: dict[str, ApiSession] = {}
        self._lock = threading.Lock()

    def create(self, session_id: str | None = None) -> ApiSession:
        ident = str(session_id or uuid.uuid4().hex)
        if not ident or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in ident):
            raise ValueError("session_id 只能包含字母、数字、连字符和下划线")
        with self._lock:
            session = self._sessions.get(ident)
            if session is None:
                session = ApiSession(ident, self.root)
                self._sessions[ident] = session
        session.upload_dir.mkdir(parents=True, exist_ok=True)
        return session

    def get(self, session_id: str) -> ApiSession | None:
        with self._lock:
            return self._sessions.get(str(session_id))

    def require(self, session_id: str) -> ApiSession:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"session 不存在: {session_id}")
        return session

    def put_upload(self, session: ApiSession, filename: str, content: bytes, *, kind: str = "novel", extension: str = ".txt") -> dict[str, Any]:
        # 磁盘名带 uuid 前缀防冲突；显示名保留玩家上传的原始文件名（去除 uuid 前缀）。
        safe_name = normalize_filename(filename, extension=extension)
        display_name = Path(safe_name).stem
        upload_id = uuid.uuid4().hex
        path = (session.upload_dir / f"{upload_id}_{safe_name}").resolve()
        if session.upload_dir.resolve() not in path.parents:
            raise ValueError("上传路径无效")
        path.write_bytes(content)
        session.uploads[upload_id] = path
        session.upload_display_names[upload_id] = display_name
        return {
            "upload_id": upload_id,
            "filename": safe_name,
            "display_name": display_name,
            "kind": str(kind or "novel"),
            "bytes": len(content),
        }

    def upload_path(self, session: ApiSession, upload_id: str) -> Path:
        path = session.uploads.get(str(upload_id))
        if path is None or not path.is_file():
            raise KeyError(f"upload 不存在: {upload_id}")
        return path

    def upload_display_name(self, session: ApiSession, upload_id: str | None) -> str | None:
        """返回上传文件的显示名（不含 uuid 前缀）；无此上传时返回 None。"""
        if not upload_id:
            return None
        return session.upload_display_names.get(str(upload_id))

    def acquire(self, session: ApiSession) -> bool:
        return session.lock.acquire(blocking=False)

    def release(self, session: ApiSession) -> None:
        if session.lock.locked():
            session.lock.release()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def save_ui_state(self, session: ApiSession, ui_state: dict[str, Any]) -> None:
        """保存前端UI状态到会话内存，刷新/换设备后可恢复。"""
        with session.lock:
            session.ui_state = ui_state.copy()

    def get_ui_state(self, session: ApiSession) -> dict[str, Any]:
        """获取已保存的UI状态。"""
        with session.lock:
            return session.ui_state.copy()


async def read_upload(upload: Any, *, max_bytes: int = 100 * 1024 * 1024) -> bytes:
    """分块读取 Starlette UploadFile，限制单文件体积。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("上传文件超过 100 MiB 限制")
        chunks.append(chunk)
    return b"".join(chunks)
