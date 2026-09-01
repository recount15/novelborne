# -*- coding: utf-8 -*-
"""角色状态 patch 中台门面（重构 M4）。

最终正文生成后，针对本回合在场角色生成严格 JSON patch：

  structured_call → character_state_patch.parse_patch_payload（名字/证据子串/
  delta/重复名严校验）→ build_relationship_rows（基于完整旧列表 upsert）→
  apply_turn(source="character_patch") → state_memory/state_panel/active_summaries

失败语义：角色 patch 是增值环节，任何模型/解析/提交失败只记 meta 并跳过，
**绝不阻断主回合**；正文与其他机制照常提交。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence

from core import engine
from core.engine import character_state_patch, parallel, structured
from core.engine.distill import distill_model
from core.memory import blank_state, render_panel
from core.memory.state_store import apply_turn

Model = Callable[[str], Any]


def _names(active_members: Sequence[Any]) -> list[str]:
    names: list[str] = []
    for item in active_members or ():
        name = item.get("name") if isinstance(item, Mapping) else item
        text = str(name or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def generate_patch(state: dict, client, model: str,
                   request_kwargs: dict | None = None, provider: str = "deepseek", *,
                   narrative: str = "", active_members: Sequence[Any] = (),
                   round_no: int = 0, model_fn: Optional[Model] = None,
                   attempts: int = 2) -> dict[str, Any]:
    """生成、校验并提交角色关系 patch；返回观测 meta。

    返回 ``{ok, valid, rejected, changes, error?}``；``ok`` 表示管线自身完成，
    即使模型合法返回空 patches 也算 ok（没有关系变化是正常结果）。
    """
    names = _names(active_members)
    if not names or not str(narrative or "").strip():
        return {"ok": True, "valid": [], "rejected": [], "changes": {},
                "skipped": "本回合无在场角色或无正文"}

    prompt = character_state_patch.build_patch_prompt(active_members, narrative)
    budgeted = parallel.budget_model(
        model_fn or (lambda p: distill_model(client, model, p, request_kwargs, provider)),
        parallel.PRIORITY_TURN)
    try:
        data, call_meta = structured.structured_call(
            budgeted, prompt, character_state_patch.PATCH_SPECS,
            attempts=max(1, int(attempts)))
    except Exception as exc:  # noqa: BLE001 增值环节，传输失败按跳过处理
        return {"ok": False, "valid": [], "rejected": [], "changes": {},
                "error": str(exc), "meta": {}}
    if data is None:
        return {"ok": False, "valid": [], "rejected": [], "changes": {},
                "error": "角色 patch 输出未通过结构化校验", "meta": call_meta}

    valid, rejected = character_state_patch.parse_patch_payload(data, names, narrative)
    if not valid:
        return {"ok": True, "valid": [], "rejected": rejected, "changes": {},
                "meta": call_meta}

    current = state.get("state_memory")
    if not isinstance(current, Mapping):
        current = blank_state(state.get("mode", ""), "")
    relationships = current.get("relationships") if isinstance(current.get("relationships"), Mapping) else {}
    existing = relationships.get("characters") if isinstance(relationships.get("characters"), list) else []
    rows = character_state_patch.build_relationship_rows(valid, existing, round_no)
    patch = character_state_patch.to_memory_patch(rows)
    try:
        updated, changes = apply_turn(current, patch, round_no=round_no,
                                      source=character_state_patch.SOURCE)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "valid": valid, "rejected": rejected,
                "changes": {}, "error": str(exc), "meta": call_meta}

    state["state_memory"] = updated
    state["state_panel"] = render_panel(updated)
    state["active_summaries"] = {
        item["name"]: item["summary"] for item in valid if item.get("summary")
    }
    return {"ok": True, "valid": valid, "rejected": rejected,
            "changes": changes, "meta": call_meta}


__all__ = ["generate_patch"]
