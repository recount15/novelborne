# -*- coding: utf-8 -*-
"""作弊码结构化落地与兑现校验测试（阶段 C）。"""
import pytest
from core.engine import wish_grant


class TestPayloadParsing:
    """payload 类型解析测试。"""
    
    def test_parse_item_payload(self):
        directive = {
            "fact_norm": "主角获得了传说中的神剑",
            "scope": "item",
            "affected": ["神剑"],
        }
        payload = wish_grant.parse_payload(directive)
        assert payload["type"] == "item"
        assert any("神剑" in kw for kw in payload["keywords"])
    
    def test_parse_ability_payload(self):
        directive = {
            "fact_norm": "主角掌握了御剑术",
            "scope": "character",
            "affected": ["主角"],
        }
        payload = wish_grant.parse_payload(directive)
        assert payload["type"] == "ability"
        assert "御剑术" in payload["content"]
    
    def test_parse_relationship_payload(self):
        directive = {
            "fact_norm": "主角与李寻欢结为生死之交",
            "scope": "character",
            "affected": ["李寻欢"],
        }
        payload = wish_grant.parse_payload(directive)
        assert payload["type"] == "relationship"
        assert payload["content"]
    
    def test_parse_fact_payload(self):
        directive = {
            "fact_norm": "世界末日即将到来",
            "scope": "world",
            "affected": ["全局"],
        }
        payload = wish_grant.parse_payload(directive)
        assert payload["type"] == "fact"
        assert len(payload["keywords"]) > 0


class TestGrantToState:
    """payload 落地到 state_memory 测试。"""
    
    def test_grant_item_to_state(self):
        directive = {
            "fact_norm": "主角获得了神剑",
            "scope": "item",
            "affected": ["神剑"],
        }
        state = {}
        patch = wish_grant.grant_to_state(state, directive)
        assert "assets" in patch
        assert "神剑" in str(patch["assets"].get("items"))
    
    def test_grant_ability_to_state(self):
        directive = {
            "fact_norm": "主角掌握了御剑术",
            "scope": "character",
            "affected": ["主角"],
        }
        state = {}
        patch = wish_grant.grant_to_state(state, directive)
        assert "abilities" in patch
        assert "御剑术" in str(patch["abilities"].get("skills"))
    
    def test_grant_relationship_to_state(self):
        directive = {
            "fact_norm": "主角与李寻欢结为好友",
            "scope": "character",
            "affected": ["李寻欢"],
        }
        state = {}
        patch = wish_grant.grant_to_state(state, directive)
        assert "relationships" in patch
    
    def test_grant_fact_no_patch(self):
        """fact 类型不直接修改 state_memory。"""
        directive = {
            "fact_norm": "天下大乱",
            "scope": "world",
            "affected": ["全局"],
        }
        state = {}
        patch = wish_grant.grant_to_state(state, directive)
        assert not patch  # fact 类型返回空 patch


class TestComplianceCheck:
    """兑现校验测试。"""
    
    def test_compliant_directive(self):
        state = {"round": 5}
        narrative = "李寻欢拔出了传说中的神剑，剑光闪耀。"
        directives = [
            {
                "id": 1,
                "fact_norm": "主角获得了神剑",
                "scope": "item",
                "affected": ["神剑"],
                "round": 4,
            }
        ]
        result = wish_grant.check_compliance(state, narrative, directives)
        assert 1 in result["compliant"]
        assert 1 not in result["non_compliant"]
    
    def test_non_compliant_directive(self):
        state = {"round": 5}
        narrative = "李寻欢继续前行，什么也没有发生。"
        directives = [
            {
                "id": 1,
                "fact_norm": "主角获得了神剑",
                "scope": "item",
                "affected": ["神剑"],
                "round": 4,
            }
        ]
        result = wish_grant.check_compliance(state, narrative, directives)
        assert 1 not in result["compliant"]
        assert 1 in result["non_compliant"]
        assert len(result["repair_hints"]) > 0
    
    def test_compliance_window_expired(self):
        """超出窗口的 directive 不检查。"""
        state = {"round": 10}
        narrative = "平静的一天。"
        directives = [
            {
                "id": 1,
                "fact_norm": "主角获得了神剑",
                "scope": "item",
                "affected": ["神剑"],
                "round": 5,  # 5 回合前，超出窗口
            }
        ]
        result = wish_grant.check_compliance(state, narrative, directives)
        assert 1 not in result["compliant"]
        assert 1 not in result["non_compliant"]


class TestAnchorArbitration:
    """锚点仲裁测试。"""
    
    def test_affected_anchors_detected(self):
        directives = [
            {
                "id": 1,
                "affected": ["李寻欢", "密室"],
                "fact_norm": "李寻欢就在密室中",
            }
        ]
        anchor_words = ["李寻欢", "密室", "宝藏"]
        result = wish_grant.anchor_arbitration(directives, anchor_words)
        assert 1 in result["affected_anchors"]
        assert "李寻欢" in result["affected_anchors"][1]
        assert "密室" in result["affected_anchors"][1]
    
    def test_no_affected_anchors(self):
        directives = [
            {
                "id": 1,
                "affected": ["其他角色"],
                "fact_norm": "其他角色出现了",
            }
        ]
        anchor_words = ["李寻欢", "密室"]
        result = wish_grant.anchor_arbitration(directives, anchor_words)
        assert 1 not in result["affected_anchors"]
