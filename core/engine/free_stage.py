# -*- coding: utf-8 -*-
"""碎锚后 free 阶段专属管线支持。

free 阶段启用独立试卷（*_free.json），导演卷变体"自由航线规划"，
锚点从合同强约束降级为参考性提示，但质量门其他维度+特赦轮+keep-best
+分维无回退门+终检防线全部保留。
"""
from __future__ import annotations

from typing import Any, Mapping

#: free 阶段的锚点权重（从 0.10 降级为参考）
FREE_ANCHOR_WEIGHT = 0.02

#: free 阶段权重重分配（anchors 让渡给 continuity/world_context）
FREE_DIMENSION_WEIGHTS: dict[str, float] = {
    "format": 0.14,
    "substance": 0.10,
    "options": 0.12,
    "contracts": 0.16,
    "world_context": 0.14,  # +0.04
    "anchors": 0.02,        # -0.08
    "characters": 0.08,
    "style": 0.10,
    "continuity": 0.10,     # +0.04
    "state": 0.04,
}


def is_free_stage(state: Mapping[str, Any]) -> bool:
    """判定当前是否为 free 阶段（碎锚或 relay 激活）。"""
    from core.engine import break_anchor, cheat_code
    
    try:
        shattered = bool(break_anchor.shattered_from(state))
    except Exception:  # noqa: BLE001
        shattered = False
    
    relay_active = cheat_code.is_relay_active(state)
    
    return shattered or relay_active


def free_stage_contracts(base_contracts: Any) -> dict[str, Any]:
    """构造 free 阶段合同（锚点仅供参考，不强制）。"""
    contracts = dict(base_contracts) if isinstance(base_contracts, Mapping) else {}
    
    # 锚点改为提示性质
    contracts["anchor_mode"] = "hint"
    contracts["free_navigation"] = True
    
    return contracts


def check_shattered_history_consistency(narrative: str, 
                                        shattered_anchors: Mapping[str, Any]) -> dict[str, Any]:
    """碎锚史一致性检查：碎锚前既成事实不得复述为未发生。
    
    返回 {"violations": list[str], "evidence": list[str]}
    """
    violations: list[str] = []
    evidence: list[str] = []
    
    if not isinstance(shattered_anchors, Mapping):
        return {"violations": violations, "evidence": evidence}
    
    # 提取碎锚前的关键事实
    for anchor_id, anchor_data in shattered_anchors.items():
        if not isinstance(anchor_data, Mapping):
            continue
        
        anchor_text = str(anchor_data.get("text") or "")
        if not anchor_text:
            continue
        
        # 提取核心动作：去掉主语（"主角"、"张三"等），保留动词+宾语
        core = anchor_text
        for prefix in ["主角", "她", "他"]:
            if core.startswith(prefix):
                core = core[len(prefix):]
                break
        
        if not core:
            continue
        
        # 提取关键动词（前2字）用于否定检查，避免宾语差异
        verb = core[:2]
        
        # 检查是否被否定（匹配动词即可，不需要完整宾语）
        negation_patterns = [
            f"从未{verb}",
            f"并未{verb}",
            f"原本没有{verb}",
        ]
        
        for pattern in negation_patterns:
            if pattern in narrative:
                violations.append(f"碎锚前既成事实被否定：{anchor_text[:20]}")
                evidence.append(pattern)
                break  # 同一锚点只记录一次
    
    return {"violations": violations, "evidence": evidence}


__all__ = [
    "FREE_ANCHOR_WEIGHT",
    "FREE_DIMENSION_WEIGHTS",
    "is_free_stage",
    "free_stage_contracts",
    "check_shattered_history_consistency",
]
