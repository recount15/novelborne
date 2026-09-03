# -*- coding: utf-8 -*-
"""主角状态单源视图与生成前硬事实清单。

状态只存在存档的 state_memory；本模块只读派生，不在生成中修改状态。
"""
from __future__ import annotations

from typing import Any, Mapping


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value in (None, "", []):
        return []
    return [value]


def protagonist_sheet(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """从 state_memory 派生一份紧凑、可注入提示词的主角状态卡。"""
    state = state if isinstance(state, Mapping) else {}
    memory = _mapping(state.get("state_memory"))
    identity_obj = _mapping(memory.get("identity"))
    body = _mapping(memory.get("body"))
    assets = _mapping(memory.get("assets"))
    abilities = _mapping(memory.get("abilities"))
    relationships = _mapping(memory.get("relationships"))
    location = _mapping(memory.get("location"))
    golden_finger = _mapping(memory.get("golden_finger"))
    
    # 身份：优先 identity.role/name，兜底取 state.role/persona
    role = str(identity_obj.get("role") or state.get("role") or "").strip()
    name = str(identity_obj.get("name") or state.get("persona") or "").strip()
    identity_text = "、".join(filter(None, [role, name])) or "主角"
    
    # 伤势：格式化为 "类型（部位）" 或 "类型"
    injuries = []
    for inj in _rows(body.get("injuries")):
        if isinstance(inj, Mapping):
            inj_type = str(inj.get("type") or "").strip()
            inj_part = str(inj.get("part") or "").strip()
            injuries.append(f"{inj_type}（{inj_part}）" if inj_part else inj_type)
        else:
            injuries.append(str(inj).strip())
    
    # 物品/装备：格式化为 "名称×数量" 或 "名称（槽位）"
    items = []
    for item in _rows(assets.get("items")):
        if isinstance(item, Mapping):
            item_name = str(item.get("name") or "").strip()
            item_qty = item.get("quantity")
            items.append(f"{item_name}×{item_qty}" if item_qty else item_name)
        else:
            items.append(str(item).strip())
    
    equipment = []
    for eq in _rows(assets.get("equipment")):
        if isinstance(eq, Mapping):
            eq_name = str(eq.get("name") or "").strip()
            eq_slot = str(eq.get("slot") or "").strip()
            equipment.append(f"{eq_name}（{eq_slot}）" if eq_slot else eq_name)
        else:
            equipment.append(str(eq).strip())
    
    # 技能：格式化为 "名称 LvX" 或 "名称"
    skills = []
    for skill in _rows(abilities.get("skills")):
        if isinstance(skill, Mapping):
            skill_name = str(skill.get("name") or "").strip()
            skill_level = skill.get("level")
            skills.append(f"{skill_name} Lv{skill_level}" if skill_level else skill_name)
        else:
            skills.append(str(skill).strip())
    
    # 金手指：格式化为 "名称：描述"
    gf_name = str(golden_finger.get("name") or "").strip()
    gf_desc = str(golden_finger.get("description") or "").strip()
    golden_finger_text = f"{gf_name}：{gf_desc}" if gf_name and gf_desc else gf_name or gf_desc
    
    # 关系：格式化为 "名称（delta）：摘要"
    rel_list = []
    for rel in _rows(relationships.get("characters")):
        if isinstance(rel, Mapping):
            rel_name = str(rel.get("name") or "").strip()
            rel_delta = str(rel.get("relationship_delta") or "").strip()
            rel_summary = str(rel.get("summary") or "").strip()
            rel_list.append(f"{rel_name}（{rel_delta}）：{rel_summary}" if rel_delta and rel_summary else f"{rel_name}：{rel_summary}")
        else:
            rel_list.append(str(rel).strip())
    
    # 断言：格式化为 "key：value"
    assertions = []
    for assertion in _rows(memory.get("assertions")):
        if isinstance(assertion, Mapping):
            key = str(assertion.get("key") or "").strip()
            value = str(assertion.get("value") or "").strip()
            assertions.append(f"{key}：{value}" if key and value else key or value)
        else:
            assertions.append(str(assertion).strip())
    
    return {
        "identity": identity_text,
        "condition": str(body.get("condition") or "").strip(),
        "injuries": injuries,
        "location": str(location.get("name") or "").strip(),
        "items": items,
        "equipment": equipment,
        "skills": skills,
        "golden_finger": golden_finger_text,
        "relationships": rel_list,
        "assertions": assertions,
        "revision": int(memory.get("revision", 0) or 0),
    }


def hard_facts(state: Mapping[str, Any] | None) -> list[str]:
    """提取生成不可违背的事实；区分可变状态（允许合理演进）与不可变历史（不得否定）。"""
    sheet = protagonist_sheet(state)
    facts = []
    # 身份：非默认"主角"时才加约束
    if sheet["identity"] and sheet["identity"] != "主角":
        facts.append(f"主角身份：{sheet['identity']}")
    
    # 身体状态：死亡不可逆；重伤可治疗但需过程
    if sheet["condition"] == "死亡":
        facts.append("主角已死亡（不得无交代复活；特殊设定如复活能力需提前确立）")
    elif sheet["condition"] and sheet["condition"] not in ("正常", "健康", ""):
        injury_text = "、".join(sheet["injuries"][:8]) if sheet["injuries"] else "未细分"
        facts.append(f"主角当前身体状态：{sheet['condition']}（伤势：{injury_text}）；"
                    f"可经治疗逐渐康复，但当前回合开始时确为此状态，不得直接改写成'并未受伤'或'毫发无伤'")
    
    # 位置：可移动但需铺垫
    if sheet["location"]:
        facts.append(f"主角当前位置：{sheet['location']}；可合理移动，但需有走/骑/传送等铺垫，不得凭空瞬移到远处")
    
    # 物品/装备：可使用消耗，但不得否定既成事实
    if sheet["items"]:
        facts.append("主角当前持有物品：" + "、".join(sheet["items"][:12]) + 
                    "；可合理使用、赠送、消耗，但不得写成'从未拥有''原本没有'")
    if sheet["equipment"]:
        facts.append("主角当前装备：" + "、".join(sheet["equipment"][:8]) + 
                    "；可卸下、损坏、遗失，但不得写成'从未装备过'")
    
    # 技能：已掌握不可遗忘（除非有特殊剧情）
    if sheet["skills"]:
        facts.append("主角已掌握能力：" + "、".join(sheet["skills"][:12]) + 
                    "；可提升或弱化，但不得写成'从未学过''不会使用'（失忆等特殊情况需剧情支撑）")
    
    # 关系：历史不可改写，但关系可演变
    if sheet["relationships"]:
        facts.append("主角既成关系：" + "、".join(sheet["relationships"][:12]) + 
                    "；关系可深化或恶化，但不得改写已确认的相识/交往历史")
    
    # 断言：已确认事实不可否定
    if sheet["assertions"]:
        facts.append("角色已确认断言：" + "、".join(sheet["assertions"][:8]) + 
                    "；可补充细节，但不得直接否定既成结论")
    
    facts.append("生成纪律：本回合生成期间状态冻结；只有玩家选择并完成回合验收后，状态更新才通过 extractor 和 apply_turn 提交。")
    facts.append("硬校验：不得让主角凭空复活、否定已确认历史、凭空获得从未拥有的物品能力。合理的状态演进（治疗康复、消耗物品、移动位置、关系变化）允许且鼓励，但需有剧情支撑。")
    return facts


def hard_facts_text(state: Mapping[str, Any] | None, limit: int = 1800) -> str:
    return "【主角状态硬事实】\n- " + "\n- ".join(hard_facts(state))[:max(200, int(limit))]


__all__ = ["protagonist_sheet", "hard_facts", "hard_facts_text"]
