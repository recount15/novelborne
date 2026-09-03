"""从玩家行动与模型回复中抽取可验证的状态变更提案。

原则：只接受能在文本里找到明确依据的变更；找不到依据就不改状态，
把疑点写进 ``flags.conflicts`` 由玩家或后续回合澄清。
"""
from __future__ import annotations

import re
from typing import Any, Mapping

# 时间：显式日期、显式时刻、相对天数推进。
_DATE_RE = re.compile(r"(\d{2,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_CLOCK_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)(?!\d)")
_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
              "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
_HOUR_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*点(?:钟)?")
_CN_HOUR_RE = re.compile(r"([一两二三四五六七八九十]{1,2})点(?:钟)?(?:(半)|([一两二三四五])十?分?)?")
_PHASES = ("凌晨", "清晨", "早晨", "上午", "正午", "中午", "午后", "下午", "傍晚", "黄昏", "入夜", "夜里", "深夜", "半夜")
_NEXT_DAY = ("次日", "第二天", "翌日", "隔日", "第二日")
_DAY_AFTER = ("第三天", "两天后", "后天")
_LOCATION_RE = re.compile(r"(?:抵达|到达|来到|返回|回到|进入|走进|踏入|前往)了?([\u4e00-\u9fa5A-Za-z0-9·]{2,12})")
_INJURY_WORDS = ("重伤", "骨折", "中毒", "内伤", "失血", "撕裂", "灼伤", "冻伤", "断骨", "受伤", "擦伤", "淤青")
_HEAL_WORDS = ("伤势痊愈", "伤口愈合", "康复", "痊愈", "伤势尽复")
# v2.0.4 扩展死亡与离场关键词（质量门 state 维度配套）
_DEATH_WORDS = ("死亡", "身亡", "殒命", "毙命", "丧生", "咽气", "断气", "停止呼吸", "心跳停止", "失去生命")
_DEPARTURE_WORDS = ("离开", "离去", "离别", "远去", "告别", "分别", "出走", "出发", "启程", "起程", "动身")
_GAIN_RE = re.compile(r"(?:获得|得到|拿到|取得|捡到|收下)了?([\u4e00-\u9fa5A-Za-z0-9·]{2,12})")
_LOSE_RE = re.compile(r"(?:失去|丢失|被夺走|遗失|用掉|耗尽)了?([\u4e00-\u9fa5A-Za-z0-9·]{2,12})")
_SKILL_RE = re.compile(r"(?:学会|掌握|习得|领悟)了?([\u4e00-\u9fa5A-Za-z0-9·]{2,12})")
_MISTAKE_WORDS = ("其实并非", "并非如此", "误以为", "判断有误", "认知错误", "看错", "误判")


_QUANTIFIER_RE = re.compile(
    r"^(?:[一二三四五六七八九十百千两半数几\d]+)?(?:枚|个|把|件|块|张|瓶|颗|条|本|袋|包|柄|口|副|串|滴|份|坛|壶|盒|支)")


def _clean_noun(text: str) -> str:
    """剥掉数量词前缀，避免「一枚铜牌」与「铜牌」被记成两件物品。

    剥完为空（捕获只有量词，如「获得一枚。」）返回空串，由调用方按
    「无效捕获」过滤——原先回退原值会把「一枚」记成一件物品。
    """
    value = str(text or "").strip()
    return _QUANTIFIER_RE.sub("", value)


# 子句功能字：贪婪捕获会把动词后的整个子句吞进名词（实测「走进门洞才把册子放低」
# 被捕成地点「门洞才把册子放低」）。这些字不出现在地点/物品名里，作硬边界。
# 注意刻意排除 当/向/沿/从/后/前/里 等可出现在真实地名里的字（当铺/向阳村/从江/后山）。
_CLAUSE_STOP = frozenset("才把将便就又再且而或但于是因此让使令好的了着地得是在与时候")


def _trim_noun(text: str) -> str:
    """剪掉贪婪捕获吞进来的子句尾巴（「门洞才把册子放低」→「门洞」）。"""
    for i, ch in enumerate(text):
        if ch in _CLAUSE_STOP:
            return text[:i]
    return text


def _dedup(values: list[str], cap: int = 12) -> list[str]:
    seen: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen[-cap:]


def _extend(current: Any, additions: list[str], cap: int = 12) -> list[str]:
    base = [str(x) for x in current] if isinstance(current, (list, tuple)) else []
    return _dedup(base + additions, cap)


