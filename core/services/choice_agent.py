"""Internal option planning path; public output remains A-F compatible."""
from __future__ import annotations
import json
import os
from typing import Any, Callable, Mapping, Sequence


def filter_candidates(candidates: Sequence[Mapping[str, Any]], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in candidates or ():
        if not str(item.get("action") or item.get("text") or "").strip(): continue
        if item.get("requires_future_knowledge") or item.get("patch_valid") is False: continue
        result.append(dict(item))
    return result


def select_diverse(candidates: Sequence[Mapping[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    chosen, seen = [], set()
    for item in candidates or ():
        text = str(item.get("text") or item.get("action") or "").strip()
        sig = str(item.get("novelty_signature") or text)[:120].lower()
        tags = tuple(sorted(str(x) for x in (item.get("tags") or item.get("coverage") or [])))
        if not text or sig in seen or (tags and ("tags", tags) in seen): continue
        chosen.append(dict(item)); seen.add(sig); seen.add(("tags", tags))
        if len(chosen) >= limit: break
    return chosen


def public_options(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for i, item in enumerate(candidates[:6]):
        text = str(item.get("text") or item.get("action") or "").strip()[:60]
        if text: result.append({"key": "ABCDEF"[i], "text": text,
            "preview": str(item.get("preview") or item.get("risk") or "").strip()[:60],
            "factor": str(item.get("factor") or "剧情")})
    return result


def generate_options(state: Mapping[str, Any], model_fn: Callable[[str], Any] | None = None,
                     *, prompt: str = "") -> dict[str, Any]:
    """Generate and deterministically filter model candidates; never fabricates options."""
    candidates = list(state.get("choice_candidates") or [])
    meta = {"candidate_count": len(candidates)}
    if model_fn:
        try:
            raw = model_fn(prompt or "Return JSON array of executable story choices.")
            if isinstance(raw, str): raw = json.loads(raw)
            if isinstance(raw, Mapping): raw = raw.get("options") or raw.get("candidates")
            if isinstance(raw, list): candidates.extend(x for x in raw if isinstance(x, Mapping))
        except Exception as exc: meta["model_error"] = str(exc)
    selected = public_options(select_diverse(filter_candidates(candidates, state)))
    if len(selected) != 6: return {"options": [], "source": "none", "meta": meta, "error": "choice agent requires six valid options"}
    return {"options": selected, "source": "choice_agent", "meta": meta}


def mode() -> str:
    value = os.getenv("STORY_CHOICE_AGENT_MODE", "legacy").strip().lower()
    return value if value in {"legacy", "shadow", "agent"} else "legacy"

__all__ = ["filter_candidates", "select_diverse", "public_options", "generate_options", "mode"]
