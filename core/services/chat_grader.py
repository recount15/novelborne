# -*- coding: utf-8 -*-
"""闲聊回复批改器：apply agent_refill pattern to chat generation.

为角色闲聊服务提供结构化批改和重填能力，确保回复质量。
复用 turn_grader 的批改-重填模式。
"""
from __future__ import annotations

from typing import Any, Mapping

#: 单条回复字数窗口
CHAT_MIN_LENGTH = 50
CHAT_MAX_LENGTH = 150

#: 禁止行为关键词（扩展）
FORBIDDEN_ACTIONS = (
    "给你", "送你", "赠你", "这是给你的", "拿去吧", "收下吧",
    "告诉你一个秘密", "其实真相是", "幕后", "真相", "秘密是",
    "离开了", "走了", "离去", "告别", "再见", "得走了",
    "新的", "突然出现", "忽然发生", "突然发生", "新事件",
)

#: Voice 一致性检查阈值（更严格）
VOICE_EXCLAMATION_THRESHOLD = 2  # 冷漠角色感叹号上限
VOICE_FORMAL_NET_SLANG = ("哈哈", "嘻嘻", "嘿嘿", "哇", "wow", "666", "hhh", "emmm", "蛤")


def grade_chat_reply(character: Mapping[str, Any], reply: str) -> list[str]:
    """批改闲聊回复，返回中文错误清单（空列表 = 通过）。
    
    检查项：
    1. 字数窗口（50-150）
    2. 脚手架残留（```、【】、<<<>>>）
    3. JSON 格式残留
    4. 元叙述（"作为AI"、"我无法"）
    5. 禁止行为（给物品、剧透、离场）
    6. 角色 voice 一致性（简化版）
    """
    errors: list[str] = []
    text = str(reply or "").strip()
    
    if not text:
        errors.append("回复为空")
        return errors
    
    # 1. 字数窗口
    length = len(text)
    if length < CHAT_MIN_LENGTH:
        errors.append(f"回复过短（{length} 字，最少 {CHAT_MIN_LENGTH} 字）")
    elif length > CHAT_MAX_LENGTH:
        errors.append(f"回复过长（{length} 字，最多 {CHAT_MAX_LENGTH} 字）")
    
    # 2. 脚手架残留
    if any(marker in text for marker in ("```", "【", "】", "<<<", ">>>")):
        errors.append("残留脚手架或标记符号")
    
    # 3. JSON 格式残留
    if text.strip().startswith(("{", "[")):
        errors.append("残留 JSON 格式，应直接输出对话")
    
    # 4. 元叙述
    meta_phrases = ("作为AI", "作为人工智能", "我无法", "我不能", "我是一个")
    if any(phrase in text for phrase in meta_phrases):
        errors.append("包含元叙述，破坏角色沉浸感")
    
    # 5. 禁止行为
    for action in FORBIDDEN_ACTIONS:
        if action in text:
            errors.append(f"包含禁止行为「{action}」——闲聊不得推进剧情、给予物品或剧透")
            break
    
    # 6. Voice 一致性（强化检查，目标95%+一致性）
    voice = str(character.get("voice") or "").strip()
    if voice:
        # 检查1：voice 标注"冷漠/淡然/疏离"却出现过多感叹号
        cold_markers = ("冷漠", "淡然", "疏离", "冷淡", "严肃", "沉默", "寡言")
        if any(marker in voice for marker in cold_markers):
            exclamation_count = text.count("！") + text.count("!")
            if exclamation_count > VOICE_EXCLAMATION_THRESHOLD:
                errors.append(f"语气与角色设定「{voice}」不符（出现{exclamation_count}个感叹号，冷漠角色应≤{VOICE_EXCLAMATION_THRESHOLD}个）")
            # 额外检查：冷漠角色不应使用"太好了"、"真棒"等热情用语
            enthusiastic_phrases = ("太好了", "真棒", "太棒了", "太开心", "超级", "非常高兴")
            if any(phrase in text for phrase in enthusiastic_phrases):
                errors.append(f"用词与角色设定「{voice}」不符（冷漠角色不应使用热情用语）")
        
        # 检查2：voice 标注"正式/文雅/古典"却用网络用语
        formal_markers = ("正式", "文雅", "古典", "优雅", "高贵", "端庄", "书卷")
        if any(marker in voice for marker in formal_markers):
            if any(slang in text for slang in VOICE_FORMAL_NET_SLANG):
                errors.append(f"用词与角色设定「{voice}」不符（正式角色不应使用网络用语）")
            # 额外检查：正式角色应使用"您"而非"你"
            if "你" in text and "您" not in text and text.count("你") >= 2:
                errors.append(f"称谓与角色设定「{voice}」不符（正式角色应使用'您'而非'你'）")
        
        # 检查3：voice 标注"热情/活泼/开朗"却过于拘谨
        warm_markers = ("热情", "活泼", "开朗", "外向", "开朗", "爽朗", "乐观")
        if any(marker in voice for marker in warm_markers):
            formal_count = text.count("您") + text.count("敬请") + text.count("恕")
            sentence_count = max(1, text.count("。") + text.count("！") + text.count("？"))
            if formal_count >= sentence_count:  # 平均每句都用敬语
                errors.append(f"语气与角色设定「{voice}」不符（热情角色过于拘谨，敬语过多）")
            # 额外检查：热情角色应有情感词
            has_emotion = any(word in text for word in ("高兴", "开心", "哈哈", "嘿", "哇", "！", "~"))
            if not has_emotion and len(text) >= 80:
                errors.append(f"情感表达与角色设定「{voice}」不符（热情角色缺少情感词）")
    
    return errors


def build_refill_prompt(character: Mapping[str, Any], 
                       player_input: str,
                       state: Mapping[str, Any],
                       scene_excerpt: str,
                       recent_summary: str,
                       errors: list[str]) -> str:
    """构造重填提示词（带错误清单）。
    
    复用 build_chat_prompt 的基础结构，追加错误反馈。
    """
    from core.services.chat_service import build_chat_prompt
    
    base_prompt = build_chat_prompt(
        character, player_input, state, scene_excerpt, recent_summary
    )
    
    error_section = "\n\n# 上一版本的问题（必须修复）\n"
    error_section += "\n".join(f"- {error}" for error in errors)
    error_section += "\n\n请重新生成，完全避免上述问题，确保符合所有规则。"
    
    return base_prompt + error_section


__all__ = [
    "grade_chat_reply",
    "build_refill_prompt",
    "CHAT_MIN_LENGTH",
    "CHAT_MAX_LENGTH",
]
