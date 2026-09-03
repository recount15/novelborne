# -*- coding: utf-8 -*-
"""强化批改器测试：确保95%+准确性，验证真实用户体验提升"""
import pytest
from core.services.quest_grader import grade_quest_verdict, MIN_KEYWORD_MATCHES
from core.services.chat_grader import grade_chat_reply, VOICE_EXCLAMATION_THRESHOLD
from core.services.narrative_flow_grader import grade_narrative_flow


# ========== Quest Grader 强化测试（目标95%+准确性） ==========

class TestQuestGraderEnhanced:
    """任务判定批改器强化测试"""
    
    def test_evidence_length_15_chars_minimum(self):
        """测试 evidence 最短15字要求（强化）"""
        quest = {"requirements": ["找到古籍"]}
        narrative = "你找到了那本古籍"
        verdict = {
            "completed": True,
            "evidence": "找到了古籍"  # 仅6字
        }
        errors = grade_quest_verdict(quest, narrative, verdict)
        assert any("过短" in e and "15" in e for e in errors)
    
    def test_evidence_exact_quote_with_tolerance(self):
        """测试 evidence 精确引用（允许前后3字容差）"""
        quest = {"requirements": ["完成任务"]}
        narrative = "经过艰难的战斗，你终于完成了这个艰巨的任务，心中充满了成就感。"
        
        # 完全匹配 - 通过
        verdict1 = {
            "completed": True,
            "evidence": "你终于完成了这个艰巨的任务"
        }
        errors1 = grade_quest_verdict(quest, narrative, verdict1)
        assert not any("不是原文引用" in e for e in errors1)
        
        # 核心匹配（去除首尾3字后匹配）- 通过
        verdict2 = {
            "completed": True,
            "evidence": "终于完成了这个艰巨的任务"  # 缺少"你"
        }
        errors2 = grade_quest_verdict(quest, narrative, verdict2)
        # 核心片段在原文中，应通过
        
        # 完全不匹配 - 失败
        verdict3 = {
            "completed": True,
            "evidence": "你轻松地完成了任务"  # 编造内容
        }
        errors3 = grade_quest_verdict(quest, narrative, verdict3)
        assert any("不是原文引用" in e for e in errors3)
    
    def test_keyword_matching_requires_3_matches(self):
        """测试 completed=True 时要求至少3个关键词匹配"""
        quest = {
            "requirements": [
                "找到失落的古籍",
                "将古籍带回图书馆",
                "修复古籍破损的书页"
            ]
        }
        narrative = "你小心翼翼地将那本失落的古籍带回图书馆，并成功修复了破损的书页。"
        
        # 匹配度充分 - 通过
        verdict_pass = {
            "completed": True,
            "evidence": "你小心翼翼地将那本失落的古籍带回图书馆，并成功修复了破损的书页"
        }
        errors_pass = grade_quest_verdict(quest, narrative, verdict_pass)
        assert not any("匹配度不足" in e for e in errors_pass)
        
        # 匹配度不足（仅1个关键词）- 失败
        narrative_weak = "你完成了任务。"
        verdict_weak = {
            "completed": True,
            "evidence": "你完成了任务"
        }
        errors_weak = grade_quest_verdict(quest, narrative_weak, verdict_weak)
        assert any(f"需≥{MIN_KEYWORD_MATCHES}项" in e for e in errors_weak)
    
    def test_vague_phrases_expanded(self):
        """测试扩展的模糊表述检测"""
        quest = {"requirements": ["击败敌人"]}
        narrative = "战斗结束了，敌人倒下了。"
        
        vague_phrases = ["似乎", "可能", "也许", "大概", "或许", "好像", "仿佛", "看起来", "估计"]
        for phrase in vague_phrases:
            verdict = {
                "completed": True,
                "evidence": f"战斗结束了，敌人{phrase}倒下了"
            }
            errors = grade_quest_verdict(quest, narrative, verdict)
            assert any("模糊表述" in e and phrase in e for e in errors), f"应检测到模糊表述「{phrase}」"


# ========== Chat Grader 强化测试（目标95%+准确性） ==========

