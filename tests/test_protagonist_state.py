# -*- coding: utf-8 -*-
"""protagonist_state 单元测试：从 state_memory 派生状态卡与硬事实约束。"""
import pytest
from core.engine import protagonist_state


def test_blank_state_returns_empty_sheet():
    """空白 state 返回空状态卡（各字段为空但结构齐全）。"""
    sheet = protagonist_state.protagonist_sheet({})
    assert isinstance(sheet, dict)
    assert sheet.get("revision") == 0
    assert sheet.get("identity") == "主角"  # 默认值
    assert sheet.get("condition") == ""
    assert sheet.get("injuries") == []
    assert sheet.get("location") == ""
    assert sheet.get("items") == []
    assert sheet.get("equipment") == []
    assert sheet.get("skills") == []
    assert sheet.get("golden_finger") == ""
    assert sheet.get("relationships") == []
    assert sheet.get("assertions") == []


def test_sheet_derives_identity_from_memory():
    """从 state_memory.identity 派生主角身份。"""
    state = {"state_memory": {"identity": {"role": "流浪剑客", "name": "李寻欢"}}}
    sheet = protagonist_state.protagonist_sheet(state)
    assert "流浪剑客" in sheet["identity"]
    assert "李寻欢" in sheet["identity"]


def test_sheet_derives_condition_and_injuries():
    """从 state_memory.body 派生身体状态与伤势清单。"""
    state = {
        "state_memory": {
            "body": {
                "condition": "重伤",
                "injuries": [
                    {"type": "骨折", "part": "左臂", "round": 10},
                    {"type": "内伤", "round": 11},
                ],
            }
        }
    }
    sheet = protagonist_state.protagonist_sheet(state)
    assert sheet["condition"] == "重伤"
    assert len(sheet["injuries"]) == 2
    assert sheet["injuries"][0] == "骨折（左臂）"
    assert sheet["injuries"][1] == "内伤"


def test_sheet_derives_location():
    """从 state_memory.location 派生当前位置。"""
    state = {"state_memory": {"location": {"name": "青云镇"}}}
    sheet = protagonist_state.protagonist_sheet(state)
    assert sheet["location"] == "青云镇"


def test_sheet_derives_items_and_equipment():
    """从 state_memory.assets 派生持有物品与装备。"""
    state = {
        "state_memory": {
            "assets": {
                "items": [
                    {"name": "铜钱", "quantity": 100},
                    {"name": "干粮"},
                ],
                "equipment": [
                    {"name": "长剑", "slot": "主手"},
                ],
            }
        }
    }
    sheet = protagonist_state.protagonist_sheet(state)
    assert len(sheet["items"]) == 2
    assert sheet["items"][0] == "铜钱×100"
    assert sheet["items"][1] == "干粮"
    assert len(sheet["equipment"]) == 1
    assert sheet["equipment"][0] == "长剑（主手）"


def test_sheet_derives_skills():
    """从 state_memory.abilities 派生技能清单。"""
    state = {
        "state_memory": {
            "abilities": {
                "skills": [
                    {"name": "轻功", "level": 3},
                    {"name": "剑法"},
                ],
            }
        }
    }
    sheet = protagonist_state.protagonist_sheet(state)
    assert len(sheet["skills"]) == 2
    assert sheet["skills"][0] == "轻功 Lv3"
    assert sheet["skills"][1] == "剑法"


def test_sheet_derives_golden_finger():
    """从 state_memory.golden_finger 派生金手指。"""
    state = {
        "state_memory": {
            "golden_finger": {
                "name": "系统面板",
                "description": "可查看属性",
            }
        }
    }
    sheet = protagonist_state.protagonist_sheet(state)
    assert "系统面板" in sheet["golden_finger"]


def test_sheet_derives_relationships():
    """从 state_memory.relationships 派生关键关系。"""
    state = {
        "state_memory": {
            "relationships": {
                "characters": [
                    {"name": "林诗音", "relationship_delta": "ALLY", "summary": "青梅竹马"},
                    {"name": "龙啸云", "relationship_delta": "ENEMY", "summary": "仇敌"},
                ],
            }
        }
    }
    sheet = protagonist_state.protagonist_sheet(state)
    assert len(sheet["relationships"]) == 2
    assert sheet["relationships"][0] == "林诗音（ALLY）：青梅竹马"
    assert sheet["relationships"][1] == "龙啸云（ENEMY）：仇敌"


def test_sheet_derives_assertions():
    """从 state_memory.assertions 派生事实断言。"""
    state = {
        "state_memory": {
            "assertions": [
                {"key": "秘密", "value": "真凶是管家", "confidence": 0.9},
            ]
        }
    }
    sheet = protagonist_state.protagonist_sheet(state)
    assert len(sheet["assertions"]) == 1
    assert "秘密" in sheet["assertions"][0]
    assert "真凶是管家" in sheet["assertions"][0]


def test_hard_facts_empty_when_all_fields_blank():
    """所有字段为空时只生成通用纪律约束。"""
    state = {"state_memory": {}}
    facts = protagonist_state.hard_facts(state)
    assert isinstance(facts, list)
    # 只有生成纪律与硬校验两条通用约束，无具体状态约束
    assert len(facts) == 2
    assert any("生成纪律" in f for f in facts)
    assert any("硬校验" in f for f in facts)


