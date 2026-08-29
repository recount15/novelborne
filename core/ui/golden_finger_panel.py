# -*- coding: utf-8 -*-
"""自定义金手指面板：拉取推荐、切换自定义输入可见性、提供商切换/拉模型/测连接。"""
from __future__ import annotations

import os

import gradio as gr

from core import fate_engine as fe
from core import engine
from core.ui.profiles_panel import _provider_key


def _refresh_golden_fingers(work, novel_file, persona_preset, persona_custom, difficulty):
    """按当前作品/性格/难度重新生成 5 项推荐（含无与自定义）。"""
    world = str(work or "")
    if novel_file:
        world = os.path.splitext(os.path.basename(fe._to_path(novel_file) or str(novel_file)))[0]
    persona = str(persona_preset or "")
    if persona.startswith("自定义"):
        persona = (persona_custom or "").strip() or persona
    options = engine.choices(world, persona, difficulty)
    return gr.update(choices=options, value=options[0])


def _toggle_custom_gf(selection):
    return gr.update(visible=engine.is_custom(selection))


def _on_provider_change(provider):
    """切换提供商时刷新 Base URL / 模型下拉 / API Key。"""
    from core.ui.profiles_panel import _provider_defaults
    url, models, key, model = _provider_defaults(provider)
    return gr.update(value=url), gr.update(choices=models, value=model), gr.update(value=key)


def _fetch_models_ui(provider, base_url, api_key):
    try:
        models = fe.fetch_models((api_key or "").strip() or _provider_key(provider), provider, base_url)
        if not models:
            return gr.update(), "未获取到模型，请检查服务响应。"
        return gr.update(choices=models, value=models[0]), f"已获取 {len(models)} 个模型"
    except Exception as exc:
        return gr.update(), f"拉取模型失败：{exc}"


def _test_connection_ui(provider, base_url, api_key, model):
    try:
        ok, message = fe.test_connection((api_key or "").strip() or _provider_key(provider),
                                         provider, base_url, model)
        return ("连接成功：" if ok else "连接失败：") + message
    except Exception as exc:
        return f"连接失败：{exc}"
