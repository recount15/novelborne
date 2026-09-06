# -*- coding: utf-8 -*-
"""选项生成中台门面（重构 M2）：6+1 选项出正文，改由结构化填空独立生成。

- 模型经 distill 通道调用，占用回合优先级并发额度（engine.parallel）；
- 输出为恰好 6 条的 JSON 数组（条目为 {"text","factor"} 对象或字符串，
  形状由 parse_option_items + grade_options 严格校验，错误清单回传重试）；
- **情景扎根（v2.0.3）**：选项卷注入当前章原文节选 @@SCENE@@，要求选项
  引用情景中实际存在的人物/地点/物件；提示词层加多样性硬约束；
- **AI-only（v2.0.3 硬性原则）**：结构化产物须过 :func:`turn_grader.grade_options`
  （恰 6 条/A–F/长度/4+2 分布/相似度 ≥0.8 判重/无来源标注）；不合格带错误
  清单重试 1 次；仍不合格则清洗保序保留 ≥4 条 AI 产物（重排键、剔除重复与
  违规条目）。**绝不展示模板句**：模型与正文回退全部失败时 options 为空
  （source="none"，由调用方保留自由输入）。
- 代码分配 A–F 键、拆分「（后果：…）」预告、匹配因素标签（options.match）；
- 回退链：正文残存选项解析（正文本身是模型产物，属 AI 选项）→ 仍失败为空。

HTTP/引擎细节都不在此感知：``model_fn`` 可注入（离线测试），缺省走
distill_model(client, model, ...)。
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from core import engine
from core.engine import parallel, structured, turn_grader
from core.engine.distill import distill_model
from core.prompts import render
from core.services import choice_agent as _choice_agent

Model = Callable[[str], Any]

_SPECS = (
    # M3 契约的选项条目是对象（{"text","factor"}）或字符串两态：structured
    # 的 "list" 语义是字符串数组（同 turn_blueprint segments 的先例），会把
    # 合法对象判成「必须是非空字符串」（kimi-k3 实测复现）。故声明 "any"，
    # 条数/长度/分布/判重由 parse_option_items + grade_options + 清洗严格校验。
    structured.FieldSpec(
        "options", "any", required=True,
        hint="恰好 6 条行动的数组，每项 {\"text\": \"行动内容（后果：一句可观测后果预告）\","
             " \"factor\": \"金手指|性格\"}；4 条由剧情+金手指推导、2 条由玩家性格推导；"
             "不得标注来源"),
)

_PREVIEW_RE = re.compile(r"（后果[：:]\s*(?P<preview>[^）]*)）|\(后果[：:]\s*(?P<preview2>[^)]*)\)")
# 选项文本中残留的来源/分类标注（含无空格变体），清洗时剥离后再量长度。
_SOURCE_LABEL_RE = re.compile(r"[（(]\s*(?:金手指|性格|剧情)\s*[)）]")
# 暴露 AI/模型来源的措辞：此类选项违反 AI-only 情景模拟原则，整条剔除。
_AI_ORIGIN_RE = re.compile(
    r"(?:作为(?:一个)?AI|作为人工智能|AI生成|人工智能生成|由AI提供|模型生成|语言模型|无法替你决定)",
    re.IGNORECASE)
#: 清洗后保留的最少条数：不足此数视为生成失败（宁缺毋滥，保留自由输入）。
MIN_AI_OPTIONS = 4


def build_options_prompt(action: str, factors_block: str, context_tail: str,
                         scene: str = "", blueprint_brief: str = "",
                         variant: int = 0) -> str:
    """装配选项生成卷提示词（@@KEY@@ 占位符由 core.prompts.render 渲染）。

    ``scene`` 为当前章原文节选（~1500 字）：选项必须扎根其中的人/物/局势。
    v2.0.5 S6：``blueprint_brief`` 为导演卷本回合节拍摘要（选项与正文走向
    对齐）；``variant`` 用于 best-of-2 双卷视角差异化（0=剧情走向优先，
    1=玩家处境与代价优先）。
    """
    # variant 1 追加视角指令：与 variant 0 同一硬性契约，只改变侧重。
    variant_hint = (
        "\n\n【本卷侧重】优先从玩家当前处境出发：压力、代价、可动用的资源与"
        "人际筹码；行动须可被玩家立刻执行，风险与收益对等。"
        if variant == 1 else
        "\n\n【本卷侧重】优先顺着剧情发展脉络设计：本回合冲突在下一拍的"
        "走向、各方势力的下一步、悬念钩子的兑现或反转。"
    )
    rendered = render(
        "options_gen.md",
        ACTION=str(action or "（玩家自由行动）").strip()[:300],
        FACTORS=str(factors_block or "").strip() or "（本回合无特殊因素）",
        CONTEXT=str(context_tail or "").strip()[-1200:] or "（开局首轮，无前文）",
        SCENE=str(scene or "").strip()[:1800] or "（无原文情景节选：请以近期剧情与因素为准）",
    )
    brief = str(blueprint_brief or "").strip()
    if brief:
        rendered += ("\n\n【本回合蓝图节拍（选项须与之衔接）】" + brief[:400])
    return rendered + variant_hint


#: choice_agent 内部标记：解析时透传供过滤，出榜前剥离（不入前台契约）。
_AGENT_FLAG_FIELDS = ("requires_future_knowledge", "patch_valid")


def _strip_agent_flags(options: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in dict(item).items() if k not in _AGENT_FLAG_FIELDS}
            for item in options or ()]


def parse_option_items(items: Sequence[Any]) -> List[Dict[str, str]]:
    """把 6 条原始条目拆为 {key, text, preview, factor}；键由代码分配 A–F。

    条目兼容两种形态：字符串（M2 初版契约）与 ``{"text": ..., "factor": ...}``
    对象（M3 契约——factor ∈ 金手指/性格/剧情，供空级批改的 4+2 分布校验）。
    factor 缺省按位置推导：前 4 条金手指、后 2 条性格（与提示词契约一致）。
    """
    options: List[Dict[str, str]] = []
    for index, raw in enumerate(items or ()):
        text = ""
        factor = ""
        if isinstance(raw, Mapping):
            text = str(raw.get("text") or "").strip()
            factor = str(raw.get("factor") or "").strip()
        else:
            text = str(raw or "").strip()
        preview = ""
        match = _PREVIEW_RE.search(text)
        if match:
            preview = (match.group("preview") or match.group("preview2") or "").strip()
            text = _PREVIEW_RE.sub("", text).strip()
        if index >= len(engine.OPTION_KEYS):
            break
        key = engine.OPTION_KEYS[index]
        if not factor:
            factor = "金手指" if index < 4 else "性格"
        if factor not in ("金手指", "性格", "剧情"):
            factor = "金手指" if index < 4 else "性格"
        if text:
            entry: Dict[str, Any] = {"key": key, "text": text[:60],
                                     "preview": preview[:60], "factor": factor}
            if isinstance(raw, Mapping):
                for flag in _AGENT_FLAG_FIELDS:
                    if flag in raw:
                        entry[flag] = raw[flag]
            options.append(entry)
    return options


def _sanitize_option_items(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """AI 选项清洗（不引入任何非 AI 文案）：剥离来源标注、剔除违规与重复条目，
    重排 A–F 键；满 6 条时因素分布重标为 4 金手指 + 2 性格（纯标签记账）。"""
    kept: List[Dict[str, Any]] = []
    for item in items or ():
        if not isinstance(item, Mapping):
            continue
        text = _SOURCE_LABEL_RE.sub("", str(item.get("text") or "").strip()).strip()
        if not (turn_grader.OPTION_TEXT_MIN <= len(text) <= turn_grader.OPTION_TEXT_MAX):
            continue
        if _AI_ORIGIN_RE.search(text):
            continue
        if any(difflib.SequenceMatcher(
                None, text, str(prev.get("text") or "")).ratio()
                >= turn_grader.OPTION_SIMILARITY_THRESHOLD for prev in kept):
            continue
        kept.append({
            "key": "", "text": text,
            "preview": str(item.get("preview") or "").strip()[:turn_grader.OPTION_PREVIEW_MAX],
            "factor": str(item.get("factor") or "").strip(),
        })
    for index, item in enumerate(kept):
        if index >= len(engine.OPTION_KEYS):
            break
        item["key"] = engine.OPTION_KEYS[index]
        if len(kept) == turn_grader.OPTION_COUNT:
            item["factor"] = "金手指" if index < 4 else "性格"
        elif item["factor"] not in turn_grader.OPTION_FACTORS:
            item["factor"] = "金手指" if index < 4 else "性格"
    return kept[:turn_grader.OPTION_COUNT]


def _normalize_fallback(items: Sequence[Any], factors: Sequence[Any]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        normalized.append({"key": str(item.get("key") or "").strip().upper(),
                           "text": text[:60], "preview": str(item.get("preview") or "").strip()[:60],
                           "factors": engine.match_option_factors(text, factors or [])})
    return normalized


def _fallback_from_narrative(narrative: str) -> List[Dict[str, str]]:
    """回退链：正文残存选项（正文为模型产物，属 AI 选项）→ 弹性修复补足。"""
    parsed = engine.parse_options(narrative or "")
    if len(parsed) >= 6:
        return parsed[:6]
    if parsed:
        repaired = engine.repair_options(narrative or "", parsed)
        if repaired:
            return list(repaired)[:6]
    return []


def _choice_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把结构化选项条目转为 choice_agent 内部候选形状（text/action 同源）。"""
    return [dict(item, action=str(item.get("text") or "").strip()) for item in items or ()
            if isinstance(item, dict) and str(item.get("text") or "").strip()]


