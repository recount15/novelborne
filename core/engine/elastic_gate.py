# -*- coding: utf-8 -*-
"""弹性门限：门禁两稿不过时代码层的确定性修复。

设计（2026-08-30）：正文质量类失败（体量/交互/锚点）重写一稿仍不达标时，
不再回滚整回合，而是代码层修复两处**形式问题**后放行——
1. 剥离模型残留的非叙事块（``` 围栏、JSON/系统括号段）：思考型模型
   偶尔把分析过程包进代码块输出，占用体量且污染展示。
2. 修复选项数量：解析出的选项不足 6 个时，确定性合成补足（从正文
   行动句提取候选；不足再从模板生成），保证前端固定 6+1 的交互契约。

纯函数、零模型调用：这是「两次不能如意则只通过代码处理」的落地层。
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

# 剥离策略只针对明确的非叙事块；正文中的行内反引号不受影响。
_FENCE_RE = re.compile(r"```[a-zA-Z]*\n.*?```", re.S)
_JSON_BLOCK_RE = re.compile(r"\{[^{}\n]{80,}\}", re.S)
# 系统自检段：以【】/[] 包裹且含 系统/校验/验证/检查 字样的说明行
# （模型实际输出形如「【系统校验】…」，关键词与括号组合多样，逐词穷举
# 括号位置会漏；只要行首是括号段且含关键词即可判非叙事）。
_SYSTEM_BRACKET_RE = re.compile(
    r"^\s*[【\[][^\n】\]]{0,12}(?:系统|校验|验证|检查)[^\n】\]]{0,12}[】\]]\s*[:：]?.*$", re.M)

_OPTION_LINE_RE = re.compile(r"^\s*([A-Fa-fａ-ｆ])[.、:：\)]\s*(.+)$", re.M)
_ACTION_HINT_RE = re.compile(
    r"(?:可以|可以尝试|不妨|不妨试|试着|考虑|或者|也可|亦可|能够)[^。！？\n]{6,60}")

_FILLER_ACTIONS = (
    "就近观察四周，留意任何异常的动静",
    "整理随身物品，确认手中可用的资源",
    "找一旁的人搭话，探听更多的消息",
    "原地休整片刻，恢复体力再行动",
    "离开当前位置，去别处查看情况",
    "沿来路回撤，确认身后没有麻烦",
)


def strip_non_narrative_blocks(text: str) -> str:
    """剥掉模型残留的代码围栏与系统自检段，返回纯叙事文本。

    三类残留独立判定（围栏块/超长 JSON 单行/系统段行），命中即清理；
    全无命中时原样返回——本函数在弹性放行路径上调用，
    过度剥离比不剥更糟。
    """
    content = str(text or "")
    cleaned = _FENCE_RE.sub("", content)
    cleaned = _JSON_BLOCK_RE.sub("", cleaned)
    cleaned = _SYSTEM_BRACKET_RE.sub("", cleaned)
    if cleaned == content:
        return content
    # 压掉剥离留下的连续空行（保留段落结构）；剥空则回退原文（不做全删）
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or content


def _extract_action_candidates(narrative: str, exclude: set[str]) -> list[str]:
    """从叙事正文中提取行动句作为合成选项的候选。"""
    out: list[str] = []
    for m in _ACTION_HINT_RE.finditer(narrative):
        text = m.group(0).strip().rstrip("。，、；")
        if 6 <= len(text) <= 60 and text not in exclude and not _OPTION_LINE_RE.match(text):
            out.append(text)
        if len(out) >= len(_FILLER_ACTIONS):
            break
    return out


def repair_options(narrative: str, parsed: Sequence[Mapping[str, Any]],
                   need: int = 6) -> tuple[list[dict[str, str]], bool]:
    """把解析出的选项补足到 need 个。

    返回 (选项列表, 是否发生合成)。已有选项原样保留（哪怕编号不连续），
    新选项续用下一个空闲字母；候选优先取正文行动句，不足用中性模板补齐，
    文案不预设剧情事实，避免代码越权写故事。
    """
    options = [{"key": str(item.get("key")), "text": str(item.get("text") or "").strip()}
               for item in parsed if str(item.get("key") or "").strip()]
    options = [o for o in options if o["text"]]
    if len(options) >= need:
        return options, False
    used = {o["key"] for o in options} | {t for t in (o["text"] for o in options)}
    free_keys = [chr(ord("A") + i) for i in range(6) if chr(ord("A") + i) not in used]
    candidates = _extract_action_candidates(narrative, used)
    synthesized = False
    pool = candidates + list(_FILLER_ACTIONS)
    for text in pool:
        if len(options) >= need or not free_keys:
            break
        if text in used:
            continue
        options.append({"key": free_keys.pop(0), "text": text})
        used.add(text)
        synthesized = True
    return options, synthesized


def elastic_repair(text: str, parsed: Sequence[Mapping[str, Any]],
                   need: int = 6) -> dict[str, Any]:
    """弹性门限的入口：两稿不过后的一次性代码修复。

    返回 {narrative, options, repaired_fences, repaired_options}。
    调用方以修复后的正文放行回合，并把两个 repaired 标记写入
    state["elastic_repair"] 供前端/存档透明展示。
    """
    raw = str(text or "")
    narrative = strip_non_narrative_blocks(raw)
    repaired_fences = narrative != raw
    options, synthesized = repair_options(narrative, parsed, need)
    return {
        "narrative": narrative,
        "options": options,
        "repaired_fences": repaired_fences,
        "repaired_options": synthesized,
    }
