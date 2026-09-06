"""Character conversation skill with relationship and personality disclosure."""
from __future__ import annotations
from typing import Any, Mapping
from .contract import SkillContext, SkillResult

def _value(character: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        try:
            return float(character.get(key, default) or default)
        except (TypeError, ValueError): pass
    return default

def disclosure_profile(character: Mapping[str, Any], state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    favor = _value(character, "favorability", "affection", "好感", "好感度")
    trust = _value(character, "trust", "信任")
    fear = _value(character, "fear", "恐惧")
    guarded = _value(character, "guardedness", "戒备")
    traits = {str(x).lower() for x in (character.get("traits") or character.get("personality") or [])} if isinstance(character.get("traits") or character.get("personality"), (list, tuple, set)) else {str(character.get("personality", "")).lower()}
    openness = favor * 0.55 + trust * 0.45 - fear * 0.25 - guarded * 0.3
    if "谨慎" in traits or "多疑" in traits: openness -= 0.15
    if "坦率" in traits or "热诚" in traits: openness += 0.15
    level = "high" if openness >= 0.65 else ("medium" if openness >= 0.3 else "low")
    return {"level": level, "openness": round(max(0.0, min(1.0, openness)), 3), "may_deceive": level == "low" or "狡诈" in traits or "多疑" in traits, "motives": ["保护自身利益"] if level == "low" else []}

def chat_skill(ctx: SkillContext, character: Mapping[str, Any], *, message: str = "") -> SkillResult:
    profile = disclosure_profile(character)
    name = str(character.get("name") or character.get("角色") or "目标角色")
    known = list(character.get("known_facts") or [])
    withheld = list(character.get("withheld_facts") or []) if profile["level"] != "high" else []
    proposal = {"target": name, "reply_directive": f"以{name}的性格和当前立场回应：{message[:240]}", "disclosure_level": profile["level"], "truth_claims": known[:6], "withheld_facts": withheld[:6], "deception_claims": [{"claim": x, "motive": "保护自身利益"} for x in withheld[:3]] if profile["may_deceive"] else [], "relationship_delta": {"reason": "对话互动"}, "next_hooks": list(ctx.brief.get("must_progress") or [])[:3]}
    return SkillResult("character_chat", proposal, warnings=[{"code": "low_disclosure", "target": name}] if profile["level"] == "low" else [], degraded=False)
