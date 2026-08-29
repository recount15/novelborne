"""规则引擎服务层：把自然语言规则收敛为可测试的确定性机制。

机制按职责分散到单一职责的子模块，统一由本包对外暴露：

- ``engine.golden_finger`` 动态金手指推荐与自定义确认状态机
- ``engine.ripple``       涟漪 L0–L4、积势账目、相容性 K、难度解析
- ``engine.faction``      阵营势差评估与宿敌难度非线性计算
- ``engine.tropes``       桥段库加载检索、九风格分类、模板渲染
- ``engine.roster``       伙伴/女主/宿敌提示块装配
- ``engine.budget``       章节回合预算（转发 ``chapter_tools``）
- ``engine.textkit``      机制间共用的文本切分工具
- ``engine.skill_drift``  性格 Skill 动态更新（6 维向量 + 近期倾向提示块）
- ``engine.break_anchor`` 碎锚任务（积势进度、多阶段、成功降锚）

除金手指外的机制名字采用模块级惰性导入：``engine.search_tropes`` 这类属性
访问依然可用，但只有真正用到时才会加载对应子模块（``tropes`` 需要读 5 个
JSON），因此 ``import engine`` 本身保持轻量，不拖慢启动。
"""
from __future__ import annotations

from typing import Any

from .golden_finger import (
    CUSTOM_LABEL,
    GF_EXPONENT,
    MAX_ATTEMPTS,
    NONE_LABEL,
    NONE_SPEC,
    GoldenFingerSpec,
    apply_block,
    choices,
    confirm_custom,
    gf_scale,
    is_custom,
    is_none,
    propose_custom,
    recommend,
    resolve,
)

# 公开名字 -> 所属子模块，供模块级 __getattr__ 惰性解析。
_LAZY_EXPORTS: dict[str, str] = {
    name: "ripple" for name in (
        "ImpactLevel", "RippleAssessment", "RippleEntry", "RippleLedger",
        "difficulty_number", "ripple_threshold", "impact_level", "assess_ripple",
        "compatibility_k", "k_value", "anchor_outcome",
    )
}
_LAZY_EXPORTS.update({
    name: "faction" for name in (
        "MemberAssessment", "infer_power", "assess_member", "assess_faction_gap",
        "nemesis_difficulty",
    )
})
_LAZY_EXPORTS.update({
    name: "tropes" for name in (
        "STYLE_NAMES", "STYLE_KEYWORDS", "DEFAULT_TROPE_FILES", "classify_style",
        "classify_style_scores", "Trope", "TropeStore", "load_tropes", "search_tropes",
        "data_dir", "load_store", "default_store", "clear_store_cache",
        "instantiate_template", "render_reaction", "render_converge",
    )
})
_LAZY_EXPORTS.update({
    name: "roster" for name in (
        "ROLES", "build_character_block", "build_companion_block",
        "build_heroine_block", "build_nemesis_block",
    )
})
_LAZY_EXPORTS["turn_budget"] = "budget"
_LAZY_EXPORTS["tokens"] = "textkit"
_LAZY_EXPORTS["split_values"] = "textkit"
_LAZY_EXPORTS.update({
    name: "participation" for name in (
        "PARTICIPATION_MIN", "PARTICIPATION_MAX", "SCENE_TARGET", "SCENE_MIN", "SCENE_MAX",
        "RICHNESS_MIN", "RICHNESS_MAX", "RICHNESS_DEFAULT", "RICHNESS_STEP", "RICHNESS_TIERS",
        "normalize_richness", "richness_tier",
        "compute_participation", "calculate_participation", "participation_decision",
        "scene_budget", "build_scene_budget_prompt", "validate_scene_length",
        "validate_scene_budget", "check_scene_length", "build_interaction_constraint_block",
        "build_character_interaction_block", "validate_character_interaction", "build_anchor_constraint_block", "build_anchor_prompt",
        "validate_anchor_convergence",
        "CONVERGENCE_LEVELS", "CONVERGENCE_DEFAULT", "normalize_convergence",
    )
})
_LAZY_EXPORTS.update({
    name: "options" for name in (
        "OPTION_KEYS", "collect_option_factors", "build_option_factors_block",
        "parse_options", "count_options", "options_ok", "truncate_partial_options",
        "strip_options_block", "match_option_factors", "render_options_block",
    )
})
_LAZY_EXPORTS.update({
    name: "autoplay" for name in (
        "build_autoplay_prompt", "parse_autoplay_choice",
    )
})

_LAZY_EXPORTS.update({
    name: "agent_mode" for name in (
        "MAX_REVISIONS", "build_self_check_prompt", "parse_issues",
        "machine_findings", "local_findings", "build_revise_prompt",
    )
})

_LAZY_EXPORTS.update({
    name: "gf_designer" for name in (
        "compose_spec", "quality_gate", "polish_prompt", "apply_polish",
        "save_spec", "list_specs", "load_spec",
    )
})
_LAZY_EXPORTS.update({
    name: "skill_drift" for name in (
        "AXES", "AXIS_LABELS", "period_for", "blank_profile",
        "init_profiles", "accumulate", "maybe_settle",
        "tick_after_action", "prompt_block", "public_snapshot",
    )
})
_LAZY_EXPORTS.update({
    name: "roster_relevance" for name in (
        "scale_roster", "assess_relevance", "relevance_hint",
        "is_strongly_relevant", "relevance_of",
    )
})