def _apply_choice_agent(items: List[Dict[str, Any]], meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """专用选项通路接入点（内部代号，不出现在前台契约）。

    - legacy：原样返回，不记录；
    - shadow：仅把过滤+多样性选择结果记入 meta（不改变输出，供离线对比）；
    - agent：应用未来知识/非法 patch 过滤与多样性保序选择，不合格条目剔除。
    """
    try:
        mode = _choice_agent.mode()
    except Exception:  # noqa: BLE001 配置异常一律回退 legacy
        return items
    if mode == "legacy" or not items:
        return items
    candidates = _choice_candidates(items)
    kept = _choice_agent.select_diverse(_choice_agent.filter_candidates(candidates, {}))
    shadow = {"mode": mode, "candidate_count": len(candidates),
              "kept_keys": [str(c.get("key")) for c in kept]}
    if mode == "agent" and len(kept) >= turn_grader.OPTION_COUNT:
        allowed_keys = {str(c.get("key")) for c in kept}
        shadow["applied"] = True
        meta["choice_agent"] = shadow
        return [item for item in items if str(item.get("key")) in allowed_keys]
    if mode == "agent":
        dropped = [str(c.get("key")) for c in candidates
                   if str(c.get("key")) not in {str(k.get("key")) for k in kept}]
        if dropped:
            # 剔除后不足 6 条无法直接应用：记录被剔除项，交由批改重试链替换。
            shadow["applied"] = False
            shadow["dropped_keys"] = dropped
            meta["choice_agent"] = shadow
            return items
    shadow["applied"] = False
    meta["choice_agent"] = shadow
    return items


def generate_options(client, model: str, request_kwargs: dict | None = None,
                     provider: str = "deepseek", *, action: str = "",
                     factors_block: str = "", context_tail: str = "",
                     scene: str = "", narrative: str = "",
                     factors: Optional[Sequence[Any]] = None,
                     model_fn: Optional[Model] = None, attempts: int = 2,
                     blueprint_brief: str = "", variant: int = 0) -> Dict[str, Any]:
    """生成恰好 6 条结构化选项；返回 {options, source, meta}。

    source ∈ model（结构化生成，经 grade_options 校验或清洗保序）/ narrative
    （正文回退——正文为模型产物）/ none（均失败，options 为空，由调用方保留
    自由输入；**绝不以模板句充数**）。绝不抛错——失败信息在 meta/error。
    """
    prompt = build_options_prompt(action, factors_block, context_tail, scene,
                                  blueprint_brief=blueprint_brief, variant=variant)
    budgeted = parallel.budget_model(
        model_fn or (lambda p: distill_model(client, model, p, request_kwargs, provider)),
        parallel.PRIORITY_TURN)
    data, meta = structured.structured_call(budgeted, prompt, _SPECS, attempts=attempts)
    items = parse_option_items((data or {}).get("options") or []) if data else []
    items = _apply_choice_agent(items, meta if isinstance(meta, dict) else {})
    grade = turn_grader.grade_options(items) if items else turn_grader.GradeResult(
        errors=["结构化生成未返回任何选项"])
    # choice_agent（agent 模式）剔除的未来知识/非法候选：不足 6 条时注入批改
    # 错误清单，触发下方定向重试，由模型替换该候选而非带病出榜。
    agent_dropped = (meta or {}).get("choice_agent", {}).get("dropped_keys") \
        if isinstance(meta, dict) else None
    if agent_dropped:
        grade = turn_grader.GradeResult(errors=list(grade.errors) + [
            "选项" + "、".join(agent_dropped)
            + "依赖未发生的未来信息或非法状态变更，必须替换为当前可执行的行动"])
    warnings: List[str] = []
    if items and not grade.ok:
        # 空级批改不合格：带中文错误清单定向重试 1 次（额外恰好 1 次模型调用）。
        warnings = list(grade.errors)
        retry_prompt = prompt + (
            "\n\n【上一版选项被批改为不合格，必须全部修正后重新输出完整 JSON】\n- "
            + "\n- ".join(warnings[:10]))
        try:
            retry_data, retry_meta = structured.structured_call(
                budgeted, retry_prompt, _SPECS, attempts=1)
        except Exception as exc:  # noqa: BLE001 传输层异常按无重试处理，不阻断
            retry_data, retry_meta = None, {"transport_error": str(exc)}
        meta = dict(meta or {})
        meta["grade_retry"] = retry_meta or {}
        retry_items = parse_option_items(
            (retry_data or {}).get("options") or []) if retry_data else []
        retry_items = _apply_choice_agent(retry_items, meta)
        retry_grade = (turn_grader.grade_options(retry_items)
                       if retry_items else turn_grader.GradeResult(errors=["重试未返回选项"]))
        if len(retry_grade.errors) < len(grade.errors):
            items, grade = retry_items, retry_grade
        elif retry_grade.ok and not grade.ok:
            items, grade = retry_items, retry_grade
    if items:
        if grade.ok:
            for item in items:
                item["factors"] = engine.match_option_factors(item["text"], factors or [])
            return {"options": _strip_agent_flags(items), "source": "model", "meta": meta}
        sanitized = _sanitize_option_items(items)
        if len(sanitized) >= MIN_AI_OPTIONS:
            for item in sanitized:
                item["factors"] = engine.match_option_factors(item["text"], factors or [])
            return {"options": _strip_agent_flags(sanitized), "source": "model", "meta": meta,
                    "warnings": warnings or grade.errors}
    fallback = _normalize_fallback(_fallback_from_narrative(narrative), factors)
    if len(fallback) >= 6:
        return {"options": fallback[:6], "source": "narrative", "meta": meta}
    return {"options": [], "source": "none", "meta": meta,
            "error": "结构化生成与正文回退均未产出合格选项（AI-only：不出模板选项）",
            "warnings": warnings or (grade.errors if items else [])}


def render_display_block(options: Sequence[Any]) -> str:
    """把结构化选项渲染为旧界面文本块（行动 + 后果预告，A–F）。"""
    items = []
    for item in options or ():
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        preview = str(item.get("preview") or "").strip()
        if preview:
            text = f"{text}（后果：{preview}）"
        items.append({"key": item.get("key"), "text": text})
    return engine.render_options_block(items)


__all__ = ["build_options_prompt", "parse_option_items", "generate_options",
           "render_display_block", "_sanitize_option_items", "MIN_AI_OPTIONS"]
