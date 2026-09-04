"""开局回执：结构化展示角色概览、世界观概览和金手指介绍。"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def build_opening_receipt(state: Mapping[str, Any]) -> dict[str, Any]:
    """构建开局回执，包含角色概览、世界观概览和金手指介绍。
    
    Args:
        state: 开局后的完整状态
        
    Returns:
        结构化的开局回执字典
    """
    receipt: dict[str, Any] = {
        "version": "2.1.0",
        "characters": _build_character_overview(state),
        "worldview": _build_worldview_overview(state),
        "golden_finger": _build_golden_finger_intro(state),
        "roster": _build_roster_summary(state),
    }
    return receipt


def _build_character_overview(state: Mapping[str, Any]) -> dict[str, Any]:
    """构建角色概览"""
    overview: dict[str, Any] = {
        "protagonist": {},
        "companions": [],
        "heroines": [],
        "nemesis": {},
    }
    
    # 主角信息
    role = str(state.get("start_params", {}).get("role") or "").strip()
    protagonist_gender = str(state.get("start_params", {}).get("protagonist_gender") or "unknown")
    if role:
        overview["protagonist"] = {
            "name": role,
            "gender": protagonist_gender,
            "persona": str(state.get("start_params", {}).get("persona_label") or ""),
        }
    
    # 伙伴
    companions = state.get("companions") or []
    for companion in companions:
        if isinstance(companion, dict) and companion.get("name"):
            overview["companions"].append({
                "name": companion.get("name"),
                "skill": companion.get("skill", ""),
                "background": str(companion.get("background") or "")[:100],
            })
    
    # 女主/主线
    heroines = state.get("heroines") or []
    for heroine in heroines:
        if isinstance(heroine, dict) and heroine.get("name"):
            overview["heroines"].append({
                "name": heroine.get("name"),
                "skill": heroine.get("skill", ""),
                "background": str(heroine.get("background") or "")[:100],
            })
    
    # 宿敌
    nemesis = state.get("nemesis_private") or {}
    if isinstance(nemesis, dict) and nemesis.get("name"):
        overview["nemesis"] = {
            "name": nemesis.get("name"),
            "goal": nemesis.get("goal", ""),
            "difficulty": nemesis.get("difficulty", ""),
        }
    
    return overview


def _build_worldview_overview(state: Mapping[str, Any]) -> dict[str, Any]:
    """构建世界观概览"""
    overview: dict[str, Any] = {
        "work": "",
        "genre": "",
        "premise": "",
        "major_threads": [],
        "tone": "",
    }
    
    # 作品名
    work_label = state.get("work") or ""
    novel_name = state.get("novel_name") or ""
    if novel_name:
        overview["work"] = f"《{novel_name}》（上传）"
    elif work_label:
        overview["work"] = work_label
    
    # 从开局蒸馏报告中提取剧情信息
    plot_summary = (state.get("distill") or {}).get("plot_summary") or {}
    if isinstance(plot_summary, dict):
        overview["genre"] = str(plot_summary.get("genre") or "")
        overview["premise"] = str(plot_summary.get("premise") or "")
        overview["tone"] = str(plot_summary.get("tone") or "")
        
        major_threads = plot_summary.get("major_threads") or []
        if isinstance(major_threads, (list, tuple)):
            overview["major_threads"] = [str(t) for t in major_threads if t][:6]
    
    return overview


def _build_golden_finger_intro(state: Mapping[str, Any]) -> dict[str, Any]:
    """构建金手指介绍"""
    intro: dict[str, Any] = {
        "name": "",
        "effect": "",
        "scope": "",
        "cost": "",
        "cooldown": "",
        "limits": "",
        "blocked": False,
    }
    
    gf_decision = state.get("gf_decision") or {}
    if isinstance(gf_decision, dict):
        intro["name"] = str(gf_decision.get("label") or "")
        intro["blocked"] = bool(gf_decision.get("blocked"))
        
        spec = gf_decision.get("spec") or {}
        if isinstance(spec, dict):
            intro["effect"] = str(spec.get("effect") or "")
            intro["scope"] = str(spec.get("scope") or "")
            intro["cost"] = str(spec.get("cost") or "")
            intro["cooldown"] = str(spec.get("cooldown") or "")
            intro["limits"] = str(spec.get("limits") or "")
    
    # 如果没有从 gf_decision 获取到，尝试从 start_params 获取
    if not intro["name"]:
        intro["name"] = str(state.get("start_params", {}).get("golden_finger") or "")
    
    return intro


def _build_roster_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """构建名册摘要（四栏卡选择）"""
    summary: dict[str, Any] = {
        "protagonist_card": None,
        "mainline_cards": [],
        "partner_cards": [],
        "nemesis_card": None,
    }
    
    roster_cards = state.get("roster_cards") or []
    for card in roster_cards:
        if not isinstance(card, dict):
            continue
        
        slot = card.get("slot", "")
        card_info = {
            "id": card.get("id", ""),
            "name": card.get("name", ""),
            "work": card.get("work", ""),
        }
        
        if slot == "主角":
            summary["protagonist_card"] = card_info
        elif slot == "主线":
            summary["mainline_cards"].append(card_info)
        elif slot == "伙伴":
            summary["partner_cards"].append(card_info)
        elif slot == "宿敌":
            summary["nemesis_card"] = card_info
    
    return summary


def format_receipt_text(receipt: Mapping[str, Any]) -> str:
    """将开局回执格式化为可读文本"""
    lines: list[str] = []
    
    # 标题
    lines.append("# 开局回执")
    lines.append("")
    
    # 世界观概览
    worldview = receipt.get("worldview") or {}
    if worldview.get("work"):
        lines.append("## 世界观")
        lines.append(f"**作品**：{worldview['work']}")
        if worldview.get("genre"):
            lines.append(f"**题材**：{worldview['genre']}")
        if worldview.get("premise"):
            lines.append(f"**核心前提**：{worldview['premise']}")
        if worldview.get("tone"):
            lines.append(f"**叙事基调**：{worldview['tone']}")
        
        major_threads = worldview.get("major_threads") or []
        if major_threads:
            lines.append("**主要线索**：")
            for idx, thread in enumerate(major_threads, 1):
                lines.append(f"  {idx}. {thread}")
        lines.append("")
    
    # 角色概览
    characters = receipt.get("characters") or {}
    lines.append("## 角色")
    
    protagonist = characters.get("protagonist") or {}
    if protagonist.get("name"):
        lines.append(f"**主角**：{protagonist['name']}")
        if protagonist.get("persona"):
            lines.append(f"  性格：{protagonist['persona']}")
    
    companions = characters.get("companions") or []
    if companions:
        lines.append(f"**伙伴**（{len(companions)}位）：")
        for comp in companions:
            name = comp.get("name", "")
            skill = comp.get("skill", "")
            lines.append(f"  · {name}" + (f"（{skill}）" if skill else ""))
    
    heroines = characters.get("heroines") or []
    if heroines:
        lines.append(f"**主线角色**（{len(heroines)}位）：")
        for her in heroines:
            name = her.get("name", "")
            skill = her.get("skill", "")
            lines.append(f"  · {name}" + (f"（{skill}）" if skill else ""))
    
    nemesis = characters.get("nemesis") or {}
    if nemesis.get("name"):
        lines.append(f"**宿敌**：{nemesis['name']}")
        if nemesis.get("difficulty"):
            lines.append(f"  难度：{nemesis['difficulty']}")
    
    lines.append("")
    
    # 金手指
    gf = receipt.get("golden_finger") or {}
    if gf.get("name"):
        lines.append("## 金手指")
        lines.append(f"**名称**：{gf['name']}")
        if gf.get("blocked"):
            lines.append("  （本局全员无金手指）")
        elif gf.get("effect"):
            lines.append(f"**作用**：{gf['effect']}")
            if gf.get("scope"):
                lines.append(f"**范围**：{gf['scope']}")
            if gf.get("cost"):
                lines.append(f"**代价**：{gf['cost']}")
            if gf.get("cooldown"):
                lines.append(f"**冷却**：{gf['cooldown']}")
            if gf.get("limits"):
                lines.append(f"**限制**：{gf['limits']}")
    
    return "\n".join(lines)


__all__ = [
    "build_opening_receipt",
    "format_receipt_text",
]
