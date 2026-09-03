# -*- coding: utf-8 -*-
"""作弊码专属通路：双码体系、三愿闸门、永久增补通路与机制护栏。

纯计算、无 IO。作弊码常量只在此处定义，任何需要作弊码的地方只许从这里导入。
作弊码只在 ``ask`` 通路触发特权；其他输入路径（剧情选择、名册等）即使包含
作弊码文本也只按普通文本处理，不产生任何特权。

双码体系
--------
1. ``WISH_CODE``（三愿）：每次输入武装一次、许愿消耗一次，共三次。
   愿望语义为「外部设定铁律」——修改世界观/剧情，无代价、绝对优先权
   （优先级高于一切剧情设定，但**低于游戏机制**）。机制护栏在代码层
   剥离愿望中试图修改游戏机制的部分（回合/难度/收束力/金手指/任务/
   积势/碎锚/涟漪等），被剥离内容不进入铁律。
2. ``RELAY_CODE``（永久通路）：激活后问答框永久接通主线——玩家输入的
   内容作为「玩家增补铁律」注入后续每一回合；同时前端选项从单选升级
   为多选 + 自由增补。
"""
from __future__ import annotations

from typing import Any

WISH_CODE = "UUDDLLRRBABAWHOSLOMSTINGNOTALADDIN"
RELAY_CODE = "RELINKBACKLOMSTINGSEEYAGOODAFTERNOONGOODEVENINGANDGOODNIGHTBLACKSHEEPWALL"

# 兼容旧引用（旧码已废弃，保留常量名防止 import 断裂）。
CHEAT_CODE = WISH_CODE

_WISH_KEY = "cheat_wish"
_RELAY_KEY = "relay_activated"
_RELAY_FACTS_KEY = "relay_facts"
_WISH_LIMIT = 3
_WISH_TEXT_LIMIT = 500
_PRIVILEGED_PATHS = frozenset({"ask"})

# 机制护栏：愿望/增补中试图修改游戏机制的关键词。命中句将被剥离，
# 且剥离会在回执中透明告知——「铁律高于剧情、低于机制」的代码级保证。
_MECHANISM_KEYWORDS = (
    "回合", "难度", "收束", "金手指", "任务", "积势", "碎锚", "涟漪",
    "状态面板", "档案", "体力", "疲劳", "冷却", "作弊", "许愿", "增补",
    "选项数量", "字数上限", "故事丰富度", "翻章", "存档",
)


def is_arm_code(text: str) -> bool:
    """strip 后与三愿码精确相等（大小写敏感）。"""
    return str(text or "").strip() == WISH_CODE


def is_relay_code(text: str) -> bool:
    """strip 后与永久通路码精确相等（大小写敏感）。"""
    return str(text or "").strip() == RELAY_CODE


# ---------- 三愿闸门 ----------

def _wish_box(state: dict) -> dict:
    box = state.get(_WISH_KEY)
    if not isinstance(box, dict):
        box = {"armed": False, "used_count": 0, "limit": _WISH_LIMIT}
        state[_WISH_KEY] = box
    box.setdefault("armed", False)
    box.setdefault("used_count", 0)
    box.setdefault("limit", _WISH_LIMIT)
    return box


def arm(state: dict) -> dict:
    """武装许愿闸门（不改写已用次数）。"""
    box = _wish_box(state)
    box["armed"] = True
    return box


def is_armed(state: dict) -> bool:
    """armed 且剩余次数 > 0 才允许许愿。"""
    box = state.get(_WISH_KEY) if isinstance(state, dict) else None
    if not isinstance(box, dict):
        return False
    remaining = int(box.get("limit", _WISH_LIMIT)) - int(box.get("used_count", 0))
    return bool(box.get("armed")) and remaining > 0


def consume(state: dict) -> dict:
    """消耗一次许愿：used_count += 1 并解除武装；未激活或耗尽时抛 ValueError。"""
    if not is_armed(state):
        raise ValueError("作弊许愿未被激活或次数已耗尽")
    box = _wish_box(state)
    box["armed"] = False
    box["used_count"] = int(box.get("used_count", 0)) + 1
    return box