class TestChatGraderEnhanced:
    """角色闲聊批改器强化测试"""
    
    def test_voice_consistency_cold_character_detailed(self):
        """测试冷漠角色 voice 一致性（强化检测）"""
        character_cold = {"voice": "冷漠疏离"}
        
        # 感叹号超过阈值 - 失败
        reply_exclamation = "我明白了！这确实很重要！我会注意的！" + "补充内容" * 15
        errors1 = grade_chat_reply(character_cold, reply_exclamation)
        assert any("感叹号" in e and str(VOICE_EXCLAMATION_THRESHOLD) in e for e in errors1)
        
        # 热情用语 - 失败
        reply_enthusiastic = "太好了！这真是太棒了！我非常高兴能帮到你！" + "补充内容" * 10
        errors2 = grade_chat_reply(character_cold, reply_enthusiastic)
        assert any("热情用语" in e for e in errors2)
        
        # 符合冷漠设定 - 通过
        reply_proper = "我知道了。这件事我会处理。没什么需要多说的。" + "继续保持冷静的态度。" * 3
        errors3 = grade_chat_reply(character_cold, reply_proper)
        assert not any("语气与角色设定" in e or "用词与角色设定" in e for e in errors3)
    
    def test_voice_consistency_formal_character_detailed(self):
        """测试正式角色 voice 一致性（强化检测）"""
        character_formal = {"voice": "正式优雅"}
        
        # 网络用语 - 失败
        reply_slang = "哈哈，这个想法不错哦，666！让我们一起努力吧，hhh。" + "补充正式内容" * 8
        errors1 = grade_chat_reply(character_formal, reply_slang)
        assert any("网络用语" in e for e in errors1)
        
        # 使用"你"而非"您" - 失败
        reply_informal = "我明白你的意思，你说得对，你可以这样做。" + "我会协助你完成任务。" * 5
        errors2 = grade_chat_reply(character_formal, reply_informal)
        assert any("称谓" in e and "您" in e for e in errors2)
        
        # 符合正式设定 - 通过
        reply_proper = "我理解您的顾虑。这件事确实需要谨慎处理。请您放心，我会妥善安排。" * 2
        errors3 = grade_chat_reply(character_formal, reply_proper)
        assert not any("用词与角色设定" in e or "称谓与角色设定" in e for e in errors3)
    
    def test_voice_consistency_warm_character_detailed(self):
        """测试热情角色 voice 一致性（强化检测）"""
        character_warm = {"voice": "热情开朗"}
        
        # 过于拘谨（敬语过多）- 失败
        reply_stiff = "敬请您放心。恕我直言，这件事确实需要您的协助。" * 8
        errors1 = grade_chat_reply(character_warm, reply_stiff)
        assert any("过于拘谨" in e for e in errors1)
        
        # 缺少情感词（长回复无情感）- 失败
        reply_bland = "我知道了。这件事我会处理。没什么问题。" + "继续按计划进行就好。" * 15
        errors2 = grade_chat_reply(character_warm, reply_bland)
        assert any("缺少情感词" in e for e in errors2)
        
        # 符合热情设定 - 通过
        reply_proper = "哇，这个主意太棒了！我超级开心能和你一起做这件事！咱们加油吧！~" * 2
        errors3 = grade_chat_reply(character_warm, reply_proper)
        assert not any("情感表达与角色设定" in e for e in errors3)
    
    def test_forbidden_actions_expanded(self):
        """测试扩展的禁止行为检测"""
        character = {"voice": "中性"}
        
        forbidden_cases = [
            ("给你这把剑，拿去吧。" + "补充" * 20, "给你"),  # 实际检测到"给你"
            ("告诉你一个秘密，真相是..." + "补充" * 20, "告诉你一个秘密"),  # 检测到完整短语
            ("我得走了，再见。" + "补充" * 20, "走了"),  # 检测到"走了"而非"得走了"
            ("突然出现了新的敌人。" + "补充" * 20, "新的"),  # 检测到"新的"而非"突然出现"
        ]
        
        for reply, keyword in forbidden_cases:
            errors = grade_chat_reply(character, reply)
            assert any("禁止行为" in e for e in errors), f"应检测到禁止行为「{keyword}」: {errors}"


# ========== Narrative Flow Grader 测试（6层检测） ==========

