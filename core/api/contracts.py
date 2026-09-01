"""API 边界的纯函数与稳定事件契约。"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_SECRET_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret[_-]?key)",
    re.IGNORECASE,
)
_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\u3400-\u9fff-]+")


@dataclass(frozen=True)
class StreamEvent:
    """Vue 可消费的单条 NDJSON 事件。

    ``operation_id``, ``seq`` and ``client_request_id`` are optional so old
    two-field events remain valid while durable operation streams can carry
    reconnect and idempotency metadata.
    """

    type: str
    data: dict[str, Any]
    operation_id: str | None = None
    seq: int | None = None
    client_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"type": self.type, "data": self.data}
        if self.operation_id is not None:
            result["operation_id"] = self.operation_id
        if self.seq is not None:
            result["seq"] = self.seq
        if self.client_request_id is not None:
            result["client_request_id"] = self.client_request_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StreamEvent":
        """Decode both legacy two-field and journal event dictionaries."""
        return cls(
            str(value.get("type", "message")),
            dict(value.get("data") or {}),
            value.get("operation_id"),
            value.get("seq"),
            value.get("client_request_id"),
        )


def normalize_filename(filename: str, *, extension: str = ".txt") -> str:
    """规范化上传名；只保留单层文件名并强制指定扩展名。"""
    raw = Path(str(filename or "")).name.replace("\x00", "")
    stem = Path(raw).stem
    stem = _FILENAME_RE.sub("_", stem).strip("._-")[:96] or "upload"
    suffix = extension if extension.startswith(".") else f".{extension}"
    return f"{stem}{suffix.lower()}"


def redact_secrets(value: Any) -> Any:
    """递归删除凭据字段，不修改调用方对象。"""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_RE.search(name):
                continue
            result[name] = redact_secrets(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return redact_secrets(vars(value))
    except TypeError:
        return str(value)


def _strip_private(value: Any) -> Any:
    """递归删除 ``nemesis_private`` 键：宿敌私密状态玩家绝不可见。"""
    if isinstance(value, Mapping):
        return {
            key: _strip_private(item)
            for key, item in value.items()
            if str(key) != "nemesis_private"
        }
    if isinstance(value, (list, tuple)):
        return [_strip_private(item) for item in value]
    return value


def public_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """复制并脱敏 state；内部 system prompt 与宿敌私密状态不对 Vue 暴露。"""
    source = dict(state or {})
    clean = redact_secrets(copy.deepcopy(source))
    if isinstance(clean, dict):
        clean.pop("system", None)
        clean.pop("api_key", None)
        clean.pop("request_kwargs", None)
        clean.pop("persona_text", None)
        try:
            from core import engine  # 惰性：避免 contracts 在引擎未就绪时硬依赖

            if "skill_profiles" in source:
                skill_snap = engine.skill_drift.public_snapshot(source)
                if skill_snap:
                    clean["skill_profiles"] = skill_snap
            if "break_anchor" in source or "broken_anchors" in source:
                ba_snap = engine.break_anchor.public_snapshot(source)
                clean["break_anchor"] = ba_snap
                clean["momentum_bar"] = ba_snap.get("momentum_bar")
                clean["broken_anchors"] = ba_snap.get("broken_anchors", [])
                clean["anchors_shattered_from"] = ba_snap.get("anchors_shattered_from", 0)
            # 作弊码状态聚合（前端多选/增补模式判定）：
            # relay_active=永久通路是否接通；wish_remaining=三愿剩余次数。
            clean["relay_active"] = engine.cheat_code.is_relay_active(source)
            clean["wish_remaining"] = engine.cheat_code.remaining_wishes(source)
            
            # Character states: migrate and expose public projections
            if "character_states" in source:
                from core.services import character_state_service
                char_states = source.get("character_states", {})
                if isinstance(char_states, dict):
                    clean["character_states"] = {
                        name: character_state_service.public_projection(state_data)
                        for name, state_data in char_states.items()
                    }
            
            # Pre-game state: expose stage but not internal details
            if "pre_game_state" in source:
                pre_game = source.get("pre_game_state", )
                if isinstance(pre_game, dict):
                    clean["pre_game_state"] = {
                        "stage": pre_game.get("stage", "book_selected"),
                        "difficulty": pre_game.get("difficulty"),
                        "prepared_script": {
                            "title": (pre_game.get("prepared_script") or {}).get("title"),
                            "evidence": (pre_game.get("prepared_script") or {}).get("evidence"),
                        } if "prepared_script" in pre_game else None
                    }
        except Exception:  # noqa: BLE001  快照失败时不得把含 pending 的原始档案暴露给前端
            clean.pop("skill_profiles", None)
    return _strip_private(clean)


def _update_data(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): redact_secrets(v) for k, v in value.items()}
    return {"value": redact_secrets(value)}


def gradio_state_from_output(output: Any) -> Mapping[str, Any] | None:
    """按已知输出契约提取状态，避免把 ``gr.update`` 误认成 state。"""
    if not isinstance(output, (tuple, list)):
        return None
    if len(output) == 11 and isinstance(output[1], Mapping):
        return output[1]
    if len(output) == 7 and isinstance(output[2], Mapping):
        return output[2]
    return None


def stream_event_from_gradio(output: Any) -> StreamEvent:
    """将 on_start/on_send 的一次 yield 转换为稳定事件。"""
    if isinstance(output, StreamEvent):
        return output
    if isinstance(output, (tuple, list)):
        state = gradio_state_from_output(output)
        if state is not None:
            status = str(output[2] or "") if len(output) == 11 else ""
            return StreamEvent("state", {
                "chat": redact_secrets(output[0]),
                "state": public_state(state),
                "status": status,
                "meta": {"outputs": len(output)},
            })
    if isinstance(output, Mapping):
        return StreamEvent("state", _update_data(output))
    return StreamEvent("message", {"content": str(output or "")})
