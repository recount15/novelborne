# -*- coding: utf-8 -*-
"""空级批改器：段空/选项空/角色空逐空批改 + 唯一保留的整回合格式性检验。

设计红线（docs/REFACTOR_PLAN.md §3「门禁粒度下沉」）：质量检测全部落在
「空」级——每个空产出中文错误清单，供 :func:`build_refill_prompt` 定向重填；
整回合只保留**格式性**检验（正文无 JSON/代码围栏/系统标记/隐藏段/选项块
残留）。纯函数、零模型、零 IO：同一输入必得同一结果。

机制复用（单一事实源，不复制逻辑）：
- ``participation``：交互 marker、锚点动作/结果/因果/否定 marker 与
  「遮蔽锚点词面后再查 marker」的判定手法（``validate_anchor_convergence``
  /``validate_character_interaction`` 同口径的段级化）；
- ``elastic_gate``：系统自检段行的检测正则（这里只检不修）；
- ``options``：字母选项行正则与 A–F 键集。
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# —— 复用 participation 的 marker 词表（锚点收束/交互判定同口径）——
from core.engine.participation import (
    _ANCHOR_ACTION_MARKERS,
    _ANCHOR_CAUSAL_MARKERS,
    _ANCHOR_NEGATION_MARKERS,
    _ANCHOR_RESULT_MARKERS,
    _INTERACTION_MARKERS,
)
# —— 复用 elastic_gate 的系统自检段检测正则（strip_non_narrative_blocks 同源，只检不修）——
from core.engine.elastic_gate import _SYSTEM_BRACKET_RE
# —— 复用 options 的字母选项行正则与键集（parse_options/render_options_block 同源）——
from core.engine.options import OPTION_KEYS, _OPTION_LINE

# 选项空口径
OPTION_COUNT = 6
OPTION_TEXT_MIN = 8
OPTION_TEXT_MAX = 60
OPTION_PREVIEW_MAX = 60
OPTION_FACTORS = ("金手指", "性格", "剧情")
OPTION_SIMILARITY_THRESHOLD = 0.8
# 选项文本中不得出现的来源标注（rounds_rule.md「一律不得标注来源或分类」）
_SOURCE_LABELS = ("（金手指）", "（性格）", "（剧情）", "(金手指)", "(性格)", "(剧情)")

# 角色状态空口径（relationship_delta 合法取值，与 participation 交互约束块同词表）
RELATIONSHIP_DELTAS = ("升温", "稳定", "恶化", "无变化")
RELATIONSHIP_SUMMARY_MAX = 60

# 整回合格式性检验口径
JSON_LINE_MIN_CHARS = 80          # 单行 JSON 痕迹的最短长度（对齐 elastic_gate 的 80 字符口径）
OPTION_BLOCK_MIN_LINES = 4        # 连续 ≥4 行字母编号即判选项块混入正文
NEGATION_WINDOW = 8               # 锚点词 ±8 字否定窗（participation 同口径）
_HIDDEN_MARKS = ("<<<LOG>>>", "<<<ARCHIVE>>>")
# 单行 JSON 痕迹的兜底特征：一行内出现 ≥2 个 "键": 模式
_JSON_KEY_RE = re.compile(r'"[^"\n]{1,40}"\s*:')


@dataclass
class GradeResult:
    """批改结果：``errors`` 为中文错误清单（空列表 = 通过），``ok`` 为便捷布尔。

    ``issues`` 是 ``errors`` 的别名拷贝，供整回合门禁对齐
    ``scene_validation`` 的 dict 风格（``{"valid": bool, "issues": [...]}``）。
    """

    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def issues(self) -> list[str]:
        return list(self.errors)

    def add(self, message: str) -> None:
        text = str(message).strip()
        if text and text not in self.errors:
            self.errors.append(text)


@dataclass(frozen=True)
class SegmentContract:
    """段空合约：一份试卷中单个段落的填空要求（Wave A 出卷产物）。

    ``window`` 的字数容差（±20%）已含在窗口里，由出卷层保证；
    ``anchor_require_climax`` 为 True（收束段）时启用锚点证据词 +
    动作/结果/因果 marker + 否定窗的收束检查。
    """

    index: int
    role: str
    window: tuple[int, int]
    must_include: tuple[str, ...] = ()   # 逐词子串，缺一词即错
    must_mention: tuple[str, ...] = ()   # 人名：至少一名命中 + 交互 marker
    forbidden: tuple[str, ...] = ()      # 雷区词：命中即错
    anchor_terms: tuple[str, ...] = ()   # 锚点证据词（可选）
    anchor_require_climax: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "index", int(self.index))
        object.__setattr__(self, "role", str(self.role or "正文段"))
        for name in ("must_include", "must_mention", "forbidden", "anchor_terms"):
            words = tuple(str(item).strip() for item in (getattr(self, name) or ())
                          if str(item).strip())
            object.__setattr__(self, name, words)
        low, high = (int(v) for v in self.window)
        object.__setattr__(self, "window", (min(low, high), max(low, high)))


def grade_segment(contract: SegmentContract, text: str) -> GradeResult:
    """批改单个段空：字数窗口/必含词/点名交互/雷区词/锚点收束。

    每条错误带段号与角色前缀，供定向重填提示词直接引用。
    """
    result = GradeResult()
    content = str(text or "")
    low, high = contract.window
    count = len(content)
    tag = f"第{contract.index}段（{contract.role}）"

    if not (low <= count <= high):
        result.add(f"{tag}字数 {count} 字，不在要求窗口 {low}–{high} 字内")

    missing = [word for word in contract.must_include if word not in content]
    if missing:
        result.add(f"{tag}缺少必含词：「{'」、「'.join(missing)}」")

    if contract.must_mention:
        if not any(name in content for name in contract.must_mention):
            result.add(f"{tag}未点名任何指定角色（需含：「{'」、「'.join(contract.must_mention)}」中至少一人）")
        if not _INTERACTION_MARKERS.search(content):
            result.add(f"{tag}缺少可观察的回应或动作词（如 说道/看向/接过 等）")

    forbid_hit = [word for word in contract.forbidden if word in content]
    if forbid_hit:
        result.add(f"{tag}出现雷区词：「{'」、「'.join(forbid_hit)}」（绝对不得出现）")

    if contract.anchor_require_climax:
        _grade_anchor_climax(contract, content, result)
    return result


def _grade_anchor_climax(contract: SegmentContract, content: str,
                         result: GradeResult) -> None:
    """收束段的锚点检查：participation.validate_anchor_convergence 的段级化。

    证据词至少一个命中；命中词遮蔽词面后再查动作/结果/因果 marker
    （防止锚点词本身自带 marker 字骗过检查）；锚点词 ±8 字窗口内出现
    否定词即判 conflicted（锚点被取消/绕过）。
    """
    tag = f"第{contract.index}段（{contract.role}）"
    terms = contract.anchor_terms
    if not terms:
        result.add(f"{tag}锚点收束要求已启用，但合约未提供锚点证据词表")
        return
    matched = [term for term in terms if term in content]
    if not matched:
        result.add(f"{tag}锚点证据词均未出现（需含：「{'」、「'.join(terms)}」中至少一个）")
        return
    # 命中词遮蔽（长词优先），再查 marker——与 participation 的遮蔽做法一致
    masked = content
    for term in sorted(matched, key=len, reverse=True):
        masked = masked.replace(term, " " * len(term))
    if not _ANCHOR_ACTION_MARKERS.search(masked):
        result.add(f"{tag}锚点缺少可观察动作词（如 打开/撞开/阻止/夺取 等）")
    if not _ANCHOR_RESULT_MARKERS.search(masked):
        result.add(f"{tag}锚点缺少可观察结果词（如 失守/获救/落定/曝光 等）")
    if not _ANCHOR_CAUSAL_MARKERS.search(masked):
        result.add(f"{tag}锚点缺少因果连接词（如 因此/导致/随后/代价 等）")
    for term in matched:
        # 同一锚点词可能出现多次：逐次检查 ±8 字否定窗，不能只查首现。
        start_at = 0
        while True:
            offset = content.find(term, start_at)
            if offset < 0:
                break
            start = max(0, offset - NEGATION_WINDOW)
            end = min(len(content), offset + len(term) + NEGATION_WINDOW)
            negation = _ANCHOR_NEGATION_MARKERS.search(content[start:end])
            if negation:
                result.add(f"{tag}锚点词「{term}」前后 {NEGATION_WINDOW} 字内出现否定表述"
                           f"（{negation.group(0)}），锚点疑似被取消或绕过")
                break
            start_at = offset + max(1, len(term))


def grade_options(options: Sequence[Mapping[str, Any]]) -> GradeResult:
    """批改选项空：恰 6 条、键 A–F 唯一、文本 8–60 字、因素合法、
    preview 非空 ≤60 字、不得含来源标注、difflib 相似度 ≥0.8 判重。"""
    result = GradeResult()
    raw_items = list(options or ())
    if len(raw_items) != OPTION_COUNT:
        result.add(f"选项数量必须恰好 {OPTION_COUNT} 条（当前 {len(raw_items)} 条）")
    items: list[tuple[int, str, str]] = []
    seen_keys: dict[str, int] = {}
    factor_counts = {factor: 0 for factor in OPTION_FACTORS}
    for position, item in enumerate(raw_items, 1):
        if not isinstance(item, Mapping):
            result.add(f"第{position}条必须是包含 key/text/factor/preview 的对象")
            continue
        key = str(item.get("key") or "").strip().upper()
        label = f"第{position}条（{key or '无编号'}）"
        if key not in OPTION_KEYS:
            result.add(f"{label} 编号非法：必须为 A–F（当前「{key or '缺失'}」）")
        elif key in seen_keys:
            result.add(f"{label} 编号与第{seen_keys[key]}条重复（{key}）")
        else:
            seen_keys[key] = position
        text = str(item.get("text") or "").strip()
        if not (OPTION_TEXT_MIN <= len(text) <= OPTION_TEXT_MAX):
            result.add(f"{label} 选项文本长度需 {OPTION_TEXT_MIN}–{OPTION_TEXT_MAX} 字（当前 {len(text)} 字）")
        source_hit = [mark for mark in _SOURCE_LABELS if mark in text]
        if source_hit:
            result.add(f"{label} 文本含来源标注「{source_hit[0]}」：6 个选项一律不得标注来源或分类")
        factor = str(item.get("factor") or "").strip()
        if factor not in OPTION_FACTORS:
            result.add(f"{label} 来源因素必须是：{'、'.join(OPTION_FACTORS)}（当前「{factor or '缺失'}」）")
        else:
            factor_counts[factor] += 1
        preview = str(item.get("preview") or "").strip()
        if not preview:
            result.add(f"{label} 缺少后果预告 preview（非空，≤{OPTION_PREVIEW_MAX} 字）")
        elif len(preview) > OPTION_PREVIEW_MAX:
            result.add(f"{label} 后果预告超长：最多 {OPTION_PREVIEW_MAX} 字（当前 {len(preview)} 字）")
        items.append((position, key, text))
    # 固定 6+1 契约：恰 4 条金手指向 + 2 条性格向；剧情只能作为方向素材，
    # 不能占用这 6 条的来源配额（否则「4+2」失去代码级保证）。
    if len(raw_items) == OPTION_COUNT and all(
            str(item.get("factor") or "").strip() in OPTION_FACTORS
            for item in raw_items if isinstance(item, Mapping)):
        if factor_counts.get("金手指", 0) != 4 or factor_counts.get("性格", 0) != 2:
            result.add(
                "选项因素分布必须恰为 4 条金手指 + 2 条性格"
                f"（当前 金手指 {factor_counts.get('金手指', 0)}、"
                f"性格 {factor_counts.get('性格', 0)}、剧情 {factor_counts.get('剧情', 0)}）")
    # 去重：两两相似度（difflib）≥ 阈值即判重
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            first, second = items[i], items[j]
            ratio = difflib.SequenceMatcher(None, first[2], second[2]).ratio()
            if first[2] and ratio >= OPTION_SIMILARITY_THRESHOLD:
                result.add(f"第{first[0]}条与第{second[0]}条文本相似度过高"
                           f"（{round(ratio, 2)}），疑似重复，须改写其中一条")
    return result


def grade_character_patch(
    patches: Sequence[Mapping[str, Any]],
    roster_names: Sequence[str],
    narrative: str,
) -> tuple[list[dict[str, Any]], list[tuple[Any, str]]]:
    """批改角色状态补丁：名字 ∈ 名册、evidence 为正文子串、
    relationship_delta 合法、summary ≤60 字。

    返回 ``(valid, rejected)``：不合格项整条剔除并附中文原因
    （``rejected`` 每项为 ``(原始条目, 原因)``，原因内多错以「；」连接）。
    """
    roster = {str(name or "").strip() for name in (roster_names or ())
              if str(name or "").strip()}
    story = str(narrative or "")
    valid: list[dict[str, Any]] = []
    rejected: list[tuple[Any, str]] = []
    for item in patches or ():
        if not isinstance(item, Mapping):
            rejected.append((item, "条目必须是包含 name/evidence/relationship_delta/summary 的对象"))
            continue
        patch = dict(item)
        name = str(patch.get("name") or "").strip()
        evidence = str(patch.get("evidence") or "").strip()
        delta = str(patch.get("relationship_delta") or "").strip()
        summary = str(patch.get("summary") or "").strip()
        reasons: list[str] = []
        if name not in roster:
            reasons.append(f"名字「{name or '缺失'}」不在本回合在场名册中")
        if not evidence:
            reasons.append("证据句缺失，且必须是本回合正文的子串")
        elif evidence not in story:
            reasons.append(f"证据句并非正文子串：「{evidence[:20]}{'…' if len(evidence) > 20 else ''}」")
        if delta not in RELATIONSHIP_DELTAS:
            reasons.append(f"关系走向必须是：{'、'.join(RELATIONSHIP_DELTAS)}（当前「{delta or '缺失'}」）")
        if not summary:
            reasons.append("关系摘要不得为空")
        elif len(summary) > RELATIONSHIP_SUMMARY_MAX:
            reasons.append(f"关系摘要超长：最多 {RELATIONSHIP_SUMMARY_MAX} 字（当前 {len(summary)} 字）")
        if reasons:
            rejected.append((item, "；".join(reasons)))
        else:
            valid.append(patch)
    return valid, rejected


def grade_format_whole(text: str) -> GradeResult:
    """整回合格式性检验——唯一保留的整回合门禁（只检不修）。

    五类残留：``` 围栏、单行 ≥80 字符 JSON 痕迹、【系统…】/【校验…】行、
    ``<<<LOG>>>/<<<ARCHIVE>>>`` 隐藏段标记、字母选项块混入正文（连续
    ≥4 行 A–F 编号）。作用于**正文/叙事文本**（选项块组装前）；
    组装后的完整展示文本天然含选项块，不是本函数的检测对象。
    """
    result = GradeResult()
    content = str(text or "")
    if not content.strip():
        result.add("正文为空")
        return result
    if "```" in content:
        # 比 elastic_gate 的闭合围栏正则更严：只检不修，任何 ``` 痕迹都算残留
        result.add("正文残留代码围栏（```），须剥离后重新提交")
    option_run = max_run = 0
    for lineno, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if _OPTION_LINE.match(line):
            option_run += 1
            max_run = max(max_run, option_run)
            continue
        if not line:
            continue  # 空行不打断选项块连续性（真实混入块可能夹空行）
        option_run = 0
        if (len(line) >= JSON_LINE_MIN_CHARS and line[:1] in '{["'
                and (_is_json_trace(line) or bool(_JSON_KEY_RE.search(line)))):
            result.add(f"正文第 {lineno} 行残留单行 JSON 痕迹（长度 {len(line)} 字符），须剥离后重新提交")
        if _SYSTEM_BRACKET_RE.match(raw_line):
            result.add(f"正文第 {lineno} 行残留系统自检段（【系统…】/【校验…】），须剥离后重新提交")
    if max_run >= OPTION_BLOCK_MIN_LINES:
        result.add(f"正文混入字母选项块（连续 {max_run} 行 A–F 编号）：选项块由代码单独渲染，不得写入正文")
    for mark in _HIDDEN_MARKS:
        if mark in content:
            result.add(f"正文残留隐藏段标记 {mark}：日志/存档由代码渲染，不得进入正文")
    return result