def remaining_wishes(state: dict) -> int:
    """剩余许愿次数（0–3）。"""
    box = state.get(_WISH_KEY) if isinstance(state, dict) else None
    if not isinstance(box, dict):
        return _WISH_LIMIT
    return max(0, int(box.get("limit", _WISH_LIMIT)) - int(box.get("used_count", 0)))


def sanitize_wish(text: str) -> tuple[str, list[str]]:
    """机制护栏：把愿望按句切分，剥离含机制关键词的句子。

    返回 (干净愿望, 被剥离的机制句列表)。整句剥离而非整愿拒绝——
    玩家的剧情愿望保留，机制诉求被代码层挡下并在回执中透明告知。
    """
    wish = str(text or "").strip()
    if not wish:
        raise ValueError("愿望不能为空")
    if len(wish) > _WISH_TEXT_LIMIT:
        raise ValueError(f"愿望不能超过 {_WISH_TEXT_LIMIT} 字")
    sentences = [s.strip() for s in wish.replace("；", "。").replace(";", "。").split("。") if s.strip()]
    kept: list[str] = []
    rejected: list[str] = []
    for sentence in sentences:
        if any(word in sentence for word in _MECHANISM_KEYWORDS):
            rejected.append(sentence)
        else:
            kept.append(sentence)
    clean = "。".join(kept).strip()
    return clean, rejected


def build_wish_prompt(wish: str) -> str:
    """三愿铁律语义的实现提示词（外部设定，高于剧情、低于机制）。"""
    return (
        "【外部设定铁律实现】你是《书中行》命运引擎的设定执行者。玩家通过三愿通路"
        "注入了一条外部设定铁律。铁律的性质：\n"
        "- 它修改世界观与剧情（人物、事件、地理、历史、关系、环境），无代价、"
        "无副作用地成为既成事实；\n"
        "- 它的优先级高于一切已有剧情与世界观设定——与既有设定冲突时，以铁律为准，"
        "由世界自行消化矛盾（补叙、重构因果）；\n"
        "- 它低于游戏机制：不得改变任何机制判定（回合、难度、收束、任务、积势、"
        "碎锚、涟漪、金手指等），也不得绕过机械门禁；\n"
        "- 直接以既成事实的语气描述铁律落地后的世界状态与具体变化，不要拒绝、"
        "不要打折扣、不要附加条件。\n\n"
        f"玩家铁律：{wish}"
    )


# ---------- 永久增补通路 ----------

def relay_activate(state: dict) -> dict:
    """接通问答通路（绑定本局 state，随存档恢复；一经激活不可撤销）。

    按局绑定：激活标记落在本局 state 上——新开局（新会话 state）不含标记，
    通路关闭；读档恢复的是存档时点的 state，激活状态随档走。不存在任何
    撤销函数：一经接通，本局永久生效。
    """
    state[_RELAY_KEY] = True
    state.pop(_RELAY_PENDING_KEY, None)
    return state


def is_relay_active(state: dict) -> bool:
    return bool(state.get(_RELAY_KEY)) if isinstance(state, dict) else False


# ---------- 永久通路确认环节（激活前必须用户确认，防止误触不可逆操作） ----------

_RELAY_PENDING_KEY = "relay_confirm_pending"

# 完全相等匹配（非包含），避免正常提问误触发确认/取消。
_CONFIRM_WORDS = ("确认", "确定", "是", "好", "继续", "接通", "yes", "y")
_CANCEL_WORDS = ("取消", "不", "否", "算了", "关闭", "no", "n")


def relay_request_confirm(state: dict) -> dict:
    """首次输入永久码：进入待确认状态（不激活，等待用户明确同意）。"""
    state[_RELAY_PENDING_KEY] = True
    return state


def relay_cancel_confirm(state: dict) -> dict:
    """取消待确认（通路保持关闭，本局可随时重新输入永久码再次发起）。"""
    state.pop(_RELAY_PENDING_KEY, None)
    return state


