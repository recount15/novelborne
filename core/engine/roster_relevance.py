# -*- coding: utf-8 -*-
"""选角剧情相关度（roster relevance）与强相关缩放。

需求来源：四栏选角（伴侣/伙伴/宿敌）与当前书的剧情相关度必须「产生效果」，
且选了太多强剧情相关角色时自动缩放，避免开局阵容把主线焦点稀释。

设计（代码级保障，纯本地确定性计算，不依赖模型）：

1. 相关度评分 assess_relevance(member, book_context) ∈ [0, 1]：
   - 能力/背景/知识域关键词与锚点事件文本、剧情大概的重合度（权重 0.5）；
   - 成员类型（slot_keys/原型）与书题材的匹配度（权重 0.3）；
   - 与作品名/主要角色名的直接关联（权重 0.2，同书角色天然强相关）。
2. 三档：强相关（≥0.6）/ 中相关（0.3–0.6）/ 弱相关（<0.3）。
3. scale_roster(members, book_context, strong_cap=2)：
   伙伴+伴侣+宿敌的强相关总数超上限时，按分值保留前 N 个，
   其余降为中相关并打 scaled 标记（开局核对透明提示）。
4. 效果出口（由 app 层接线）：
   - 出场权重：强相关成员更容易进入本回合 active_members；
   - 任务生成：强相关成员在任务上下文中显式标注，模型围绕其设计锚点任务；
   - 涟漪加成：点名强相关成员的大动作 pressure +1（相关角色推动改变更省力）。
"""
from __future__ import annotations

import re
from typing import Any, Mapping

STRONG_CAP_DEFAULT = 2          # 伙伴+伴侣+宿敌合计的强相关上限
STRONG_TIER = 0.55              # ≥ 此分为强相关
MEDIUM_TIER = 0.3               # ≥ 此分为中相关

# 停用词：不参与重合统计的泛化词。
_STOPWORDS = {
    "并且", "一个", "具有", "进行", "通过", "可以", "能够", "以及", "非常", "十分",
    "所有", "各种", "之后", "以后", "然后", "但是", "因为", "所以", "如果", "此时",
    "他们", "她们", "自己", "什么", "怎么", "如何", "就是", "还有", "没有", "已经",
    "the", "and", "for", "with", "this", "that", "from", "into",
}

_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5]{2,}|[A-Za-z]{3,}")


def _tokens(text: Any) -> set[str]:
    """中文按 2-gram、英文按词切分，滤停用词。"""
    raw = str(text or "")
    tokens: set[str] = set()
    for word in _TOKEN_RE.findall(raw):
        word = word.lower()
        if word in _STOPWORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5]+", word):
            tokens.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.add(word)
    return tokens


def _member_text(member: Mapping[str, Any]) -> str:
    parts = [member.get("name") or "", member.get("skill") or "",
             member.get("background") or "", member.get("archetype") or "",
             member.get("desire") or "", member.get("fear") or ""]
    for extra in (member.get("knowledge_scope") or []):
        parts.append(str(extra))
    for slot, keys in (member.get("slot_keys") or {}).items():
        parts.append(str(slot))
        parts.extend(str(k) for k in keys or [])
    return " ".join(str(p) for p in parts)


def _book_text(book_context: Mapping[str, Any]) -> str:
    parts = [book_context.get("work") or "", book_context.get("title") or "",
             book_context.get("genre") or "", book_context.get("premise") or ""]
    for event in (book_context.get("anchor_events") or []):
        if isinstance(event, Mapping):
            parts.append(str(event.get("title") or ""))
            parts.append(str(event.get("summary") or ""))
    for thread in (book_context.get("major_threads") or []):
        parts.append(str(thread))
    return " ".join(str(p) for p in parts)


def _hit_density(member_tokens: set[str], target_tokens: set[str], full: int) -> float:
    """命中密度：重合 token 数 / full（达到 full 个命中即满分 1.0）。"""
    if not member_tokens or not target_tokens:
        return 0.0
    return min(1.0, len(member_tokens & target_tokens) / max(1, full))


