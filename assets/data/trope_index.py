"""通用桥段资产与强化模式校验工具。

该模块只依赖标准库，不改变 FateEngine 核心流程；运行时可按需调用。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

ASSET_DIR = Path(__file__).resolve().parent
TROPE_FILES = (
    "tropes_biz.json",
    "tropes_combat.json",
    "tropes_life.json",
    "tropes_mystery.json",
    "tropes_romance.json",
)
CHOICE_STYLES = ("强硬", "隐忍", "智取", "示弱", "反将", "借势", "试探", "斡旋", "收买")
ANCHOR_FIELDS = ("chap", "title", "vol", "arc", "chars", "event", "detail", "quotes", "significance")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_DECISION_RE = re.compile(r"(决定|答应|拒绝|发现|获得|得到|击败|揭露|承诺|威胁|约定|离开|进入|返回|失去|选择)")
_EMOTION_RE = re.compile(r"(误会|愤怒|醋意|温情|决心|失望|震惊|怀疑|和解|动摇|不安)")


@lru_cache(maxsize=1)
def load_tropes() -> tuple[dict[str, Any], ...]:
    """加载全部通用桥段；解析失败直接抛错，不静默跳过。"""
    rows: list[dict[str, Any]] = []
    for filename in TROPE_FILES:
        with (ASSET_DIR / filename).open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError(f"桥段资产必须是数组: {filename}")
        rows.extend(data)
    return tuple(rows)


@lru_cache(maxsize=1)
def _style_index() -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in load_tropes():
        for style in row.get("choice_styles", []):
            index.setdefault((row.get("cat", ""), style), []).append(row)
    return {key: tuple(value) for key, value in index.items()}


def _overlap(left: str, right: str) -> int:
    left_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}|\d+", left or ""))
    right_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}|\d+", right or ""))
    return len(left_terms & right_terms)


def search_tropes(cat: str = "", sub: str = "", trigger: str = "", style: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """按锚点情境与选项风格确定性检索桥段。"""
    if style and cat:
        candidates = list(_style_index().get((cat, style), ()))
    else:
        candidates = list(load_tropes())
    if cat:
        candidates = [row for row in candidates if row.get("cat") == cat]
    if style and not cat:
        candidates = [row for row in candidates if style in row.get("choice_styles", [])]
    candidates.sort(key=lambda row: (
        3 if row.get("sub") == sub and sub else 0,
        _overlap(str(row.get("trigger", "")), trigger),
        float(row.get("K", 0)),
        str(row.get("id", "")),
    ), reverse=True)
    return candidates[: max(0, limit)]


def instantiate(template: str, context: dict[str, Any]) -> str:
    """替换通用模板占位符；未提供的变量保留，便于调用方发现缺口。"""
    return _PLACEHOLDER.sub(lambda match: str(context.get(match.group(1), match.group(0))), template)


def chapter_turn_budget(char_count: int) -> int:
    """按章节字符数返回 3–9 的本地回合预算。"""
    for threshold, budget in ((1500, 3), (3000, 4), (5000, 5), (8000, 6), (12000, 7), (18000, 8)):
        if char_count < threshold:
            return budget
    return 9


def extract_anchor_candidates(chapter_text: str) -> list[dict[str, Any]]:
    """从章节段落提取可供模型复核的事件/情感候选，不把候选直接当事实锚点。"""
    candidates: list[dict[str, Any]] = []
    for position, paragraph in enumerate(re.split(r"\n\s*\n+", chapter_text.strip())):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if _DECISION_RE.search(paragraph):
            kind = "事件"
        elif _EMOTION_RE.search(paragraph):
            kind = "情感"
        else:
            continue
        candidates.append({"type": kind, "text": paragraph, "position": position})
    return candidates


def map_anchors_to_turns(anchors: list[dict[str, Any]], turn_budget: int) -> list[list[dict[str, Any]]]:
    """将候选锚点按顺序映射到回合；事件优先，超预算时同回合合并。"""
    if turn_budget < 1:
        raise ValueError("turn_budget 必须为正整数")
    if not anchors:
        return []
    if len(anchors) <= turn_budget:
        return [[anchor] for anchor in anchors] + [[] for _ in range(turn_budget - len(anchors))]
    groups = [[] for _ in range(turn_budget)]
    priority = sorted(range(len(anchors)), key=lambda index: (anchors[index].get("type") != "事件", index))
    for order, index in enumerate(priority):
        groups[min(order, turn_budget - 1)].append(anchors[index])
    for group in groups:
        group.sort(key=lambda anchor: anchor.get("position", 0))
    return groups


def validate_anchor(anchor: dict[str, Any], source_text: str | None = None) -> list[str]:
    """校验九字段锚点；传入 source_text 时额外验证 quotes 逐字命中。"""
    errors: list[str] = []
    missing = [field for field in ANCHOR_FIELDS if field not in anchor]
    if missing:
        errors.append("缺少字段: " + ",".join(missing))
        return errors
    if not str(anchor["chap"]).strip():
        errors.append("chap 不能为空")
    if not str(anchor["title"]).strip() or not str(anchor["vol"]).strip():
        errors.append("title/vol 不能为空")
    if not 4 <= len(str(anchor["arc"])) <= 8:
        errors.append("arc 长度必须为 4-8")
    chars = anchor["chars"]
    if not chars or (isinstance(chars, (list, tuple)) and not any(str(item).strip() for item in chars)):
        errors.append("chars 不能为空")
    if not 15 <= len(str(anchor["event"])) <= 30:
        errors.append("event 长度必须为 15-30")
    if not 150 <= len(str(anchor["detail"])) <= 250:
        errors.append("detail 长度必须为 150-250")
    quotes = anchor["quotes"]
    if not isinstance(quotes, list) or len(quotes) > 2 or any(not isinstance(item, str) or not item.strip() for item in quotes):
        errors.append("quotes 必须是 0-2 条非空字符串")
    if source_text is not None and any(quote not in source_text for quote in quotes):
        errors.append("quotes 未逐字命中章节原文")
    if str(anchor["event"]).strip() in quotes:
        errors.append("quotes 不得直接复述 event")
    if not 20 <= len(str(anchor["significance"])) <= 40:
        errors.append("significance 长度必须为 20-40")
    return errors


def validate_anchors(anchors: Iterable[dict[str, Any]], source_text: str | None = None) -> dict[str, Any]:
    """返回逐条错误与总状态，适合在构建/冷启动阶段直接调用。"""
    results = [{"index": index, "errors": validate_anchor(anchor, source_text)} for index, anchor in enumerate(anchors)]
    failures = [item for item in results if item["errors"]]
    return {"ok": not failures, "count": len(results), "failures": failures}