def extract_time(text: str, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """解析日期、时刻与跨天推进；无依据时返回空 patch。"""
    current = dict(current or {})
    source = str(text or "")
    patch: dict[str, Any] = {}
    date_hit = _DATE_RE.search(source)
    if date_hit:
        year, month, day = (int(x) for x in date_hit.groups())
        patch["date"] = f"{year:04d}-{month:02d}-{day:02d}" if year > 99 else f"{year:02d}年{month:02d}月{day:02d}日"
    else:
        md_hit = _MD_RE.search(source)
        if md_hit:
            patch["date"] = f"{int(md_hit.group(1)):02d}-{int(md_hit.group(2)):02d}"
    clock_hit = _CLOCK_RE.search(source)
    if clock_hit:
        patch["clock"] = f"{int(clock_hit.group(1)):02d}:{clock_hit.group(2)}"
    else:
        hour_hit = _HOUR_RE.search(source)
        if hour_hit:
            patch["clock"] = f"{int(hour_hit.group(1)):02d}:00"
        else:
            # 中文数字时刻（「三点」「卯时三刻」口语化为「三点」）：叙事里比
            # 阿拉伯数字更常见，原先完全不认。
            cn_hit = _CN_HOUR_RE.search(source)
            if cn_hit and cn_hit.group(1) in _CN_DIGITS:
                hour = _CN_DIGITS[cn_hit.group(1)]
                minute = 30 if cn_hit.group(2) else 0
                if cn_hit.group(3):
                    minute = _CN_DIGITS.get(cn_hit.group(3), 0) * 10
                patch["clock"] = f"{hour:02d}:{minute:02d}"
    # 相位取「就近」命中词（最后一次出现），而非固定优先级首个：叙事结尾
    # 通常是场景的当前状态，开头/中段的相位词常属背景叙述（实测「药铺半夜
    # 煎药」出现在口供转述里，把场景从半夜后误标回半夜前的午后）。
    # 显式时刻（clock）命中时相位降为兜底——「三更时分」类表达优先采信数字。
    phase_hits = [p for p in _PHASES if p in source]
    phase = phase_hits[-1] if phase_hits else ""
    if phase and "clock" not in patch:
        patch["day_phase"] = phase
    day_index = int(current.get("day_index", 0) or 0)
    if any(word in source for word in _DAY_AFTER):
        patch["day_index"] = day_index + 2
    elif any(word in source for word in _NEXT_DAY):
        patch["day_index"] = day_index + 1
    return patch


def extract_patch(reply: str, *, action: str = "", current: Mapping[str, Any] | None = None,
                  round_no: int | None = None) -> dict[str, Any]:
    """从模型回复（叙事结果）抽取状态变更；action 只作为补充线索。"""
    state = dict(current or {})
    text = str(reply or "")
    combined = text + "\n" + str(action or "")
    patch: dict[str, Any] = {}

    time_patch = extract_time(text, state.get("time"))
    if time_patch:
        patch["time"] = time_patch

    place = _LOCATION_RE.search(text)
    if place:
        name = _trim_noun(place.group(1))
        if len(name) >= 2 and name != (state.get("location") or {}).get("name"):
            patch["location"] = {"name": name}
    # v2.0.4 离场检测（记录离开事件，用于后续 AI 软校验）
    departure_hits = [word for word in _DEPARTURE_WORDS if word in text]
    if departure_hits:
        patch.setdefault("scene", {})["departure"] = departure_hits[:3]

    body_now = dict(state.get("body") or {})
    injuries = [word for word in _INJURY_WORDS if word in text]
    if injuries:
        patch.setdefault("body", {})["injuries"] = _extend(body_now.get("injuries"), injuries, 8)
        patch["body"]["condition"] = "受伤"
    elif any(word in text for word in _HEAL_WORDS):
        patch.setdefault("body", {})["injuries"] = []
        patch["body"]["condition"] = "正常"
    # v2.0.4 死亡检测（保守：只标记，不自动进入死亡流程）
    if any(word in text for word in _DEATH_WORDS):
        patch.setdefault("body", {})["condition"] = "死亡"
        patch["body"]["death_evidence"] = [word for word in _DEATH_WORDS if word in text][:3]

    assets_now = dict(state.get("assets") or {})
    gains = [n for n in (_trim_noun(_clean_noun(m.group(1))) for m in _GAIN_RE.finditer(text)) if len(n) >= 2]
    losses = {n for n in (_trim_noun(_clean_noun(m.group(1))) for m in _LOSE_RE.finditer(text)) if len(n) >= 2}
    if gains or losses:
        items = _extend(assets_now.get("items"), gains, 16)
        items = [item for item in items if item not in losses]
        patch["assets"] = {"items": items}

    skills = [n for n in (_trim_noun(_clean_noun(m.group(1))) for m in _SKILL_RE.finditer(text)) if len(n) >= 2]
    if skills:
        patch["abilities"] = {"skills": _extend((state.get("abilities") or {}).get("skills"), skills, 16)}

    knowledge_now = dict(state.get("knowledge") or {})
    if any(word in combined for word in _MISTAKE_WORDS):
        patch["knowledge"] = {"misconceptions": _extend(knowledge_now.get("misconceptions"),
                                                       ["本回合出现认知修正，需复核既有判断"], 8)}

    if round_no is not None:
        patch.setdefault("scene", {})["round"] = int(round_no)
    return patch


def action_patch(action: str, *, round_no: int, chapter: int | None = None) -> dict[str, Any]:
    """玩家提交行动时的最小提案：只登记意图，不预设结果。"""
    patch: dict[str, Any] = {
        "scene": {"round": int(round_no), "pending": [str(action or "")[:160]]},
    }
    if chapter is not None:
        patch["scene"]["chapter"] = max(1, int(chapter))
    return patch


__all__ = ["extract_patch", "extract_time", "action_patch"]
