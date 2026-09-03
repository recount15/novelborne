# -*- coding: utf-8 -*-
"""测试 free 阶段专属管线（碎锚后/relay 接通后）。"""
import pytest

from core.engine import free_stage, break_anchor, cheat_code
from core.engine.quality_gate import score_turn, DIMENSION_WEIGHTS


class TestFreeStageDetection:
    """测试 free 阶段判定。"""
    
    def test_not_free_by_default(self):
        """默认状态不是 free 阶段"""
        state = {"round": 10}
        assert not free_stage.is_free_stage(state)
    
    def test_free_when_shattered(self):
        """碎锚后进入 free 阶段"""
        state = {
            "round": 50,
            "broken_anchors": ["锚点1"],
            "anchors_shattered_from": 45,
        }
        assert free_stage.is_free_stage(state)
    
    def test_free_when_relay_active(self):
        """relay 接通后进入 free 阶段"""
        state = {
            "round": 10,
            "relay_activated": True,
        }
        assert free_stage.is_free_stage(state)


class TestFreeStageContracts:
    """测试 free 阶段合同变体。"""
    
    def test_contracts_hint_mode(self):
        """free 阶段合同标记 anchor_mode=hint"""
        base = {"must_include": ["关键词"]}
        contracts = free_stage.free_stage_contracts(base)
        assert contracts["anchor_mode"] == "hint"
        assert contracts["free_navigation"]
    
    def test_preserves_base_contracts(self):
        """保留基础合同内容"""
        base = {"must_include": ["关键词"], "forbidden": ["禁忌"]}
        contracts = free_stage.free_stage_contracts(base)
        assert "must_include" in contracts
        assert "forbidden" in contracts


class TestShatteredHistoryConsistency:
    """测试碎锚史一致性检查。"""
    
    def test_no_violation_clean_narrative(self):
        """正常正文无违规"""
        shattered = {
            "anchor1": {"text": "主角击败敌人", "round": 40}
        }
        narrative = "主角继续前进，回想起之前击败敌人的情景。"
        result = free_stage.check_shattered_history_consistency(narrative, shattered)
        assert result["violations"] == []
    
    def test_detect_negation_cong_wei(self):
        """检出「从未」否定"""
        shattered = {
            "anchor1": {"text": "主角获得宝剑", "round": 30}
        }
        narrative = "主角从未获得过任何武器，只能赤手空拳。"
        result = free_stage.check_shattered_history_consistency(narrative, shattered)
        assert len(result["violations"]) > 0
        assert "从未" in result["evidence"][0]
    
    def test_detect_negation_bing_wei(self):
        """检出「并未」否定"""
        shattered = {
            "anchor1": {"text": "主角受伤", "round": 25}
        }
        narrative = "主角并未受伤，状态完好。"
        result = free_stage.check_shattered_history_consistency(narrative, shattered)
        assert len(result["violations"]) > 0
    
    def test_detect_negation_yuan_ben_mei_you(self):
        """检出「原本没有」否定"""
        shattered = {
            "anchor1": {"text": "主角认识张三", "round": 15}
        }
        narrative = "主角原本没有认识张三，两人是初次见面。"
        result = free_stage.check_shattered_history_consistency(narrative, shattered)
        assert len(result["violations"]) > 0


class TestFreeStageWeights:
    """测试 free 阶段维度权重调整。"""
    
    def test_anchor_weight_reduced(self):
        """anchors 权重从 0.10 降至 0.02"""
        assert free_stage.FREE_ANCHOR_WEIGHT == 0.02
        assert free_stage.FREE_DIMENSION_WEIGHTS["anchors"] == 0.02
    
    def test_weights_sum_to_one(self):
        """权重总和仍为 1.0"""
        total = sum(free_stage.FREE_DIMENSION_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01
    
    def test_continuity_world_increased(self):
        """continuity 和 world_context 权重提升"""
        assert free_stage.FREE_DIMENSION_WEIGHTS["continuity"] > DIMENSION_WEIGHTS["continuity"]
        assert free_stage.FREE_DIMENSION_WEIGHTS["world_context"] > DIMENSION_WEIGHTS["world_context"]


class TestQualityGateFreeVariant:
    """测试质量门接受 free 权重。"""
    
    def test_score_turn_accepts_custom_weights(self):
        """score_turn 接受自定义权重"""
        narrative = "这是一段测试正文。主角继续前进，探索未知的领域。选项如下：\nA. 向左走\nB. 向右走"
        options = [
            {"key": "A", "text": "向左走"},
            {"key": "B", "text": "向右走"},
        ]
        mctx = {
            "anchor_terms": [],
            "character_names": ["主角"],
            "required_terms": [],
            "world_terms": [],
            "state_hard_facts": [],
            "recent_narrative": "",
        }
        
        # 默认权重
        score_default = score_turn(narrative, options, mctx, None)
        
        # free 权重
        score_free = score_turn(narrative, options, mctx, None, 
                               dimension_weights=free_stage.FREE_DIMENSION_WEIGHTS)
        
        # 两者总分可能不同（因为权重分布不同）
        assert score_default.total >= 0
        assert score_free.total >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
