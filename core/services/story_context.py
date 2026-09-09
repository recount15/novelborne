"""Layered context budgeting."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Sequence

def estimate_tokens(text: str) -> int:
    """Conservative UTF-8 byte fallback; not an exact token count."""
    return len(str(text or "").encode("utf-8", errors="surrogatepass"))

@dataclass
class ContextLayer:
    name: str; text: str; priority: int = 10; required: bool = False

def fit_layers(layers: Sequence[ContextLayer], provider_limit: int = 200000, output_budget: int = 18000, safety: int = 8000, *, token_counter: Callable[[str], int] | None = None) -> dict[str, Any]:
    for name, value in (("provider_limit", provider_limit), ("output_budget", output_budget), ("safety", safety)):
        if isinstance(value, bool) or not isinstance(value, int): raise TypeError(f"{name} must be a nonnegative integer")
        if value < 0: raise ValueError(f"{name} must be nonnegative")
    counter = token_counter or estimate_tokens
    available = provider_limit - output_budget - safety
    budget = max(0, min(185000, available))
    reserve_overflow = max(0, -available)
    counted = []
    for layer in layers:
        cost = counter(layer.text)
        if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0: raise ValueError("token_counter must return a nonnegative integer")
        counted.append((layer, cost))
    counted.sort(key=lambda x: (not x[0].required, x[0].priority))
    required_tokens = sum(c for l, c in counted if l.required)
    selected, dropped, used, audit = [], [], required_tokens, []
    for layer, cost in counted:
        keep = layer.required or (not reserve_overflow and used + cost <= budget)
        if keep:
            selected.append(ContextLayer(layer.name, layer.text, layer.priority, layer.required))
            if not layer.required: used += cost
        else: dropped.append(layer.name)
        audit.append({"name": layer.name, "required": layer.required, "estimated_tokens": cost, "selected": keep})
    overflow = max(0, used - budget) + reserve_overflow
    return {"layers": selected, "estimated_input": used, "budget": budget, "dropped_blocks": dropped, "ok": overflow == 0, "overflow": overflow > 0, "overflow_tokens": overflow, "required_overflow_tokens": max(0, required_tokens-budget), "required_tokens": required_tokens, "optional_tokens": used-required_tokens, "estimator": "injected_token_counter" if token_counter else "utf8_bytes_conservative", "layer_audit": audit}
