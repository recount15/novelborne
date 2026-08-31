# -*- coding: utf-8 -*-
"""宿敌自主决策：私密状态容器、按难度分级的信息视野与可复现的失真摘要。

纯计算、无 IO、无模型调用。宿敌的全部私密信息只写入 ``state["nemesis_private"]``，
该键绝不允许进入公开状态：集成层负责在 ``api/contracts.py`` 剥离，本模块提供
``PUBLIC_BLOCK_KEYS`` 常量与 ``assert_hidden`` 防御函数做最后一道校验。

宿敌的任务系统由 ``engine.quest``（并行模块）承载，本模块只在私密容器中预留
``quest`` 字段，不实现任何任务逻辑。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any, Mapping, Sequence

from .options import OPTION_KEYS
from .ripple import difficulty_number

# 绝不允许进入公开状态的私密键；集成层剥离 + assert_hidden 双重防线。
PUBLIC_BLOCK_KEYS = ("nemesis_private",)

_PRIVATE_KEY = "nemesis_private"
_SUMMARY_KEY = "nemesis_summary"
_LOG_LIMIT = 30
_SUMMARY_WINDOW = 5
# 每个选项字母的资源消耗（A=1 … F=6），代码确定性决定，不让模型自由裁量。
_RESOURCE_KEY = "资源点"

# 信息视野档位阈值（与 player_info_level 的 D1→0.2 … D9→1.0 线性映射配套）。
_TIER_LOCATION = 0.3
_TIER_GOALS = 0.5
_TIER_COMPANIONS = 0.6
_TIER_GOLDEN_FINGER = 0.8
_TIER_RECENT_ACTIONS = 0.9

# 摘要失真档位阈值。
_DISTORT_NUMBERS = 0.25
_DISTORT_PLACES = 0.5
_DISTORT_SHUFFLE = 0.75

_FENCE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE_SPLIT = re.compile(r"([；;。])")


def init_private(state: dict, nemesis_config: Mapping[str, Any] | None = None) -> dict:
    """初始化（或重置）宿敌私密状态容器并写回 state，返回该容器。"""
    config = nemesis_config if isinstance(nemesis_config, Mapping) else {}
    resources = config.get("resources")
    private = {
        "name": str(config.get("name") or "宿敌"),
        "goal": str(config.get("goal") or ""),
        "plan": str(config.get("plan") or ""),
        "resources": dict(resources) if isinstance(resources, Mapping) else {},
        "location": str(config.get("location") or ""),
        "quest": None,  # 任务系统接口预留，由 engine.quest 集成时填充。
        "log": [],
    }
    state[_PRIVATE_KEY] = private
    return private


def _private(state: Mapping[str, Any]) -> dict:
    box = state.get(_PRIVATE_KEY) if isinstance(state, Mapping) else None
    return box if isinstance(box, dict) else {}


def assert_hidden(public_state_dict: Mapping[str, Any]) -> bool:
    """防御函数：公开状态中一旦出现私密键立即抛 AssertionError。"""
    if not isinstance(public_state_dict, Mapping):
        raise AssertionError("公开状态必须是映射对象")
    for key in PUBLIC_BLOCK_KEYS:
        if key in public_state_dict:
            raise AssertionError(f"私密键 {key} 泄露到公开状态")
    return True


def _difficulty_float(value: int | float | str) -> float:
    """从难度表述提取浮点值（如 D6.35→6.35），取不到时按 4.0。"""
    text = str(value or "")
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if nums:
        return max(0.01, min(9.99, float(nums[0])))
    return 4.0


def player_info_level(difficulty: int | float | str) -> float:
    """难度 D0.01→0.2 到 D9.99→1.0 线性映射，决定宿敌能看到多少玩家信息。"""
    number = _difficulty_float(difficulty)
    return round(0.2 + (number - 0.01) * (1.0 - 0.2) / (9.99 - 0.01), 4)


def _names(pool: Any) -> list[str]:
    names = []
    for item in pool or ():
        name = str(item.get("name") if isinstance(item, Mapping) else item).strip()
        if name:
            names.append(name)
    return names


def build_nemesis_context(state: Mapping[str, Any], info_level: float) -> dict:
    """按 info_level 从玩家 state 摘取信息，供集成层组装宿敌子智能体提示词。

    档位越高信息越具体：位置→目标→伙伴→金手指→最近行动逐层解锁；
    低档位只有模糊传闻级信息（rumor 字段始终存在）。
    """
    state = state if isinstance(state, Mapping) else {}
    level = max(0.0, min(1.0, float(info_level)))
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    context: dict[str, Any] = {
        "info_level": level,
        "rumor": "坊间隐约传来玩家阵营的只言片语，真假难辨。",
    }
    if level >= _TIER_LOCATION:
        location = memory.get("location") if isinstance(memory.get("location"), Mapping) else {}
        context["location"] = str(location.get("name") or "").strip() or None
    if level >= _TIER_GOALS:
        goals = memory.get("goals") if isinstance(memory.get("goals"), Mapping) else {}
        context["goals"] = [str(g) for g in (goals.get("current") or [])][:3]
    if level >= _TIER_COMPANIONS:
        context["companions"] = _names(state.get("companions")) + _names(state.get("heroines"))
    if level >= _TIER_GOLDEN_FINGER:
        abilities = memory.get("abilities") if isinstance(memory.get("abilities"), Mapping) else {}
        gf = abilities.get("golden_finger") if isinstance(abilities.get("golden_finger"), Mapping) else {}
        name = str(gf.get("name") or "").strip()
        context["golden_finger"] = None if (not name or name.startswith("无")) else name
    if level >= _TIER_RECENT_ACTIONS:
        history = state.get("history") or []
        actions = [
            str(item.get("content") or "").strip()[:60]
            for item in history
            if isinstance(item, Mapping) and item.get("role") == "user"
        ]
        context["recent_actions"] = actions[-3:]
    return context


def build_nemesis_prompt(context: Mapping[str, Any], nemesis_private: Mapping[str, Any]) -> str:
    """组装宿敌自主决策提示词，要求输出严格 JSON。"""
    context_json = json.dumps(context or {}, ensure_ascii=False, indent=2)
    private = nemesis_private if isinstance(nemesis_private, Mapping) else {}
    return (
        f"你是宿敌「{private.get('name') or '宿敌'}」的自主决策体，目标是：{private.get('goal') or '未设定'}。\n"
        f"你当前的计划：{private.get('plan') or '暂无'}；所在：{private.get('location') or '不明'}；"
        f"资源：{json.dumps(private.get('resources') or {}, ensure_ascii=False)}。\n"
        "以下是你能掌握的玩家情报（视野受难度限制）：\n"
        f"{context_json}\n\n"
        "请以宿敌视角判断局势并自主做出一个选择，只输出严格 JSON，不要输出任何其他文字：\n"
        '{"situation": "宿敌视角的当前情景", '
        '"options": [{"key": "A", "text": "行动方案"}, …（A–F 至少 2 个至多 6 个）], '
        '"choice": "A"（必须是 options 中出现的 key）, '
        '"rationale": "这样选择的理由"}'
    )


def parse_nemesis_turn(text: str) -> dict:
    """剥 fence 解析宿敌回合 JSON 并校验；choice 必须在 options 的 key 集合内。"""
    content = str(text or "").strip()
    match = _FENCE.search(content)
    if match:
        content = match.group(1).strip()
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"宿敌回合不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("宿敌回合必须是 JSON 对象")
    situation = str(data.get("situation") or "").strip()
    if not situation:
        raise ValueError("宿敌回合缺少 situation")
    options = data.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("宿敌回合缺少 options")
    parsed_options: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in options:
        if not isinstance(item, Mapping):
            raise ValueError("宿敌选项必须是对象")
        key = str(item.get("key") or "").strip().upper()
        body = str(item.get("text") or "").strip()
        if key not in OPTION_KEYS or not body:
            raise ValueError(f"宿敌选项非法：key={key!r}")
        if key in seen:
            raise ValueError(f"宿敌选项 key 重复：{key}")
        seen.add(key)
        parsed_options.append({"key": key, "text": body})
    choice = str(data.get("choice") or "").strip().upper()
    if choice not in seen:
        raise ValueError(f"宿敌选择 {choice!r} 不在 options 的 key 集合内")
    rationale = data.get("rationale")
    if rationale is None or not isinstance(rationale, (str, int, float)):
        raise ValueError("宿敌回合缺少 rationale")
    return {
        "situation": situation,
        "options": parsed_options,
        "choice": choice,
        "rationale": str(rationale).strip(),
    }


def apply_nemesis_choice(state: dict, turn: Mapping[str, Any]) -> dict:
    """把宿敌选择结果确定性写入 nemesis_private，返回对玩家世界的渗漏事件。

    plan 更新为所选方案文本；资源按字母序确定性消耗（A=1 … F=6）；log 保留最近
    30 条。哪些影响泄露到玩家叙事由代码决定：资源耗尽或大手笔行动（E/F）产生
    模糊传闻级渗漏，其余返回 None。
    """
    private = _private(state)
    if not private:
        private = init_private(state if isinstance(state, dict) else {})
    turn = turn if isinstance(turn, Mapping) else {}
    choice = str(turn.get("choice") or "").strip().upper()
    options = turn.get("options") if isinstance(turn.get("options"), Sequence) else []
    chosen = next(
        (item for item in options
         if isinstance(item, Mapping) and str(item.get("key") or "").strip().upper() == choice),
        None,
    )
    if choice not in OPTION_KEYS or chosen is None:
        raise ValueError(f"宿敌选择 {choice!r} 不在 options 的 key 集合内")
    chosen_text = str(chosen.get("text") or "").strip()
    private["plan"] = chosen_text

    cost = OPTION_KEYS.index(choice) + 1
    resources = private.setdefault("resources", {})
    remaining = int(resources.get(_RESOURCE_KEY, 0) or 0) - cost
    resources[_RESOURCE_KEY] = max(0, remaining)

    log = private.setdefault("log", [])
    log.append({
        "round": int(state.get("round", 0) or 0) if isinstance(state, Mapping) else 0,
        "choice": choice,
        "text": chosen_text,
        "rationale": str(turn.get("rationale") or "").strip(),
        "cost": cost,
    })
    del log[:-_LOG_LIMIT]

    name = private.get("name") or "宿敌"
    leak = None
    if remaining < 0:
        leak = f"有消息称{name}四处搜刮资源、捉襟见肘，似乎在强撑某个大计划。"
    elif choice in ("E", "F"):
        leak = f"坊间传闻{name}在某处频频调动人手，似有大动作。"
    return {"leak": leak}


def _stable_seed(name: str, round_no: int) -> int:
    digest = hashlib.md5(f"{name}:{round_no}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _place_names(state: Mapping[str, Any], private: Mapping[str, Any]) -> list[str]:
    names = []
    own = str(private.get("location") or "").strip()
    if own:
        names.append(own)
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    location = memory.get("location") if isinstance(memory.get("location"), Mapping) else {}
    other = str(location.get("name") or "").strip()
    if other:
        names.append(other)
    return [n for n in dict.fromkeys(names) if len(n) >= 2]


def _distort(text: str, distortion: float, seed: int, places: Sequence[str]) -> str:
    """确定性降级文本：删数字→删地名→打乱非关键句序，同参数必然同结果。"""
    out = text
    if distortion >= _DISTORT_NUMBERS:
        out = _NUMBER.sub("若干", out)
    if distortion >= _DISTORT_PLACES:
        for name in places:
            out = out.replace(name, "某处")
    if distortion >= _DISTORT_SHUFFLE:
        parts = _SENTENCE_SPLIT.split(out)
        sentences = ["".join(parts[i:i + 2]) for i in range(0, len(parts), 2)]
        sentences = [s for s in sentences if s.strip()]
        if len(sentences) > 2:
            head, tail = sentences[0], sentences[1:]
            random.Random(seed).shuffle(tail)
            out = "".join([head] + tail)
    return out


def summarize_nemesis(state: dict, difficulty: int | float | str) -> dict:
    """每 5 回合由集成层调用：生成宿敌动向摘要并按难度确定性失真。

    失真度 distortion = clamp((D-0.01)/(9.99-0.01), 0, 1)，越高越失真；以
    hash(宿敌名+回合) 做种子保证同参数可复现。结果写入 state["nemesis_summary"]。
    """
    number = _difficulty_float(difficulty)
    distortion = max(0.0, min(1.0, (number - 0.01) / (9.99 - 0.01)))
    round_no = int(state.get("round", 0) or 0) if isinstance(state, Mapping) else 0
    private = _private(state)
    name = private.get("name") or "宿敌"
    entries = (private.get("log") or [])[-_SUMMARY_WINDOW:]
    if entries:
        base = f"宿敌{name}近五回合动向：" + "；".join(
            f"第{e.get('round', '?')}回合{e.get('choice', '?')}：{e.get('text', '')}"
            for e in entries
        ) + "。"
    else:
        base = f"宿敌{name}近五回合暂无公开动向。"
    text = _distort(base, distortion, _stable_seed(str(name), round_no),
                    _place_names(state, private))
    summary = {"round": round_no, "text": text, "distortion": round(distortion, 4)}
    if isinstance(state, dict):
        state[_SUMMARY_KEY] = summary
    return summary


def nemesis_public_view(state: Mapping[str, Any]) -> dict:
    """玩家可见视图：只含最近一条 5 回合摘要，绝不包含 nemesis_private。"""
    summary = state.get(_SUMMARY_KEY) if isinstance(state, Mapping) else None
    return {"summary": summary if isinstance(summary, dict) else None}


__all__ = [
    "PUBLIC_BLOCK_KEYS", "init_private", "assert_hidden", "player_info_level",
    "build_nemesis_context", "build_nemesis_prompt", "parse_nemesis_turn",
    "apply_nemesis_choice", "summarize_nemesis", "nemesis_public_view",
]
