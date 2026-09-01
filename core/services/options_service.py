# -*- coding: utf-8 -*-
"""选项生成中台门面（重构 M2）：6+1 选项出正文，改由结构化填空独立生成。

- 模型经 distill 通道调用，占用回合优先级并发额度（engine.parallel）；
- 输出为恰好 6 条字符串的 JSON（FieldSpec 校验，错误清单回传重试 ≤2）；
- 代码分配 A–F 键、拆分「（后果：…）」预告、匹配因素标签（options.match）；
- 回退链：正文残存选项解析 → 正文行动句/中性模板合成（elastic 修复）。

HTTP/引擎细节都不在此感知：``model_fn`` 可注入（离线测试），缺省走
distill_model(client, model, ...)。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from core import engine
from core.engine import parallel, structured
from core.engine.distill import distill_model
from core.prompts import render

Model = Callable[[str], Any]

_SPECS = (
    structured.FieldSpec(
        "options", "list", required=True, min_items=6, max_items=6, item_max_len=90,
        hint="恰好 6 条行动，每条形如：行动内容（后果：一句可观测后果预告）；"
             "4 条由剧情+金手指推导、2 条由玩家性格推导；不得标注来源"),
)

_PREVIEW_RE = re.compile(r"（后果[：:]\s*(?P<preview>[^）]*)）|\(后果[：:]\s*(?P<preview2>[^)]*)\)")


def build_options_prompt(action: str, factors_block: str, context_tail: str) -> str:
    """装配选项生成卷提示词（@@KEY@@ 占位符由 core.prompts.render 渲染）。"""
    return render(
        "options_gen.md",
        ACTION=str(action or "（玩家自由行动）").strip()[:300],
        FACTORS=str(factors_block or "").strip() or "（本回合无特殊因素）",
        CONTEXT=str(context_tail or "").strip()[-600:] or "（开局首轮，无前文）",
    )


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
            options.append({"key": key, "text": text[:60],
                            "preview": preview[:60], "factor": factor})
    return options


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
    """回退链：正文残存选项 → 不足 6 时弹性合成补足（零模型成本）。"""
    parsed = engine.parse_options(narrative or "")
    if len(parsed) >= 6:
        return parsed[:6]
    if parsed:
        repaired = engine.repair_options(narrative or "", parsed)
        if repaired:
            return list(repaired)[:6]
    return []


def generate_options(client, model: str, request_kwargs: dict | None = None,
                     provider: str = "deepseek", *, action: str = "",
                     factors_block: str = "", context_tail: str = "",
                     narrative: str = "", factors: Optional[Sequence[Any]] = None,
                     model_fn: Optional[Model] = None, attempts: int = 2) -> Dict[str, Any]:
    """生成恰好 6 条结构化选项；返回 {options, source, meta}。

    source ∈ model（结构化生成）/ narrative（正文回退）/ none（均失败，
    options 为空，由调用方走最终兜底）。绝不抛错——失败信息在 meta/error。
    """
    prompt = build_options_prompt(action, factors_block, context_tail)
    budgeted = parallel.budget_model(
        model_fn or (lambda p: distill_model(client, model, p, request_kwargs, provider)),
        parallel.PRIORITY_TURN)
    data, meta = structured.structured_call(budgeted, prompt, _SPECS, attempts=attempts)
    items = parse_option_items((data or {}).get("options") or []) if data else []
    if len(items) == 6:
        for item in items:
            item["factors"] = engine.match_option_factors(item["text"], factors or [])
        return {"options": items, "source": "model", "meta": meta}
    fallback = _normalize_fallback(_fallback_from_narrative(narrative), factors)
    if len(fallback) >= 6:
        return {"options": fallback[:6], "source": "narrative", "meta": meta}
    return {"options": [], "source": "none", "meta": meta,
            "error": "结构化生成与正文回退均未凑齐 6 个选项"}


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
           "render_display_block"]
