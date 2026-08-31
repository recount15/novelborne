"""性格 Skill 动态更新：6 维倾向向量 + 短提示「近期倾向」。

设计论证（实现严格照此）：

- 双层：``settled`` 是对外暴露的向量，``pending`` 只在结算点以 EMA 合入。
  未到点不改 settled，避免每回合性格跟着抖。
- 每回合增量 clamp ±0.04，单次结算 pending 硬顶 ±0.12，终身偏离 clamp ±0.6，
  衰减 0.85：历史倾向会淡，persona 底色不会被选择淹没。
- persona Markdown 永不重写；提示块只写 |v|≥0.15 的近期倾向，最多 4 条、
  ≤180 字；``core_locked`` 时明确禁止改写底色 / 口头禅 / 底线。
- 本模块纯计算、无 IO、不调用模型、不依赖 SDK；同样输入必然得到同样向量。
"""
from __future__ import annotations

from typing import Any, Mapping

AXES = ("caution", "altruism", "candor", "ambition", "violence", "loyalty")

# 负端（-1）→ 正端（+1）。0 为 persona 基线。
AXIS_LABELS: dict[str, tuple[str, str]] = {
    "caution": ("猛冲", "苟稳"),
    "altruism": ("自利", "利他"),
    "candor": ("城府", "坦荡"),
    "ambition": ("知足", "进取"),
    "violence": ("避战", "嗜战"),
    "loyalty": ("背信", "守诺"),
}

# 基础模式非主角：用极大周期表示本局内不结算。
NEVER_SETTLE_PERIOD = 10**9

_TURN_DELTA_CAP = 0.04
_SETTLE_DELTA_CAP = 0.12
_SETTLED_CAP = 0.6
_EMA = 0.85
_NPC_INIT_CAP = 0.3
_PROMPT_THRESHOLD = 0.15
_PROMPT_MAX_AXES = 4
_PROMPT_MAX_CHARS = 180
_KEYWORD_WEIGHT = 0.02
_INIT_KEYWORD_WEIGHT = 0.1

_PROTAGONIST = "主角"
_CLOSE_ROLES = ("伙伴", "女主", "宿敌")
_ROLE_ALIASES = {
    "主角": _PROTAGONIST, "protagonist": _PROTAGONIST, "player": _PROTAGONIST,
    "hero": _PROTAGONIST, "穿越者": _PROTAGONIST,
    "伙伴": "伙伴", "companion": "伙伴", "partner": "伙伴",
    "女主": "女主", "heroine": "女主", "主女": "女主",
    "宿敌": "宿敌", "nemesis": "宿敌", "enemy": "宿敌",
}

# 每轴 (正端词, 负端词)。命中后加权，accumulate 再按轴 clamp 到 ±0.04。
_AXIS_LEXICON: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "caution": (
        ("苟稳", "谨慎", "稳妥", "观望", "隐忍", "低调", "避险", "先观察", "按兵", "试探", "保全"),
        ("猛冲", "硬刚", "冲上去", "冒险", "强突", "死磕", "硬闯", "正面硬刚", "立即动手", "不管不顾"),
    ),
    "altruism": (
        ("利他", "救人", "帮他", "帮她", "帮人", "让利", "保护同伴", "舍己", "仗义", "相助", "护送"),
        ("自利", "独吞", "先保自己", "牺牲他人", "见死不救", "只顾自己", "趁火打劫", "抛下同伴"),
    ),
    "candor": (
        ("坦荡", "直说", "坦白", "开诚", "不瞒", "说清", "如实", "坦诚", "摊牌"),
        ("城府", "隐瞒", "撒谎", "演戏", "设套", "口不对心", "暗中", "欺骗", "伪装"),
    ),
    "ambition": (
        ("进取", "争权", "上位", "扩张", "夺位", "更进一步", "野心", "谋划大业", "不甘人后"),
        ("知足", "退让", "安稳度日", "不愿争", "收手", "守成", "不争", "知难而退"),
    ),
    "violence": (
        ("嗜战", "开打", "血战", "动武", "斩杀", "灭口", "以武", "开战", "杀过去", "动手"),
        ("避战", "讲和", "谈判", "收手不打", "停战", "非暴力", "劝和", "化干戈", "议和"),
    ),
    "loyalty": (
        ("守诺", "守约", "不负", "忠于", "兑现承诺", "讲信用", "义气", "一言既出"),
        ("背信", "毁约", "反水", "出卖", "违约", "叛变", "食言", "临阵倒戈"),
    ),
}

