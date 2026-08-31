"""选项因素排查与字母选项（A–F）解析管线。

纯计算、无 IO、无模型调用：同样的 state/文本必然得到同样的因素列表与解析
结果，便于回放与离线测试。因素排查回答「当前局面里有哪些可用素材」；解析器
负责把模型输出的 A–F 选项块结构化，并支持把选项块从叙事正文中剥离。
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

OPTION_KEYS = ("A", "B", "C", "D", "E", "F")

# 处于开局确认阶段的回复不含选项，属正常，不做补发（与 fate_engine 数字版一致）。
_CONFIRM_MARKERS = ("是否确认", "请确认", "开局核对", "确认后开始", "待你确认", "等你确认",
                    "候选", "是否开始")

# 字母编号选项行：兼容 "A. "、"A、"、"【A】"、"A)"、"（A）"、"**A.**"、全角 Ａ–Ｆ。
_OPTION_LINE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]*)?(?:\*\*|__)?[【\[（(]?[ \t]*([A-Fa-fＡ-Ｆａ-ｆ])[ \t]*"
    r"(?:[】\]）)]|[\.、．:：])[ \t]*(?:\*\*|__)?[ \t]*\S",
    re.M,
)
# 数字编号容错行：模型偶发回退为 1–6 编号时仍能解析，键统一映射为 A–F。
_DIGIT_LINE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]*)?(?:\*\*|__)?[（(]?[ \t]*([1-6])[ \t]*(?:[）)]|[\.、．:：])[ \t]*\S",
    re.M,
)

# 选项正文起始处的编号残余符号（括号、分隔符、加粗标记）统一剥掉。
_LEADING_JUNK = re.compile(r"^[*_【】\[\]（）()\s\.、．:：]+")


def _normalize_key(raw: str) -> str:
    ch = str(raw or "").strip()[:1]
    if not ch:
        return ""
    code = ord(ch)
    if 0xFF21 <= code <= 0xFF26:  # Ａ–Ｆ
        return chr(code - 0xFF21 + ord("A"))
    if 0xFF41 <= code <= 0xFF46:  # ａ–ｆ
        return chr(code - 0xFF41 + ord("A"))
    return ch.upper() if ch.upper() in OPTION_KEYS else ""


def _line_tail(text: str, start: int) -> str:
    end = text.find("\n", start)
    return text[start:end if end >= 0 else len(text)]


def parse_options(text: str) -> list[dict[str, str]]:
    """解析文本中的字母编号选项块，返回 [{key, text}]，键固定为 A–F。

    优先识别字母编号；一个字母都没有时容错识别 1–6 数字编号并映射为 A–F。
    同一字母重复出现时保留第一次出现。
    """
    content = str(text or "")
    found: dict[str, str] = {}
    for match in _OPTION_LINE.finditer(content):
        key = _normalize_key(match.group(1))
        if key and key not in found:
            tail = _line_tail(content, match.start())
            head = tail.find(match.group(1))
            found[key] = _LEADING_JUNK.sub("", tail[head + 1:]).strip()
    if not found:
        for match in _DIGIT_LINE.finditer(content):
            key = OPTION_KEYS[int(match.group(1)) - 1]
            if key not in found:
                tail = _line_tail(content, match.start())
                head = tail.find(match.group(1))
                found[key] = _LEADING_JUNK.sub("", tail[head + 1:]).strip()
    return [{"key": key, "text": found[key]} for key in OPTION_KEYS if key in found]


def count_options(text: str) -> int:
    return len(parse_options(text))


def is_confirmation_stage(text: str) -> bool:
    return any(marker in (text or "") for marker in _CONFIRM_MARKERS)


def options_ok(text: str, need: int = 6) -> bool:
    """字母版校验：叙述回合是否给出了至少 need 个选项。确认阶段直接合格。"""
    if is_confirmation_stage(text):
        return True
    return count_options(text) >= need


def _first_option_offset(text: str) -> int | None:
    """返回选项块第一行的起始偏移；字母与数字编号都认。"""
    letter = _OPTION_LINE.search(text)
    digit = _DIGIT_LINE.search(text)
    starts = [m.start() for m in (letter, digit) if m]
    return min(starts) if starts else None


def truncate_partial_options(text: str) -> str:
    """截掉末尾残缺的选项块，保留剧情部分；仅当选项块位于后半段时才截断。"""
    content = str(text or "")
    first = _first_option_offset(content)
    if first is None or first < len(content) // 2:
        return content
    cut = content.rfind("\n", 0, first)
    return content[:cut if cut > 0 else first].rstrip()


def strip_options_block(text: str) -> str:
    """把完整或残缺的选项块从叙事正文中剥离（选项由前端按钮渲染）。

    选项块按规则固定出现在回复末尾；为避免误伤正文中偶发的字母编号，
    只在选项块起始位置超过全文三分之一时才剥离。
    """
    content = str(text or "")
    first = _first_option_offset(content)
    if first is None or first < len(content) // 3:
        return content.rstrip()
    cut = content.rfind("\n", 0, first)
    return content[:cut if cut > 0 else first].rstrip()


def render_options_block(options: Sequence[Mapping[str, Any]]) -> str:
    """把结构化选项渲染为纯文本列表，供旧界面继续展示文本选项。"""
    lines = []
    for item in options or ():
        key = str(item.get("key") or "").strip().upper()
        body = str(item.get("text") or "").strip()
        if key and body:
            lines.append(f"{key}. {body}")
    return "\n".join(lines)


# ---------- 选项可用因素排查 ----------


def _text(value: Any, limit: int = 60) -> str:
    return str(value or "").strip()[:limit]


def _factor(kind: str, label: str, detail: str = "") -> dict[str, str]:
    return {"kind": kind, "label": _text(label, 40), "detail": _text(detail, 120)}


def collect_option_factors(state: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """排查当前局面中可供选项利用的因素，返回 [{kind, label, detail}]。

    覆盖：主角性格 persona、金手指名称/冷却、在场伙伴/女主、宿敌、当前锚点、
    难度、最近涟漪等级、状态记忆中的 location 与 goals。缺项自动跳过。
    """
    state = state if isinstance(state, Mapping) else {}
    factors: list[dict[str, str]] = []
    start_params = state.get("start_params") if isinstance(state.get("start_params"), Mapping) else {}
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}

    persona = _text(state.get("persona") or start_params.get("persona"), 40)
    if persona:
        factors.append(_factor("persona", persona, "主角性格，可用于性格向选项"))

    abilities = memory.get("abilities") if isinstance(memory.get("abilities"), Mapping) else {}
    gf = abilities.get("golden_finger") if isinstance(abilities.get("golden_finger"), Mapping) else {}
    gf_name = _text(gf.get("name") or start_params.get("golden_finger"), 40)
    if gf_name and not gf_name.startswith("无"):
        cooldown = gf.get("cooldown", 0) or 0
        detail = f"金手指，冷却 {cooldown}" + ("，当前被封印" if gf.get("status") == "inactive" else "")
        factors.append(_factor("golden_finger", gf_name, detail))

    for key, kind, desc in (("companions", "companion", "在场伙伴"), ("heroines", "heroine", "在场女主")):
        pool = state.get(key) or []
        for item in pool:
            name = _text(item.get("name") if isinstance(item, Mapping) else item, 40)
            if name:
                factors.append(_factor(kind, name, desc))

    if state.get("nemesis"):
        label = _text(start_params.get("nemesis") or "宿敌", 40)
        factors.append(_factor("nemesis", label, "宿敌动向可制造冲突选项"))

    timeline = state.get("anchor_timeline") if isinstance(state.get("anchor_timeline"), Mapping) else {}
    current_anchor = timeline.get("current") if isinstance(timeline.get("current"), Mapping) else {}
    anchor_title = _text(current_anchor.get("title"), 40)
    if anchor_title:
        factors.append(_factor("anchor", anchor_title, "当前锚点，选项应推动收束"))

    difficulty = _text(start_params.get("difficulty"), 20)
    if difficulty:
        factors.append(_factor("difficulty", difficulty, "当前难度，决定代价量级"))

    ripple = state.get("last_ripple") if isinstance(state.get("last_ripple"), Mapping) else {}
    ripple_level = _text(ripple.get("level"), 10)
    if ripple_level:
        total = ripple.get("effective_total", ripple.get("total", 0)) or 0
        threshold = ripple.get("threshold", 0) or 0
        factors.append(_factor("ripple", f"涟漪{ripple_level}", f"有效积势 {total}/{threshold}"))

    location = memory.get("location") if isinstance(memory.get("location"), Mapping) else {}
    location_name = _text(location.get("name"), 40)
    if location_name:
        factors.append(_factor("location", location_name, "当前所在地点"))

    goals = memory.get("goals") if isinstance(memory.get("goals"), Mapping) else {}
    for goal in (goals.get("current") or [])[:2]:
        goal_text = _text(goal, 40)
        if goal_text:
            factors.append(_factor("goal", goal_text, "当前目标"))

    return factors


def build_option_factors_block(factors: Sequence[Mapping[str, Any]]) -> str:
    """把因素列表装配为生成提示约束文本；无因素时返回空串。"""
    rows = []
    for item in factors or ():
        label = _text(item.get("label"), 40)
        detail = _text(item.get("detail"), 120)
        if label:
            rows.append(f"- {label}" + (f"（{detail}）" if detail else ""))
    if not rows:
        return ""
    return (
        "【选项可用因素】本回合的 6 个选项需体现以下可用因素中的若干项，"
        "让选项与当前局面素材挂钩，不得凭空捏造未登记的人物或能力：\n"
        + "\n".join(rows)
    )


def match_option_factors(option_text: str, factors: Sequence[Mapping[str, Any]]) -> list[str]:
    """判定单个选项命中了哪些因素：因素 label 出现在选项文本中即命中。"""
    content = str(option_text or "")
    hits = []
    for item in factors or ():
        label = _text(item.get("label"), 40)
        if label and len(label) >= 2 and label in content:
            hits.append(label)
    return hits


__all__ = [
    "OPTION_KEYS", "collect_option_factors", "build_option_factors_block",
    "parse_options", "count_options", "options_ok", "truncate_partial_options",
    "strip_options_block", "match_option_factors", "render_options_block",
    "is_confirmation_stage",
]