_LAZY_EXPORTS.update({
    name: "gender_guard" for name in (
        "normalize_gender", "probe_gender_from_text", "gender_probe_prompt",
        "parse_gender_probe", "build_gender_constraint", "guard_entries",
        "apply_model_probe",
    )
})

_LAZY_EXPORTS.update({
    name: "break_anchor" for name in (
        "BREAK_THRESHOLDS", "momentum_bar", "can_offer", "template_stages",
        "offer_prompt", "parse_offer", "new_offer", "accept", "settle_stage",
        "apply_success", "apply_fail", "is_anchor_broken",
    )
})

_SUBMODULES = (
    "golden_finger", "ripple", "faction", "tropes", "roster", "budget", "textkit",
    "participation", "options", "autoplay", "agent_mode", "gf_designer",
    "skill_drift", "break_anchor", "name_collision", "gender_guard", "roster_relevance",
    "cheat_code",
    "distill",
)


def __getattr__(name: str) -> Any:
    """惰性解析机制名字与子模块，避免导入本包即加载全部实现。"""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None and name in _SUBMODULES:
        module_name, name = name, ""
    if module_name is None:
        raise AttributeError(f"module 'engine' has no attribute '{name}'")
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = module if not name else getattr(module, name)
    globals()[name or module_name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_SUBMODULES))


__all__ = [
    # 动态金手指
    "GoldenFingerSpec", "NONE_SPEC", "NONE_LABEL", "CUSTOM_LABEL", "MAX_ATTEMPTS",
    "GF_EXPONENT", "gf_scale",
    "recommend", "choices", "is_none", "is_custom", "propose_custom", "confirm_custom",
    "resolve", "apply_block",
    # 涟漪 / 积势 / 相容性 K / 难度
    "ImpactLevel", "RippleAssessment", "RippleEntry", "RippleLedger",
    "difficulty_number", "ripple_threshold", "impact_level", "assess_ripple",
    "compatibility_k", "k_value", "anchor_outcome",
    # 阵营势差 / 宿敌难度
    "MemberAssessment", "infer_power", "assess_member", "assess_faction_gap",
    "nemesis_difficulty",
    # 桥段库 / 风格分类 / 模板渲染
    "STYLE_NAMES", "STYLE_KEYWORDS", "DEFAULT_TROPE_FILES", "classify_style",
    "classify_style_scores", "Trope", "TropeStore", "load_tropes", "search_tropes",
    "data_dir", "load_store", "default_store", "clear_store_cache",
    "instantiate_template", "render_reaction", "render_converge",
    # 人物名册提示块
    "ROLES", "build_character_block", "build_companion_block",
    "build_heroine_block", "build_nemesis_block",
    # 章节回合预算
    "turn_budget",
    # 文本工具
    "tokens", "split_values",
    # 参与度 / 强化模式场景与收束约束
    "PARTICIPATION_MIN", "PARTICIPATION_MAX", "SCENE_TARGET", "SCENE_MIN", "SCENE_MAX",
    "RICHNESS_MIN", "RICHNESS_MAX", "RICHNESS_DEFAULT", "RICHNESS_STEP", "RICHNESS_TIERS",
    "normalize_richness", "richness_tier",
    "compute_participation", "calculate_participation", "participation_decision",
    "scene_budget", "build_scene_budget_prompt", "validate_scene_length",
    "validate_scene_budget", "check_scene_length", "build_interaction_constraint_block",
    "build_character_interaction_block", "validate_character_interaction", "build_anchor_constraint_block", "build_anchor_prompt",
    "validate_anchor_convergence",
    "CONVERGENCE_LEVELS", "CONVERGENCE_DEFAULT", "normalize_convergence",
    # 选项因素排查与字母选项解析
    "OPTION_KEYS", "collect_option_factors", "build_option_factors_block",
    "parse_options", "count_options", "options_ok", "truncate_partial_options",
    "strip_options_block", "match_option_factors", "render_options_block",
    # 金手指设计器
    "compose_spec", "quality_gate", "polish_prompt", "apply_polish",
    "save_spec", "list_specs", "load_spec",
    # 性格 Skill 动态更新
    "AXES", "AXIS_LABELS", "period_for", "blank_profile",
    "init_profiles", "accumulate", "maybe_settle",
    "tick_after_action", "prompt_block", "public_snapshot",
    # 选角剧情相关度与缩放
    "scale_roster", "assess_relevance", "relevance_hint",
    "is_strongly_relevant", "relevance_of",
    # 穿越性别保障
    "normalize_gender", "probe_gender_from_text", "gender_probe_prompt",
    "parse_gender_probe", "build_gender_constraint", "guard_entries",
    "apply_model_probe",
    # 碎锚（public_snapshot 经 engine.break_anchor 取，避免与 skill_drift 撞名）
    "BREAK_THRESHOLDS", "momentum_bar", "can_offer", "template_stages",
    "offer_prompt", "parse_offer", "new_offer", "accept", "settle_stage",
    "apply_success", "apply_fail", "is_anchor_broken",
]
