# -*- coding: utf-8 -*-
"""每实例实时测试日志：JSONL 逐条 append+flush，崩溃后可续读。

文件位于实例 var/testlog/ 下：http.jsonl（每个请求）、stream.jsonl（每个流式帧）、
quality.jsonl（质量瑕疵发现）、problems.jsonl（问题台账事件）。
脱敏在写入前统一执行：任何字段值中出现 api_key 原文一律替换为 ***。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class JsonlLogger:
    """线程安全的 JSONL 追加日志器；每条写盘即 flush。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._secret: str | None = None

    def set_secret(self, secret: str) -> None:
        """注册凭据原文：写入前对所有字符串做替换脱敏。"""
        if secret and len(secret) >= 8:
            self._secret = secret

    def _scrub(self, value: Any) -> Any:
        secret = self._secret
        if secret is None:
            return value
        if isinstance(value, str):
            return value.replace(secret, "***")
        if isinstance(value, dict):
            return {k: self._scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._scrub(v) for v in value]
        return value

    def write(self, kind: str, **fields: Any) -> None:
        record = {"ts": round(time.time(), 3), "kind": kind,
                  **{k: self._scrub(v) for k, v in fields.items()}}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def find_secret(self, text: str, key_hint: str = "") -> dict[str, Any] | None:
        """凭据泄露检查：正文/日志片段中出现 Key 原文即返回发现记录。"""
        if self._secret and self._secret in text:
            return {"type": "secret_leak", "where": key_hint,
                    "excerpt": text[max(0, text.find(self._secret) - 60)]
                    .replace(self._secret, "***")[:200]}
        return None


def testlog_dir(var_dir: str | Path) -> Path:
    return Path(var_dir) / "testlog"


def open_loggers(var_dir: str | Path) -> dict[str, JsonlLogger]:
    base = testlog_dir(var_dir)
    loggers = {name: JsonlLogger(base / f"{name}.jsonl")
               for name in ("http", "stream", "quality", "problems", "lane")}
    secret = os.getenv("FATE_TEST_API_KEY", "")
    for logger in loggers.values():
        if secret:
            logger.set_secret(secret)
    return loggers
