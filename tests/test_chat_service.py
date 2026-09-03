# -*- coding: utf-8 -*-
"""角色闲聊服务测试（阶段 F）。"""
import pytest
from core.services import chat_service


class TestGetRoster:
    """活跃角色名册测试。"""
    
    def test_get_roster_from_active_members(self):
        state = {
            "active_members": [
                {"name": "李寻欢", "voice": "潇洒不羁", "desire": "找到真相"},
                {"name": "阿飞", "voice": "沉默寡言", "desire": "保护朋友"},
            ]
        }
        roster = chat_service.get_roster(state)
        assert len(roster) == 2
        assert roster[0]["name"] == "李寻欢"
        assert roster[1]["name"] == "阿飞"
    
    def test_empty_roster(self):
        state = {}
        roster = chat_service.get_roster(state)
        assert roster == []
    
    def test_roster_filters_invalid_entries(self):
        state = {
            "active_members": [
                {"name": "李寻欢", "voice": "潇洒"},
                {"voice": "无名"},  # 缺少 name
                None,
            ]
        }
        roster = chat_service.get_roster(state)
        assert len(roster) == 1
        assert roster[0]["name"] == "李寻欢"


class TestChatQuality:
    """闲聊质量门测试。"""
    
    def test_too_short(self):
        text = "好的"
        issues = chat_service._check_chat_quality(text)
        assert any("过短" in issue for issue in issues)
    
    def test_too_long(self):
        text = "这是一段超长的回复" * 20
        issues = chat_service._check_chat_quality(text)
        assert any("过长" in issue for issue in issues)
    
    def test_scaffold_residue(self):
        text = "李寻欢微笑道：「好的。」【系统提示：这是测试】"
        issues = chat_service._check_chat_quality(text)
        assert any("脚手架" in issue for issue in issues)
    
    def test_meta_narrative(self):
        text = "作为AI，我无法回答这个问题。"
        issues = chat_service._check_chat_quality(text)
        assert any("元叙述" in issue for issue in issues)
    
    def test_forbidden_action_gift(self):
        text = "李寻欢递给你一把剑：「这是给你的。」"
        issues = chat_service._check_chat_quality(text)
        assert any("禁止行为" in issue for issue in issues)
    
    def test_forbidden_action_departure(self):
        text = "李寻欢说完便离开了。"
        issues = chat_service._check_chat_quality(text)
        assert any("禁止行为" in issue for issue in issues)
    
    def test_valid_chat(self):
        text = "李寻欢微笑道：「好的，我明白了。我们继续吧。」他神色平静，没有多说什么。这是一段符合长度要求的有效闲聊回复内容。"
        issues = chat_service._check_chat_quality(text)
        assert len(issues) == 0


class TestSaveChat:
    """聊天记录保存测试。"""
    
    def test_save_chat_creates_side_chats(self):
        state = {}
        chat_service.save_chat(state, "李寻欢", "你好", "你好啊，朋友。")
        assert "side_chats" in state
        assert "李寻欢" in state["side_chats"]
        assert len(state["side_chats"]["李寻欢"]) == 1
    
    def test_save_chat_appends_to_history(self):
        state = {
            "side_chats": {
                "李寻欢": [
                    {"player": "第一条", "reply": "回复1", "round": 1}
                ]
            }
        }
        chat_service.save_chat(state, "李寻欢", "第二条", "回复2")
        assert len(state["side_chats"]["李寻欢"]) == 2
    
    def test_save_chat_limits_history(self):
        state = {
            "side_chats": {
                "李寻欢": [{"player": f"msg{i}", "reply": f"reply{i}", "round": i} 
                          for i in range(15)]
            }
        }
        chat_service.save_chat(state, "李寻欢", "new", "new_reply")
        assert len(state["side_chats"]["李寻欢"]) == 10  # 只保留最近 10 条


class TestStateIsolation:
    """状态隔离硬保证测试。"""
    
    def test_chat_does_not_modify_history(self):
        state = {
            "history": [{"round": 1, "narrative": "第一回合"}],
            "active_members": [{"name": "李寻欢", "voice": "潇洒"}],
        }
        import copy
        original_history = copy.deepcopy(state["history"])
        
        chat_service.save_chat(state, "李寻欢", "测试", "回复")
        
        assert state["history"] == original_history
    
    def test_chat_does_not_modify_state_memory(self):
        state = {
            "state_memory": {"body": {"condition": "健康"}},
            "active_members": [{"name": "李寻欢", "voice": "潇洒"}],
        }
        import copy
        original_memory = copy.deepcopy(state["state_memory"])
        
        chat_service.save_chat(state, "李寻欢", "测试", "回复")
        
        assert state["state_memory"] == original_memory
    
    def test_chat_only_modifies_side_chats(self):
        state = {
            "history": [{"round": 1}],
            "state_memory": {"body": {"condition": "健康"}},
            "quest": {"active": []},
            "active_members": [{"name": "李寻欢", "voice": "潇洒"}],
        }
        import copy
        original_keys = set(state.keys())
        
        chat_service.save_chat(state, "李寻欢", "测试", "回复")
        
        # 只增加了 side_chats 键
        new_keys = set(state.keys())
        added_keys = new_keys - original_keys
        assert added_keys == {"side_chats"}
