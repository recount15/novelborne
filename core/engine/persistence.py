# -*- coding: utf-8 -*-
"""命运引擎独立持久化模块。

本模块不依赖 app.py 或 fate_engine.py，可保存/恢复运行态快照，并维护
sessions/<timestamp>/ 下的可读工作记录。所有写入均使用 UTF-8 和原子替换。
"""
from __future__ import annotations

import copy
import datetime as _datetime
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Union

SCHEMA = "fate-engine-save"
# Version 2 adds a stable, redacted opening snapshot while retaining the full
# state payload for compatibility with existing callers and saves.
VERSION = 2
PathLike = Union[str, os.PathLike[str]]
_SECRET_KEYS = {
    "api_key", "apikey", "deepseek_api_key", "openai_api_key",
    "dashscope_api_key", "moonshot_api_key", "zhipuai_api_key",
    "access_token", "refresh_token", "client_secret", "secret_key",
}
_SECRET_RE = re.compile(r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret[_-]?key)", re.I)
_SAFE_ID_RE = re.compile(r"[^\w.@-]+", re.UNICODE)


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _safe_part(value: Any, fallback: str = "session") -> str:
    text = str(value or "").strip()
    text = _SAFE_ID_RE.sub("_", text).strip("._")
    return text[:96] or fallback


def _root(path: Optional[PathLike] = None) -> Path:
    """返回存储根目录；path 只决定本地根目录，不写入快照内容。"""
    return Path(path) if path is not None else Path.cwd()


