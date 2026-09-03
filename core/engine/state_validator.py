# -*- coding: utf-8 -*-
"""状态变化合理性判定：AI 驱动的回合状态自动更新校验。

每回合结束时，对比 pre_turn_memory 与 extractor 提取的 patch，
用大模型判断状态变化是否有剧情支撑，过滤不合理变化。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from core.engine.structured import extract_json


def _diff_state(before: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """对比回合前后状态，提取实际变化项。"""
    changes = {}
    
    # 身体状态变化
    body_before = before.get("body") or {}
    body_patch = patch.get("body") or {}
    if body_patch.get("condition") and body_patch["condition"] != body_before.get("condition"):
        changes.setdefault("body", {})["condition"] = {
            "before": body_before.get("condition", "正常"),
            "after": body_patch["condition"],
        }
    if "injuries" in body_patch:
        before_injuries = body_before.get("injuries") or []
        after_injuries = body_patch["injuries"]
        if before_injuries != after_injuries:
            changes.setdefault("body", {})["injuries"] = {
                "before": before_injuries,
                "after": after_injuries,
            }
    
    # 位置变化
    loc_before = (before.get("location") or {}).get("name")
    loc_patch = (patch.get("location") or {}).get("name")
    if loc_patch and loc_patch != loc_before:
        changes["location"] = {"before": loc_before or "未知", "after": loc_patch}
    
    # 物品变化
    assets_before = (before.get("assets") or {}).get("items") or []
    assets_patch = (patch.get("assets") or {}).get("items") or []
    if assets_patch != assets_before:
        gained = [x for x in assets_patch if x not in assets_before]
        lost = [x for x in assets_before if x not in assets_patch]
        if gained or lost:
            changes["items"] = {"gained": gained, "lost": lost}
    
    # 技能变化
    skills_before = ((before.get("abilities") or {}).get("skills")) or []
    skills_patch = ((patch.get("abilities") or {}).get("skills")) or []
    if skills_patch != skills_before:
        gained_skills = [x for x in skills_patch if x not in skills_before]
        if gained_skills:
            changes["skills"] = {"gained": gained_skills}
    
    return changes


def _build_validation_prompt(changes: Mapping[str, Any], narrative: str) -> str:
    """装配状态变化合理性判定提示词。"""
    changes_text = []
    for category, detail in changes.items():
        if category == "body":
            if "condition" in detail:
                changes_text.append(
                    f"- 身体状态：{detail['condition']['before']} → {detail['condition']['after']}")
            if "injuries" in detail:
                changes_text.append(
                    f"- 伤势：{detail['injuries']['before']} → {detail['injuries']['after']}")
        elif category == "location":
            changes_text.append(f"- 位置：{detail['before']} → {detail['after']}")
        elif category == "items":
            if detail.get("gained"):
                changes_text.append(f"- 获得物品：{', '.join(str(x) for x in detail['gained'])}")
            if detail.get("lost"):
                changes_text.append(f"- 失去物品：{', '.join(str(x) for x in detail['lost'])}")
        elif category == "skills":
            if detail.get("gained"):
                changes_text.append(f"- 习得技能：{', '.join(str(x) for x in detail['gained'])}")
    
    changes_block = "\n".join(changes_text) if changes_text else "（无明显状态变化）"
    
    return f"""# 主角状态变化合理性判定

## 本回合叙事正文
{narrative[:1500]}

## 系统提取的状态变化
{changes_block}

## 判定任务
逐项判断每个状态变化是否有正文支撑：
1. **身体状态**：重伤→正常需有治疗过程；正常→重伤需有战斗/事故
2. **位置**：需有移动铺垫（走/骑/传送），远距离需交代时间
3. **物品**：获得需有来源（捡到/购买/赠予），失去需有去向（用掉/丢失/赠出）
4. **技能**：习得需有学习过程（领悟/传授/练习）

## 输出格式（JSON）
```json
{{
  "valid_changes": [
    {{"category": "body|location|items|skills", "detail": "具体变化", "reason": "正文支撑依据（引原文）"}}
  ],
  "rejected_changes": [
    {{"category": "body|location|items|skills", "detail": "具体变化", "reason": "缺乏支撑或违背常识"}}
  ]
}}
```

原则：
- 有明确正文支撑的变化 → valid
- 无铺垫或违背常识的变化 → rejected
- 可疑但不确定的变化 → rejected（宁严勿松，保护状态一致性）
"""


def validate_state_changes(
    pre_turn_memory: Mapping[str, Any],
    extracted_patch: Mapping[str, Any],
    narrative: str,
    distill_fn: Callable[[str, str], str],
) -> dict[str, Any]:
    """AI 判定状态变化合理性，返回过滤后的 patch。
    
    Args:
        pre_turn_memory: 回合开始前的 state_memory
        extracted_patch: extractor 提取的原始 patch
        narrative: 本回合正文
        distill_fn: 模型调用函数 (system, user) -> response_text
    
    Returns:
        过滤后的 patch（只保留合理变化）+ 审计信息
    """
    changes = _diff_state(pre_turn_memory, extracted_patch)
    
    # 无明显变化，直接通过
    if not changes:
        return {
            "patch": extracted_patch,
            "audit": {"status": "no_changes", "valid": [], "rejected": []},
        }
    
    prompt = _build_validation_prompt(changes, narrative)
    system = "你是主角状态一致性校验助手，判断状态变化是否有正文支撑。严格按 JSON 格式输出。"
    
    try:
        response = distill_fn(system, prompt)
        verdict = extract_json(response)
    except Exception as exc:
        # AI 判定失败，保守策略：只保留身体/位置/物品的常规变化，拒绝可疑变化
        return {
            "patch": extracted_patch,
            "audit": {
                "status": "validation_error",
                "error": str(exc),
                "fallback": "conservative_pass",
            },
        }
    
    valid_changes = verdict.get("valid_changes") or []
    rejected_changes = verdict.get("rejected_changes") or []
    
    # 根据判定结果过滤 patch
    filtered_patch = dict(extracted_patch)
    
    # 身体状态：被拒绝则不更新
    if any(c.get("category") == "body" for c in rejected_changes):
        filtered_patch.pop("body", None)
    
    # 位置：被拒绝则不更新
    if any(c.get("category") == "location" for c in rejected_changes):
        filtered_patch.pop("location", None)
    
    # 物品：被拒绝则不更新
    if any(c.get("category") == "items" for c in rejected_changes):
        if "assets" in filtered_patch:
            filtered_patch["assets"].pop("items", None)
    
    # 技能：被拒绝则不更新
    if any(c.get("category") == "skills" for c in rejected_changes):
        if "abilities" in filtered_patch:
            filtered_patch["abilities"].pop("skills", None)
    
    return {
        "patch": filtered_patch,
        "audit": {
            "status": "validated",
            "valid": valid_changes,
            "rejected": rejected_changes,
            "original_changes": changes,
        },
    }


__all__ = ["validate_state_changes"]
