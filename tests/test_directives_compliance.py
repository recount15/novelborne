# -*- coding: utf-8 -*-
"""测试 directives 模块的 v2.0.4 增强功能：类型化 payload、兑现检查、锚点仲裁。"""
import pytest

from core.engine import directives
from core.memory import blank_state


class TestTypedPayload:
    """测试类型化 payload 解析与落地。"""
    
    def test_parse_item_payload(self):
        """解析 item payload"""
        data = '''{
            "payload_type": "item",
            "content": {"name": "倚天剑", "quantity": 1},
            "keywords": ["倚天剑", "宝剑"]
        }'''
        result, errors = directives.parse_typed_payload(data)
        assert not errors
        assert result["payload_type"] == "item"
        assert result["content"]["name"] == "倚天剑"
        assert "倚天剑" in result["keywords"]
    
    def test_parse_ability_payload(self):
        """解析 ability payload"""
        data = '''{
            "payload_type": "ability",
            "content": {"name": "乾坤大挪移", "level": 5},
            "keywords": ["乾坤大挪移", "挪移"]
        }'''
        result, errors = directives.parse_typed_payload(data)
        assert not errors
        assert result["payload_type"] == "ability"
        assert result["content"]["level"] == 5
    
    def test_parse_relationship_payload(self):
        """解析 relationship payload"""
        data = '''{
            "payload_type": "relationship",
            "content": {"name": "赵敏", "delta": "挚爱", "summary": "生死与共"},
            "keywords": ["赵敏", "挚爱"]
        }'''
        result, errors = directives.parse_typed_payload(data)
        assert not errors
        assert result["payload_type"] == "relationship"
        assert result["content"]["name"] == "赵敏"
    
    def test_parse_fact_payload(self):
        """解析 fact payload"""
        data = '''{
            "payload_type": "fact",
            "content": {"statement": "张无忌已成为明教教主"},
            "keywords": ["明教教主", "张无忌"]
        }'''
        result, errors = directives.parse_typed_payload(data)
        assert not errors
        assert result["payload_type"] == "fact"
    
    def test_parse_invalid_type(self):
        """无效的 payload_type"""
        data = '{"payload_type": "invalid", "content": {}, "keywords": ["x"]}'
        result, errors = directives.parse_typed_payload(data)
        assert errors
        assert "payload_type" in errors[0]
    
    def test_parse_missing_keywords(self):
        """缺少 keywords"""
        data = '{"payload_type": "item", "content": {"name": "x"}}'
        result, errors = directives.parse_typed_payload(data)
        assert errors
        assert "keywords" in errors[0]
    
    def test_grant_item_payload(self):
        """落地 item payload 到 state_memory"""
        state = {
            "state_memory": blank_state("互动", "倚天屠龙记"),
            "mode": "互动",
        }
        payload = {
            "payload_type": "item",
            "content": {"name": "倚天剑", "quantity": 1},
            "keywords": ["倚天剑"],
        }
        success, msg = directives.grant_typed_payload(state, directive_id=1, payload=payload, round_no=10)
        assert success
        assert "倚天剑" in msg
        # 检查是否写入 state_memory
        items = state["state_memory"].get("assets", {}).get("items") or []
        assert any(item.get("name") == "倚天剑" for item in items)
    
    def test_grant_ability_payload(self):
        """落地 ability payload 到 state_memory"""
        state = {
            "state_memory": blank_state("互动", "倚天屠龙记"),
            "mode": "互动",
        }
        payload = {
            "payload_type": "ability",
            "content": {"name": "乾坤大挪移", "level": 5},
            "keywords": ["乾坤大挪移"],
        }
        success, msg = directives.grant_typed_payload(state, directive_id=2, payload=payload, round_no=10)
        assert success
        skills = state["state_memory"].get("abilities", {}).get("skills") or []
        assert any(skill.get("name") == "乾坤大挪移" for skill in skills)
    
    def test_grant_relationship_payload(self):
        """落地 relationship payload 到 state_memory"""
        state = {
            "state_memory": blank_state("互动", "倚天屠龙记"),
            "mode": "互动",
        }
        payload = {
            "payload_type": "relationship",
            "content": {"name": "赵敏", "delta": "挚爱", "summary": "生死与共"},
            "keywords": ["赵敏"],
        }
        success, msg = directives.grant_typed_payload(state, directive_id=3, payload=payload, round_no=10)
        assert success
        rels = state["state_memory"].get("relationships", {}).get("characters") or []
        assert any(rel.get("name") == "赵敏" for rel in rels)


