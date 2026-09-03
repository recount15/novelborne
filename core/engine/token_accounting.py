# -*- coding: utf-8 -*-
"""Token 使用量计量（阶段 6E）。

在 distill_model choke point 捕获所有模型调用的 usage，回合级累加，
并实时更新 UI。支持非流式直接取 response.usage，流式通过 usage_box。
"""
from __future__ import annotations

import contextvars
from typing import Any

#: 回合级 Token 累加器（contextvar，支持并发）
_turn_usage: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar(
    "_turn_usage", default=None
)


def init_turn_usage() -> dict[str, int]:
    """初始化回合级累加器（每回合开始时调用）。"""
    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        # 分项
        "director": 0,
        "segments": 0,
        "options": 0,
        "polish": 0,
        "gate": 0,
        "quest": 0,
        "chat": 0,
        "other": 0,
    }
    _turn_usage.set(usage)
    return usage


def get_turn_usage() -> dict[str, int] | None:
    """获取当前回合累加器。"""
    return _turn_usage.get(None)


def record_usage(prompt_tokens: int, completion_tokens: int, category: str = "other") -> None:
    """记录一次调用的 usage（累加到回合级）。"""
    usage = get_turn_usage()
    if usage is None:
        # 回合外调用（chat/quest offer 等），不累加到回合级
        return
    
    usage["prompt_tokens"] += prompt_tokens
    usage["completion_tokens"] += completion_tokens
    usage["total_tokens"] += prompt_tokens + completion_tokens
    
    # 分项累加
    if category in usage:
        usage[category] += prompt_tokens + completion_tokens
    else:
        usage["other"] += prompt_tokens + completion_tokens


def extract_usage(response: Any) -> dict[str, int] | None:
    """从 OpenAI response 对象提取 usage。"""
    if not hasattr(response, "usage"):
        return None
    
    usage_obj = response.usage
    if not usage_obj:
        return None
    
    return {
        "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
    }


def estimate_tokens(text: str) -> int:
    """估算文本 token 数（中文 ~1.5 字/token，英文 ~4 字符/token）。"""
    if not text:
        return 0
    
    # 简单规则：中文字数 / 1.5 + 英文字符数 / 4
    chinese_count = sum(1 for char in text if '\u4e00' <= char <= '\u9fff')
    total_len = len(text)
    english_count = total_len - chinese_count
    
    return int(chinese_count / 1.5 + english_count / 4)


__all__ = [
    "init_turn_usage",
    "get_turn_usage",
    "record_usage",
    "extract_usage",
    "estimate_tokens",
]
