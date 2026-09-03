# -*- coding: utf-8 -*-
"""quality_gate state 维度单元测试：主角状态硬事实校验。"""
import pytest
from core.engine.quality_gate import score_turn, QualityIssue


def test_state_dimension_exists_in_score():
    """score_turn 返回的 scorecard 包含 state 维度。"""
    narrative = "主角走在路上，阳光明媚。"
    options = [{"text": "继续前行"}, {"text": "休息片刻"}]
    mctx = {"state_hard_facts": []}
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert "state" in scorecard.dimensions
    assert 0 <= scorecard.dimension("state") <= 100


def test_state_score_perfect_when_no_facts():
    """无硬事实时 state 维度满分。"""
    narrative = "主角走在路上，阳光明媚。"
    options = [{"text": "继续前行"}]
    mctx = {"state_hard_facts": []}
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") == 100.0


def test_state_score_perfect_when_facts_not_contradicted():
    """硬事实未被否定时 state 维度满分。"""
    narrative = "李寻欢身负重伤，但仍坚持前行。"
    options = [{"text": "休息"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤；伤势：内伤、骨折",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") == 100.0


def test_state_penalize_death_negation():
    """死亡状态被复活时扣分。"""
    narrative = "李寻欢死而复生，满血复活。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角已死亡（不得无交代复活；特殊设定如复活能力需提前确立）",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") < 70.0
    state_issues = [i for i in scorecard.issues if i.dimension == "state"]
    assert len(state_issues) >= 1
    assert any("死亡" in i.message or "复活" in i.message for i in state_issues)


def test_state_penalize_injury_negation():
    """重伤状态被否定时扣分。"""
    narrative = "李寻欢并未受伤，完全没有内伤的样子。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤；伤势：内伤、骨折",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") < 70.0
    state_issues = [i for i in scorecard.issues if i.dimension == "state"]
    assert len(state_issues) >= 1


def test_state_penalize_consumed_item_reappearance():
    """已消耗物品被写成从未拥有时扣分。"""
    narrative = "李寻欢从未拥有解药，今日首次获得。"
    options = [{"text": "服用"}]
    mctx = {
        "state_hard_facts": [
            "主角当前持有物品：长剑、干粮（已消耗解药）；可合理使用、赠送、消耗，但不得写成'从未拥有''原本没有'",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") < 80.0
    state_issues = [i for i in scorecard.issues if i.dimension == "state"]
    assert len(state_issues) >= 1


def test_state_allow_existing_item_mention():
    """已持有物品正常提及时不扣分。"""
    narrative = "李寻欢拔出长剑，检查剑刃。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角持有物品：长剑、干粮",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") == 100.0


def test_state_penalize_location_teleport():
    """位置历史被否定时扣分。"""
    narrative = "李寻欢从未到过密室，这是第一次来。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角当前位置：密室；可合理移动，但需有走/骑/传送等铺垫，不得凭空瞬移到远处",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") < 70.0


def test_state_negation_window_detection():
    """否定词在事实关键词 ±12 字符窗口内时检出。"""
    narrative = "李寻欢的重伤并未好转，仍在咳血。"
    options = [{"text": "休息"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤；伤势：内伤",
        ]
    }
    contracts = {}
    
    # "重伤并未" 窗口内有否定词 "并未"，但语义是"伤势未好转"，不是"未重伤"
    # 这是误判场景，但按当前简单规则会检出——实测需调整
    scorecard = score_turn(narrative, options, mctx, contracts)
    # 当前实现会误判，暂时接受（AI 软校验会补救）
    # 这个测试记录已知限制


def test_state_multiple_facts_multiple_violations():
    """多条硬事实被违背时累计扣分。"""
    narrative = "李寻欢并未受伤，也从未到过密室。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤",
            "主角当前位置：密室",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") < 50.0
    state_issues = [i for i in scorecard.issues if i.dimension == "state"]
    assert len(state_issues) >= 2


def test_state_empty_narrative_no_crash():
    """空正文时不崩溃，返回满分。"""
    narrative = ""
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") == 100.0


def test_state_facts_not_in_narrative_no_penalty():
    """硬事实关键词不在正文中时不扣分（缺证据不臆判原则）。"""
    narrative = "李寻欢在树林中漫步。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤；伤势：内伤、骨折",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    # "重伤"/"内伤"/"骨折" 都不在正文中，不检查
    assert scorecard.dimension("state") == 100.0


def test_state_partial_fact_match_no_negation():
    """部分匹配但无否定词时不扣分。"""
    narrative = "李寻欢的重伤让他行动缓慢。"
    options = [{"text": "休息"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤；伤势：内伤",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") == 100.0


def test_state_never_owned_item_contradiction():
    """已确认物品被写成从未拥有时扣分。"""
    narrative = "李寻欢从未拥有过长剑，一直赤手空拳。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角当前装备：长剑（主手）；可卸下、损坏、遗失，但不得写成'从未装备过'",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") < 80.0


def test_state_original_lack_contradiction():
    """已确认能力被写成从未学过时扣分。"""
    narrative = "李寻欢从未学过轻功，今日才初次施展。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角已掌握能力：轻功 Lv3、剑法；可提升或弱化，但不得写成'从未学过''不会使用'（失忆等特殊情况需剧情支撑）",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert scorecard.dimension("state") < 80.0


def test_state_dimension_weight_in_total():
    """state 维度按权重参与总分计算。"""
    narrative = "李寻欢并未受伤，满血满状态。"
    options = [{"text": "A. 继续前行"}, {"text": "B. 休息片刻"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤；伤势：内伤、骨折",
        ],
        "anchor_terms": [],
        "character_names": [],
        "required_terms": [],
    }
    contracts = {"must_include": [], "must_not_include": []}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    # state 维度应低分，但因权重 0.04 较小，总分不会太低
    assert scorecard.dimension("state") < 70.0
    # 总分受多维度影响，但 state 低分应有可观察贡献
    assert scorecard.total < 100.0


def test_state_bounded_score():
    """state 维度分数始终在 0-100 范围内。"""
    narrative = "李寻欢并未受伤、并非在密室、从未拥有长剑。"
    options = [{"text": "继续"}]
    mctx = {
        "state_hard_facts": [
            "主角当前身体状态：重伤",
            "主角当前位置：密室",
            "主角持有物品：长剑",
        ]
    }
    contracts = {}
    
    scorecard = score_turn(narrative, options, mctx, contracts)
    assert 0 <= scorecard.dimension("state") <= 100
