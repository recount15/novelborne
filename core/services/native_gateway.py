"""Provider-native requests and provider-neutral text/token usage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GatewayResponse:
    text: str
    # Total input includes Anthropic's separately reported cache reads/writes.
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def normalize_usage(usage: Any, provider: str) -> GatewayResponse:
    def count(key: str) -> int:
        return int(_get(usage, key, 0) or 0)

    if provider == "anthropic":
        cached = count("cache_read_input_tokens")
        created = count("cache_creation_input_tokens")
        return GatewayResponse("", count("input_tokens") + cached + created,
                               count("output_tokens"), cached, created)
    details = _get(usage, "prompt_tokens_details")
    cached = count("prompt_cache_hit_tokens") or int(_get(details, "cached_tokens", 0) or 0)
    return GatewayResponse("", count("prompt_tokens"), count("completion_tokens"), cached)


def _anthropic_request(model: str, history: list, system: str, max_tokens: int,
                       timeout: float | None, extra: dict | None) -> dict:
    options = dict(extra or {})
    # Copy messages: adding cache breakpoints must never mutate session history.
    messages = [dict(message) for message in history]
    kwargs = {"model": model, "max_tokens": options.get("max_tokens", max_tokens),
              "messages": messages}
    if system:
        kwargs["system"] = [{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}]
    elif messages and isinstance(messages[-1].get("content"), str):
        # Single-prompt subcalls have no system layer; cache their reusable input.
        messages[-1]["content"] = [{"type": "text", "text": messages[-1]["content"],
                                    "cache_control": {"type": "ephemeral"}}]
    effective_timeout = timeout if timeout is not None else options.get("timeout")
    if effective_timeout is not None:
        kwargs["timeout"] = effective_timeout
    # Do not leak OpenAI-only stream_options, reasoning_effort or extra_body.
    for key in ("temperature", "top_p", "top_k", "thinking", "stop_sequences"):
        if key in options:
            kwargs[key] = options[key]
    return kwargs


def native_complete(client: Any, provider: str, model: str, prompt: str, *,
                    system: str = "", max_tokens: int = 4000,
                    timeout: float | None = None, extra: dict | None = None) -> GatewayResponse:
    if provider == "anthropic":
        response = client.messages.create(**_anthropic_request(
            model, [{"role": "user", "content": prompt}], system, max_tokens, timeout, extra))
        text = "".join(_get(block, "text", "") for block in _get(response, "content", [])
                       if _get(block, "type") == "text")
    else:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        kwargs = {"model": model, "messages": messages, "max_tokens": max_tokens, **(extra or {})}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = client.chat.completions.create(**kwargs)
        text = str(response.choices[0].message.content or "") if getattr(response, "choices", None) else ""
    result = normalize_usage(_get(response, "usage"), provider)
    result.text = text
    return result


def anthropic_stream(client: Any, model: str, system: str, history: list, *,
                     usage_box: dict | None = None, extra: dict | None = None):
    """Yield cumulative text, close the SDK stream, and collect final usage."""
    kwargs = _anthropic_request(model, history, system, 4000, None, extra)
    with client.messages.stream(**kwargs) as stream:
        text = ""
        for piece in stream.text_stream:
            text += piece
            yield text
        if usage_box is not None:
            usage = normalize_usage(_get(stream.get_final_message(), "usage"), "anthropic")
            usage_box.update(prompt=usage.input_tokens, completion=usage.output_tokens,
                             cache_hit=usage.cached_tokens,
                             cache_creation=usage.cache_creation_tokens)
