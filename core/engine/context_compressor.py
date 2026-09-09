# -*- coding: utf-8 -*-
"""上下文压缩接手：每 10 回合把长历史压缩为接手摘要 + 末 4 条原文。

纯计算、无 IO、无模型调用：接手包完全从 state 读取组装，模型只负责把
``handoff_prompt`` 给出的接手包改写成摘要文本；压缩后的 history 写回与落盘
由集成层负责。
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

COMPRESS_INTERVAL = 10
KEEP_TAIL = 4
SUMMARY_PREFIX = "[接手摘要] "


def should_compress(round_no: int) -> bool:
    """round_no > 0 且为 10 的倍数时触发压缩。"""
    try:
        number = int(round_no)
    except (TypeError, ValueError):
        return False
    return number > 0 and number % COMPRESS_INTERVAL == 0


def _member_entries(pool: Any, active: Mapping[str, Any]) -> list[dict[str, Any]]:
    """伙伴/女主名称与参与度：优先读名册条目自带字段，其次读 active_members。"""
    entries = []
    for item in pool or ():
        if isinstance(item, Mapping):
            name = str(item.get("name") or "").strip()
            participation = item.get("participation", item.get("level"))
        else:
            name, participation = str(item).strip(), None
        if not name:
            continue
        if participation is None and name in active:
            participation = active[name]
        entries.append({"name": name, "participation": participation})
    return entries


def build_handoff(state: Mapping[str, Any]) -> dict:
    """代码组装接手包；全部从 state 读取，不调用模型。"""
    state = state if isinstance(state, Mapping) else {}
    distill = state.get("distill") if isinstance(state.get("distill"), Mapping) else {}
    timeline = state.get("anchor_timeline") if isinstance(state.get("anchor_timeline"), Mapping) else {}
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    ledger = state.get("ledger") if isinstance(state.get("ledger"), Mapping) else {}

    active_raw = state.get("active_members") or []
    active: dict[str, Any] = {}
    for item in active_raw:
        if isinstance(item, Mapping):
            name = str(item.get("name") or "").strip()
            if name:
                active[name] = item.get("participation", item.get("level"))

    ledger_summary: dict[str, Any] = {}
    for field, value in ledger.items():
        count = len(value) if isinstance(value, (list, dict)) else (0 if value in (None, "") else 1)
        ledger_summary[field] = {"count": count}
    ripples = state.get("ripples") or ledger.get("ripples") or []
    ledger_summary["recent_ripples"] = [deepcopy(r) for r in ripples[-3:]]

    character_states = {
        "relationships": deepcopy(memory.get("relationships") or {}),
        "goals": deepcopy(memory.get("goals") or {}),
        "location": deepcopy(memory.get("location") or {}),
        "body": deepcopy(memory.get("body") or {}),
        "companions": _member_entries(state.get("companions"), active),
        "heroines": _member_entries(state.get("heroines"), active),
        "nemesis_summary": deepcopy(state.get("nemesis_summary")),
    }

    return {
        "round": int(state.get("round", 0) or 0),
        "plot_summary": deepcopy(distill.get("plot_summary")),
        "anchors_recent": {
            "past": deepcopy(timeline.get("past") or []),
            "current": deepcopy(timeline.get("current")),
            "upcoming": deepcopy(timeline.get("upcoming") or []),
        },
        "character_states": character_states,
        "ledger_summary": ledger_summary,
        "quest": deepcopy(state.get("quest")),  # engine.quest 接口预留
        "convergence": deepcopy(state.get("convergence_state", state.get("convergence"))),
    }


def handoff_prompt(handoff: Mapping[str, Any]) -> str:
    """让子智能体把接手包写成剧情摘要的提示词（锚点与角色状态事实必须保留）。"""
    payload = json.dumps(handoff or {}, ensure_ascii=False, indent=2, default=str)
    return (
        "你是剧情接手摘要器。请把下面的接手包改写成一段连贯的剧情摘要，供下一轮"
        "叙事无缝接手。硬性要求：\n"
        "1. 每个锚点标题（past/current/upcoming）都必须原样出现；\n"
        "2. 主要角色（伙伴/女主/宿敌）的名字与其当前状态事实必须保留，不得捏造；\n"
        "3. 若 quest 存在，其目标必须原样保留；\n"
        "4. 只依据接手包内容，不得补写不存在的事实。\n\n"
        f"【接手包】\n{payload}"
    )


def apply_handoff(state: Mapping[str, Any], summary_text: str) -> dict:
    """返回压缩后的新 history 与 compression_record；写回 state 由集成层负责。"""
    state = state if isinstance(state, Mapping) else {}
    history = [item for item in (state.get("history") or []) if isinstance(item, Mapping)]
    kept = [dict(item) for item in history[-KEEP_TAIL:]]
    new_history = [{"role": "assistant", "content": SUMMARY_PREFIX + str(summary_text or "").strip()}] + kept
    round_no = int(state.get("round", 0) or 0)
    record = {
        "round": round_no,
        "compressed_at": round_no,
        "kept_messages": len(kept),
    }
    return {"history": new_history, "compression_record": record}


def estimate_tokens(text: str) -> int:
    """粗略 Token 估计（中英混合按 2 字符 1 token）。"""
    return len(str(text or "")) // 2


def _anchor_titles(anchors: Mapping[str, Any]) -> list[str]:
    titles = []
    for item in anchors.get("past") or ():
        if isinstance(item, Mapping):
            titles.append(str(item.get("title") or "").strip())
    current = anchors.get("current")
    if isinstance(current, Mapping):
        titles.append(str(current.get("title") or "").strip())
    for item in anchors.get("upcoming") or ():
        if isinstance(item, Mapping):
            titles.append(str(item.get("title") or "").strip())
    return [t for t in titles if t]


def _quest_facts(quest: Any) -> list[str]:
    if isinstance(quest, Mapping):
        facts = []
        for key in ("goal", "title", "objective"):
            value = str(quest.get(key) or "").strip()
            if value:
                facts.append(value)
        return facts
    text = str(quest or "").strip()
    return [text] if text else []


def fidelity_check(before_state: Mapping[str, Any], new_history: Sequence[Mapping[str, Any]],
                   handoff: Mapping[str, Any]) -> dict:
    """断言锚点标题、主要角色名、quest 目标在压缩结果中仍可检索。"""
    del before_state  # 事实清单全部来自 handoff（其本身即从 before_state 读出）
    handoff = handoff if isinstance(handoff, Mapping) else {}
    haystack = json.dumps(list(new_history or []), ensure_ascii=False, default=str)

    required: list[str] = []
    anchors = handoff.get("anchors_recent") if isinstance(handoff.get("anchors_recent"), Mapping) else {}
    required.extend(_anchor_titles(anchors))
    characters = handoff.get("character_states") if isinstance(handoff.get("character_states"), Mapping) else {}
    for key in ("companions", "heroines"):
        for item in characters.get(key) or ():
            if isinstance(item, Mapping):
                name = str(item.get("name") or "").strip()
                if name:
                    required.append(name)
    required.extend(_quest_facts(handoff.get("quest")))

    missing = [fact for fact in dict.fromkeys(required) if fact not in haystack]
    return {"ok": not missing, "missing": missing}


def handoff_for_save(state: Mapping[str, Any]) -> dict:
    """给 persistence 附加入档的轻量接手包：复用 build_handoff，去掉大文本。"""
    handoff = build_handoff(state)
    handoff.pop("plot_summary", None)
    ledger_summary = handoff.get("ledger_summary")
    if isinstance(ledger_summary, dict):
        ledger_summary.pop("recent_ripples", None)
    return handoff


__all__ = [
    "COMPRESS_INTERVAL", "KEEP_TAIL", "SUMMARY_PREFIX", "should_compress",
    "build_handoff", "handoff_prompt", "apply_handoff", "estimate_tokens",
    "fidelity_check", "handoff_for_save",
]
