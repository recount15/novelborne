# -*- coding: utf-8 -*-
"""伙伴/女主动态名单表单的纯数据辅助。"""
from __future__ import annotations

# 下拉项保持短小，确保每个角色 skill 在界面中只占一行。
SKILL_PRESETS = [
    "按设定推断", "情报搜集", "护卫与战斗", "医术与救援", "谋略与谈判",
    "工程与制造", "追踪与潜伏", "社交与资源", "自定义",
]

HEROINE_POOLS = {
    "单女主": [
        {"name": "原著女主", "skill": "按设定推断", "background": "沿用原著女主模板"},
    ],
    "多女主": [
        {"name": "女主一", "skill": "按设定推断", "background": "沿用原著女主模板"},
        {"name": "女主二", "skill": "按设定推断", "background": "沿用原著女主模板"},
        {"name": "女主三", "skill": "按设定推断", "background": "沿用原著女主模板"},
    ],
}
# 动态表单不预设角色数量上限；该常量仅作为旧版调用方的兼容默认值。
MAX_ROSTER = 10000


def normalize_count(value, maximum: int | None = MAX_ROSTER) -> int:
    try:
        count = max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
    return count if maximum is None else min(maximum, count)


def skill_value(preset, custom="") -> str:
    """合并预设/自定义 skill；自定义内容优先，便于一行显示。"""
    custom = str(custom or "").strip()
    return custom if custom else str(preset or "").strip()


def heroine_pool(mode: str):
    return [dict(item) for item in HEROINE_POOLS.get(mode, [])]


