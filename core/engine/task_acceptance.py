"""Flexible, non-blocking task acceptance.

C06：裸子串命中不算事实——否定/计划/尝试/疑问语境的出现不计数；「完成」
额外要求证据落在已提交回合（story_ledger committed 行）上，未提交的候选
文本至多记 partial。宁漏勿滥：漏记走 repair 提示继续推进，误记会伪造完成。
"""
from __future__ import annotations

import re
from typing import Any, Mapping

# 命中词紧前方（紧贴、4 字窗口内）出现这些标记 => 该次出现是否定/计划/
# 尝试，不是既成事实。逐次出现独立判定：先否定后达成的文本只看达成那次。
_PRE_MARKERS: tuple[str, ...] = (
    "没有", "并未", "尚未", "还没", "不曾", "不再", "无法", "没能", "无从",
    "准备", "打算", "计划", "想要", "企图", "试图", "尝试", "希望", "期望",
    "若能", "如果能", "要是能", "等我", "将要", "将会", "即将", "为了",
    "未", "没", "别", "非", "无", "想",
)
# 命中词紧后方出现这些 => 疑问/反问，不是断言。
_TAIL_DOUBT: tuple[str, ...] = ("吗", "么", "？", "?", "没有", "不成")
_MARKER_WINDOW = 4  # 覆盖最长 4 字标记（如「如果能」）

_STRIP_RE = re.compile(
    r"[\s，。；：、！？…\-—·（）()\"'「」『』【】<>《》/\\,.\[\]{}:;!?~\*#@%^&+=|]+")


def _clean(text: Any) -> str:
    return _STRIP_RE.sub("", str(text or "")).lower()


def _marker_before(blob: str, idx: int) -> bool:
    prefix = blob[max(0, idx - _MARKER_WINDOW):idx]
    return any(prefix.endswith(marker) for marker in _PRE_MARKERS)


def _doubt_after(blob: str, idx: int, length: int) -> bool:
    tail = blob[idx + length: idx + length + 3]
    if tail[:1] in ("了", "啦"):  # 「找到了吗」：跳过时态助词再看疑问
        tail = tail[1:]
    return any(tail.startswith(doubt) for doubt in _TAIL_DOUBT)


def untainted_occurrence(blob: str, phrase: str) -> bool:
    """phrase 在 blob（已清洗小写）中是否存在至少一次「干净」出现。

    干净 = 紧前方无否定/计划/尝试标记、紧后方不是疑问语气。找不到或全部
    出现都被污染时返回 False。
    """
    blob = str(blob or "")
    phrase = str(phrase or "")
    if not phrase:
        return False
    idx = blob.find(phrase)
    while idx != -1:
        if not _marker_before(blob, idx) and not _doubt_after(blob, idx, len(phrase)):
            return True
        idx = blob.find(phrase, idx + 1)
    return False


def window_hit_clean(blob: str, window: str, offset: int) -> bool:
    """条件短语 4 字滑窗的干净命中（quest.requirement_hits 共用）。

    窗口命中除自身语境外，还要回看「条件短语重构起点」（窗口在短语内的
    偏移回推）——否定/计划标记贴在短语开头时（「没有查证北墙裂痕」），
    短语中段窗口自身看不见标记，必须从短语起点判定。
    """
    idx = blob.find(window)
    while idx != -1:
        if (not _marker_before(blob, idx)
                and not _doubt_after(blob, idx, len(window))
                and not (offset and _marker_before(blob, idx - offset))):
            return True
        idx = blob.find(window, idx + 1)
    return False


def intent_statement(phrase: str, conditions) -> bool:
    """证据引文内部检查：引文里某条完成条件窗口被否定/计划标记直接引领。

    引文逐字摘自正文仍可能只是角色的口头计划（「我准备查证北墙裂痕」），
    引文自身就带着「打算做」的标记。
    """
    text = _clean(phrase)
    for cond in conditions or []:
        cleaned = _clean(cond)
        if not cleaned:
            continue
        windows = ([cleaned[i:i + 4] for i in range(len(cleaned) - 3)]
                   if len(cleaned) >= 4 else [cleaned])
        for window in windows:
            idx = text.find(window)
            while idx != -1:
                prefix = text[max(0, idx - _MARKER_WINDOW):idx]
                if any(prefix.endswith(marker) for marker in _PRE_MARKERS):
                    return True
                idx = text.find(window, idx + 1)
    return False


def committed_text(state: Mapping[str, Any] | None) -> str:
    """已提交回合的文本证据（action+narrative+events 拼接），无提交行返回空。"""
    state = state if isinstance(state, Mapping) else {}
    rows = state.get("story_ledger")
    parts: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or row.get("committed") is not True:
            continue
        for key in ("action", "narrative"):
            if row.get(key):
                parts.append(str(row[key]))
        for event in row.get("events") or []:
            if isinstance(event, Mapping):
                title = str(event.get("title") or event.get("event_id") or "")
                if title:
                    parts.append(title)
            elif event:
                parts.append(str(event))
    return " ".join(parts)


def _has_ledger_rows(state: Mapping[str, Any]) -> bool:
    """门禁：账本里已有回合记录（含未提交草稿）即启用已提交验证。

    有账本却拿不出已提交证据说明事实只存在于候选/草稿文本——至多 partial。
    完全没有账本（离线评估、旧调用方）不启用门禁，保留旧语义。
    """
    rows = state.get("story_ledger")
    return isinstance(rows, list) and len(rows) > 0


def evaluate(task: Mapping[str, Any], *, action: str = "", narrative: str = "",
             state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    state = state if isinstance(state, Mapping) else {}
    blob = _clean(str(action) + " " + str(narrative))
    hard = [str(x).lower() for x in (task.get("hard_facts") or [])]
    soft = [str(x).lower() for x in (task.get("soft_signals") or [])]
    hard_hit = sum(1 for x in hard if x and untainted_occurrence(blob, x))
    soft_hit = sum(1 for x in soft if x and untainted_occurrence(blob, x))
    committed_gate = _has_ledger_rows(state)
    committed_verified = (not committed_gate) or all(
        untainted_occurrence(_clean(committed_text(state)), x) for x in hard if x)
    if hard_hit == len(hard) and (hard or soft_hit):
        status = "complete" if committed_verified else "partial"
    elif hard_hit or soft_hit:
        status = "partial"
    else:
        status = "pending"
    progress = {"hard_hits": hard_hit, "hard_total": len(hard),
                "soft_hits": soft_hit, "soft_total": len(soft),
                "committed_verified": committed_verified}
    if status == "complete":
        repair = []
    elif (status == "partial" and committed_gate and not committed_verified
          and hard and hard_hit == len(hard)):
        repair = [{"action": "await_commit_or_restate",
                   "reason": "evidence not in committed turns yet"}]
    else:
        repair = [{"action": "continue_or_redirect", "reason": "insufficient evidence"}]
    return {"status": status, "progress": progress,
            "evidence": {"action": str(action)[:240], "narrative": str(narrative)[:400],
                         "committed_gate": committed_gate},
            "repair": repair, "degraded": status != "complete"}
