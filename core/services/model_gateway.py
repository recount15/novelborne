"""模型运行时固定接线。

服务层和 API 层通过本模块解析凭据、创建客户端并调用蒸馏模型，避免各端点
重复实现 provider/base_url/api_key/model 的优先级与超时传递。模块不依赖
FastAPI，调用方保留自己的 HTTP 异常映射和用户可见文案。
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from core import fate_engine as fe
from core.engine.distill import distill_model


@dataclass(frozen=True)
class ModelRuntime:
    provider: str
    base_url: str | None
    api_key: str
    model: str
    client: Any


def resolve_runtime(
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
) -> ModelRuntime:
    """按现有优先级构造模型运行时，不持久化任何凭据。"""
    resolved_provider = (provider or "deepseek").strip() or "deepseek"
    config = fe.provider_config(resolved_provider, base_url)
    resolved_base_url = (base_url or config.get("base_url") or "").strip() or None
    resolved_key = (api_key or "").strip() or os.environ.get(config.get("env_key", ""), "")
    resolved_model = (model or config.get("model") or "").strip()
    return ModelRuntime(
        provider=resolved_provider,
        base_url=resolved_base_url,
        api_key=resolved_key,
        model=resolved_model,
        client=fe.make_client(resolved_key, resolved_provider, resolved_base_url),
    )


def run_prompt(
    runtime: ModelRuntime,
    prompt: str,
    extra_kwargs: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> str:
    """执行单次模型调用；调用方负责领域校验及异常映射。"""
    return distill_model(
        runtime.client,
        runtime.model,
        prompt,
        extra_kwargs,
        runtime.provider,
        timeout=timeout,
    )
