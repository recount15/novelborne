# -*- coding: utf-8 -*-
"""强化模式进度账本：固定字段读写，用于存档/读档。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Union

LEDGER_FIELDS = (
    "player_state", "cheat", "foreshadowing", "echoes",
    "chapter_budget", "anchors", "ripples",
)
DEFAULT_LEDGER: Dict[str, Any] = {
    "player_state": {},
    "cheat": {},
    "foreshadowing": [],
    "echoes": [],
    "chapter_budget": {},
    "anchors": {},
    "ripples": [],
}


def new_ledger(**values: Any) -> Dict[str, Any]:
    """返回固定字段的新账本；未知字段会拒绝，避免存档漂移。"""
    unknown = set(values) - set(LEDGER_FIELDS)
    if unknown:
        raise ValueError("未知账本字段: %s" % sorted(unknown))
    result = {key: value for key, value in DEFAULT_LEDGER.items()}
    result.update(values)
    return result


def validate_ledger(value: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("账本必须是 JSON 对象")
    missing = [field for field in LEDGER_FIELDS if field not in value]
    extra = [field for field in value if field not in LEDGER_FIELDS]
    if missing or extra:
        raise ValueError("账本字段不匹配，缺少=%s，多余=%s" % (missing, extra))
    return {field: value[field] for field in LEDGER_FIELDS}


def save_ledger(path: Union[str, os.PathLike], ledger: Dict[str, Any]) -> Path:
    """以 UTF-8 JSON 原子写入账本文件。"""
    payload = validate_ledger(ledger)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temp_name).replace(target)
    except Exception:
        try:
            Path(temp_name).unlink()
        except OSError:
            pass
        raise
    return target


def load_ledger(path: Union[str, os.PathLike], default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """读取并严格校验账本；文件不存在时返回固定字段默认值。"""
    target = Path(path)
    if not target.is_file():
        return new_ledger(**(default or {}))
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("账本读取失败: %s" % exc) from exc
    return validate_ledger(value)


class Ledger:
    """面向对象的薄封装，便于在游戏状态中持有一个存档。"""

    def __init__(self, path: Union[str, os.PathLike], value: Optional[Dict[str, Any]] = None):
        self.path = Path(path)
        self.data = new_ledger(**(value or {})) if value is not None else load_ledger(self.path)

    def save(self) -> Path:
        self.data = validate_ledger(self.data)
        return save_ledger(self.path, self.data)

    def update(self, **values: Any) -> Dict[str, Any]:
        updated = new_ledger(**{**self.data, **values})
        self.data = updated
        return self.data

    @classmethod
    def load(cls, path: Union[str, os.PathLike]) -> "Ledger":
        return cls(path, load_ledger(path))