def _without_secrets(value: Any, key: str = "") -> Any:
    """递归移除凭据，同时保留诸如 tok_in/tok_out 的运行统计。"""
    if isinstance(value, Mapping):
        result = {}
        for raw_key, item in value.items():
            name = str(raw_key)
            normalized = name.lower().replace("-", "_")
            if normalized in _SECRET_KEYS or _SECRET_RE.search(name):
                continue
            result[name] = _without_secrets(item, name)
        return result
    if isinstance(value, (list, tuple)):
        return [_without_secrets(item, key) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return _without_secrets(vars(value), key)
    except TypeError:
        return str(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        # Windows 偶发 WinError 5：杀毒/索引服务或并发读者短暂持有目标句柄，
        # os.replace 直接失败。短退避重试（tmp 名唯一，原子性不受影响），
        # 重试穷尽仍失败才上抛。并发多线程写同一目标时后写覆盖先写，
        # 与既有"最后落盘者胜"语义一致。
        last_exc: Optional[BaseException] = None
        for attempt in range(4):
            try:
                os.replace(temp_name, path)
                last_exc = None
                break
            except PermissionError as exc:  # WinError 5（目标被短暂占用）
                last_exc = exc
                time.sleep(0.05 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return str(path)


def _record_params(value: Mapping[str, Any]) -> dict[str, Any]:
    """工作记录只保留通用运行参数，不写入作品标题、原文或角色专名。"""
    blocked = {"work", "novel", "novel_name", "title", "source", "source_name", "source_path"}
    return {str(key): _without_secrets(item) for key, item in value.items()
            if str(key).lower() not in blocked}


def _record_state(value: Mapping[str, Any]) -> dict[str, Any]:
    """工作记录不复制 system prompt 与聊天历史，避免原著内容进入记录文件。"""
    blocked = {"system", "history", "chatbot", "start_params", "novel_excerpt", "work_label"}
    return {str(key): _without_secrets(item) for key, item in value.items()
            if str(key).lower() not in blocked}


_OPENING_DEFAULTS = {
    "role": None,
    "skill": "",
    "heroine_mode": "单女主",
    "participation": {},
    "thinking_mode": "auto",
    "thinking_param": "",
    "request_kwargs": {},
    "plot_summary": None,
    "anchors": {},
    "chapter_budget": {},
    "stage_state": {},
    "opening_confirmed": False,
}


def _first_value(state: Mapping[str, Any], params: Mapping[str, Any], *keys: str) -> Any:
    for source in (state, params):
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
    return None


def _opening_snapshot(state: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Build the stable, credential-free data needed to resume an opening."""
    distill = state.get("distill", state.get("distillation", state.get("distill_state", {})))
    ledger = state.get("ledger", state.get("accounting", state.get("token_ledger", {})))
    if not isinstance(distill, Mapping):
        distill = {}
    if not isinstance(ledger, Mapping):
        ledger = {}
    stage_keys = ("stage", "stage_state", "phase", "phase_state", "gf_stage", "gf_confirmed",
                  "plot_ready", "distill_status", "distill_enabled")
    stage_state = dict(state.get("stage_state") or {}) if isinstance(state.get("stage_state"), Mapping) else {}
    stage_state.update({key: state[key] for key in stage_keys if key in state})
    anchors = _first_value(state, params, "anchors", "anchor", "anchor_state")
    if anchors is None:
        anchors = ledger.get("anchors", {})
    chapter_budget = _first_value(state, params, "chapter_budget", "chapter_budgets")
    if chapter_budget is None:
        chapter_budget = ledger.get("chapter_budget", {})
    participation = _first_value(state, params, "participation", "participation_state", "participation_map")
    if participation is None:
        participation = {}
    snapshot = {
        "role": _first_value(state, params, "role", "protagonist_role", "character_role"),
        "skill": _first_value(state, params, "skill", "protagonist_skill", "character_skill") or "",
        "heroine_mode": _first_value(state, params, "heroine_mode", "female_lead_mode") or "单女主",
        "participation": participation,
        "thinking_mode": _first_value(state, params, "thinking_mode") or "auto",
        "thinking_param": _first_value(state, params, "thinking_param") or "",
        "request_kwargs": state.get("request_kwargs", {}),
        "plot_summary": distill.get("plot_summary"),
        "anchors": anchors,
        "chapter_budget": chapter_budget,
        "stage_state": stage_state,
        "opening_confirmed": bool(_first_value(state, params, "opening_confirmed") or False),
        "companions": state.get("companions", []),
        "heroines": state.get("heroines", []),
    }
    snapshot["thinking_params"] = {
        "mode": snapshot["thinking_mode"], "param": snapshot["thinking_param"],
        "request_kwargs": snapshot["request_kwargs"],
    }
    return _without_secrets(snapshot)


def _snapshot(state: Optional[Mapping[str, Any]], start_params: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    state = dict(state or {})
    params = dict(start_params or state.get("start_params") or state.get("settings") or {})
    mechanics_keys = ("nemesis", "companion", "protagonist", "heroine", "female_lead",
                      "female_protagonist", "main_character", "ripple", "ripple_state")
    mechanics = {key: state[key] for key in mechanics_keys if key in state}
    current_keys = ("chapter", "current_chapter", "chapter_no", "current_chapter_no",
                    "budget", "turn_budget", "progress", "round", "turn")
    current = {key: state[key] for key in current_keys if key in state}
    ledger = state.get("ledger", state.get("accounting", state.get("token_ledger", {})))
    distill = state.get("distill", state.get("distillation", state.get("distill_state", {})))
    state_memory = state.get("state_memory", {})
    lore = state.get("lore", {})
    payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "saved_at": _now(),
        "start_params": _without_secrets(params),
        "opening_snapshot": _opening_snapshot(state, params),
        "history": _without_secrets(state.get("history", [])),
        "ledger": _without_secrets(ledger),
        "current": _without_secrets(current),
        "distill": _without_secrets(distill),
        "state_memory": _without_secrets(state_memory),
        "lore": _without_secrets(lore),
        "mechanics": _without_secrets(mechanics),
        "state": _without_secrets(state),
    }
    return payload


def _scoped_save_id(save_id: Optional[str], session_id: Optional[str]) -> str:
    """自动存档（latest）按会话隔离命名：latest -> latest-<会话前8位>。

    存/取两侧共用同一归一，保证同名存档在不同会话间互不覆盖互不串档；
    手动命名的存档原样返回。
    """
    safe = _safe_part(save_id, "latest")
    if safe == "latest" and session_id:
        return f"latest-{str(session_id)[:8]}"
    return safe


def save_state(state: Optional[Mapping[str, Any]], save_id: str = "latest", root: Optional[PathLike] = None,
               start_params: Optional[Mapping[str, Any]] = None, session_id: Optional[str] = None) -> str:
    """保存到 ``<root>/saves/<save_id>.json``（兼容镜像）并写入 SQLite，返回写入路径。"""
    payload = _snapshot(state, start_params)
    # 自动存档（save_id=latest）按会话隔离文件名：不同会话互不覆盖镜像，
    # 磁盘回填按 save_id 加载不会张冠李戴；手动命名的存档不受影响。
    effective_id = _scoped_save_id(save_id, session_id)
    path = _root(root) / "saves" / f"{_safe_part(effective_id, 'latest')}.json"
    result = _atomic_json(path, payload)
    _db_save(payload, effective_id, root, session_id or "legacy")
    return result


# ---------------------------------------------------------------------------
# SQLite 自由存档库
# ---------------------------------------------------------------------------

_DB_FILE = "fate_engine.db"
_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS saves (
    session_id TEXT NOT NULL,
    save_id    TEXT NOT NULL,
    saved_at   TEXT NOT NULL,
    mode       TEXT NOT NULL DEFAULT '',
    work       TEXT NOT NULL DEFAULT '',
    novel      TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT '',
    persona    TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL DEFAULT '',
    round      INTEGER NOT NULL DEFAULT 0,
    chapter    INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (session_id, save_id)
)
"""


def _db_path(root: Optional[PathLike]) -> Path:
    return _root(root) / "db" / _DB_FILE


def _db_connect(root: Optional[PathLike]) -> sqlite3.Connection:
    path = _db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_DB_SCHEMA)
    return conn


def _first_text(*values: Any) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def _metadata(payload: Mapping[str, Any], session_id: str, save_id: str) -> dict[str, Any]:
    """从存档 envelope 提取列表页可见的描述信息。"""
    state = payload.get("state") if isinstance(payload.get("state"), Mapping) else {}
    params = payload.get("start_params") if isinstance(payload.get("start_params"), Mapping) else {}
    current = payload.get("current") if isinstance(payload.get("current"), Mapping) else {}
    memory = payload.get("state_memory") if isinstance(payload.get("state_memory"), Mapping) else {}
    scene = memory.get("scene") if isinstance(memory.get("scene"), Mapping) else {}

    def _int(*values: Any) -> int:
        for value in values:
            try:
                return int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return 0

    return {
        "session_id": session_id,
        "save_id": save_id,
        "saved_at": _first_text(payload.get("saved_at"), _now()),
        "mode": _first_text(params.get("mode"), state.get("mode")),
        "work": _first_text(params.get("work"), state.get("work")),
        "novel": _first_text(params.get("novel"), state.get("novel")),
        "role": _first_text(params.get("role"), state.get("role")),
        "persona": _first_text(params.get("persona"), params.get("persona_preset"),
                               state.get("persona"), state.get("persona_preset")),
        "difficulty": _first_text(params.get("difficulty"), state.get("difficulty")),
        "round": _int(state.get("round"), current.get("round")),
        "chapter": _int(state.get("current_chapter"), current.get("current_chapter"),
                        current.get("chapter"), scene.get("chapter")), 
    }


def _db_save(payload: Mapping[str, Any], save_id: str, root: Optional[PathLike], session_id: str) -> None:
    meta = _metadata(payload, session_id, _safe_part(save_id, "latest"))
    body = json.dumps(dict(payload), ensure_ascii=False)
    conn = _db_connect(root)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO saves (session_id, save_id, saved_at, mode, work, novel, role,
                                   persona, difficulty, round, chapter, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, save_id) DO UPDATE SET
                    saved_at=excluded.saved_at, mode=excluded.mode, work=excluded.work,
                    novel=excluded.novel, role=excluded.role, persona=excluded.persona,
                    difficulty=excluded.difficulty, round=excluded.round,
                    chapter=excluded.chapter, payload_json=excluded.payload_json
                """,
                (meta["session_id"], meta["save_id"], meta["saved_at"], meta["mode"],
                 meta["work"], meta["novel"], meta["role"], meta["persona"],
                 meta["difficulty"], meta["round"], meta["chapter"], body),
            )
    finally:
        conn.close()


def _restore_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """从 envelope 恢复完整运行态（load_state 与严格读档共用的还原逻辑）。"""
    saved = payload.get("state")
    if not isinstance(saved, dict):
        saved = {}
    result = copy.deepcopy(saved)
    result["history"] = copy.deepcopy(payload.get("history", result.get("history", [])))
    result["ledger"] = copy.deepcopy(payload.get("ledger", result.get("ledger", {})))
    result["distill"] = copy.deepcopy(payload.get("distill", result.get("distill", {})))
    result["state_memory"] = copy.deepcopy(payload.get("state_memory", result.get("state_memory", {})))
    result["lore"] = copy.deepcopy(payload.get("lore", result.get("lore", {})))
    result["start_params"] = copy.deepcopy(payload.get("start_params", result.get("start_params", {})))
    result.update(copy.deepcopy(payload.get("current", {})))
    result.update(copy.deepcopy(payload.get("mechanics", {})))
    return result


def _read_json_save(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return payload if _valid_payload(payload) else None


def _import_legacy_saves(root: Optional[PathLike], conn: sqlite3.Connection) -> None:
    """把旧的 saves/*.json 一次性导入数据库（幂等，不覆盖已有同名行）。"""
    saves_dir = _root(root) / "saves"
    if not saves_dir.is_dir():
        return
    for path in sorted(saves_dir.glob("*.json")):
        payload = _read_json_save(path)
        if payload is None:
            continue
        save_id = path.stem
        # save_state 会同时写 JSON 镜像与数据库行；镜像不重复导入。
        # 只有库里完全没有同名存档时，才视为真正的旧档导入。
        exists = conn.execute(
            "SELECT 1 FROM saves WHERE save_id=? LIMIT 1", (save_id,)
        ).fetchone()
        if exists:
            continue
        meta = _metadata(payload, "legacy", save_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO saves (session_id, save_id, saved_at, mode, work, novel,
                                         role, persona, difficulty, round, chapter, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (meta["session_id"], meta["save_id"], meta["saved_at"], meta["mode"],
             meta["work"], meta["novel"], meta["role"], meta["persona"],
             meta["difficulty"], meta["round"], meta["chapter"],
             json.dumps(payload, ensure_ascii=False)),
        )


def list_saves(root: Optional[PathLike] = None, session_id: Optional[str] = None) -> list[dict[str, Any]]:
    """列出全部存档点的描述信息（日期、小说、角色、难度、模式等），新的在前。"""
    conn = _db_connect(root)
    try:
        with conn:
            _import_legacy_saves(root, conn)
        query = ("SELECT session_id, save_id, saved_at, mode, work, novel, role, persona,"
                 " difficulty, round, chapter FROM saves")
        args: tuple[Any, ...] = ()
        if session_id:
            query += " WHERE session_id=?"
            args = (session_id,)
        query += " ORDER BY saved_at DESC, rowid DESC"
        rows = conn.execute(query, args).fetchall()
    finally:
        conn.close()
    keys = ("session_id", "save_id", "saved_at", "mode", "work", "novel", "role",
            "persona", "difficulty", "round", "chapter")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def load_state_strict(save_id: str, root: Optional[PathLike] = None,
                      session_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """严格读档：成功返回恢复状态，失败返回 None，绝不回退到当前状态。

    session_id 为空时按“自由读档”语义在全库中查找同名存档（取最新一条）。
    """
    safe_id = _scoped_save_id(save_id, session_id)
    payload: Optional[dict[str, Any]] = None
    conn = _db_connect(root)
    try:
        with conn:
            _import_legacy_saves(root, conn)
        if session_id:
            row = conn.execute(
                "SELECT payload_json FROM saves WHERE session_id=? AND save_id=?",
                (session_id, safe_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT payload_json FROM saves WHERE save_id=? ORDER BY saved_at DESC, rowid DESC LIMIT 1",
                (safe_id,),
            ).fetchone()
    finally:
        conn.close()
    if row:
        try:
            candidate = json.loads(row[0])
            if _valid_payload(candidate):
                payload = candidate
        except (ValueError, TypeError, json.JSONDecodeError):
            payload = None
    if payload is None:
        # 兼容尚未导入库的旧 JSON 路径。
        payload = _read_json_save(_root(root) / "saves" / f"{safe_id}.json")
    if payload is None:
        return None
    return _restore_payload(payload)


def _valid_payload(payload: Any) -> bool:
    return (isinstance(payload, dict) and payload.get("schema") == SCHEMA
            and isinstance(payload.get("version"), int)
            and 1 <= payload["version"] <= VERSION)


def load_state(save_id: str = "latest", root: Optional[PathLike] = None,
               current_state: Optional[Mapping[str, Any]] = None) -> Optional[dict[str, Any]]:
    """读取存档；坏档、未知版本或 IO 错误时返回 current_state 的副本。"""
    path = Path(save_id) if str(save_id).lower().endswith(".json") and Path(save_id).parent != Path(".") else _root(root) / "saves" / f"{_safe_part(save_id, 'latest')}.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not _valid_payload(payload):
            raise ValueError("存档 schema/version 无效")
        return _restore_payload(payload)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return copy.deepcopy(dict(current_state)) if isinstance(current_state, Mapping) else None


# 兼容更直观的调用命名。
save_game = save_state
load_game = load_state
save_checkpoint = save_state
load_checkpoint = load_state


def create_session(root: Optional[PathLike] = None, timestamp: Optional[str] = None,
                   start_params: Optional[Mapping[str, Any]] = None,
                   state: Optional[Mapping[str, Any]] = None) -> str:
    """创建 sessions/<timestamp>/ 四件套，并返回相对 sessions 路径。"""
    stamp = _safe_part(timestamp, _datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    base = _root(root) / "sessions" / stamp
    base.mkdir(parents=True, exist_ok=True)
    clean_params = _record_params(dict(start_params or {}))
    clean_state = _record_state(dict(state or {}))
    _atomic_json(base / "锚点状态.json", {"schema": SCHEMA, "version": VERSION,
                                      "updated_at": _now(), "state": clean_state})
    (base / "开始.md").write_text("# 命运引擎 · 开始\n\n" + _markdown_mapping(clean_params), encoding="utf-8")
    (base / "数据状态.md").write_text("# 数据状态\n\n" + _markdown_mapping(clean_state), encoding="utf-8")
    (base / "进度账本.md").write_text("# 进度账本\n\n" + _progress_text(clean_state), encoding="utf-8")
    return os.path.relpath(base, _root(root))


def update_session(session: PathLike, root: Optional[PathLike] = None,
                   state: Optional[Mapping[str, Any]] = None,
                   note: str = "", start_params: Optional[Mapping[str, Any]] = None) -> str:
    """更新工作记录；session 可为 create_session 返回值或目录名。"""
    base = Path(session)
    if not base.is_absolute():
        base = _root(root) / base
    base.mkdir(parents=True, exist_ok=True)
    clean_state = _record_state(dict(state or {}))
    anchor = {"schema": SCHEMA, "version": VERSION, "updated_at": _now(), "state": clean_state}
    _atomic_json(base / "锚点状态.json", anchor)
    if start_params is not None:
        (base / "开始.md").write_text("# 命运引擎 · 开始\n\n" + _markdown_mapping(_record_params(dict(start_params))), encoding="utf-8")
    (base / "数据状态.md").write_text("# 数据状态\n\n" + _markdown_mapping(clean_state), encoding="utf-8")
    (base / "进度账本.md").write_text("# 进度账本\n\n" + _progress_text(clean_state) + ("\n\n" + note.strip() if note.strip() else ""), encoding="utf-8")
    return str(base)


def _markdown_mapping(value: Any) -> str:
    if not isinstance(value, Mapping):
        return str(value) if value else "（暂无）"
    return "\n".join(f"- **{key}**：{json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else item}" for key, item in value.items()) or "（暂无）"


def _progress_text(state: Mapping[str, Any]) -> str:
    keys = ("chapter", "current_chapter", "chapter_no", "round", "progress", "budget", "turn_budget", "ledger")
    selected = {key: state[key] for key in keys if key in state}
    return _markdown_mapping(selected)


__all__ = ["SCHEMA", "VERSION", "save_state", "load_state", "load_state_strict", "list_saves",
           "save_game", "load_game", "save_checkpoint", "load_checkpoint",
           "create_session", "update_session"]
