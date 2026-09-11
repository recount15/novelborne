"""状态快照、差异和面板渲染。"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any, Mapping

from .schema import clone, blank_state
from .state_validator import validate_patch, validate_state


def _merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for category, values in patch.items():
        target = result.setdefault(category, {})
        for key, value in values.items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = copy.deepcopy(value)
    return result


def diff_state(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for category in set(before) | set(after):
        if before.get(category) != after.get(category):
            changes[category] = {"before": clone(before.get(category)), "after": clone(after.get(category))}
    return changes


def apply_turn(state: Mapping[str, Any] | None, patch: Mapping[str, Any] | None = None,
               *, round_no: int | None = None, source: str = "engine") -> tuple[dict[str, Any], dict[str, Any]]:
    current = validate_state(state or blank_state(), fill=True)
    proposal = validate_patch(patch or {})
    updated = _merge(current, proposal)
    if round_no is not None:
        updated["scene"]["round"] = max(0, int(round_no))
    updated["flags"]["last_update"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    changes = diff_state(current, updated)
    # 保留最近 29 条（+本回合 1 条 = 30）：history 纯审计用途，面板与 prompt
    # 装配都不读全量，而每条含 before/after 双份 diff，会随 state 进入每回合
    # 存档序列化、流式 public_state 深拷贝与前端全量替换。
    updated["history"] = list(current.get("history", []))[-29:]
    if changes:
        updated["history"].append({"round": updated["scene"].get("round", 0), "source": source,
                                    "changes": changes, "at": updated["flags"]["last_update"]})
    return validate_state(updated), changes


class StateStore:
    def __init__(self, state: Mapping[str, Any] | None = None):
        self.state = validate_state(state or blank_state(), fill=True)

    def propose(self, patch: Mapping[str, Any] | None, *, round_no: int | None = None,
                source: str = "engine") -> dict[str, Any]:
        self.state, changes = apply_turn(self.state, patch, round_no=round_no, source=source)
        return changes

    def snapshot(self) -> dict[str, Any]:
        return clone(self.state)


# 面板是给用户看的业务文本：枚举与常见键一律映射为中文，杜绝 json.dumps 泄漏。
_ENUM_ZH = {
    "inactive": "未激活", "active": "已激活", "ready": "可用", "cooldown": "冷却中",
    "pending": "待定", "running": "进行中", "done": "已完成", "failed": "失败",
    "cancelled": "已取消", "unknown": "未知", "none": "无", "normal": "正常",
}
_KEY_ZH = {
    "name": "名称", "status": "状态", "cooldown": "冷却", "cost": "代价",
    "costs": "代价", "scope": "作用域", "tier": "层级", "type": "类型",
    "level": "等级", "count": "数量", "description": "说明", "desc": "说明",
    "title": "标题", "label": "标签", "value": "数值", "progress": "进度",
    "round": "回合", "chapter": "章节", "gender": "性别", "age": "年龄",
    "condition": "状况", "fatigue": "疲劳", "date": "日期", "clock": "时刻",
    "blocked_for_others": "他人禁用", "revision": "修订",
}
# 语义主键：列表条目优先只展示主键，其余字段并入括号补充。
_NAME_KEYS = ("name", "名称", "title", "标题", "label", "标签")


def _zh_key(key: str) -> str:
    return _KEY_ZH.get(str(key).lower(), str(key))


def _zh_value(value: Any) -> str:
    text = str(value).strip()
    return _ENUM_ZH.get(text.lower(), text)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _zh_value(value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        if not value:
            return ""
        parts = []
        for key, item in value.items():
            rendered = _text(item)
            parts.append(f"{_zh_key(key)}：{rendered}" if rendered else _zh_key(key))
        return "；".join(parts)
    if isinstance(value, list):
        if not value:
            return ""
        items = []
        for item in value:
            if isinstance(item, dict):
                label = next((item[k] for k in _NAME_KEYS
                              if item.get(k) not in (None, "")), None)
                if label is not None:
                    rest = {k: v for k, v in item.items()
                            if k not in _NAME_KEYS and v not in (None, "", [])}
                    extra = _text(rest)
                    items.append(f"{_text(label)}（{extra}）" if extra else _text(label))
                    continue
            rendered = _text(item)
            if rendered:
                items.append(rendered)
        return "、".join(items)
    return _zh_value(value)


def render_panel(state: Mapping[str, Any] | None) -> str:
    s = validate_state(state or blank_state(), fill=True)
    t, loc, body, assets = s["time"], s["location"], s["body"], s["assets"]
    abilities, rel, goals, knowledge, scene = s["abilities"], s["relationships"], s["goals"], s["knowledge"], s["scene"]
    flags = s["flags"]
    day = int(t.get("day_index", 0) or 0)
    day_text = f"第 {day + 1} 天" if day else "第 1 天"
    lines = [
        "### 状态记忆面板",
        f"- **时空**：{_text(loc.get('name')) or '未记录'} ｜ {_text(t.get('date')) or '日期未知'} "
        f"{_text(t.get('clock'))} {_text(t.get('day_phase'))} ｜ {day_text}",
        f"- **身体**：{_text(body.get('condition'))}；伤势 {_text(body.get('injuries')) or '无'}；异常 {_text(body.get('abnormalities')) or '无'}；疲劳 {body.get('fatigue', 0)}",
        f"- **资产**：货币 {_text(assets.get('currency')) or '无'}；装备 {_text(assets.get('equipment')) or '无'}；物品 {_text(assets.get('items')) or '无'}",
        f"- **能力**：技能 {_text(abilities.get('skills')) or '无'}；修为 {_text(abilities.get('cultivation')) or '未记录'}；金手指 {_text(abilities.get('golden_finger'))}",
        f"- **关系/阵营**：{_text(rel.get('characters')) or '无'}；{_text(rel.get('factions')) or '无'}",
        f"- **目标/伏笔**：目标 {_text(goals.get('current')) or '无'}；任务 {_text(goals.get('tasks')) or '无'}；伏笔 {_text(goals.get('foreshadowing')) or '无'}",
        f"- **认知**：已知 {_text(knowledge.get('known')) or '无'}；未知 {_text(knowledge.get('unknown')) or '无'}；误判 {_text(knowledge.get('misconceptions')) or '无'}",
        f"- **进度**：第 {scene.get('chapter', 1)} 章，第 {scene.get('round', 0)} 回合；场景 {_text(scene.get('name')) or '未命名'}；锚点 {_text(scene.get('anchor_ids')) or '无'}",
        f"- **待结算**：{_text(scene.get('pending')) or '无'}",
        f"- **世界书命中**：{_text(flags.get('last_worldbook')) or '无'} ｜ 待澄清 {_text(flags.get('conflicts')) or '无'}",
    ]
    return "\n".join(lines)


__all__ = ["StateStore", "apply_turn", "diff_state", "render_panel"]
