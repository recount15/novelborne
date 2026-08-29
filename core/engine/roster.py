"""人物名册机制：伙伴/女主/宿敌的提示块装配与默认背景补全。

三类角色的难度规则不同，因此在同一装配函数里按角色分支：伙伴与主角同档、
女主由设定与影响力驱动且不占 D 编号、宿敌由主角难度与阵营势差非线性计算
（实际数值见 ``engine.faction``）。伙伴和女主数量由名册配置决定，宿敌仍只取 1 位。
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .tropes import classify_style

ROLES = ("伙伴", "女主", "宿敌")

_DEFAULT_BACKGROUNDS = {
    "伙伴": "在关键节点与主角并肩，因共同目标形成稳定协作。",
    "女主": "在主线推进中与主角相遇，关系随选择与行动逐步变化。",
    "宿敌": "与主角追求相反目标，持续制造可回应的压力与选择。",
}

_CARD_LABELS = (
    ("goal", "目标"),
    ("fear", "恐惧"),
    ("abilities", "能力"),
    ("relationship_vector", "关系倾向"),
    ("knowledge_scope", "认知范围"),
    ("speech_style", "语言风格"),
    ("unacceptable_behaviors", "不可接受行为"),
)


def _background(config: Mapping[str, Any], index: int, role: str) -> str:
    given = str(config.get("background", "")).strip()
    if given:
        return given
    style = str(config.get("style", classify_style(config.get("skill", config.get("description", "")))))
    defaults = _DEFAULT_BACKGROUNDS
    return f"{defaults[role]}倾向{style}，第{index + 1}位。"


def _card_lines(card: Mapping[str, Any]) -> list[str]:
    """把角色卡非空字段渲染为「标签：值」行；列表/字典压缩为 JSON。"""
    lines: list[str] = []
    for key, label in _CARD_LABELS:
        value = card.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple, dict)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value).strip()
        if rendered:
            lines.append(f"{label}：{rendered}")
    return lines


def build_character_block(configs: Sequence[Mapping[str, Any]] | None, role: str) -> str:
    if role not in ROLES:
        raise ValueError("角色类型必须是伙伴、女主或宿敌")
    values = list(configs or ())[:1] if role == "宿敌" else list(configs or ())
    if not values:
        return f"# {role}机制（未开启）"
    lines = [f"# {role}机制（已开启）"]
    for index, config in enumerate(values):
        name = str(config.get("name", f"{role}{index + 1}"))
        background = _background(config, index, role)
        lines.extend((f"- {role}{index + 1}：{name}", f"  - 背景：{background}"))
        persona_preset = str(config.get("persona_preset", "")).strip()
        if persona_preset:
            lines.append(f"  - 性格：{persona_preset}（穿越者人格，其行为、决策与语言风格受此驱动）")
        skill = str(config.get("skill", "")).strip()
        if skill:
            lines.append(f"  - 技能：{skill}")
        try:
            participation = int(config.get("participation", 0) or 0)
        except (TypeError, ValueError):
            participation = 0
        if participation:
            lines.append(f"  - 参与度：{max(1, min(9, participation))}/9")
        card = config.get("character_card")
        if isinstance(card, Mapping):
            card_lines = _card_lines(card)
            if card_lines:
                lines.append("  - 角色卡：")
                lines.extend(f"    - {line}" for line in card_lines)
        if role == "女主":
            lines.append("  - 难度：由设定与影响力驱动，不分配 D 编号。")
        elif role == "伙伴":
            lines.append("  - 难度：与主角采用同一难度档位。")
        else:
            lines.append("  - 难度：由主角难度与阵营势差非线性计算。")
    return "\n".join(lines)


def build_companion_block(configs: Sequence[Mapping[str, Any]] | None) -> str:
    return build_character_block(configs, "伙伴")


def build_heroine_block(configs: Sequence[Mapping[str, Any]] | None) -> str:
    return build_character_block(configs, "女主")


def build_nemesis_block(config: Mapping[str, Any] | None) -> str:
    return build_character_block([config] if config else [], "宿敌")


__all__ = ["ROLES", "build_character_block", "build_companion_block",
           "build_heroine_block", "build_nemesis_block"]
