"""开局四阶段状态机。

该模块只处理开局门禁，不调用模型、不读写文件，也不修改传入状态。
状态和结果均为由 JSON 基础类型组成的字典，适合直接放入存档。

阶段顺序：

``txt_uploaded`` -> ``plot_ready`` -> ``gf_confirmed`` ->
``opening_confirmed`` -> ``started``

其中 ``started`` 是 ``opening_confirmed`` 之后的正式开局动作，不计入四个
准备阶段。旧存档中的 ``plot_ready``、``gf_stage``、``gf_confirmed`` 字段会
被保留并与新字段同步。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional

PHASE_INIT = "init"
PHASE_TXT_UPLOADED = "txt_uploaded"
PHASE_PLOT_READY = "plot_ready"
PHASE_GF_CONFIRMED = "gf_confirmed"
PHASE_OPENING_CONFIRMED = "opening_confirmed"
PHASE_STARTED = "started"

PHASES = (
    PHASE_INIT,
    PHASE_TXT_UPLOADED,
    PHASE_PLOT_READY,
    PHASE_GF_CONFIRMED,
    PHASE_OPENING_CONFIRMED,
    PHASE_STARTED,
)

EVENT_UPLOAD_TXT = "upload_txt"
EVENT_EXTRACT_PLOT = "extract_plot"
EVENT_CONFIRM_GF = "confirm_gf"
EVENT_CONFIRM_OPENING = "confirm_opening"
EVENT_START = "start"

_ERROR_MESSAGES = {
    "txt_required": "必须先上传 TXT 原著",
    "plot_required": "必须先完成剧情提取",
    "gf_required": "必须先在正式游戏聊天框确认金手指",
    "opening_required": "必须先确认开局设定",
    "already_started": "正式游戏已经开局",
    "invalid_confirmation": "确认值必须为 True",
    "empty_plot": "剧情提取结果不能为空",
    "empty_txt": "TXT 文件标识不能为空",
    "invalid_state": "开局状态不是有效对象",
    "unknown_event": "未知的开局事件",
}


def _reason(code: str) -> str:
    return _ERROR_MESSAGES.get(code, code)


def initial_state(**values: Any) -> dict[str, Any]:
    """返回全新的初始状态；可用关键字覆盖业务元数据。"""
    state: dict[str, Any] = {
        "schema": "fate-engine-opening-flow",
        "version": 1,
        "phase": PHASE_INIT,
        "txt_uploaded": False,
        "plot_ready": False,
        "plot_summary": None,
        "gf_stage": "pending",
        "gf_confirmed": False,
        "gf_decision": None,
        "opening_confirmed": False,
        "started": False,
    }
    state.update(deepcopy(values))
    return normalize_state(state)


new_state = initial_state


def _bool(value: Any) -> bool:
    return value is True or (isinstance(value, int) and value == 1)


def _has_text(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return bool(str(value or "").strip())


def _infer_txt_uploaded(state: Mapping[str, Any]) -> bool:
    if "txt_uploaded" in state:
        return _bool(state.get("txt_uploaded"))
    # 兼容上传流程可能使用的历史字段；不把 plot_ready 单独当成 TXT 上传。
    return any(_has_text(state.get(key)) for key in (
        "txt_path", "upload_path", "uploaded_file", "uploaded_txt", "book_path",
    ))


def _infer_phase(state: Mapping[str, Any], txt_uploaded: bool, plot_ready: bool,
                 gf_confirmed: bool, opening_confirmed: bool, started: bool) -> str:
    if started:
        return PHASE_STARTED
    if opening_confirmed:
        return PHASE_OPENING_CONFIRMED
    if gf_confirmed:
        return PHASE_GF_CONFIRMED
    if plot_ready:
        return PHASE_PLOT_READY
    if txt_uploaded:
        return PHASE_TXT_UPLOADED
    return PHASE_INIT


def normalize_state(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """复制并补齐状态，同时从旧字段推导阶段。

    该函数不会原地修改输入。显式的新字段优先；缺少新字段时从旧字段
    ``plot_ready``、``gf_stage``、``gf_confirmed`` 推导。
    """
    if not isinstance(value, Mapping):
        return initial_state()
    state = deepcopy(dict(value))
    txt_uploaded = _infer_txt_uploaded(state)
    plot_ready = _bool(state.get("plot_ready"))
    gf_stage = str(state.get("gf_stage") or "pending").strip().lower()
    gf_confirmed = _bool(state.get("gf_confirmed")) or gf_stage in {"confirmed", "ready"}
    opening_confirmed = _bool(state.get("opening_confirmed"))
    started = _bool(state.get("started"))
    phase = _infer_phase(state, txt_uploaded, plot_ready, gf_confirmed, opening_confirmed, started)
    state.update({
        "schema": state.get("schema") or "fate-engine-opening-flow",
        "version": state.get("version") or 1,
        "phase": phase,
        "txt_uploaded": txt_uploaded,
        "plot_ready": plot_ready,
        "gf_stage": "confirmed" if gf_confirmed else ("pending" if gf_stage in {"", "confirmed", "ready"} else gf_stage),
        "gf_confirmed": gf_confirmed,
        "opening_confirmed": opening_confirmed,
        "started": started,
    })
    return state


def _result(state: Mapping[str, Any], ok: bool, code: str = "ok", **extra: Any) -> dict[str, Any]:
    normalized = normalize_state(state)
    return {
        "ok": ok,
        "allowed": ok,
        "code": code,
        "reason": "允许" if ok else _reason(code),
        "phase": normalized["phase"],
        "state": normalized,
        **extra,
    }


def gate(value: Optional[Mapping[str, Any]], event: str) -> dict[str, Any]:
    """检查事件门禁，不改变状态。"""
    state = normalize_state(value)
    if event not in {EVENT_UPLOAD_TXT, EVENT_EXTRACT_PLOT, EVENT_CONFIRM_GF,
                     EVENT_CONFIRM_OPENING, EVENT_START}:
        return _result(state, False, "unknown_event")
    if state["started"]:
        return _result(state, False, "already_started")
    if event == EVENT_UPLOAD_TXT:
        return _result(state, True)
    if not state["txt_uploaded"]:
        return _result(state, False, "txt_required")
    if event == EVENT_EXTRACT_PLOT:
        return _result(state, True)
    if not state["plot_ready"]:
        return _result(state, False, "plot_required")
    if event == EVENT_CONFIRM_GF:
        return _result(state, True)
    if not state["gf_confirmed"]:
        return _result(state, False, "gf_required")
    if event == EVENT_CONFIRM_OPENING:
        return _result(state, True)
    if not state["opening_confirmed"]:
        return _result(state, False, "opening_required")
    return _result(state, True)


def _apply(value: Optional[Mapping[str, Any]], event: str, **payload: Any) -> dict[str, Any]:
    checked = gate(value, event)
    if not checked["ok"]:
        return checked
    state = checked["state"]
    if event == EVENT_UPLOAD_TXT:
        txt = payload.get("txt_path", payload.get("source", payload.get("file")))
        if not _has_text(txt):
            return _result(state, False, "empty_txt")
        state.update({"txt_uploaded": True, "txt_path": str(txt), "phase": PHASE_TXT_UPLOADED})
        if "metadata" in payload:
            state["txt_metadata"] = deepcopy(payload["metadata"])
    elif event == EVENT_EXTRACT_PLOT:
        summary = payload.get("plot_summary", payload.get("summary"))
        if not _has_text(summary):
            return _result(state, False, "empty_plot")
        state.update({"plot_summary": deepcopy(summary), "plot_ready": True, "phase": PHASE_PLOT_READY})
    elif event == EVENT_CONFIRM_GF:
        if not _bool(payload.get("confirmed", payload.get("decision", False))):
            return _result(state, False, "invalid_confirmation")
        state.update({"gf_stage": "confirmed", "gf_confirmed": True,
                      "gf_decision": deepcopy(payload.get("gf_decision", payload.get("decision_data"))),
                      "phase": PHASE_GF_CONFIRMED})
    elif event == EVENT_CONFIRM_OPENING:
        if not _bool(payload.get("confirmed", payload.get("decision", False))):
            return _result(state, False, "invalid_confirmation")
        state.update({"opening_confirmed": True, "phase": PHASE_OPENING_CONFIRMED})
    elif event == EVENT_START:
        state.update({"started": True, "phase": PHASE_STARTED})
    return _result(state, True)


def transition(value: Optional[Mapping[str, Any]], event: str, payload: Optional[Mapping[str, Any]] = None,
               **kwargs: Any) -> dict[str, Any]:
    """执行一个开局事件，失败时返回原状态及明确原因。"""
    data = dict(payload or {})
    data.update(kwargs)
    return _apply(value, event, **data)


def mark_txt_uploaded(value: Optional[Mapping[str, Any]], txt_path: Any = None, **metadata: Any) -> dict[str, Any]:
    return transition(value, EVENT_UPLOAD_TXT, txt_path=txt_path, metadata=metadata or None)


def mark_plot_ready(value: Optional[Mapping[str, Any]], plot_summary: Any = None) -> dict[str, Any]:
    return transition(value, EVENT_EXTRACT_PLOT, plot_summary=plot_summary)


def confirm_golden_finger(value: Optional[Mapping[str, Any]], confirmed: bool = True,
                          gf_decision: Any = None) -> dict[str, Any]:
    return transition(value, EVENT_CONFIRM_GF, confirmed=confirmed, gf_decision=gf_decision)


def confirm_opening(value: Optional[Mapping[str, Any]], confirmed: bool = True) -> dict[str, Any]:
    return transition(value, EVENT_CONFIRM_OPENING, confirmed=confirmed)


def start_game(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    return transition(value, EVENT_START)


# 常见语义别名，便于调用方按旧代码命名迁移。
mark_plot_extracted = mark_plot_ready
confirm_gf = confirm_golden_finger
start = start_game


__all__ = [
    "PHASE_INIT", "PHASE_TXT_UPLOADED", "PHASE_PLOT_READY", "PHASE_GF_CONFIRMED",
    "PHASE_OPENING_CONFIRMED", "PHASE_STARTED", "PHASES", "EVENT_UPLOAD_TXT",
    "EVENT_EXTRACT_PLOT", "EVENT_CONFIRM_GF", "EVENT_CONFIRM_OPENING", "EVENT_START",
    "initial_state", "new_state", "normalize_state", "gate", "transition",
    "mark_txt_uploaded", "mark_plot_ready", "mark_plot_extracted", "confirm_golden_finger",
    "confirm_gf", "confirm_opening", "start_game", "start",
]