class TestComplianceCheck:
    """测试兑现检查（compliance）。"""
    
    def test_no_active_directives(self):
        """没有激活的 directives 时不检查"""
        state = {"round": 10}
        result = directives.check_compliance(state, "这是正文内容", round_window=2)
        assert result["checked"] == []
        assert result["violations"] == []
    
    def test_directive_outside_window(self):
        """超出窗口的 directive 不检查"""
        state = {
            "round": 20,
            "ledger": {
                "cheat": {
                    "directives": [
                        {
                            "id": 1,
                            "fact_norm": "主角获得倚天剑",
                            "activated_round": 10,  # 10回合前激活
                            "keywords": ["倚天剑"],
                            "superseded_by": 0,
                        }
                    ]
                }
            }
        }
        result = directives.check_compliance(state, "正文内容", round_window=2)
        # 20 - 10 = 10 > 2，不检查
        assert result["checked"] == []
    
    def test_compliant_directive(self):
        """关键词在正文中，兑现成功"""
        state = {
            "round": 12,
            "ledger": {
                "cheat": {
                    "directives": [
                        {
                            "id": 1,
                            "fact_norm": "主角获得倚天剑",
                            "activated_round": 11,
                            "keywords": ["倚天剑", "宝剑"],
                            "superseded_by": 0,
                        }
                    ]
                }
            }
        }
        narrative = "张无忌握住了倚天剑，这把宝剑锋利无比。"
        result = directives.check_compliance(state, narrative, round_window=2)
        assert len(result["checked"]) == 1
        assert result["checked"][0]["compliant"]
        assert result["violations"] == []
    
    def test_non_compliant_directive(self):
        """关键词不在正文中，未兑现"""
        state = {
            "round": 12,
            "ledger": {
                "cheat": {
                    "directives": [
                        {
                            "id": 1,
                            "fact_norm": "主角获得倚天剑",
                            "activated_round": 11,
                            "keywords": ["倚天剑", "宝剑"],
                            "superseded_by": 0,
                        }
                    ]
                }
            }
        }
        narrative = "张无忌在山洞里修炼武功。"  # 没有提到倚天剑
        result = directives.check_compliance(state, narrative, round_window=2)
        assert len(result["checked"]) == 1
        assert not result["checked"][0]["compliant"]
        assert len(result["violations"]) == 1
        assert "倚天剑" in result["violations"][0]["missing_keywords"]
    
    def test_mark_activated(self):
        """标记 directive 已激活"""
        state = {
            "ledger": {
                "cheat": {
                    "directives": [
                        {"id": 1, "fact_norm": "测试", "activated_round": 0}
                    ]
                }
            }
        }
        success = directives.mark_activated(state, directive_id=1, round_no=10)
        assert success
        assert state["ledger"]["cheat"]["directives"][0]["activated_round"] == 10


class TestAffectedAnchors:
    """测试锚点仲裁（affected_anchors）。"""
    
    def test_register_with_affected_anchors(self):
        """登记时携带 affected_anchors"""
        state = {"ledger": {}}
        entry = {
            "fact_norm": "张无忌提前学会乾坤大挪移",
            "scope": "character",
            "affected": ["张无忌"],
            "affected_anchors": ["光明顶之战", "六大派围攻光明顶"],
        }
        row = directives.register(state, entry, kind="wish", round_no=5)
        assert row["affected_anchors"] == ["光明顶之战", "六大派围攻光明顶"]
    
    def test_parse_registration_with_anchors(self):
        """解析带 affected_anchors 的登记卷"""
        data = '''{
            "fact_norm": "主角掌握了乾坤大挪移",
            "scope": "character",
            "affected": ["张无忌"],
            "conflicts": [],
            "affected_anchors": ["光明顶之战"]
        }'''
        result, notes = directives.parse_registration(data, allowed=["张无忌"])
        assert not notes or all("错误" not in n for n in notes)
        assert result["affected_anchors"] == ["光明顶之战"]
    
    def test_affected_anchors_in_build_block(self):
        """构建注入块时保留 affected_anchors（但不显示）"""
        selected = [
            {
                "kind": "wish",
                "fact_norm": "张无忌提前学会乾坤大挪移",
                "scope": "character",
                "affected": ["张无忌"],
                "affected_anchors": ["光明顶之战"],
            }
        ]
        block = directives.build_directives_block(selected)
        assert "张无忌" in block
        assert "乾坤大挪移" in block
        # affected_anchors 不显示在注入块中（仅供质量门使用）


class TestRelayEntry:
    """测试 relay 真注册（修复假入口）。"""
    
    def test_relay_registration_flow(self):
        """relay 通过 directives_service.append_relay_fact 注册"""
        # 这个测试验证 relay 走真注册路径
        # 实际调用链: App.vue -> ask_service -> directives_service.append_relay_fact
        # 本测试只验证 directives.register 支持 kind="relay"
        state = {"ledger": {}}
        entry = {
            "fact_norm": "玩家增补：张三丰传授张无忌太极剑",
            "scope": "plot",
            "affected": ["张无忌", "张三丰"],
        }
        row = directives.register(state, entry, kind="relay", round_no=8)
        assert row["kind"] == "relay"
        assert "张三丰" in row["affected"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