def _is_json_trace(line: str) -> bool:
    """单行 JSON 痕迹判定：整行能 json.loads，或含 ≥2 个 ``"键":`` 模式。"""
    try:
        json.loads(line)
        return True
    except ValueError:
        return len(_JSON_KEY_RE.findall(line)) >= 2


def format_gate(text: str) -> dict[str, Any]:
    """给 app 层的最终门禁形态（对齐 scene_validation 的 dict 风格）。"""
    check = grade_format_whole(text)
    return {"valid": check.ok, "issues": check.issues}


def build_refill_prompt(contract: SegmentContract | Mapping[str, Any],
                        errors: Sequence[str]) -> str:
    """把错误清单渲染成该空的定向重填指令（中文）。

    附带原合约要求（字数窗口/必含词/点名/雷区词/锚点收束），并强调
    「只重写本段/本空」。``contract`` 也接受普通 Mapping（选项空、角色空
    等非段空复用同一渲染骨架）。
    """
    error_lines = [f"- {item}" for item in (errors or ()) if str(item).strip()]
    if isinstance(contract, SegmentContract):
        head = f"【定向重填：第{contract.index}段（{contract.role}）】"
        requirement_lines = _segment_requirements(contract)
        tail = ("只重写本段：直接输出重写后的段落正文，不要输出标题、编号、解释或代码围栏，"
                "不要改动其它段落。")
    else:
        head = "【定向重填：本空】"
        requirement_lines = [f"- {key}：{value}" for key, value in dict(contract or {}).items()]
        tail = "只重写本空：按上述要求直接输出该空的内容，不要输出解释或任何额外内容。"
    parts = [head]
    if error_lines:
        parts.append("上一稿存在以下问题，必须全部修正：\n" + "\n".join(error_lines))
    if requirement_lines:
        parts.append("本空原始要求：\n" + "\n".join(requirement_lines))
    parts.append(tail)
    return "\n\n".join(parts)


