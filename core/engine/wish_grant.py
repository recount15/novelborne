# -*- coding: utf-8 -*-
"""作弊码结构化落地：payload 类型化 + state_memory 写入 + 兑现校验。

三愿与永久通路的铁律登记后，需要按类型将 payload 落地到 state_memory：
- item: 写入 assets.items
- ability/skill: 写入 abilities.skills
- relationship: 写入 relationships.characters
- fact: 仅作为 directive 注入，不直接修改 state_memory

兑现校验（compliance check）：激活后 2 回合内，检查 payload 关键词是否
在正文中出现；未兑现→生成修复指令；连续 2 回合未兑现→系统提示（不强制）。
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

#: payload 类型枚举
PAYLOAD_TYPES = ("item", "ability", "skill", "relationship", "fact")

#: 兑现校验窗口（回合数）
COMPLIANCE_WINDOW = 2

#: 关键词最小长度（中文 2 字）
KEYWORD_MIN_LEN = 2


def parse_payload(directive: Mapping[str, Any]) -> dict[str, Any]:
    """从 directive 条目中解析 payload 类型和内容。
    
    返回 {"type": str, "content": Any, "keywords": list[str]}
    """
    fact_norm = str(directive.get("fact_norm") or "").strip()
    scope = str(directive.get("scope") or "world").strip()
    
    # 根据 scope 和 fact_norm 推断 payload 类型
    payload_type = "fact"  # 默认为事实类
    content: Any = fact_norm
    keywords: list[str] = []
    
    if scope == "item":
        payload_type = "item"
        # 从 fact_norm 提取物品名（简单规则：取名词）
        content = _extract_item_name(fact_norm)
        keywords = [content] if content else []
    elif scope == "character":
        # 可能是关系或能力
        if any(word in fact_norm for word in ("掌握", "学会", "获得能力", "拥有技能", "修为")):
            payload_type = "ability"
            content = _extract_ability_name(fact_norm)
            keywords = [content] if content else []
        else:
            payload_type = "relationship"
            content = _extract_relationship(fact_norm)
            keywords = [content] if isinstance(content, str) and content else []
    elif any(word in fact_norm for word in ("技能", "能力", "修为")):
        payload_type = "ability"
        content = _extract_ability_name(fact_norm)
        keywords = [content] if content else []
    
    # 如果无法提取具体内容，回退到 fact 类型，用整句作为关键词
    if not keywords and fact_norm:
        keywords = _extract_keywords(fact_norm)
    
    return {
        "type": payload_type,
        "content": content,
        "keywords": keywords,
    }


def _extract_item_name(text: str) -> str:
    """从铁律文本中提取物品名（简单规则）。"""
    # 匹配：获得/拥有/持有 + 物品名
    patterns = [
        r"获得[了]?([^，。；,;]{2,12})",
        r"拥有[了]?([^，。；,;]{2,12})",
        r"持有([^，。；,;]{2,12})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _extract_ability_name(text: str) -> str:
    """从铁律文本中提取能力/技能名。"""
    patterns = [
        r"掌握[了]?([^，。；,;]{2,12})",
        r"学会[了]?([^，。；,;]{2,12})",
        r"获得[了]?([^，。；,;]{2,12})(?:能力|技能)",
        r"(?:能力|技能)[：:]([^，。；,;]{2,12})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _extract_relationship(text: str) -> str:
    """从铁律文本中提取关系描述。"""
    # 简单规则：取 affected 或整句
    return text[:50]


def _extract_keywords(text: str, max_count: int = 3) -> list[str]:
    """从文本中提取关键词（4-8 字的连续片段）。"""
    keywords: list[str] = []
    # 移除标点，按字分词
    clean = re.sub(r"[，。；、：！？""''（）\s,;:.!()\[\]{}]", "", text)
    # 提取 4-8 字的滑动窗口
    for length in (6, 5, 4):
        for i in range(len(clean) - length + 1):
            fragment = clean[i:i+length]
            if len(fragment) >= KEYWORD_MIN_LEN and fragment not in keywords:
                keywords.append(fragment)
                if len(keywords) >= max_count:
                    return keywords
    return keywords[:max_count]


def grant_to_state(state: dict, directive: Mapping[str, Any]) -> dict[str, Any]:
    """将 directive 的 payload 落地到 state_memory（通过 state_store.apply_turn）。
    
    返回 patch（供 apply_turn 使用）。
    """
    payload = parse_payload(directive)
    patch: dict[str, Any] = {}
    
    ptype = payload["type"]
    content = payload["content"]
    
    if ptype == "item" and content:
        # 写入 assets.items（追加模式）
        patch.setdefault("assets", {})["items"] = f"{content}×1"
    elif ptype in ("ability", "skill") and content:
        # 写入 abilities.skills（追加模式）
        patch.setdefault("abilities", {})["skills"] = content
    elif ptype == "relationship" and content:
        # 写入 relationships.characters（追加模式）
        patch.setdefault("relationships", {})["characters"] = content
    # fact 类型不写 state_memory，仅作为 directive 注入
    
    return patch


def check_compliance(state: Mapping[str, Any], narrative: str, 
                     directives_in_window: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """检查激活后窗口内的 directives 是否在正文中兑现。
    
    返回 {"compliant": list[int], "non_compliant": list[int], "repair_hints": list[str]}
    """
    compliant_ids: list[int] = []
    non_compliant_ids: list[int] = []
    repair_hints: list[str] = []
    
    current_round = int(state.get("round") or 0)
    
    for directive in directives_in_window:
        did = int(directive.get("id") or 0)
        activated_round = int(directive.get("round") or 0)
        # 只检查窗口内的
        if current_round - activated_round > COMPLIANCE_WINDOW:
            continue
        
        payload = parse_payload(directive)
        keywords = payload.get("keywords") or []
        
        # 检查关键词是否在正文中
        hit_count = sum(1 for kw in keywords if kw in narrative)
        
        if hit_count > 0:
            compliant_ids.append(did)
        else:
            non_compliant_ids.append(did)
            fact_norm = str(directive.get("fact_norm") or "")
            if fact_norm:
                repair_hints.append(f"需要体现：{fact_norm[:50]}")
    
    return {
        "compliant": compliant_ids,
        "non_compliant": non_compliant_ids,
        "repair_hints": repair_hints,
    }


def anchor_arbitration(directives_list: Sequence[Mapping[str, Any]], 
                       anchor_words: Sequence[str]) -> dict[str, Any]:
    """锚点仲裁：标注哪些 directives 影响了哪些锚点。
    
    返回 {"affected_anchors": {directive_id: [anchor_word, ...]}}
    """
    affected_anchors: dict[int, list[str]] = {}
    
    for directive in directives_list:
        did = int(directive.get("id") or 0)
        affected_terms = directive.get("affected") or []
        
        # 检查 affected 是否与锚点词重叠
        hits: list[str] = []
        for term in affected_terms:
            for anchor in anchor_words:
                if term == anchor or term in anchor or anchor in term:
                    hits.append(anchor)
        
        if hits:
            affected_anchors[did] = list(set(hits))
    
    return {"affected_anchors": affected_anchors}


__all__ = [
    "PAYLOAD_TYPES",
    "COMPLIANCE_WINDOW",
    "parse_payload",
    "grant_to_state",
    "check_compliance",
    "anchor_arbitration",
]