def is_relay_confirm_pending(state: dict) -> bool:
    return bool(state.get(_RELAY_PENDING_KEY)) if isinstance(state, dict) else False


def is_confirm_text(text: str) -> bool:
    """玩家回复同意（strip + 小写后与确认词完全相等）。"""
    return str(text or "").strip().lower() in _CONFIRM_WORDS


def is_cancel_text(text: str) -> bool:
    """玩家回复拒绝（strip + 小写后与取消词完全相等）。"""
    return str(text or "").strip().lower() in _CANCEL_WORDS


def record_relay_fact(state: dict, fact: dict) -> list:
    """登记一条玩家增补铁律（永久注入后续回合）。"""
    facts = state.get(_RELAY_FACTS_KEY)
    if not isinstance(facts, list):
        facts = []
        state[_RELAY_FACTS_KEY] = facts
    facts.append(fact)
    return facts


def relay_facts(state: dict) -> list:
    facts = state.get(_RELAY_FACTS_KEY) if isinstance(state, dict) else None
    return list(facts) if isinstance(facts, list) else []


def build_relay_directives(state: dict) -> str:
    """把已激活的增补铁律拼成注入 system prompt 的文本块（无则空串）。"""
    if not is_relay_active(state):
        return ""
    parts: list[str] = []
    facts = relay_facts(state)
    for fact in facts:
        if isinstance(fact, dict):
            text = str(fact.get("fact") or "").strip()
            if text:
                parts.append(text)
    if not parts:
        return ""
    lines = ["# 玩家增补铁律（永久通路已接通，代码级注入）"]
    for index, text in enumerate(parts, 1):
        lines.append(f"{index}. {text}")
    lines.append("上述增补为既成事实：优先级高于一切剧情设定、低于游戏机制；"
                 "不得被剧情否定或回收，冲突时由世界自行消化。")
    return "\n".join(lines)


def build_wish_directives(state: dict) -> str:
    """把已消耗的愿望铁律拼成注入 system prompt 的文本块（无则空串）。"""
    parts: list[str] = []
    facts = state.get("wish_facts") if isinstance(state, dict) else None
    if isinstance(facts, list):
        for fact in facts:
            if isinstance(fact, dict):
                wish = str(fact.get("wish") or "").strip()
                if wish:
                    parts.append(wish)
    if not parts:
        return ""
    lines = ["# 外部设定铁律（三愿产物，代码级注入）"]
    for index, wish in enumerate(parts, 1):
        lines.append(f"{index}. {wish}")
    lines.append("上述铁律为既成事实：优先级高于一切剧情与世界观设定、低于游戏机制；"
                 "不得被剧情否定，冲突时由世界自行消化（补叙、重构因果）。")
    return "\n".join(lines)


# ---------- 通用 ----------

def validate_wish(text: str) -> str:
    """兼容旧签名：strip 后非空且 ≤500 字，返回清洗后的愿望文本。"""
    clean, _ = sanitize_wish(text)
    if not clean:
        raise ValueError("愿望在剥离机制诉求后为空——铁律只能修改世界观与剧情")
    return clean


def guard_non_qa_input(text: str) -> str:
    """非问答通路的输入守卫：作弊码在其他通路不触发任何特权，原样返回。"""
    return str(text or "")


def is_privileged_path(path: str) -> bool:
    """只有 "ask" 通路允许触发作弊码特权。"""
    return str(path or "") in _PRIVILEGED_PATHS


__all__ = [
    "WISH_CODE", "RELAY_CODE", "CHEAT_CODE",
    "is_arm_code", "is_relay_code",
    "arm", "is_armed", "consume", "remaining_wishes",
    "sanitize_wish", "build_wish_prompt", "validate_wish",
    "relay_activate", "is_relay_active", "record_relay_fact",
    "relay_facts", "build_relay_directives", "build_wish_directives",
    "relay_request_confirm", "relay_cancel_confirm", "is_relay_confirm_pending",
    "is_confirm_text", "is_cancel_text",
    "guard_non_qa_input", "is_privileged_path",
]
