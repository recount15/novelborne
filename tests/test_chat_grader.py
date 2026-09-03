# -*- coding: utf-8 -*-
"""测试 chat_grader 批改器。"""
from __future__ import annotations

import pytest

from core.services import chat_grader


class TestGradeChatReply:
    """测试 grade_chat_reply 批改逻辑。"""
    
    def test_pass_good_reply(self):
        """正常回复通过批改"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "我明白你的意思。这件事确实需要谨慎处理，我会帮你留意的。最近城里发生了不少事情，你也要多加小心才是。"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert errors == []
    
    def test_reject_too_short(self):
        """过短回复被拒"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "好的。"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("过短" in err for err in errors)
    
    def test_reject_too_long(self):
        """过长回复被拒"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "我" * 200
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("过长" in err for err in errors)
    
    def test_reject_scaffolding(self):
        """脚手架残留被拒"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "【旁白】他走了过来```code```，说了一句话。"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("脚手架" in err for err in errors)
    
    def test_reject_json_format(self):
        """JSON 格式残留被拒"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = '{"reply": "你好啊，我是测试角色，很高兴见到你。"}'
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("JSON" in err for err in errors)
    
    def test_reject_meta_narrative(self):
        """元叙述被拒"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "作为AI，我无法回答这个问题，因为我不能理解你的意图。"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("元叙述" in err for err in errors)
    
    def test_reject_forbidden_give_item(self):
        """禁止行为：给物品"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "这是给你的礼物，拿着吧。我觉得你一定会喜欢的。"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("禁止行为" in err and "给你" in err for err in errors)
    
    def test_reject_forbidden_reveal_secret(self):
        """禁止行为：剧透秘密"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "告诉你一个秘密，其实幕后真相是城主早就知道这件事了。"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("禁止行为" in err for err in errors)
    
    def test_reject_forbidden_leave(self):
        """禁止行为：离场"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "我现在有急事，先离开了。你自己保重，我们后会有期。"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("禁止行为" in err and "离开了" in err for err in errors)
    
    def test_reject_forbidden_new_event(self):
        """禁止行为：新事件"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = "就在这时，突然出现了一个神秘人，他走到我们面前说道……"
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("禁止行为" in err for err in errors)
    
    def test_voice_consistency_cold_vs_excited(self):
        """Voice 一致性：冷漠角色不应过于激动"""
        character = {"name": "冰山", "voice": "冷漠疏离"}
        reply = "哇！太棒了！我真的非常非常开心！！！这实在是太让人激动了！！！" + "补充内容" * 10  # 满足50字最低要求
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("语气与角色设定" in err or "用词与角色设定" in err for err in errors)
    
    def test_voice_consistency_formal_vs_slang(self):
        """Voice 一致性：正式角色不应用网络用语"""
        character = {"name": "学者", "voice": "文雅正式"}
        reply = "哈哈哈，你说得对哦！这个方案真的是666，我觉得超级棒哒！" + "需要补充一些内容以满足字数要求。" * 2
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("用词与角色设定" in err for err in errors)
    
    def test_voice_consistency_allows_appropriate_tone(self):
        """Voice 一致性：符合设定的语气允许通过"""
        character = {"name": "学者", "voice": "文雅正式"}
        reply = "您所言极是。此事确需谨慎处理，容我细细思量，稍后再与您商议。"
        errors = chat_grader.grade_chat_reply(character, reply)
        # 不应有 voice 相关错误（可能有其他错误如字数）
        assert not any("语气与角色设定" in err for err in errors)
    
    def test_empty_reply(self):
        """空回复被拒"""
        character = {"name": "测试角色", "voice": "温和"}
        reply = ""
        errors = chat_grader.grade_chat_reply(character, reply)
        assert any("为空" in err for err in errors)


class TestBuildRefillPrompt:
    """测试 build_refill_prompt 重填提示词。"""
    
    def test_includes_error_list(self):
        """重填提示词包含错误清单"""
        character = {"name": "测试角色", "voice": "温和", "desire": "", "fear": ""}
        state = {"state_memory": {}, "history": []}
        errors = ["回复过短（10 字，最少 50 字）", "包含禁止行为「给你」"]
        
        prompt = chat_grader.build_refill_prompt(
            character, "你好", state, "", "", errors
        )
        
        assert "上一版本的问题" in prompt
        assert "回复过短" in prompt
        assert "包含禁止行为" in prompt
        assert "重新生成" in prompt
    
    def test_includes_base_rules(self):
        """重填提示词包含基础规则"""
        character = {"name": "测试角色", "voice": "温和", "desire": "", "fear": ""}
        state = {"state_memory": {}, "history": []}
        errors = ["回复过短"]
        
        prompt = chat_grader.build_refill_prompt(
            character, "你好", state, "", "", errors
        )
        
        # 应包含基础规则（来自 build_chat_prompt）
        assert "符合角色 voice" in prompt or "语气特征" in prompt
        assert "生成规则" in prompt or "铁律" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
