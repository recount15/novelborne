# -*- coding: utf-8 -*-
"""伙伴/女主/宿敌配置面板：打包成员数据、控制可见槽位。"""
from __future__ import annotations

import gradio as gr

from core import fate_engine as fe

POWER_CHOICES = [("按设定推断", -1), ("未评估/凡人 0", 0), ("偏弱 1", 1),
                 ("相当 2", 2), ("偏强 3", 3), ("远强 4", 4)]


def _member_pack(name, skill, background, power=-1, golden_finger=None):
    """把单个伙伴/女主的 UI 输入归一成引擎可用的 dict。"""
    if not name:
        return None
    item = {"name": name, "skill": fe.read_upload_text(skill) if skill else "",
            "background": background or ""}
    if golden_finger:
        item["golden_finger"] = golden_finger
    labels = {label: value for label, value in POWER_CHOICES}
    if power in labels:
        level = labels[power]
    else:
        try:
            level = int(power)
        except (TypeError, ValueError):
            level = -1
    if level >= 0:
        item["power"] = level
    return item


def _slot_updates(count, *components):
    """按数量切换伙伴/女主字段可见性。"""
    visible_count = max(0, min(3, int(count or 0)))
    per = 4 if len(components) % 4 == 0 else 3
    return [gr.update(visible=(i // per) < visible_count) for i in range(len(components))]