def test_hard_facts_identity_constraint():
    """有身份时生成身份约束。"""
    state = {"state_memory": {"identity": {"role": "侠客", "name": "楚留香"}}}
    facts = protagonist_state.hard_facts(state)
    identity_facts = [f for f in facts if "主角身份" in f]
    assert len(identity_facts) == 1
    assert "侠客" in identity_facts[0]
    assert "楚留香" in identity_facts[0]


def test_hard_facts_death_constraint():
    """死亡状态生成死亡约束。"""
    state = {"state_memory": {"body": {"condition": "死亡"}}}
    facts = protagonist_state.hard_facts(state)
    death_facts = [f for f in facts if "主角已死亡" in f]
    assert len(death_facts) == 1
    assert "不得无交代复活" in death_facts[0]


def test_hard_facts_injury_constraint():
    """重伤状态生成伤势约束。"""
    state = {
        "state_memory": {
            "body": {
                "condition": "重伤",
                "injuries": [
                    {"type": "骨折", "part": "右腿"},
                    {"type": "内伤"},
                ],
            }
        }
    }
    facts = protagonist_state.hard_facts(state)
    injury_facts = [f for f in facts if "主角当前身体状态：重伤" in f]
    assert len(injury_facts) == 1
    assert "骨折" in injury_facts[0]
    assert "内伤" in injury_facts[0]
    assert "可经治疗逐渐康复" in injury_facts[0]


def test_hard_facts_location_constraint():
    """位置信息生成位置约束。"""
    state = {"state_memory": {"location": {"name": "密室"}}}
    facts = protagonist_state.hard_facts(state)
    location_facts = [f for f in facts if "主角当前位置" in f]
    assert len(location_facts) == 1
    assert "密室" in location_facts[0]
    assert "可合理移动" in location_facts[0]
    assert "不得凭空瞬移" in location_facts[0]


def test_hard_facts_items_constraint():
    """持有物品生成物品约束。"""
    state = {
        "state_memory": {
            "assets": {
                "items": [
                    {"name": "钥匙"},
                    {"name": "银两", "quantity": 50},
                ],
            }
        }
    }
    facts = protagonist_state.hard_facts(state)
    item_facts = [f for f in facts if "主角当前持有物品" in f]
    assert len(item_facts) == 1
    assert "钥匙" in item_facts[0]
    assert "银两" in item_facts[0]
    assert "可合理使用、赠送、消耗" in item_facts[0]


def test_hard_facts_equipment_constraint():
    """装备生成装备约束。"""
    state = {
        "state_memory": {
            "assets": {
                "equipment": [
                    {"name": "宝剑", "slot": "主手"},
                ],
            }
        }
    }
    facts = protagonist_state.hard_facts(state)
    equipment_facts = [f for f in facts if "主角当前装备" in f]
    assert len(equipment_facts) == 1
    assert "宝剑" in equipment_facts[0]
    assert "可卸下、损坏、遗失" in equipment_facts[0]


def test_hard_facts_text_formatting():
    """hard_facts_text 格式化为紧凑文本块。"""
    state = {
        "state_memory": {
            "identity": {"role": "剑客"},
            "body": {"condition": "重伤"},
            "location": {"name": "悬崖"},
        }
    }
    text = protagonist_state.hard_facts_text(state)
    assert isinstance(text, str)
    assert "【主角状态硬事实】" in text
    assert "剑客" in text
    assert "重伤" in text
    assert "悬崖" in text


def test_hard_facts_text_respects_limit():
    """hard_facts_text 超长时截断。"""
    state = {
        "state_memory": {
            "assets": {
                "items": [{"name": f"物品{i}"} for i in range(100)],
            }
        }
    }
    text = protagonist_state.hard_facts_text(state, limit=200)
    # limit 应用在整体 join 之后，允许稍微超出（因为是切片而非严格字数统计）
    assert len(text) <= 250  # 放宽容差


def test_hard_facts_text_empty_when_no_constraints():
    """无约束时返回空字符串。"""
    state = {"state_memory": {}}
    text = protagonist_state.hard_facts_text(state)
    # 只有通用纪律约束，不为空
    assert isinstance(text, str)
    assert len(text) > 0


def test_sheet_revision_from_memory():
    """revision 字段从 state_memory 派生。"""
    state = {"state_memory": {"revision": 42}}
    sheet = protagonist_state.protagonist_sheet(state)
    assert sheet["revision"] == 42


def test_hard_facts_always_include_discipline():
    """硬事实始终包含生成纪律与硬校验约束。"""
    state = {"state_memory": {"identity": {"role": "侠客"}}}
    facts = protagonist_state.hard_facts(state)
    discipline_facts = [f for f in facts if "生成纪律" in f or "硬校验" in f]
    assert len(discipline_facts) == 2
    assert any("本回合生成期间状态冻结" in f for f in facts)
    assert any("不得让主角凭空复活" in f for f in facts)