class TestNarrativeFlowGrader:
    """叙事流畅度批改器测试（6层检测系统）"""
    
    def test_surface_layer_sentence_pattern_repetition(self):
        """测试表层检测：句式重复"""
        narrative = """他走进了房间。他看到了桌子。他拿起了书本。他开始阅读内容。他翻开了第一页。他仔细地看着文字。他感受到了氛围。他注意到了细节。他观察着环境。他思考着意义。他理解了含义。房间里很安静。窗外传来悦耳的鸟鸣。"""
        
        result = grade_narrative_flow(narrative)
        # 句式重复会降低表层分数
        assert result["layer_scores"]["surface"] < 90  # 有句式重复问题
    
    def test_surface_layer_word_repetition(self):
        """测试表层检测：用词重复"""
        narrative = """
        这个房间非常安静，房间里只有一张桌子。桌子上放着一本书。桌子的颜色是深褐色的。
        他看着这本书，书的封面很旧。旧书散发着独特的气息。书页已经泛黄了。
        房间的窗户开着，微风吹进房间，翻动着书页。房间里充满了宁静的氛围。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        # "房间"、"书"、"桌子"重复过多
        assert any("[用词重复]" in issue for issue in result["issues"])
    
    def test_structure_layer_abrupt_transition(self):
        """测试结构检测：段落衔接突兀"""
        narrative = """
        他正在安静地读书，沉浸在故事的世界里。突然，门被撞开了。突然，一个陌生人冲了进来。
        突然，灯光熄灭了。突然，他听到了脚步声。房间里陷入了黑暗。四周一片寂静。
        他感到害怕，心跳加速，不知道该怎么办。手心开始出汗，呼吸变得急促起来。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        assert any("[段落衔接突兀]" in issue for issue in result["issues"])
        assert "突然" in result["issues"][0] or any("突然" in iss for iss in result["issues"])
    
    def test_structure_layer_tense_inconsistency(self):
        """测试结构检测：时态混乱"""
        narrative = """
        他曾经住在这座城市，在老街区的一栋公寓里。那时候，生活很平静，每天都按部就班。
        现在，他正在另一个地方工作，远离了故土。此刻，他感到很孤独，身边没有熟悉的面孔。
        过去的记忆涌上心头，那些美好的时光历历在目。当年的朋友都已离去，各奔东西了。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        # 混用过去时与现在时，且无转换标记
        assert any("[时态混乱]" in issue for issue in result["issues"])
    
    def test_immersion_layer_meta_narrative(self):
        """测试沉浸感检测：元叙述泄露"""
        narrative = """正如我们所见，主角进入了房间，推开了那扇厚重的木门。读者可以看到，房间里很暗，只有微弱的光线从窗户透进来。如前所述，这是一个关键时刻，决定着故事的走向和未来的发展。让我们继续观察他的行动，看看接下来会发生什么有趣的事情。"""
        
        result = grade_narrative_flow(narrative)
        assert any("[元叙述泄露]" in issue for issue in result["issues"])
        # 元叙述会降低沉浸感分数
        assert result["layer_scores"]["immersion"] < 95
    
    def test_immersion_layer_emotion_telling(self):
        """测试沉浸感检测：情感钝化（告知而非展示）"""
        narrative = """
        他感到非常高兴，心情愉悦极了。他觉得很激动，难以抑制内心的情绪。他意识到这是一个好机会，不能错过。
        他发现自己很幸运，真是太幸运了。他注意到周围的人都很友好，对他很热情。
        他感到十分满意，非常满意。他觉得异常欣慰，心里很舒坦。他无比开心，简直开心到了极点。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        assert any("[情感钝化]" in issue for issue in result["issues"])
        assert "展示" in result["issues"][0] or any("展示" in iss for iss in result["issues"])
    
    def test_language_layer_colloquial_markers(self):
        """测试语言质量：口语化残留"""
        narrative = """
        嗯，他走进了房间，看了看四周。那个，桌子上有一本书，很厚的样子。然后，他拿起了书，翻开看了看。
        就是说，这本书很旧，都发黄了。其实吧，内容挺有趣的，讲的是古代的故事。
        啊，窗外的风景很美，阳光明媚。呢，他决定坐下来慢慢看，细细品味这本书的内容。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        assert any("[口语化残留]" in issue for issue in result["issues"])
    
    def test_language_layer_cliche_phrases(self):
        """测试语言质量：陈词滥调"""
        narrative = """
        说时迟那时快，他冲了过去，身影如同闪电一般。电光火石之间，剑已出鞘，寒光闪烁。
        千钧一发之际，他做出了决定，绝不能犹豫。不知为何，心中五味杂陈，难以名状。
        这场战斗真是惊心动魄，让人热血沸腾。敌人的武器美轮美奂，精美绝伦，令人叹为观止。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        assert any("[陈词滥调]" in issue for issue in result["issues"])
    
    def test_tension_layer_conflict_missing(self):
        """测试叙事张力：冲突缺失"""
        narrative = """
        房间里很安静，静得能听见呼吸声。阳光透过窗户照进来，洒在木质地板上，形成一片片光斑。
        他坐在椅子上，姿势很放松。桌子上有一本书，封面是深蓝色的，看起来有些年头了。
        时间慢慢流逝，分针一格一格地移动。一切都很平静，没有任何波澜。
        外面的树叶在风中摇曳，发出沙沙的声音。天空是蔚蓝色的，飘着几朵白云，景色宜人。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        assert any("[冲突缺失]" in issue for issue in result["issues"])
    
    def test_tension_layer_sensory_details_missing(self):
        """测试叙事张力：描写空洞"""
        narrative = """
        他进入了房间，迈步走了进去。房间里有一些东西，摆放在各处，看起来有点乱。
        他看了看周围，目光扫视着四周。然后他坐了下来，在椅子上坐下了。
        他拿起一个物品，用手拿起来了。这个物品很重要，对他来说意义重大。
        他思考了一会儿，在脑海中想了想。最后他做出了决定，确定了自己的选择。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        assert any("[描写空洞]" in issue for issue in result["issues"])
        assert "感官" in result["issues"][0] or any("感官" in iss for iss in result["issues"])
    
    def test_readability_layer_fog_index(self):
        """测试可读性：雾凇指数（句子过长过复杂）"""
        narrative = """当他终于意识到这个隐藏在表面现象之下的深层次的、复杂的、难以言说的、但又确实存在的、不可否认的、需要通过仔细思考和深入分析才能理解的真相之后，他的内心产生了一种难以名状的、复杂而矛盾的、既包含惊讶又包含恐惧的、同时还夹杂着一丝释然和解脱的情感，这种情感让他感到困惑不已，不知道该如何处理这一切。他站在原地，思绪万千，陷入了深深的沉思之中。这一切都显得那么复杂而又充满了不确定性。"""
        
        result = grade_narrative_flow(narrative)
        # 这种超长复杂句应该被检测出来并降低可读性分数
        assert result["layer_scores"]["readability"] < 95, f"复杂长句应降低可读性分数，实际：{result['layer_scores']['readability']}"
        assert result["metrics"]["fog_index"] > 30
        assert any("[可读性差]" in issue for issue in result["issues"]), "应检测到可读性问题"
    
    def test_readability_layer_emotion_resonance(self):
        """测试可读性：情感共鸣度"""
        narrative = """
        他观察了环境，仔细查看了周围的情况。然后他分析了情况，对局势进行了评估和判断。
        接着他制定了计划，安排好了每一个步骤。最后他执行了行动，按照计划进行操作。
        整个过程很顺利，没有出现任何问题。结果符合预期，达到了预定的目标。
        任务完成了，所有工作都已结束。他离开了现场，前往下一个地点继续工作。
        """.strip()
        
        result = grade_narrative_flow(narrative)
        assert any("[情感共鸣弱]" in issue for issue in result["issues"])
    
    def test_high_quality_narrative_scores_95_plus(self):
        """测试高质量叙述应达到90+分"""
        narrative = """寒风刺骨，他裹紧了单薄的外套。街道空无一人，只有路灯投下孤独的影子。脚步声在身后响起——沉重、缓慢，却不可阻挡。他的心跳加速，手心渗出冷汗。不能回头，绝对不能。前方的巷口透出微弱的光。那是唯一的希望。他咬紧牙关，双腿机械地奔跑，肺部灼烧般疼痛。"就快到了。"他在心中默念，指尖已经触到转角的墙壁。"""
        
        result = grade_narrative_flow(narrative)
        # 高质量叙述：长短句交错、感官细节丰富、有张力、有情感
        assert result["score"] >= 85, f"高质量叙述分数应≥85，实际：{result['score']}"
        assert len(result["issues"]) <= 3, f"高质量叙述问题应≤3个，实际：{len(result['issues'])}"
    
    def test_layer_scores_breakdown(self):
        """测试分层评分机制"""
        narrative = """他走进了房间。他看到了桌子。他拿起了书。他开始阅读。他翻动着书页。他专注地看着。他思考着内容。他理解了含义。他感受到氛围。他注意到细节。突然，门开了。突然，有人进来了。突然，灯亮了。突然，声音响起。突然，气氛变了。正如我们所见，这是一个转折点。读者可以看到，局势发生了变化。嗯，他感到很紧张。那个，他不知道该怎么办。然后，他站起来了。"""
        
        result = grade_narrative_flow(narrative)
        
        # 验证所有6层都有评分
        assert "surface" in result["layer_scores"]
        assert "structure" in result["layer_scores"]
        assert "immersion" in result["layer_scores"]
        assert "language" in result["layer_scores"]
        assert "tension" in result["layer_scores"]
        assert "readability" in result["layer_scores"]
        
        # 验证评分范围合理
        for layer, score in result["layer_scores"].items():
            assert 0 <= score <= 100, f"{layer} 分数应在 0-100 范围内"
        
        # 这个低质量叙述应该在表层有句式重复问题
        assert result["layer_scores"]["surface"] < 90  # 句式重复
