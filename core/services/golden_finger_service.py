"""Pre-game golden-finger drafting and correction service.

Model output is advisory only.  Deterministic guards retain the original draft on
invalid corrections and provide a stable fallback spec when generation fails.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from core.engine import golden_finger
from core.engine.gf_designer import (LOCKED_LIMITS_TEXT, apply_polish, as_spec,
                                     compose_spec, quality_gate)
from core.services.pre_game_service import PreGameError, PreGameService


class GoldenFingerError(ValueError):
    pass


def deterministic_budget(prepared_script: Mapping[str, Any] | str, difficulty: Any = 4) -> dict[str, int]:
    """Return a reproducible budget derived only from prepared script and D-level."""
    text = str(prepared_script.get("script") if isinstance(prepared_script, Mapping) else prepared_script or "")
    level = golden_finger._difficulty_num(difficulty)
    # Longer prepared packages get a little more room; difficulty tightens it.
    target = max(120, min(900, 180 + len(text) // 35 - level * 5))
    minimum = max(80, target * 3 // 4)
    maximum = min(1200, target * 5 // 4)
    return {"target": target, "minimum": minimum, "maximum": maximum}


def _mechanism_ok(spec: Mapping[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    effect = str(spec.get("effect") or "")
    limits = str(spec.get("limits") or "")
    forbidden = ("全知", "无限", "无敌", "改写现实", "抹除一切", "凭空创造")
    if any(word in effect for word in forbidden):
        issues.append("机制越界：效果包含不可验证或全局性能力")
    if not limits or any(item not in limits for item in ("不得抹除既成事实", "不得越过世界上限", "必须可验证")):
        issues.append("机制越界：三条锁定限制不完整")
    return not issues, issues


def validate_spec(spec: Mapping[str, Any], difficulty: Any = 4,
                  budget: Mapping[str, int] | None = None) -> dict[str, Any]:
    data = as_spec(spec).to_dict()
    gate = quality_gate(data, difficulty=difficulty)
    ok_mech, mech_issues = _mechanism_ok(data)
    issues = list(gate.get("issues") or []) + mech_issues
    if budget:
        # The budget governs generated narrative/proposals, not the fixed-size
        # JSON envelope of a specification.  Callers may provide ``proposal``
        # explicitly when they want this guard applied.
        proposal = spec.get("proposal") if isinstance(spec, Mapping) else None
        if proposal is not None and len(str(proposal)) > int(budget.get("maximum", 10**9)):
            issues.append("提案超过确定性预算")
    return {"ok": not issues, "issues": issues, "spec": data}


def fallback_spec(difficulty: Any = 4, world: str = "", persona: str = "") -> dict[str, Any]:
    level = golden_finger._difficulty_num(difficulty)
    candidates = golden_finger.recommend(world, persona, f"D{level}", limit=1)
    spec = candidates[0] if candidates else compose_spec({"composition": "信息", "difficulty": f"D{level}", "cost": "精神负荷", "cooldown": "每日一次"})
    return as_spec(spec).to_dict()


def correct_spec(original: Mapping[str, Any], model_fn: Callable[[str], Any] | None = None,
                 *, model_text: str = "", world: str = "", difficulty: Any = 4,
                 budget: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Return original/revised/reasons; only a fully legal revision is accepted."""
    base = as_spec(original).to_dict()
    reasons: list[str] = []
    prompt = "请仅返回 JSON 金手指规格；保留机制边界、代价、冷却与三条限制。" + json.dumps(base, ensure_ascii=False)
    text = model_text
    if not text and model_fn:
        try:
            text = str(model_fn(prompt) or "")
        except Exception as exc:  # deterministic graceful fallback
            reasons.append(f"AI 修正失败：{exc}")
    revised = as_spec(base).to_dict()
    if text:
        candidate = apply_polish(as_spec(base), text).to_dict()
        check = validate_spec(candidate, difficulty=difficulty, budget=budget)
        if check["ok"]:
            revised = candidate
            if candidate == base:
                reasons.append("AI 修正未产生可接受变化，保留原稿")
        else:
            reasons.extend(check["issues"])
    else:
        reasons.append("未提供 AI 修正，保留原稿")
    if revised == base and not reasons:
        reasons.append("原稿已通过确定性质量、预算与机制检查")
    return {"original": base, "revised": revised, "reasons": reasons,
            "accepted": revised != base or not reasons,
            "fallback": False}


def prepare_golden_finger(state: dict[str, Any], draft: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Advance a pre-game state from difficulty_ready to gf_draft_ready."""
    svc = PreGameService(state)
    if svc.stage != "difficulty_ready":
        raise GoldenFingerError("必须先完成 difficulty_ready")
    difficulty = svc.state["difficulty"]
    prepared = svc.state["prepared_script"]
    data = dict(draft or {})
    data.setdefault("difficulty", difficulty.get("label", "D4"))
    data.setdefault("cost", "精神负荷")
    data.setdefault("cooldown", "每日一次")
    data.setdefault("composition", "信息")
    spec = compose_spec(data)
    budget = deterministic_budget(prepared, difficulty.get("level", 4))
    check = validate_spec(spec.to_dict(), difficulty.get("label", "D4"), budget)
    if not check["ok"]:
        spec = as_spec(fallback_spec(difficulty.get("label", "D4"), difficulty.get("world", ""), difficulty.get("player", "")))
    return svc.mark("gf_draft_ready", gf_draft={"spec": spec.to_dict(), "budget": budget, "check": check})


def correct_golden_finger(state: dict[str, Any], model_fn: Callable[[str], Any] | None = None,
                          *, model_text: str = "") -> dict[str, Any]:
    svc = PreGameService(state)
    if svc.stage != "gf_draft_ready":
        raise GoldenFingerError("必须先完成 gf_draft_ready")
    draft = svc.state["gf_draft"]
    diff = svc.state["difficulty"]
    result = correct_spec(draft["spec"], model_fn, model_text=model_text,
                          world=diff.get("world", ""), difficulty=diff.get("label", "D4"), budget=draft.get("budget"))
    return svc.mark("gf_corrected", gf_correction=result, gf_spec=result["revised"])


def confirm_golden_finger(state: dict[str, Any], confirmed: bool = True) -> dict[str, Any]:
    svc = PreGameService(state)
    if svc.stage != "gf_corrected":
        raise GoldenFingerError("必须先完成 gf_corrected")
    if not confirmed:
        raise GoldenFingerError("金手指未确认")
    check = validate_spec(svc.state["gf_spec"], svc.state["difficulty"].get("label", "D4"), svc.state["gf_draft"].get("budget"))
    if not check["ok"]:
        raise GoldenFingerError("确认失败：" + "；".join(check["issues"]))
    return svc.mark("gf_confirmed", gf_confirmed=True)


def make_game_ready(state: dict[str, Any]) -> dict[str, Any]:
    svc = PreGameService(state)
    if svc.stage != "gf_confirmed":
        raise GoldenFingerError("必须先确认金手指")
    return svc.mark("game_ready")


__all__ = ["GoldenFingerError", "deterministic_budget", "validate_spec", "fallback_spec",
           "correct_spec", "prepare_golden_finger", "correct_golden_finger",
           "confirm_golden_finger", "make_game_ready"]
