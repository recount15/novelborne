# -*- coding: utf-8 -*-
"""确定性模块化上下文组装。

本模块只从运行态读取数据并渲染文本，不做 IO、网络或模型调用。每个模块有
独立字符预算，最终上下文还有总预算；因此旧存档缺字段或携带异常对象时也能
稳定地产出可供提示词使用的上下文。
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

# 字符预算（中文字符近似 token 的保守上界）；这些是接口的硬约束。
SECTION_BUDGETS: dict[str, int] = {
    "work": 1800,
    "style": 1000,
    "anchors": 1800,
    "characters": 2200,
    "state": 3000,
    "recent": 3500,
    "sample": 1200,
    "inputs": 2500,
}
TOTAL_BUDGET = 12000
DEFAULT_RECENT_ROUNDS = 10


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:max(0, int(limit))]


def _json(value: Any, limit: int = 2000) -> str:
    """Compact, deterministic rendering for mappings/lists and odd legacy values."""
    if value in (None, "", [], {}):
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return _clip(text, limit)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(state: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = state.get(key)
        if value not in (None, "", [], {}):
            return value
    params = _mapping(state.get("start_params"))
    for key in keys:
        value = params.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def build_recent_digest(state: Mapping[str, Any] | None, rounds: int = DEFAULT_RECENT_ROUNDS) -> str:
    """Render at most ``rounds`` recent conversation turns from ``state['history']``.

    A turn is counted at each assistant message; a trailing user message is retained
    as the current action. Invalid history entries are ignored and no state is changed.
    """
    state = state if isinstance(state, Mapping) else {}
    try:
        limit = max(0, min(DEFAULT_RECENT_ROUNDS, int(rounds)))
    except (TypeError, ValueError):
        limit = DEFAULT_RECENT_ROUNDS
    if not limit:
        return ""
    history = [item for item in (state.get("history") or ()) if isinstance(item, Mapping)]
    selected: list[Mapping[str, Any]] = []
    assistant_count = 0
    for item in reversed(history):
        role = str(item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        selected.append(item)
        if role == "assistant":
            assistant_count += 1
            if assistant_count >= limit:
                break
    selected.reverse()
    # Include the player action immediately preceding the oldest retained result.
    # This keeps each retained assistant result interpretable without adding a turn.
    if selected and str(selected[0].get("role") or "").lower() == "assistant":
        for index, item in enumerate(history):
            if item is selected[0] and index > 0:
                previous = history[index - 1]
                if str(previous.get("role") or "").lower() == "user":
                    selected.insert(0, previous)
                break
    if assistant_count == 0:
        # Legacy/imported histories occasionally contain only player messages.
        selected = selected[-limit:]
    if not selected:
        return ""
    lines = []
    for item in selected:
        role = "玩家" if str(item.get("role")).lower() == "user" else "叙事"
        content = _clip(item.get("content"), 500)
        if content:
            lines.append(f"{role}：{content}")
    return _clip("\n".join(lines), SECTION_BUDGETS["recent"])


def _section(title: str, body: Any, budget: int) -> str:
    prefix = f"【{title}】\n"
    text = _clip(body, max(0, int(budget) - len(prefix)))
    return prefix + text if text else ""


def _quest_context_block(state: Mapping[str, Any]) -> str:
    """构建进行中任务的紧凑上下文块（单源复用：段卷/导演卷/选项卷）。
    
    返回格式：
    【进行中任务】标题：目标｜剩余X回合
    完成条件：条件1；条件2｜完成奖励（由系统发放）：积势×2点、物资×1件
    """
    from ..engine import quest as quest_module
    
    quest_box = state.get("quest")
    if not isinstance(quest_box, Mapping) or quest_box.get("status") != "active":
        return ""
    
    current_round = int(state.get("round", 0) or 0)
    return quest_module.quest_context_block(state, current_round)


def _anchor_text(state: Mapping[str, Any], supplied: str) -> str:
    if supplied:
        return str(supplied)
    timeline = _mapping(state.get("anchor_timeline"))
    parts: list[str] = []
    for label, value in (("过去", timeline.get("past")),
                         ("当前", timeline.get("current")),
                         ("后续", timeline.get("upcoming"))):
        values = value if isinstance(value, list) else [value]
        for item in values:
            rendered = _json(item, 650)
            if rendered:
                parts.append(f"{label}：{rendered}")
    anchors = state.get("anchors")
    if anchors and not parts:
        parts.append(_json(anchors, 1800))
    return "\n".join(parts)


def _character_contracts(state: Mapping[str, Any], active_members: Sequence[Any]) -> str:
    members = list(active_members or ())
    if not members:
        raw = state.get("active_members") or state.get("active_characters") or ()
        members = list(raw) if isinstance(raw, (list, tuple)) else []
    summaries = _mapping(state.get("active_summaries"))
    lines: list[str] = []
    for item in members:
        if isinstance(item, Mapping):
            name = _clip(item.get("name") or item.get("character") or "", 80)
            contract = {key: item.get(key) for key in (
                "role", "role_type", "participation", "skill", "background",
                "character_card", "contract", "speech_style", "goal", "fear")
                        if item.get(key) not in (None, "", [], {})}
        else:
            name, contract = _clip(item, 80), {}
        if not name:
            continue
        summary = summaries.get(name, "")
        suffix = _json(contract, 650)
        if summary:
            suffix += ("；" if suffix else "") + _clip(summary, 350)
        lines.append(f"- {name}" + (f"：{suffix}" if suffix else ""))
    return "\n".join(lines)


def build_modular_context(state: Mapping[str, Any] | None, message: str = "",
                          system_prompt: str = "", anchor_text: str = "",
                          active_members: Sequence[Any] = (),
                          context_blocks: str = "") -> str:
    """Assemble bounded work, style, plot and runtime context for a model prompt."""
    state = state if isinstance(state, Mapping) else {}
    memory = _mapping(state.get("state_memory"))
    ripple = state.get("last_ripple") or state.get("ripple")
    quest = state.get("quest")
    convergence = state.get("convergence_state") or state.get("convergence")
    abilities = _mapping(memory.get("abilities"))
    golden = abilities.get("golden_finger") or state.get("golden_finger") or _first(state, "golden_finger")

    work = _first(state, "work_context", "work", "novel", "work_label", "title")
    system = system_prompt or state.get("system") or state.get("system_context")
    work_body = "\n".join(part for part in (
        f"作品：{_clip(work, 500)}" if work else "",
        f"系统：{_clip(system, 1200)}" if system else "",
        f"模式：{_clip(state.get('mode'), 100)}" if state.get("mode") else "",
    ) if part)

    language = _first(state, "language_style", "style", "last_style")
    pacing = _first(state, "pacing", "pacing_hint", "last_pacing")
    style_body = "\n".join(part for part in (
        f"语言风格：{_json(language, 600)}" if language else "",
        f"节奏：{_json(pacing, 350)}" if pacing else "",
    ) if part)

    # 任务块单独提取（紧凑可读格式，优先级高）
    quest_block = _quest_context_block(state)
    
    state_body = "\n".join(part for part in (
        f"state_memory：{_json(memory, 1100)}" if memory else "",
        f"ripple：{_json(ripple, 500)}" if ripple else "",
        quest_block,  # 使用紧凑格式替代 JSON
        f"convergence：{_json(convergence, 450)}" if convergence else "",
        f"golden_finger：{_json(golden, 400)}" if golden else "",
    ) if part)

    sample = _first(state, "original_text_style_sample", "style_sample", "original_text", "novel_excerpt")
    inputs = "\n".join(part for part in (
        f"玩家行动：{_clip(message, 900)}" if message else "",
        f"补充上下文：{_clip(context_blocks, 1600)}" if context_blocks else "",
    ) if part)
    sections = [
        _section("作品与系统", work_body, SECTION_BUDGETS["work"]),
        _section("语言风格与节奏", style_body, SECTION_BUDGETS["style"]),
        _section("锚点", _anchor_text(state, anchor_text), SECTION_BUDGETS["anchors"]),
        _section("在场角色合约", _character_contracts(state, active_members), SECTION_BUDGETS["characters"]),
        _section("运行态记忆", state_body, SECTION_BUDGETS["state"]),
        _section("最近回合摘要", build_recent_digest(state), SECTION_BUDGETS["recent"]),
        _section("原文风格样本", sample, SECTION_BUDGETS["sample"]),
        _section("本回合输入", inputs, SECTION_BUDGETS["inputs"]),
    ]
    result = "\n\n".join(section for section in sections if section)
    return result[:TOTAL_BUDGET]


__all__ = ["SECTION_BUDGETS", "TOTAL_BUDGET", "build_recent_digest", "build_modular_context"]