_OPTION_KEYS = frozenset("ABCDEF")
_ROSTER_TEXT_KEYS = (
    "skill", "background", "description", "goal", "role",
    "character_model", "persona", "hint", "speech_style",
)
_CARD_TEXT_KEYS = (
    "goal", "fear", "abilities", "relationship_vector", "knowledge_scope",
    "speech_style", "unacceptable_behaviors",
)
_CORE_LOCK_LINE = "不得改写角色底色 / 口头禅 / 底线。"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _round(value: float) -> float:
    return round(float(value), 6)


def _normalize_role(role: Any) -> str:
    text = str(role or "").strip()
    if not text:
        return _PROTAGONIST
    return _ROLE_ALIASES.get(text, _ROLE_ALIASES.get(text.lower(), text))


def _is_enhanced(mode: Any) -> bool:
    return str(mode or "").strip().startswith("强化")


def _zeros() -> dict[str, float]:
    return {axis: 0.0 for axis in AXES}


def _copy_axes(source: Mapping[str, Any] | None) -> dict[str, float]:
    raw = source if isinstance(source, Mapping) else {}
    out: dict[str, float] = {}
    for axis in AXES:
        try:
            out[axis] = _round(float(raw.get(axis, 0.0) or 0.0))
        except (TypeError, ValueError):
            out[axis] = 0.0
    return out


def _score_text(text: str, weight: float, cap: float) -> dict[str, float]:
    blob = str(text or "")
    scores: dict[str, float] = {}
    for axis in AXES:
        pos, neg = _AXIS_LEXICON[axis]
        total = 0.0
        for word in pos:
            if word:
                total += weight * blob.count(word)
        for word in neg:
            if word:
                total -= weight * blob.count(word)
        scores[axis] = _round(_clamp(total, -cap, cap))
    return scores


def period_for(role: Any, mode: Any = "") -> int:
    """返回该角色在当前模式下的结算周期（回合数）。

    基础模式仅主角每 10 回合；非主角返回 ``NEVER_SETTLE_PERIOD`` 表示不结算。
    强化模式：主角 2 / 伙伴·女主·宿敌 5 / 其他 10。
    """
    role_name = _normalize_role(role)
    if _is_enhanced(mode):
        if role_name == _PROTAGONIST:
            return 2
        if role_name in _CLOSE_ROLES:
            return 5
        return 10
    if role_name == _PROTAGONIST:
        return 10
    return NEVER_SETTLE_PERIOD