def assess_relevance(member: Mapping[str, Any],
                     book_context: Mapping[str, Any]) -> dict[str, Any]:
    """评估单个成员与当前书的剧情相关度，返回 {score, tier, basis}。"""
    member_tokens = _tokens(_member_text(member))
    book_tokens = _tokens(_book_text(book_context))
    work_tokens = _tokens(book_context.get("work") or "")

    # 1) 锚点命中（权重 0.4）：命中 2 个锚点 token 即满分——剧情强关联主信号
    anchor_tokens = set()
    for event in (book_context.get("anchor_events") or []):
        if isinstance(event, Mapping):
            anchor_tokens |= _tokens(str(event.get("title") or "") + str(event.get("summary") or ""))
    anchor_hit = _hit_density(member_tokens, anchor_tokens, 2)

    # 2) 剧情大概命中（权重 0.2）：命中 3 个 token 即满分
    plot_tokens = _tokens(" ".join(str(p) for p in (
        book_context.get("premise"), *(book_context.get("major_threads") or []))))
    plot_hit = _hit_density(member_tokens, plot_tokens, 3)

    # 3) 全书题材命中（权重 0.1）：命中 5 个 token 即满分
    type_hit = _hit_density(member_tokens, book_tokens, 5)

    # 4) 同书直连（权重 0.3）：成员出处=当前作品 → 天然偏强相关
    member_work = str(member.get("work") or "")
    work_direct = 1.0 if (member_work and work_tokens and
                          _tokens(member_work) & work_tokens) else 0.0

    score = round(min(1.0, anchor_hit * 0.4 + plot_hit * 0.2 +
                      type_hit * 0.1 + work_direct * 0.3), 3)
    tier = "强" if score >= STRONG_TIER else ("中" if score >= MEDIUM_TIER else "弱")
    basis = {
        "anchor_hit": round(anchor_hit, 3),
        "plot_hit": round(plot_hit, 3),
        "type_hit": round(type_hit, 3),
        "same_work": bool(work_direct),
    }
    return {"score": score, "tier": tier, "basis": basis}


def relevance_of(member: Mapping[str, Any], report: Mapping[str, Any]) -> float:
    """从评估报告里取成员的最终相关度（缩放后）。"""
    entry = (report.get("members") or {}).get(str(member.get("name") or ""))
    if not isinstance(entry, Mapping):
        return 0.0
    return float(entry.get("score") or 0.0)


def is_strongly_relevant(member: Mapping[str, Any], report: Mapping[str, Any]) -> bool:
    entry = (report.get("members") or {}).get(str(member.get("name") or ""))
    return bool(isinstance(entry, Mapping) and entry.get("tier") == "强" and not entry.get("scaled"))


def scale_roster(members: list[Mapping[str, Any]], book_context: Mapping[str, Any],
                 strong_cap: int = STRONG_CAP_DEFAULT) -> dict[str, Any]:
    """评估全体成员相关度并执行强相关缩放。

    返回报告 {members: {name: {score, tier, scaled}}, strong_count,
    strong_cap, scaled_names}——伙伴+伴侣+宿敌合计强相关超过 cap 时，
    按分值排序保留前 cap 个，其余 tier 降为中并打 scaled 标记。
    """
    assessed: dict[str, dict[str, Any]] = {}
    for member in members or []:
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        result = assess_relevance(member, book_context)
        result["scaled"] = False
        assessed[name] = result

    strong = [name for name, info in assessed.items() if info["tier"] == "强"]
    strong.sort(key=lambda n: assessed[n]["score"], reverse=True)
    scaled_names: list[str] = []
    for name in strong[max(0, int(strong_cap)):]:
        assessed[name]["scaled"] = True
        assessed[name]["tier"] = "中"
        scaled_names.append(name)

    return {
        "members": assessed,
        "strong_count": len(strong) - len(scaled_names),
        "strong_cap": int(strong_cap),
        "scaled_names": scaled_names,
    }


def relevance_hint(report: Mapping[str, Any]) -> str:
    """生成注入开局提示/玩法速览的相关度摘要（纯中文）。"""
    if not report or not report.get("members"):
        return ""
    parts: list[str] = []
    for name, info in (report.get("members") or {}).items():
        tag = "%s（%s相关%s）" % (name, info.get("tier"), "·已缩放" if info.get("scaled") else "")
        parts.append(tag)
    head = "选角剧情相关度：" + "、".join(parts)
    scaled = report.get("scaled_names") or []
    if scaled:
        head += ("。强相关角色过多（上限 %d），%s 已自动缩放为中相关——"
                 "他们的剧情参与将保持适度，避免稀释主线焦点"
                 % (report.get("strong_cap"), "、".join(scaled)))
    return head


__all__ = [
    "STRONG_CAP_DEFAULT", "STRONG_TIER", "MEDIUM_TIER",
    "assess_relevance", "relevance_of", "is_strongly_relevant",
    "scale_roster", "relevance_hint",
]
