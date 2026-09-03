# -*- coding: utf-8 -*-
"""测试 quest_grader 批改器。"""
from __future__ import annotations

import pytest

from core.services import quest_grader


class TestGradeQuestVerdict:
    """测试 grade_quest_verdict 批改逻辑。"""
    
    def test_pass_valid_verdict(self):
        """正常判定通过批改"""
        quest = {
            "title": "寻找古籍",
            "requirements": ["找到失落的古籍", "将古籍带回图书馆"]
        }
        narrative = "你终于在废墟深处找到了那本失落的古籍，小心翼翼地将它放入背包。"
        verdict = {
            "completed": False,
            "evidence": "你终于在废墟深处找到了那本失落的古籍"
        }
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert errors == []
    
    def test_reject_non_dict_verdict(self):
        """非字典 verdict 被拒"""
        quest = {"title": "测试", "requirements": []}
        narrative = "测试正文"
        verdict = "not a dict"
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("必须是字典" in err for err in errors)
    
    def test_reject_non_boolean_completed(self):
        """completed 非布尔值被拒"""
        quest = {"title": "测试", "requirements": []}
        narrative = "测试正文"
        verdict = {"completed": "yes", "evidence": "测试"}
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("必须是布尔值" in err for err in errors)
    
    def test_reject_empty_evidence(self):
        """空 evidence 被拒"""
        quest = {"title": "测试", "requirements": []}
        narrative = "测试正文"
        verdict = {"completed": False, "evidence": ""}
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("为空" in err for err in errors)
    
    def test_reject_short_evidence(self):
        """过短 evidence 被拒"""
        quest = {"title": "测试", "requirements": []}
        narrative = "测试正文内容较长"
        verdict = {"completed": False, "evidence": "短"}
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("过短" in err for err in errors)
    
    def test_reject_non_quoted_evidence(self):
        """非原文引用被拒"""
        quest = {"title": "测试", "requirements": []}
        narrative = "你走进了房间，发现桌上放着一封信。"
        verdict = {"completed": False, "evidence": "主角进入房间看到了信件"}
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("不是原文引用" in err for err in errors)
    
    def test_reject_vague_evidence(self):
        """模糊表述被拒"""
        quest = {"title": "测试", "requirements": []}
        narrative = "你走进了房间，发现桌上似乎放着一封信。"
        verdict = {"completed": False, "evidence": "你走进了房间，发现桌上似乎放着一封信"}
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("模糊表述" in err for err in errors)
    
    def test_completed_true_requires_requirement_match(self):
        """Completed=True 需要匹配 requirements"""
        quest = {
            "title": "寻找古籍",
            "requirements": ["找到失落的古籍", "将古籍带回图书馆"]
        }
        narrative = "你在城里闲逛了一圈，买了一些日用品。"
        verdict = {
            "completed": True,
            "evidence": "你在城里闲逛了一圈，买了一些日用品"
        }
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("匹配度不足" in err or "未体现任何任务要求" in err for err in errors)
    
    def test_completed_true_with_matched_requirement(self):
        """Completed=True 且匹配 requirement 通过"""
        quest = {
            "title": "寻找古籍",
            "requirements": ["找到失落的古籍", "将古籍带回图书馆"]
        }
        narrative = "你小心翼翼地将那本失落的古籍放回图书馆的架上，管理员向你表示感谢。"
        verdict = {
            "completed": True,
            "evidence": "你小心翼翼地将那本失落的古籍放回图书馆的架上"
        }
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        # 可能有其他问题（如字数），但不应有 requirement 匹配问题
        assert not any("未体现任何任务要求" in err for err in errors)
    
    def test_completed_false_with_positive_evidence_warns(self):
        """Completed=False 但 evidence 是正面描述给出警告"""
        quest = {"title": "测试", "requirements": ["完成任务"]}
        narrative = "你成功完成了任务，获得了奖励。"
        verdict = {
            "completed": False,
            "evidence": "你成功完成了任务"
        }
        errors = quest_grader.grade_quest_verdict(quest, narrative, verdict)
        assert any("逻辑不一致" in err for err in errors)


class TestBuildRefillPrompt:
    """测试 build_refill_prompt 重填提示词。"""
    
    def test_includes_error_list(self):
        """重填提示词包含错误清单"""
        quest = {
            "title": "寻找古籍",
            "requirements": ["找到失落的古籍"]
        }
        narrative = "测试正文"
        errors = ["evidence 过短", "不是原文引用"]
        
        prompt = quest_grader.build_refill_prompt(quest, narrative, [], errors)
        
        assert "上一版本的问题" in prompt
        assert "evidence 过短" in prompt
        assert "不是原文引用" in prompt
        assert "重新判定" in prompt
    
    def test_includes_quest_info(self):
        """重填提示词包含任务信息"""
        quest = {
            "title": "寻找古籍",
            "requirements": ["找到失落的古籍", "带回图书馆"]
        }
        narrative = "测试正文内容较长"
        errors = ["测试错误"]
        
        prompt = quest_grader.build_refill_prompt(quest, narrative, [], errors)
        
        assert "寻找古籍" in prompt
        assert "找到失落的古籍" in prompt
        assert "带回图书馆" in prompt
    
    def test_includes_recent_progress(self):
        """重填提示词包含历史进展"""
        quest = {"title": "测试", "requirements": []}
        narrative = "正文"
        recent_progress = [
            {"round": 10, "evidence": "第一次进展"},
            {"round": 11, "evidence": "第二次进展"},
        ]
        errors = ["测试错误"]
        
        prompt = quest_grader.build_refill_prompt(quest, narrative, recent_progress, errors)
        
        assert "回合 10" in prompt
        assert "第一次进展" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