def blank_profile(role: Any = _PROTAGONIST, core_locked: bool | None = None,
                  hint: str = "", settled: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """构造一份空白（或带开局 settled）性格档案，不读盘、不改 persona。"""
    role_name = _normalize_role(role)
    if core_locked is None:
        core_locked = role_name == _PROTAGONIST
    values = _copy_axes(settled)
    for axis, value in values.items():
        values[axis] = _round(_clamp(value, -1.0, 1.0))
    return {
        "role": role_name,
        "settled": values,
        "pending": _zeros(),
        "last_settled_round": 0,
        "core_locked": bool(core_locked),
        "hint": str(hint or ""),
    }


def _ensure_profile(profile: Any, role: Any = _PROTAGONIST) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return blank_profile(role)
    profile["role"] = _normalize_role(profile.get("role", role))
    profile["settled"] = _copy_axes(profile.get("settled"))
    profile["pending"] = _copy_axes(profile.get("pending"))
    try:
        profile["last_settled_round"] = int(profile.get("last_settled_round", 0) or 0)
    except (TypeError, ValueError):
        profile["last_settled_round"] = 0
    profile["core_locked"] = bool(profile.get("core_locked", profile["role"] == _PROTAGONIST))
    profile["hint"] = str(profile.get("hint") or "")
    return profile


def accumulate(profile: dict[str, Any] | None, action_text: Any,
               option_key: Any = None) -> dict[str, Any]:
    """按行动文本做关键词加权，增量写入 pending，不改 settled。

    每轴本回合增量 clamp 到 ±0.04。``option_key`` 若是超过单字母的正文则并入匹配。
    """
    box = _ensure_profile(profile)
    blob = str(action_text or "")
    extra = str(option_key or "").strip()
    if extra and not (len(extra) <= 2 and extra.upper()[:1] in _OPTION_KEYS):
        blob = f"{blob} {extra}".strip()
    delta = _score_text(blob, _KEYWORD_WEIGHT, _TURN_DELTA_CAP)
    pending = box["pending"]
    for axis in AXES:
        pending[axis] = _round(pending[axis] + delta[axis])
    box["pending"] = pending
    return box


def maybe_settle(profile: dict[str, Any] | None, current_round: Any,
                 mode: Any = None, period: int | None = None) -> tuple[dict[str, Any], bool]:
    """到结算点才把 pending 以 EMA 合入 settled，并清零 pending。

    ``settled = clamp(settled * 0.85 + clamp(pending, -0.12, 0.12), -0.6, 0.6)``
    未到点返回原 profile 且 ``settled_bool=False``。
    """
    box = _ensure_profile(profile)
    try:
        round_no = int(current_round or 0)
    except (TypeError, ValueError):
        round_no = 0
    if period is None:
        period = period_for(box.get("role"), mode)
    try:
        period = int(period)
    except (TypeError, ValueError):
        period = NEVER_SETTLE_PERIOD
    if period <= 0:
        period = NEVER_SETTLE_PERIOD
    last = int(box.get("last_settled_round", 0) or 0)
    if round_no <= 0 or round_no - last < period:
        return box, False

    pending = box["pending"]
    settled = box["settled"]
    for axis in AXES:
        delta = _clamp(pending[axis], -_SETTLE_DELTA_CAP, _SETTLE_DELTA_CAP)
        settled[axis] = _round(_clamp(settled[axis] * _EMA + delta, -_SETTLED_CAP, _SETTLED_CAP))
        pending[axis] = 0.0
    box["settled"] = settled
    box["pending"] = pending
    box["last_settled_round"] = round_no
    return box, True


def _roster_blob(member: Mapping[str, Any] | None) -> str:
    if not isinstance(member, Mapping):
        return ""
    chunks: list[str] = []
    for key in _ROSTER_TEXT_KEYS:
        value = member.get(key)
        if value not in (None, ""):
            chunks.append(str(value))
    card = member.get("character_card", member.get("card"))
    if isinstance(card, Mapping):
        for key in _CARD_TEXT_KEYS:
            value = card.get(key)
            if value not in (None, ""):
                chunks.append(str(value))
    return " ".join(chunks)


def _infer_settled(text: str) -> dict[str, float]:
    return _score_text(text, _INIT_KEYWORD_WEIGHT, _NPC_INIT_CAP)


def _iter_named(rows: Any, default_role: str) -> list[tuple[str, str, Mapping[str, Any]]]:
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    found: list[tuple[str, str, Mapping[str, Any]]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip() or f"{default_role}{index + 1}"
        role = _normalize_role(row.get("role") or row.get("role_type") or default_role)
        found.append((name, role, row))
    return found


def _add_profile(profiles: dict[str, Any], name: str, role: str,
                 text: str = "", core_locked: bool | None = None) -> None:
    if name in profiles and isinstance(profiles[name], dict):
        _ensure_profile(profiles[name], role)
        return
    settled = _infer_settled(text) if text else None
    if settled and role == _PROTAGONIST:
        # 主角开局全 0：persona 已表达基线，弱推断只给 NPC。
        settled = None
    profiles[name] = blank_profile(role, core_locked=core_locked, settled=settled)


def init_profiles(state: dict[str, Any] | None) -> dict[str, Any]:
    """在对局 state 上初始化 ``skill_profiles``（``on_start`` 调一次）。

    主角向量全 0；NPC 可从 roster 文本做一次弱推断（绝对值 ≤ 0.3）。
    已有档案不覆盖。永不改写 persona Markdown / ``persona_text``。
    """
    box = state if isinstance(state, dict) else {}
    existing = box.get("skill_profiles")
    profiles: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}

    _add_profile(profiles, _PROTAGONIST, _PROTAGONIST, core_locked=True)

    for name, role, row in _iter_named(box.get("companions"), "伙伴"):
        _add_profile(profiles, name, role or "伙伴", _roster_blob(row), core_locked=False)
    for name, role, row in _iter_named(box.get("heroines"), "女主"):
        _add_profile(profiles, name, role or "女主", _roster_blob(row), core_locked=False)

    roster = box.get("roster")
    if isinstance(roster, Mapping):
        for name, role, row in _iter_named(roster.get("companions"), "伙伴"):
            _add_profile(profiles, name, role or "伙伴", _roster_blob(row), core_locked=False)
        for name, role, row in _iter_named(roster.get("heroines"), "女主"):
            _add_profile(profiles, name, role or "女主", _roster_blob(row), core_locked=False)

    nemesis_enabled = bool(box.get("nemesis"))
    private = box.get("nemesis_private") if isinstance(box.get("nemesis_private"), Mapping) else {}
    start = box.get("start_params") if isinstance(box.get("start_params"), Mapping) else {}
    nemesis_name = str(
        (private or {}).get("name")
        or start.get("nemesis")
        or box.get("nemesis_label")
        or ""
    ).strip()
    if nemesis_enabled or nemesis_name:
        label = nemesis_name or "宿敌"
        blob = " ".join(
            str(part) for part in (
                (private or {}).get("goal"),
                (private or {}).get("plan"),
                start.get("nemesis_persona"),
                box.get("nemesis_persona"),
            ) if part
        )
        _add_profile(profiles, label, "宿敌", blob, core_locked=False)

    if isinstance(state, dict):
        state["skill_profiles"] = profiles
    return profiles


def _lookup_option_text(state: Mapping[str, Any], option_key: str) -> str:
    key = option_key.strip().upper()[:1]
    rows = state.get("options")
    if not isinstance(rows, (list, tuple)):
        return ""
    for row in rows:
        if isinstance(row, Mapping) and str(row.get("key") or "").strip().upper()[:1] == key:
            return str(row.get("text") or "")
    return ""


def _parse_action(state: Mapping[str, Any], action: Any) -> tuple[str, str | None]:
    option_key = None
    if isinstance(action, Mapping):
        option_key = action.get("option_key", action.get("key"))
        text = str(action.get("text") or action.get("action") or "")
        option_key = None if option_key in (None, "") else str(option_key)
        return text, option_key
    text = str(action or "")
    stripped = text.strip()
    key = stripped.upper()[:1]
    if key in _OPTION_KEYS and len(stripped) <= 2:
        body = _lookup_option_text(state, key)
        return body or text, key
    return text, None


def tick_after_action(state: dict[str, Any] | None, action: Any = "") -> dict[str, Any]:
    """门禁通过后、回合提交时调用：累计行动并按周期尝试结算。

    不改写 persona。缺档案时先 ``init_profiles``。返回传入的 state。
    """
    if not isinstance(state, dict):
        raise ValueError("state 必须是可写入的对局字典")
    profiles = init_profiles(state)
    text, option_key = _parse_action(state, action)
    try:
        round_no = int(state.get("round", 0) or 0)
    except (TypeError, ValueError):
        round_no = 0
    start = state.get("start_params") if isinstance(state.get("start_params"), Mapping) else {}
    mode = state.get("mode") or start.get("mode") or ""
    for name, profile in list(profiles.items()):
        box = _ensure_profile(profile, name)
        accumulate(box, text, option_key)
        maybe_settle(box, round_no, mode=mode)
        profiles[name] = box
    state["skill_profiles"] = profiles
    return state


def _tendency_items(profile: Mapping[str, Any]) -> list[tuple[str, float, str]]:
    settled = _copy_axes(profile.get("settled") if isinstance(profile, Mapping) else None)
    ranked: list[tuple[str, float, str]] = []
    for axis in AXES:
        value = settled[axis]
        if abs(value) < _PROMPT_THRESHOLD:
            continue
        negative, positive = AXIS_LABELS[axis]
        label = positive if value > 0 else negative
        ranked.append((axis, value, label))
    ranked.sort(key=lambda item: (-abs(item[1]), AXES.index(item[0])))
    return ranked[:_PROMPT_MAX_AXES]


def prompt_block(profile: Mapping[str, Any] | None) -> str:
    """只把 |v|≥0.15 的轴写成「近期倾向」，最多 4 条，总长 ≤180 字。

    传入对局 state 时默认取主角档案。core_locked 时写明不得改写底色，
    且该锁定行完整优先：预算先留给它，倾向段只占剩余空间。
    永不输出 persona 全文。
    """
    box: Mapping[str, Any] | None = profile
    if isinstance(profile, Mapping) and "settled" not in profile:
        profiles = profile.get("skill_profiles")
        if isinstance(profiles, Mapping):
            box = profiles.get(_PROTAGONIST) if isinstance(profiles.get(_PROTAGONIST), Mapping) else None
    box = box if isinstance(box, Mapping) else {}
    items = _tendency_items(box)
    if box.get("core_locked"):
        # 锁定行是硬约束，永不截断：先占预算，倾向段裁剪到剩余空间。
        locked = _CORE_LOCK_LINE
        remaining = _PROMPT_MAX_CHARS - len(locked)
        parts = [locked]
        if items and remaining > 0:
            tendency = f"【近期倾向】{'、'.join(label for _, _, label in items)}。"
            if len(tendency) > remaining:
                tendency = tendency[:remaining]
            parts.append(tendency)
        return "".join(parts)
    text = ""
    if items:
        text = f"【近期倾向】{'、'.join(label for _, _, label in items)}。"
        if len(text) > _PROMPT_MAX_CHARS:
            text = text[:_PROMPT_MAX_CHARS]
    return text


def _one_snapshot(profile: Mapping[str, Any]) -> dict[str, Any]:
    box = _ensure_profile(dict(profile))
    return {
        "role": box["role"],
        "settled": dict(box["settled"]),
        "last_settled_round": box["last_settled_round"],
        "core_locked": box["core_locked"],
        "hint": box["hint"],
    }


def public_snapshot(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """对外快照：只暴露 settled，不含 pending，避免未结算抖动。"""
    if not isinstance(state, Mapping):
        return {}
    if "settled" in state and "pending" in state:
        return _one_snapshot(state)
    profiles = state.get("skill_profiles") if "skill_profiles" in state else state
    if not isinstance(profiles, Mapping):
        return {}
    out: dict[str, Any] = {}
    for name, profile in profiles.items():
        if isinstance(profile, Mapping) and "settled" in profile:
            out[str(name)] = _one_snapshot(profile)
    return out


__all__ = [
    "AXES", "AXIS_LABELS", "NEVER_SETTLE_PERIOD",
    "period_for", "blank_profile", "init_profiles",
    "accumulate", "maybe_settle", "tick_after_action",
    "prompt_block", "public_snapshot",
]
