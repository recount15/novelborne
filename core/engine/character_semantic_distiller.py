# -*- coding: utf-8 -*-
"""Evidence-grounded four-dimension character distillation.

The transport is deliberately injected as a ``str -> str`` callable. Opening
distillation supplies the existing ``distill_model`` wrapper through its
parallel budget path; this module never talks to a model SDK directly.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core import prompts
from core.engine import structured

Model = Callable[[str], Any]

SCHEMA_VERSION = 1
SEMANTIC_FIELDS: Tuple[str, ...] = (
    "mind_model",
    "decision_policy",
    "voice_transfer",
    "behavior_boundaries",
)

FIELD_LABELS = {
    "mind_model": "心智模型",
    "decision_policy": "决策策略",
    "voice_transfer": "声线迁移",
    "behavior_boundaries": "行为边界",
}

SEMANTIC_SPECS: Tuple[structured.FieldSpec, ...] = tuple(
    structured.FieldSpec(
        name,
        "dict",
        hint="对象，必须包含 rules 字符串数组和 evidence 证据数组",
    )
    for name in SEMANTIC_FIELDS
)

MAX_RULES = 6
MAX_EVIDENCE = 4
MAX_RULE_TEXT = 160
MAX_QUOTE_TEXT = 240
MAX_INTERPRETATION_TEXT = 180
MAX_SOURCE_CHAPTERS = 4
MAX_SOURCE_CHARS_PER_CHAPTER = 3200


def empty_semantic_fields() -> Dict[str, Dict[str, list]]:
    """Return a fresh backward-compatible empty semantic payload."""
    return {name: {"rules": [], "evidence": []} for name in SEMANTIC_FIELDS}


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _as_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def normalize_dimension(
    value: Any,
    *,
    source_by_chapter: Optional[Mapping[int, str]] = None,
    strict: bool = False,
) -> Tuple[Dict[str, list], List[str]]:
    """Normalize one dimension and optionally enforce exact source evidence.

    Stored historical cards use ``strict=False`` and may therefore remain
    empty. Model output uses ``strict=True`` and must provide at least one rule
    and one exact quote for every dimension.
    """
    errors: List[str] = []
    raw = _as_mapping(value)
    if raw is None:
        if value not in (None, "", {}):
            errors.append("维度必须是对象")
        if strict:
            errors.append("维度缺少 rules/evidence")
        return {"rules": [], "evidence": []}, errors

    raw_rules = raw.get("rules")
    if isinstance(raw_rules, str) and not strict:
        raw_rules = [raw_rules]
    if not isinstance(raw_rules, (list, tuple)):
        if strict:
            errors.append("rules 必须是字符串数组")
        raw_rules = []
    rules: List[str] = []
    for index, item in enumerate(raw_rules):
        text = _text(item, MAX_RULE_TEXT)
        if not text:
            if strict:
                errors.append("rules 第 %d 项为空" % (index + 1))
            continue
        if text not in rules:
            rules.append(text)
        if len(rules) >= MAX_RULES:
            break

    raw_evidence = raw.get("evidence")
    if not isinstance(raw_evidence, (list, tuple)):
        if strict:
            errors.append("evidence 必须是证据对象数组")
        raw_evidence = []
    evidence: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_evidence):
        if not isinstance(item, Mapping):
            if strict:
                errors.append("evidence 第 %d 项必须是对象" % (index + 1))
            continue
        try:
            chapter = int(item.get("chapter") or 0)
        except (TypeError, ValueError):
            chapter = 0
        quote = _text(item.get("quote"), MAX_QUOTE_TEXT)
        interpretation = _text(item.get("interpretation"), MAX_INTERPRETATION_TEXT)
        if chapter < 1:
            if strict:
                errors.append("evidence 第 %d 项 chapter 必须是正整数" % (index + 1))
            continue
        if not quote:
            if strict:
                errors.append("evidence 第 %d 项缺少 quote" % (index + 1))
            continue
        if strict and not interpretation:
            errors.append("evidence 第 %d 项缺少 interpretation" % (index + 1))
            continue
        if source_by_chapter is not None:
            source = str(source_by_chapter.get(chapter) or "")
            if not source:
                errors.append("evidence 第 %d 项引用了未提供的第 %d 章" % (index + 1, chapter))
                continue
            if quote not in source:
                errors.append("evidence 第 %d 项 quote 不是第 %d 章原文逐字片段" % (
                    index + 1, chapter))
                continue
        evidence.append({
            "chapter": chapter,
            "quote": quote,
            "interpretation": interpretation,
        })
        if len(evidence) >= MAX_EVIDENCE:
            break

    if strict and not rules:
        errors.append("rules 至少需要 1 条可执行规则")
    if strict and not evidence:
        errors.append("evidence 至少需要 1 条原文证据")
    return {"rules": rules, "evidence": evidence}, errors


def normalize_semantic_fields(payload: Mapping[str, Any] | None) -> Dict[str, Dict[str, list]]:
    """Normalize all four dimensions for catalog, JSON, and API boundaries."""
    payload = payload if isinstance(payload, Mapping) else {}
    result: Dict[str, Dict[str, list]] = {}
    for name in SEMANTIC_FIELDS:
        result[name], _errors = normalize_dimension(payload.get(name), strict=False)
    return result


def validate_distilled_fields(
    payload: Mapping[str, Any], source_by_chapter: Mapping[int, str]
) -> Tuple[Optional[Dict[str, Dict[str, list]]], List[str]]:
    """Strictly validate model output, including exact quote containment."""
    normalized: Dict[str, Dict[str, list]] = {}
    errors: List[str] = []
    for name in SEMANTIC_FIELDS:
        dimension, dimension_errors = normalize_dimension(
            payload.get(name), source_by_chapter=source_by_chapter, strict=True)
        normalized[name] = dimension
        errors.extend("%s：%s" % (name, error) for error in dimension_errors)
    return (None if errors else normalized), errors


def grounded_dimensions(payload: Mapping[str, Any] | None) -> Tuple[str, ...]:
    """Return dimensions that contain both executable rules and evidence."""
    normalized = normalize_semantic_fields(payload)
    return tuple(
        name for name in SEMANTIC_FIELDS
        if normalized[name]["rules"] and normalized[name]["evidence"]
    )


def _source_excerpt(text: str, name: str) -> str:
    """Keep exact source windows while limiting prompt size."""
    if len(text) <= MAX_SOURCE_CHARS_PER_CHAPTER:
        return text
    positions: List[int] = []
    start = 0
    while name and len(positions) < 3:
        found = text.find(name, start)
        if found < 0:
            break
        positions.append(found)
        start = found + len(name)
    if not positions:
        return text[:MAX_SOURCE_CHARS_PER_CHAPTER]
    window = max(400, MAX_SOURCE_CHARS_PER_CHAPTER // len(positions))
    parts: List[str] = []
    for position in positions:
        left = max(0, position - window // 2)
        right = min(len(text), left + window)
        parts.append(text[left:right])
    return "\n[同章原文续段]\n".join(parts)[:MAX_SOURCE_CHARS_PER_CHAPTER]


def _source_context(
    card: Mapping[str, Any], chapters: Sequence[Mapping[str, Any]]
) -> Tuple[Dict[int, str], str]:
    name = str(card.get("name") or "").strip()
    relevant: List[Tuple[int, str]] = []
    fallback: List[Tuple[int, str]] = []
    for chapter in chapters or ():
        try:
            number = int(chapter.get("idx") or chapter.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        text = str(chapter.get("text") or "")
        if number < 1 or not text:
            continue
        fallback.append((number, text))
        if name and name in text:
            relevant.append((number, text))
    selected = (relevant or fallback)[:MAX_SOURCE_CHAPTERS]
    sections = [
        "【第%d章原文】\n%s" % (number, _source_excerpt(text, name))
        for number, text in selected
    ]
    selected_sources = {number: text for number, text in selected}
    return selected_sources, "\n\n".join(sections)


def build_prompt(card: Mapping[str, Any], source_text: str) -> str:
    """Render the dedicated semantic distillation prompt and schema."""
    return prompts.render(
        "opening_character_semantics.md",
        CHARACTER=json.dumps(dict(card), ensure_ascii=False),
        SOURCE=source_text,
        FIELD_SPECS=structured.spec_prompt(SEMANTIC_SPECS),
    )


def distill_character(
    model: Model,
    card: Mapping[str, Any],
    chapters: Sequence[Mapping[str, Any]],
    *,
    attempts: int = 2,
) -> Tuple[Optional[Dict[str, Dict[str, list]]], Dict[str, Any]]:
    """Distill and source-validate one character's four semantic dimensions."""
    source_by_chapter, source_text = _source_context(card, chapters)
    meta: Dict[str, Any] = {"attempts": 0, "errors": [], "grounded": []}
    if not source_by_chapter:
        meta["errors"].append("没有可用于角色语义蒸馏的章节原文")
        return None, meta

    base_prompt = build_prompt(card, source_text)
    retry_errors: List[str] = []
    for attempt in range(max(1, int(attempts))):
        meta["attempts"] = attempt + 1
        prompt = base_prompt
        if retry_errors:
            prompt += (
                "\n\n【上次嵌套字段校验失败】\n"
                + "\n".join("- " + error for error in retry_errors)
                + "\n请修正后重新输出完整 JSON。"
            )
        data, call_meta = structured.structured_call(
            model, prompt, SEMANTIC_SPECS, attempts=1)
        meta["errors"].extend(list((call_meta or {}).get("errors") or []))
        if data is None:
            retry_errors = list((call_meta or {}).get("errors") or ["结构化输出无效"])
            continue
        normalized, retry_errors = validate_distilled_fields(data, source_by_chapter)
        if normalized is not None:
            meta["grounded"] = list(SEMANTIC_FIELDS)
            return normalized, meta
        meta["errors"].extend(retry_errors)
    return None, meta


def render_runtime_fields(payload: Mapping[str, Any] | None) -> str:
    """Render compact executable semantic constraints for runtime prompts."""
    normalized = normalize_semantic_fields(payload)
    lines: List[str] = []
    for name in SEMANTIC_FIELDS:
        dimension = normalized[name]
        if not dimension["rules"]:
            continue
        lines.append("%s：%s" % (FIELD_LABELS[name], "；".join(dimension["rules"])))
        for evidence in dimension["evidence"][:2]:
            lines.append("  证据（第%d章）：%s" % (
                evidence["chapter"], evidence["quote"]))
    return "\n".join(lines)


__all__ = [
    "SCHEMA_VERSION", "SEMANTIC_FIELDS", "FIELD_LABELS", "SEMANTIC_SPECS",
    "empty_semantic_fields", "normalize_dimension", "normalize_semantic_fields",
    "validate_distilled_fields", "grounded_dimensions", "build_prompt",
    "distill_character", "render_runtime_fields",
]
