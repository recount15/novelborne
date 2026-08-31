# -*- coding: utf-8 -*-
"""Gradio ``update`` 的轻量兼容层。

当前产品运行路径是 FastAPI + Vue；旧 ``core.app.build_app`` 仍可在源码环境
动态导入 Gradio，但 API/Windows Release 只需要 ``gr.update`` 产生的普通字典。
避免在生产包中收编 Gradio 及其 pandas/torch/transformers 等可选生态。
"""
from __future__ import annotations

from typing import Any


def update(**kwargs: Any) -> dict[str, Any]:
    """返回与 Gradio 5 ``gr.update(**kwargs)`` 相同的更新字典。"""
    return {**kwargs, "__type__": "update"}


__all__ = ["update"]
