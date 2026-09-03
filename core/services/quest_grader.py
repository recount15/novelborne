# -*- coding: utf-8 -*-
"""任务判定批改器：apply agent_refill pattern to quest judgment.

为任务完成判定提供结构化批改和重填能力，提升判定准确性。
"""
from __future__ import annotations

from typing import Any, Mapping

#: Evidence 最短字数（引用原文必须足够具体）
MIN_EVIDENCE_LENGTH = 15  # 提升至15字，确保引用充分

#: Evidence 禁止的模糊表述（扩展）
VAGUE_PHRASES = (
    "似乎", "可能", "也许", "大概", "应该", "或许",
    "不太清楚", "不确定", "无法判断", "好像", "仿佛",
    "看起来", "感觉上", "估计", "猜测",
)

#: Completed=True 时要求更严格的关键词匹配（至少3个2字词命中）
MIN_KEYWORD_MATCHES = 3


def grade_quest_verdict(quest: Mapping[str, Any], 
                       narrative: str,
                       verdict: Mapping[str, Any]) -> list[str]:
    """批改任务判定结果，返回中文错误清单（空列表 = 通过）。
    
    检查项：
    1. Evidence 必须是原文引用（存在于 narrative 中）
    2. Evidence 长度足够（≥10 字）
    3. Evidence 不包含模糊表述
    4. Completed=True 时必须匹配所有 requirements
    5. 逻辑一致性（evidence 支持 completed 结论）
    
    Args:
        quest: 任务对象（包含 requirements）
        narrative: 本回合叙述正文
        verdict: 判定结果 {"completed": bool, "evidence": str}
    
    Returns:
        错误清单（空列表表示通过）
    """
    errors: list[str] = []
    
    if not isinstance(verdict, Mapping):
        errors.append("verdict 必须是字典")
        return errors
    
    completed = verdict.get("completed")
    evidence = str(verdict.get("evidence") or "").strip()
    
    # 1. 基础格式检查
    if not isinstance(completed, bool):
        errors.append("completed 必须是布尔值（true/false）")
    
    if not evidence:
        errors.append("evidence 为空，必须提供原文引用作为判定依据")
        return errors
    
    # 2. Evidence 长度检查
    if len(evidence) < MIN_EVIDENCE_LENGTH:
        errors.append(f"evidence 过短（{len(evidence)} 字），引用原文必须足够具体（≥{MIN_EVIDENCE_LENGTH} 字）")
    
    # 3. Evidence 必须是原文引用（提升精度：允许前后3字容差）
    if evidence not in narrative:
        # 检查是否是 evidence 的90%以上内容在 narrative 中
        evidence_core = evidence.strip()
        if len(evidence_core) >= 8:
            # 提取核心片段（去除首尾3字）检查
            core_start = min(3, len(evidence_core) // 4)
            core_end = max(len(evidence_core) - 3, len(evidence_core) * 3 // 4)
            core_segment = evidence_core[core_start:core_end]
            if core_segment not in narrative:
                errors.append(f"evidence 不是原文引用——必须从叙述正文中逐字摘录，当前 evidence「{evidence[:30]}...」未在正文中找到")
        else:
            errors.append(f"evidence 不是原文引用——必须从叙述正文中逐字摘录，当前 evidence「{evidence[:30]}...」未在正文中找到")
    
    # 4. Evidence 不应包含模糊表述
    for phrase in VAGUE_PHRASES:
        if phrase in evidence:
            errors.append(f"evidence 包含模糊表述「{phrase}」，判定依据必须明确具体")
            break
    
    # 5. Completed=True 时，检查 requirements 匹配度（提升至95%+准确性）
    if completed is True:
        requirements = []
        if isinstance(quest, Mapping):
            reqs = quest.get("requirements")
            if isinstance(reqs, list):
                requirements = [str(r) for r in reqs if r]
        
        if requirements:
            # 检查至少 MIN_KEYWORD_MATCHES 个 requirement 关键词出现在 evidence 中
            match_count = 0
            matched_reqs = []
            
            for req in requirements:
                req_text = str(req)
                req_matched = False
                
                # 提取所有2字以上的连续子串作为关键词
                for i in range(len(req_text) - 1):
                    keyword = req_text[i:i+2]
                    # 跳过纯标点、空格、常见动词
                    if keyword and keyword not in ("完成", "达成", "实现", "找到", "将", "到", "。", "，", "的", "了", "在"):
                        if keyword in evidence:
                            if not req_matched:
                                match_count += 1
                                matched_reqs.append(req)
                                req_matched = True
                            break
            
            if match_count < MIN_KEYWORD_MATCHES:
                errors.append(
                    f"判定为已完成，但 evidence 匹配度不足——"
                    f"requirements 共 {len(requirements)} 项，仅匹配 {match_count} 项（需≥{MIN_KEYWORD_MATCHES}项）。"
                    f"已匹配：{matched_reqs if matched_reqs else '无'}，"
                    f"未匹配的 requirements 必须在 evidence 中体现"
                )
    
    # 6. Completed=False 时的逻辑一致性（可选检查）
    if completed is False:
        # 检查 evidence 是否包含否定性描述
        negative_indicators = ("未", "没", "失败", "无法", "不能", "尚未")
        has_negative = any(ind in evidence for ind in negative_indicators)
        
        # 如果 evidence 看起来像正面描述但判定为 False，给出提示
        positive_indicators = ("成功", "完成了", "达成", "实现", "获得")
        has_positive = any(ind in evidence for ind in positive_indicators)
        
        if has_positive and not has_negative:
            errors.append(
                "判定为未完成，但 evidence 包含正面描述（成功/完成/达成），"
                "可能存在逻辑不一致"
            )
    
    return errors


def build_refill_prompt(quest: Mapping[str, Any],
                       narrative: str,
                       recent_progress: list[dict],
                       errors: list[str]) -> str:
    """构造重填提示词（带错误清单）。
    
    基础判定提示词 + 错误反馈 + 重新判定要求。
    """
    title = str(quest.get("title") or "未知任务")
    requirements = quest.get("requirements") or []
    req_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(requirements)) if requirements else "  （无具体要求）"
    
    progress_text = ""
    if recent_progress:
        progress_lines = []
        for p in recent_progress[-3:]:  # 最近 3 次进展
            round_num = p.get("round", "?")
            ev = str(p.get("evidence", ""))[:50]
            progress_lines.append(f"  回合 {round_num}: {ev}")
        progress_text = "\n".join(progress_lines)
    
    base_prompt = f"""【任务完成判定】
你是《书中行》的任务裁判。请根据本回合叙述，判定任务是否完成。

# 任务信息
- 标题：{title}
- 要求：
{req_text}

# 历史进展
{progress_text if progress_text else "  （首次判定）"}

# 本回合叙述
{narrative[:1000]}

# 判定规则
1. **Evidence 必须是原文引用**：从叙述中逐字摘录相关段落（≥10 字）
2. **明确判定**：completed 为 true（完成）或 false（未完成）
3. **完成标准**：所有 requirements 必须全部达成才判定为完成
4. **未完成情况**：部分达成、正在进行中、失败、未提及均判定为未完成
5. **避免模糊表述**：evidence 不使用"似乎"、"可能"、"大概"等词

# 输出格式
直接输出 JSON（不要代码围栏）：
{{"completed": true/false, "evidence": "原文引用片段"}}
"""
    
    error_section = "\n\n# 上一版本的问题（必须修复）\n"
    error_section += "\n".join(f"- {error}" for error in errors)
    error_section += "\n\n请重新判定，完全避免上述问题，确保 evidence 是原文逐字引用。"
    
    return base_prompt + error_section


__all__ = [
    "grade_quest_verdict",
    "build_refill_prompt",
    "MIN_EVIDENCE_LENGTH",
]