def _segment_requirements(contract: SegmentContract) -> list[str]:
    """把段空合约渲染为重填提示里的「原始要求」行。"""
    low, high = contract.window
    rows = [f"- 字数 {low}–{high} 字（±20% 容差已含在窗口内）"]
    if contract.must_include:
        rows.append(f"- 必须逐词包含：「{'」、「'.join(contract.must_include)}」")
    if contract.must_mention:
        rows.append(f"- 必须点名（至少一人）并给出可观察的回应或动作：「{'」、「'.join(contract.must_mention)}」")
    if contract.forbidden:
        rows.append(f"- 雷区词，绝对不得出现：「{'」、「'.join(contract.forbidden)}」")
    if contract.anchor_require_climax or contract.anchor_terms:
        terms = '」、「'.join(contract.anchor_terms) or "（未提供）"
        rows.append(f"- 锚点收束：须命中锚点词（「{terms}」至少一个），由行动落成（含动作词）、"
                    f"给出可观察结果与因果连接；锚点词前后 {NEGATION_WINDOW} 字内不得出现否定表述")
    return rows


__all__ = [
    "GradeResult", "SegmentContract",
    "grade_segment", "grade_options", "grade_character_patch",
    "grade_format_whole", "format_gate", "build_refill_prompt",
    "OPTION_COUNT", "OPTION_TEXT_MIN", "OPTION_TEXT_MAX", "OPTION_PREVIEW_MAX",
    "OPTION_FACTORS", "OPTION_SIMILARITY_THRESHOLD",
    "RELATIONSHIP_DELTAS", "RELATIONSHIP_SUMMARY_MAX",
    "JSON_LINE_MIN_CHARS", "OPTION_BLOCK_MIN_LINES", "NEGATION_WINDOW",
]
