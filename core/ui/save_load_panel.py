# -*- coding: utf-8 -*-
"""保存 / 读取存档面板守卫：纯状态校验，不含 gradio 依赖。"""
from __future__ import annotations


def can_load(loaded, state):
    """读取存档守卫：仅当加载结果包含有效 system 时允许替换当前会话。"""
    return bool(loaded and loaded.get("system"))
