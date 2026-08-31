# -*- coding: utf-8 -*-
"""配置档 / 提供商面板助手：非敏感设置持久化、环境变量与提供商默认值。"""
from __future__ import annotations

import os

from core import fate_engine as fe

CONFIG_PATH = os.path.join(fe.WRITABLE_DIR, "config.json")


def _load_dotenv():
    """轻量 .env 读取（无第三方依赖）：将 KEY=VALUE 注入环境变量（不覆盖已存在项）。"""
    path = os.path.join(fe.WRITABLE_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_dotenv()
PROFILES, ACTIVE_PROFILE = fe.load_profiles(CONFIG_PATH)
_INITIAL_PROFILE = fe.normalize_profile(PROFILES.get(ACTIVE_PROFILE) or {})
_INITIAL_PROVIDER = _INITIAL_PROFILE.get("provider", "deepseek")


def _profile(provider="deepseek"):
    for value in PROFILES.values():
        if value.get("provider") == provider:
            return fe.normalize_profile(value)
    return fe.normalize_profile({"provider": provider})


def _provider_key(provider):
    """只从环境变量读取长期凭据；界面输入的 Key 仅在当前进程内使用。"""
    cfg = fe.provider_config(provider)
    return os.environ.get(cfg.get("env_key", ""), "")


def _load_saved_key(provider="deepseek"):
    """兼容旧调用名，但不再从 config.json 返回明文凭据。"""
    return _provider_key(provider)


def _save_profile(provider, api_key, base_url, model, thinking_mode="auto", thinking_param=""):
    """仅保存非敏感模型设置；api_key 参数保留用于兼容现有调用。"""
    del api_key
    name = next((n for n, p in PROFILES.items() if p.get("provider") == provider), "默认")
    PROFILES[name] = fe.normalize_profile({"provider": provider,
                                            "base_url": base_url, "model": model,
                                            "thinking_mode": thinking_mode,
                                            "thinking_param": thinking_param})
    try:
        fe.save_profiles(CONFIG_PATH, PROFILES, name)
        return True
    except OSError:
        return False


def _provider_defaults(provider):
    cfg = fe.provider_config(provider)
    models = cfg.get("models", [])
    saved = _profile(provider)
    url = saved.get("base_url") or cfg["base_url"]
    return url, models, _provider_key(provider), (saved.get("model") or (models[0] if models else ""))


def _thinking_kwargs(provider, mode="auto", param=""):
    """将思考强度转换为提供商兼容参数；请求层负责不支持时回退。"""
    return fe.thinking_kwargs(provider, mode, param)
