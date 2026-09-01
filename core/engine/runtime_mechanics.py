# -*- coding: utf-8 -*-
"""兼容转发层：剧情运行时机制的实现已全部迁入 ``engine/`` 包。

本文件不再包含任何机制实现，只从 engine 子模块 re-export 原有公开名字，
保证 ``import runtime_mechanics`` 后所有旧的属性访问路径保持不变。

新代码请直接使用 engine 侧的入口：

===============================  ==================================
原路径                            新位置
===============================  ==================================
``RippleLedger`` / 涟漪相关        ``engine.ripple``
``assess_faction_gap`` / 宿敌难度  ``engine.faction``
``TropeStore`` / ``search_tropes``  ``engine.tropes``
``build_companion_block`` 等       ``engine.roster``
===============================  ==================================
"""
from __future__ import annotations

from core.engine.faction import (
    MemberAssessment,
    assess_faction_gap,
    assess_member,
    infer_power,
    nemesis_difficulty,
)
from core.engine.ripple import (
    ImpactLevel,
    RippleAssessment,
    RippleEntry,
    RippleLedger,
    anchor_outcome,
    assess_ripple,
    compatibility_k,
    difficulty_number,
    impact_level,
    k_value,
    ripple_threshold,
)
from core.engine.roster import (
    build_character_block,
    build_companion_block,
    build_heroine_block,
    build_nemesis_block,
)
from core.engine.tropes import (
    STYLE_KEYWORDS,
    STYLE_NAMES,
    Trope,
    TropeStore,
    classify_style,
    classify_style_scores,
    instantiate_template,
    load_tropes,
    render_converge,
    render_reaction,
    search_tropes,
)
from core.engine.participation import (
    PARTICIPATION_MAX,
    PARTICIPATION_MIN,
    RICHNESS_DEFAULT,
    RICHNESS_MAX,
    RICHNESS_MIN,
    RICHNESS_STEP,
    RICHNESS_TIERS,
    SCENE_MAX,
    SCENE_MIN,
    SCENE_TARGET,
    build_anchor_constraint_block,
    build_anchor_prompt,
    build_character_interaction_block,
    build_interaction_constraint_block,
    build_scene_budget_prompt,
    calculate_participation,
    check_scene_length,
    compute_participation,
    normalize_richness,
    participation_decision,
    richness_tier,
    scene_budget,
    validate_anchor_convergence,
    validate_scene_budget,
    validate_scene_length,
)

__all__ = [
    "STYLE_NAMES", "classify_style", "classify_style_scores", "Trope", "TropeStore",
    "load_tropes", "search_tropes", "instantiate_template", "render_reaction",
    "render_converge", "compatibility_k", "k_value", "ImpactLevel", "RippleAssessment",
    "RippleEntry", "RippleLedger", "difficulty_number", "ripple_threshold", "impact_level",
    "assess_ripple", "anchor_outcome", "MemberAssessment", "assess_member",
    "assess_faction_gap", "nemesis_difficulty", "infer_power", "build_character_block",
    "build_companion_block", "build_heroine_block", "build_nemesis_block",
    "PARTICIPATION_MIN", "PARTICIPATION_MAX", "SCENE_TARGET", "SCENE_MIN", "SCENE_MAX",
    "RICHNESS_MIN", "RICHNESS_MAX", "RICHNESS_DEFAULT", "RICHNESS_STEP", "RICHNESS_TIERS",
    "normalize_richness", "richness_tier",
    "compute_participation", "calculate_participation", "participation_decision",
    "scene_budget", "build_scene_budget_prompt", "validate_scene_length",
    "validate_scene_budget", "check_scene_length", "build_interaction_constraint_block",
    "build_character_interaction_block", "build_anchor_constraint_block", "build_anchor_prompt",
    "validate_anchor_convergence",
]
