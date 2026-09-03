# -*- coding: utf-8 -*-
"""Internal helper function"""
from __future__ import annotations

import re
from typing import Any


#: 高频词过度使用阈值同一个词在100字内出现次数
HIGH_FREQ_THRESHOLD = 3  # 降至3次,更严格

#: 句式重复检测窗口连续句数
PATTERN_REPEAT_WINDOW = 3

#: 节奏单调判定:句长标准差下限字
SENTENCE_LENGTH_STD_MIN = 10  # 提升至10字

#: 信息密度下限实体词密度
INFO_DENSITY_MIN = 0.35  # 35%以上应为实体词名词,动词,形容词

#: 陈词滥调词典
CLICHE_PHRASES = [
    "不知为何", "说时迟那时快", "电光火石之间", "千钧一发",
    "心中五味杂陈", "百感交集", "心如刀绞", "肝肠寸断",
    "美轮美奂", "栩栩如生", "惟妙惟肖", "活灵活现",
    "不可名状", "难以言喻", "无法形容", "说不出",
]

#: 口语化残留标记
COLLOQUIAL_MARKERS = [
    "嗯", "啊", "呢", "哦", "哎", "唉", "喂",
    "那个", "这个", "然后", "就是说", "其实吧",
]


def grade_narrative_flow(narrative: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate narrative flow with 6-layer detection, return score and issue list."""
    issues: list[str] = []
    metrics = {}
    layer_scores = {}
    
    text = str(narrative or "").strip()
    context = context or {}
    
    if len(text) < 100:
        return {
            "score": 0,
            "issues": ["正文过短<100字,无法评估流畅度"],
            "metrics": {"text_length": len(text)},
            "layer_scores": {}
        }
    
    # 分句
    sentences = _split_sentences(text)
    sentence_lengths = [len(s) for s in sentences if len(s) >= 5]
    
    if len(sentences) < 3:
        return {
            "score": 50,
            "issues": ["句子数量过少<3句,流畅度评估不可靠"],
            "metrics": {"sentence_count": len(sentences), "text_length": len(text)},
            "layer_scores": {}
        }
    
    metrics["sentence_count"] = len(sentences)
    metrics["avg_sentence_length"] = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0
    
    # ========== 第1层:表层检测句式,用词,节奏 ==========
    surface_issues, surface_metrics = _check_surface_layer(text, sentences, sentence_lengths)
    issues.extend(surface_issues)
    metrics.update(surface_metrics)
    layer_scores["surface"] = max(0, 100 - len(surface_issues) * 10)
    
    # ========== 第2层:结构检测衔接,时态,逻辑 ==========
    structure_issues, structure_metrics = _check_structure_layer(text, sentences, context)
    issues.extend(structure_issues)
    metrics.update(structure_metrics)
    layer_scores["structure"] = max(0, 100 - len(structure_issues) * 15)
    
    # ========== 第3层:沉浸感检测元叙述,视角,情感 ==========
    immersion_issues, immersion_metrics = _check_immersion_layer(text, sentences, context)
    issues.extend(immersion_issues)
    metrics.update(immersion_metrics)
    layer_scores["immersion"] = max(0, 100 - len(immersion_issues) * 12)
    
    # ========== 第4层:语言质量口语化,陈词滥调,信息密度 ==========
    language_issues, language_metrics = _check_language_layer(text, sentences)
    issues.extend(language_issues)
    metrics.update(language_metrics)
    layer_scores["language"] = max(0, 100 - len(language_issues) * 8)
    
    # ========== 第5层:叙事张力冲突,节奏,描写 ==========
    tension_issues, tension_metrics = _check_tension_layer(text, sentences)
    issues.extend(tension_issues)
    metrics.update(tension_metrics)
    layer_scores["tension"] = max(0, 100 - len(tension_issues) * 10)
    
    # ========== 第6层:可读性雾凇指数,情感共鸣 ==========
    readability_issues, readability_metrics = _check_readability_layer(text, sentences)
    issues.extend(readability_issues)
    metrics.update(readability_metrics)
    layer_scores["readability"] = max(0, 100 - len(readability_issues) * 8)
    
    # 计算加权综合分数各层权重不同
    weights = {
        "surface": 0.15,      # 表层15%
        "structure": 0.20,    # 结构20%
        "immersion": 0.25,    # 沉浸感25%最重要
        "language": 0.15,     # 语言质量15%
        "tension": 0.15,      # 叙事张力15%
        "readability": 0.10,  # 可读性10%
    }
    
    score = sum(layer_scores.get(layer, 0) * weight for layer, weight in weights.items())
    score = max(0, min(100, int(score)))
    
    return {
        "score": score,
        "issues": issues,
        "layer_scores": layer_scores,
        "metrics": metrics
    }


def _split_sentences(text: str) -> list[str]:
    """Split into sentences by punctuation"""
    import re
    sentences = re.split(r'[。！？\n]+', text)
    return [s.strip() for s in sentences if s.strip()]


def _check_surface_layer(text: str, sentences: list[str], sentence_lengths: list[int]) -> tuple[list[str], dict]:
    """Surface layer: sentence pattern repetition, word repetition, rhythm monotony"""
    issues = []
    metrics = {}
    
    # 1. 句长标准差节奏多样性
    if len(sentence_lengths) >= 3:
        avg_len = sum(sentence_lengths) / len(sentence_lengths)
        variance = sum((length - avg_len) ** 2 for length in sentence_lengths) / len(sentence_lengths)
        std_dev = variance ** 0.5
        metrics["sentence_length_std"] = std_dev
        
        if std_dev < SENTENCE_LENGTH_STD_MIN:
            issues.append(f"[节奏单调] 句长变化过小标准差 {std_dev:.1f},应≥{SENTENCE_LENGTH_STD_MIN},读者易感疲劳。建议长短句交错,短句制造紧张,长句营造氛围")
    
    # 2. 用词重复
    repetition_issues = _check_word_repetition(text)
    issues.extend(repetition_issues)
    metrics["repetition_rate"] = len(repetition_issues) / max(1, len(text) // 100)
    
    # 3. 句式重复
    pattern_repeats = _check_pattern_repetition(sentences)
    issues.extend(pattern_repeats)
    metrics["pattern_repeat_count"] = len(pattern_repeats)
    
    return issues, metrics


def _check_word_repetition(text: str) -> list[str]:
    """Detect excessive high-frequency word usage"""
    issues = []
    
    stop_words = {
        "的", "了", "在", "是", "我", "你", "他", "她", "它", "们",
        "这", "那", "有", "和", "与", "及", "或", "但", "而", "却",
        "就", "都", "要", "会", "能", "可", "着", "过", "把", "被",
        "给", "向", "从", "到", "对", "为", "以", "将", "得", "地"
    }
    
    words: dict[str, list[int]] = {}  # 记录每个词出现的位置
    for i in range(len(text) - 1):
        word = text[i:i+2]
        if word not in stop_words and word.strip() and not re.match(r'[,。！？,；:""''\s]+', word):
            if word not in words:
                words[word] = []
            words[word].append(i)
    
    # 检查100字窗口内的高频词
    for word, positions in words.items():
        if len(positions) < HIGH_FREQ_THRESHOLD:
            continue
        
        # 检查是否在100字窗口内密集出现
        for i in range(len(positions) - HIGH_FREQ_THRESHOLD + 1):
            window_positions = positions[i:i + HIGH_FREQ_THRESHOLD]
            if window_positions[-1] - window_positions[0] <= 100:
                issues.append(f"[用词重复] 「{word}」在100字内出现{HIGH_FREQ_THRESHOLD}次,读者会感到啰嗦。建议用同义词替换,或调整句子结构避免重复")
                break
    
    return issues


def _check_pattern_repetition(sentences: list[str]) -> list[str]:
    """Detect sentence pattern repetition (3+ consecutive same structure)"""
    issues = []
    
    if len(sentences) < PATTERN_REPEAT_WINDOW:
        return issues
    
    # 提取句式特征:开头2字 + 结构词的/了/着/过位置
    def get_pattern(sentence: str) -> str:
        prefix = sentence[:2] if len(sentence) >= 2 else sentence
        has_de = "的" in sentence[:len(sentence)//2]
        has_le = "了" in sentence
        return f"{prefix}|{has_de}|{has_le}"
    
    patterns = [get_pattern(s) for s in sentences]
    
    for i in range(len(patterns) - PATTERN_REPEAT_WINDOW + 1):
        window = patterns[i:i + PATTERN_REPEAT_WINDOW]
        if len(set(window)) == 1:
            issues.append(f"[句式重复] 连续{PATTERN_REPEAT_WINDOW}句使用相同句式「{sentences[i][:10]}...」,读者会感到刻板。建议变换句式:主谓宾,倒装,被动句交替使用")
            break
    
    return issues


# ========== 第2层:结构检测 ==========

def _check_structure_layer(text: str, sentences: list[str], context: dict) -> tuple[list[str], dict]:
    """Structure layer: paragraph transitions, tense inconsistency, logic gaps"""
    issues = []
    metrics = {}
    
    # 1. 段落衔接突兀
    abrupt_count = _check_abrupt_transitions(text)
    if abrupt_count:
        sentence_count = len(sentences)
        abrupt_ratio = abrupt_count / sentence_count
        metrics["abrupt_transition_ratio"] = abrupt_ratio
        if abrupt_ratio > 0.25:
            issues.append(f"[段落衔接突兀] 过多使用「突然/忽然」{abrupt_count}次/{sentence_count}句={abrupt_ratio:.0%},读者会感到跳跃生硬。建议增加铺垫:先描写环境变化,角色感知,再引出事件")
    
    # 2. 时态混乱
    tense_issue = _check_tense_consistency(text)
    if tense_issue:
        issues.append(tense_issue)
    
    # 3. 逻辑断层
    logic_issues = _check_logic_consistency(sentences)
    issues.extend(logic_issues)
    
    # 4. 与上文连贯性如果提供了 prev_narrative
    if context.get("prev_narrative"):
        coherence_issue = _check_inter_paragraph_coherence(context["prev_narrative"], text)
        if coherence_issue:
            issues.append(coherence_issue)
    
    return issues, metrics


def _check_abrupt_transitions(text: str) -> int:
    """Detect abrupt paragraph transitions, return count"""
    abrupt_markers = ["突然", "忽然", "猛然", "骤然", "霎时", "瞬间"]
    return sum(text.count(marker) for marker in abrupt_markers)


def _check_tense_consistency(text: str) -> str | None:
    """Detect tense inconsistency"""
    has_past = any(marker in text for marker in ["曾经", "过去", "那时", "当年", "以前"])
    has_present = any(marker in text for marker in ["正在", "现在", "此刻", "眼下", "当下"])
    
    if has_past and has_present:
        has_transition = any(word in text for word in ["回忆", "想起", "仿佛", "梦中", "闪回", "恍惚"])
        if not has_transition:
            return "[时态混乱] 正文混用过去时与现在时,缺少明确转换标记。读者会困惑「这是回忆还是当前发生」。建议明确时间线,或用「想起/恍惚间」等词引导回忆"
    return None


def _check_logic_consistency(sentences: list[str]) -> list[str]:
    """Detect logic gaps"""
    issues = []
    
    # 1. 转折与因果混用
    for sentence in sentences:
        has_contrast = any(word in sentence for word in ["但是", "然而", "却", "可是"])
        has_causal = any(word in sentence for word in ["因此", "所以", "于是", "故而"])
        if has_contrast and has_causal:
            issues.append(f"[逻辑断层] 句子「{sentence[:30]}...」同时包含转折与因果,读者会感到逻辑混乱。建议分成两句:先转折但是,再因果因此")
            break
    
    # 2. 结构词不配对
    full_text = "".join(sentences)
    has_first = any(marker in full_text for marker in ["首先", "第一", "一来"])
    has_second = any(marker in full_text for marker in ["其次", "第二", "二来", "再者"])
    has_last = any(marker in full_text for marker in ["最后", "第三", "终于", "三来"])
    
    if has_first and not (has_second or has_last):
        issues.append("[逻辑断层] 使用「首先」但缺少对应的「其次/最后」,读者会期待完整列举却未完成。建议补充或改用「先...然后」")
    
    return issues


def _check_inter_paragraph_coherence(prev_text: str, current_text: str) -> str | None:
    """Detect paragraph coherence"""
    # 检查话题突变:上文最后一句的主题词是否在本段开头复现
    prev_sentences = _split_sentences(prev_text)
    current_sentences = _split_sentences(current_text)
    
    if not prev_sentences or not current_sentences:
        return None
    
    prev_last = prev_sentences[-1]
    current_first = current_sentences[0]
    
    # 提取主题词简化:提取名词性2字词
    def extract_topics(sentence: str) -> set[str]:
        topics = set()
        for i in range(len(sentence) - 1):
            word = sentence[i:i+2]
            if word and not re.match(r'[,。！？,；:""''\s]+', word):
                topics.add(word)
        return topics
    
    prev_topics = extract_topics(prev_last)
    current_topics = extract_topics(current_first)
    
    # 如果完全没有主题词重叠,可能话题跳跃
    overlap = prev_topics & current_topics
    if not overlap and len(prev_topics) >= 3 and len(current_topics) >= 3:
        return f"[段落衔接] 上段末尾「{prev_last[:20]}...」与本段开头「{current_first[:20]}...」话题断裂,读者会感到突兀。建议用承上启下的过渡句,或在开头呼应上文关键词"
    
    return None


# ========== 第3层:沉浸感检测 ==========

def _check_immersion_layer(text: str, sentences: list[str], context: dict) -> tuple[list[str], dict]:
    """Immersion layer: meta-narrative leaks, POV confusion, emotion telling"""
    issues = []
    metrics = {}
    
    # 1. 元叙述泄露打破第四堵墙
    meta_phrases = [
        "如前所述", "正如我们所见", "读者可以看到", "众所周知",
        "不言而喻", "显而易见", "毋庸置疑", "不言自明",
        "让我们", "我们可以", "可以说", "应该说",
    ]
    meta_count = sum(1 for phrase in meta_phrases if phrase in text)
    if meta_count > 0:
        issues.append(f"[元叙述泄露] 使用「如前所述/读者可以看到」等元叙述({meta_count}处),打破角色视角,读者被提醒「这是虚构的」。建议完全删除,保持角色视角沉浸")
        metrics["meta_narrative_count"] = meta_count
    
    # 2. 视角混乱POV跳跃
    if context.get("character_pov"):
        pov_issue = _check_pov_consistency(text, context["character_pov"])
        if pov_issue:
            issues.append(pov_issue)
    
    # 3. 情感钝化过度告知而非展示
    emotion_telling = _check_emotion_telling(text)
    if emotion_telling:
        issues.append(emotion_telling)
    
    return issues, metrics


def _check_pov_consistency(text: str, pov_character: str) -> str | None:
    """Detect POV confusion"""
    # 检测是否出现主视角角色不可能知道的信息
    # 简化:检测「他心中想」「她暗自决定」等他人内心描写
    others_inner = [
        "心中想", "暗自决定", "心里盘算", "内心", "心想",
        "想到", "觉得", "感到", "意识到"
    ]
    
    # 如果是第三人称全知视角,允许
    # 如果是第一人称或限制视角,检查是否描写了他人内心
    for phrase in others_inner:
        if phrase in text:
            # 检查主语是否是POV角色
            pos = text.find(phrase)
            if pos > 0:
                # 简化:检查前10字是否有其他角色名非POV角色
                prefix = text[max(0, pos-10):pos]
                if pov_character not in prefix and any(name in prefix for name in ["他", "她", "那人", "对方"]):
                    return f"[视角混乱] 描写了非视角角色的内心「{prefix}{phrase}...」,读者会困惑「主角怎么知道别人在想什么」。建议改用外部观察:表情,动作,语气"
    
    return None


def _check_emotion_telling(text: str) -> str | None:
    """Detect emotion telling (telling not showing)"""
    # "Show, Don't Tell" 检测
    telling_phrases = [
        "感到", "觉得", "意识到", "发现", "注意到",
        "很高兴", "很悲伤", "很愤怒", "很恐惧", "很惊讶",
        "非常", "十分", "极其", "无比", "异常",
    ]
    
    telling_count = sum(text.count(phrase) for phrase in telling_phrases)
    sentence_count = max(1, text.count("。") + text.count("！") + text.count("？"))
    telling_ratio = telling_count / sentence_count
    
    if telling_ratio > 0.4:  # 超过40%的句子在"告知"情感
        return f"[情感钝化] 过多直接告知情感「感到/觉得/很XX」({telling_count}次/{sentence_count}句={telling_ratio:.0%},读者无法共鸣。建议用具体细节展示:颤抖的手,急促的呼吸,紧咬的牙关"
    
    return None


# ========== 第4层:语言质量 ==========

def _check_language_layer(text: str, sentences: list[str]) -> tuple[list[str], dict]:
    """Language quality: colloquial markers, cliches, info density"""
    issues = []
    metrics = {}
    
    # 1. 口语化残留
    colloquial_count = sum(text.count(marker) for marker in COLLOQUIAL_MARKERS)
    if colloquial_count > len(sentences) * 0.2:
        issues.append(f"[口语化残留] 过多使用口语标记「嗯/啊/那个」({colloquial_count}次),书面叙述应更正式。读者会感到文本不够精炼。建议改用描述性语言")
    
    # 2. 陈词滥调
    cliche_found = [phrase for phrase in CLICHE_PHRASES if phrase in text]
    if cliche_found:
        issues.append(f"[陈词滥调] 使用陈词滥调「{cliche_found[0]}」等({len(cliche_found)}处),读者会感到陈旧无趣。建议用新鲜的比喻或具体描写替换")
    
    # 3. 信息密度实体词密度
    info_density = _calculate_info_density(text)
    metrics["info_density"] = info_density
    if info_density < INFO_DENSITY_MIN:
        issues.append(f"[信息密度低] 实体词密度{info_density:.1%},低于{INFO_DENSITY_MIN:.0%}。读者会感到内容空洞,废话多。建议删减虚词,增加名词,动词,形容词")
    
    return issues, metrics


def _calculate_info_density(text: str) -> float:
    """Calculate information density (content word ratio)"""
    # 简化:虚词列表
    function_words = {
        "的", "了", "在", "是", "有", "和", "与", "及", "或", "但",
        "而", "却", "就", "都", "要", "会", "能", "可", "着", "过",
        "把", "被", "给", "向", "从", "到", "对", "为", "以", "将",
        "得", "地", "吗", "呢", "吧", "啊", "呀", "嘛",
    }
    
    char_count = len(text)
    function_char_count = sum(text.count(word) for word in function_words)
    content_char_count = char_count - function_char_count
    
    return content_char_count / char_count if char_count > 0 else 0


# ========== 第5层:叙事张力 ==========

def _check_tension_layer(text: str, sentences: list[str]) -> tuple[list[str], dict]:
    """Narrative tension: missing conflict, rhythm issues, hollow descriptions"""
    issues = []
    metrics = {}
    
    # 1. 冲突缺失没有动作,对话,张力词
    has_action = any(word in text for word in ["冲", "跑", "抓", "推", "拉", "打", "砸", "撞", "扔"])
    has_dialogue = '"' in text or "「" in text or "道" in text or "说" in text
    has_tension = any(word in text for word in ["危", "险", "急", "怕", "惊", "惶", "慌", "颤"])
    
    if not (has_action or has_dialogue or has_tension):
        issues.append("[冲突缺失] 缺少动作,对话或张力词,叙述过于平淡。读者会感到无聊。建议加入角色行动,对话冲突,或营造紧张气氛")
    
    # 2. 节奏失控全是长句或全是短句
    long_sentence_ratio = sum(1 for length in [len(s) for s in sentences] if length > 30) / len(sentences)
    short_sentence_ratio = sum(1 for length in [len(s) for s in sentences] if length < 15) / len(sentences)
    
    if long_sentence_ratio > 0.8:
        issues.append(f"[节奏失控] 长句占比{long_sentence_ratio:.0%}过高,读者会感到沉闷疲惫。建议穿插短句制造节奏变化")
    elif short_sentence_ratio > 0.8:
        issues.append(f"[节奏失控] 短句占比{short_sentence_ratio:.0%}过高,读者会感到支离破碎。建议适当使用长句营造氛围")
    
    # 3. 描写空洞缺少感官细节
    sensory_words = {
        "visual": ["色", "光", "影", "暗", "亮", "红", "蓝", "绿"],
        "auditory": ["声", "音", "响", "静", "喧", "嚣", "轻", "重"],
        "tactile": ["冷", "热", "痛", "麻", "痒", "滑", "粗", "软"],
        "olfactory": ["香", "臭", "味", "气", "腥", "甜", "苦"],
    }
    
    sensory_count = sum(sum(text.count(word) for word in words) for words in sensory_words.values())
    sensory_density = sensory_count / len(sentences)
    metrics["sensory_density"] = sensory_density
    
    if sensory_density < 1.0:  # 平均每句不到1个感官词
        issues.append(f"[描写空洞] 感官细节不足平均每句{sensory_density:.1f}个感官词,读者无法身临其境。建议增加视觉,听觉,触觉细节")
    
    return issues, metrics


# ========== 第6层:可读性 ==========

def _check_readability_layer(text: str, sentences: list[str]) -> tuple[list[str], dict]:
    """Readability layer: fog index and emotional resonance"""
    issues = []
    metrics = {}
    
    # 1. 雾凇指数简化版中文可读性指数
    # 公式:0.4 * (平均句长 + 难词率 * 100)
    avg_sentence_length = sum(len(s) for s in sentences) / len(sentences)
    
    # 难词:4字以上的词简化检测
    difficult_words = re.findall(r'[\u4e00-\u9fff]{4,}', text)
    difficult_word_ratio = len(difficult_words) / max(1, len(text) // 2)  # 粗略估计总词数
    
    fog_index = 0.4 * (avg_sentence_length + difficult_word_ratio * 100)
    metrics["fog_index"] = fog_index
    
    if fog_index > 30:
        issues.append(f"[可读性差] 雾凇指数{fog_index:.1f}>30,句子过长或用词过难。普通读者需多次阅读才能理解。建议拆分长句,简化用词")
    
    # 2. 情感共鸣度情感词密度
    emotion_words = [
        "喜", "悲", "怒", "惧", "爱", "恨", "愁", "乐",
        "笑", "哭", "叹", "惊", "慌", "急", "暖", "寒",
    ]
    
    emotion_count = sum(text.count(word) for word in emotion_words)
    emotion_density = emotion_count / len(sentences)
    metrics["emotion_density"] = emotion_density
    
    if emotion_density < 0.5:
        issues.append(f"[情感共鸣弱] 情感词密度低每句{emotion_density:.1f}个,读者难以产生情感共鸣。建议增加情感描写,让读者与角色同喜同悲")
    
    return issues, metrics


def build_flow_refill_prompt(narrative: str, flow_result: dict[str, Any]) -> str:
    """Build refill prompt based on flow evaluation result with layered guidance"""
    issues = flow_result.get("issues", [])
    score = flow_result.get("score", 0)
    layer_scores = flow_result.get("layer_scores", {})
    
    if not issues:
        return ""
    
    # 按层级分类问题
    surface_issues = [iss for iss in issues if any(tag in iss for tag in ["[节奏单调]", "[用词重复]", "[句式重复]"])]
    structure_issues = [iss for iss in issues if any(tag in iss for tag in ["[段落衔接", "[时态混乱]", "[逻辑断层]"])]
    immersion_issues = [iss for iss in issues if any(tag in iss for tag in ["[元叙述", "[视角混乱]", "[情感钝化]"])]
    language_issues = [iss for iss in issues if any(tag in iss for tag in ["[口语化", "[陈词滥调]", "[信息密度"])]
    tension_issues = [iss for iss in issues if any(tag in iss for tag in ["[冲突缺失]", "[节奏失控]", "[描写空洞]"])]
    readability_issues = [iss for iss in issues if any(tag in iss for tag in ["[可读性", "[情感共鸣"])]
    
    parts = [
        f"# Narrative Flow Improvement Guide (Current Score: {score}/100)",
        "",
        "Your narrative has multi-layer issues that need improvement from surface to deep level.",
        "This is not mechanical metric fixing, but genuine reader experience enhancement.",
        "",
        "## Layered Diagnosis",
    ]
    
    if surface_issues:
        parts.append(f"\n### Surface Issues (Score: {layer_scores.get('surface', 0)}/100)")
        parts.extend(f"- {iss}" for iss in surface_issues)
        parts.append("**Fix Direction**: Vary sentence patterns, use synonyms, alternate long and short sentences.\n")
    
    if structure_issues:
        parts.append(f"\n### Structure Issues (Score: {layer_scores.get('structure', 0)}/100)")
        parts.extend(f"- {iss}" for iss in structure_issues)
        parts.append("**Fix Direction**: Add transitions, clarify timeline, separate contrast from causality.\n")
    
    if immersion_issues:
        parts.append(f"\n### Immersion Issues (Score: {layer_scores.get('immersion', 0)}/100) WARNING: Most Important")
        parts.extend(f"- {iss}" for iss in immersion_issues)
        parts.append("**Fix Direction**: Remove meta-narrative, maintain POV consistency, show emotions via details not telling.\n")
    
    if language_issues:
        parts.append(f"\n### Language Quality Issues (Score: {layer_scores.get('language', 0)}/100)")
        parts.extend(f"- {iss}" for iss in language_issues)
        parts.append("**Fix Direction**: Remove colloquial markers, replace cliches, increase content word density.\n")
    
    if tension_issues:
        parts.append(f"\n### Narrative Tension Issues (Score: {layer_scores.get('tension', 0)}/100)")
        parts.extend(f"- {iss}" for iss in tension_issues)
        parts.append("**Fix Direction**: Add action conflicts, adjust sentence rhythm, increase sensory details.\n")
    
    if readability_issues:
        parts.append(f"\n### Readability Issues (Score: {layer_scores.get('readability', 0)}/100)")
        parts.extend(f"- {iss}" for iss in readability_issues)
        parts.append("**Fix Direction**: Split complex sentences, simplify vocabulary, add emotion words.\n")
    
    parts.extend([
        "",
        "## Improvement Requirements",
        "1. **No Mechanical Fixes**: Don't just delete or swap words - rethink how to express this narrative",
        "2. **Preserve Meaning**: Improve prose but don't change plot facts or character actions",
        "3. **Reader Perspective**: Imagine reader's feelings - is it smooth, engaging, vivid?",
        "4. **Target Score >= 95**: Every layer must reach excellent level, no weak spots",
        "",
        "Please completely rewrite this narrative, solving all issues above for smooth natural reading experience.",
    ])
    
    prompt = "\n".join(parts)
    
    return prompt.strip()


__all__ = [
    "grade_narrative_flow",
    "build_flow_refill_prompt",
]

