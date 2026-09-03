# -*- coding: utf-8 -*-
"""独立回合质量门：确定性多维评分与有界、保优、可回滚的修订原语。

本模块零网络、零 IO。模型修订器与模型裁判均为可选注入函数；默认只执行本地
规则。质量门不会接管回合编排，供 ``turn_pipeline`` 后续按需调用。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

QUALITY_GATE_VERSION = "1.0"
MAX_REFINEMENT_ROUNDS = 3
MAX_REFINEMENT_CALLS = 8
MAX_REFINEMENT_SECONDS = 180.0
DEFAULT_EPSILON = 1.0
DEFAULT_MIN_IMPROVEMENT = 0.25
PASS_SCORE = 70.0

DIMENSION_WEIGHTS: dict[str, float] = {
    "format": 0.14,
    "substance": 0.10,
    "options": 0.12,
    "contracts": 0.16,
    "world_context": 0.10,
    "anchors": 0.10,
    "characters": 0.08,
    "style": 0.10,
    "continuity": 0.06,
    "state": 0.04,
}

_FORMAT_PATTERNS = (
    ("code_fence", re.compile(r"```"), "正文残留代码围栏"),
    ("hidden_log", re.compile(r"<<<(?:LOG|ARCHIVE)>>>"), "正文残留隐藏日志或存档标记"),
    ("system_residue", re.compile(r"(?m)^\s*【(?:系统|校验|自检)[^】]*】"), "正文残留系统自检段"),
    ("json_residue", re.compile(r"(?m)^\s*[\{\[]\s*\"[^\"\n]+\"\s*:"), "正文残留 JSON 对象"),
    ("analysis_residue", re.compile(r"(?im)^\s*(?:analysis|reasoning|思考过程|模型分析)\s*[:：]"), "正文残留推理说明"),
    # v2.0.3 实测（kimi-k3）：段卷兜底段的机械短语混进最终正文出厂——
    # 蓝图脚手架必须按格式硬伤处理，否则机械命中必含词/锚点词反把总分抬高。
    # 注意「因此落定」是段卷提示词明确要求的因果句式，合规段也会出现，
    # 不能列为脚手架特征；兜底稿的整句重复由 substance 维度另行拦截。
    ("scaffold_apply", re.compile(r"就地应对"), "正文残留蓝图脚手架（就地应对）"),
    ("scaffold_materialize", re.compile(r"随之显形"), "正文残留蓝图脚手架（随之显形）"),
    ("scaffold_role_label", re.compile(r"（(?:反应与展开|推进与转折|落点与钩子)）"), "正文残留蓝图段位标签"),
    ("blueprint_event", re.compile(r"第[0-9一二三四五六]+段主事件"), "正文残留蓝图事件句"),
    ("blueprint_seg_no", re.compile(r"（段\s*[0-9]+）"), "正文残留蓝图段号标记"),
)


def has_scaffold(text: str) -> bool:
    """正文是否混入蓝图兜底脚手架（turn_pipeline 终检复用，绝不放行出厂）。"""
    return any(pattern.search(text or "")
               for code, pattern, _ in _FORMAT_PATTERNS
               if code.startswith(("scaffold_", "blueprint_")))
_OPTION_LINE_RE = re.compile(r"(?m)^\s*[A-FＡ-Ｆ][\.、:：\)]\s*\S+")
_AI_ORIGIN_RE = re.compile(
    r"(?:作为(?:一个)?AI|作为人工智能|AI生成|人工智能生成|由AI提供|模型生成|语言模型|无法替你决定)",
    re.IGNORECASE,
)
_FILLER_PHRASES = (
    "总而言之", "值得注意的是", "不禁让人", "空气中弥漫着", "一切似乎",
    "仿佛在诉说", "命运的齿轮", "故事还在继续", "未完待续", "让我们拭目以待",
)
_ABRUPT_MARKERS = ("与此同时，另一边", "镜头一转", "回到现在", "不知过了多久")
_SENSORY_OR_ACTION_RE = re.compile(
    r"(?:看|听|闻|触|握|推|拉|走|跑|转身|抬头|低声|喊|笑|哭|风|雨|光|声|冷|热)"
)
_DIALOGUE_RE = re.compile(r"[""「『][^""」』]{2,}[""」』]")
_NEGATION_RE = re.compile(r"(?:并未|没有|从未|不曾|并非|绝非|未能|不存在)")
_WORD_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)


@dataclass(frozen=True)
class QualityIssue:
    """单条可审计发现。``severity=error`` 会阻止质量门通过。"""

    dimension: str
    code: str
    message: str
    severity: str = "warning"
    evidence: tuple[str, ...] = ()
    source: str = "rule"


@dataclass(frozen=True)
class ScoreCard:
    """一次质量评分；各维与总分均在 0–100，audit 仅含可序列化元数据。"""

    dimensions: dict[str, float]
    total: float
    issues: tuple[QualityIssue, ...] = ()
    passed: bool = False
    audit: dict[str, Any] = field(default_factory=dict)

    def dimension(self, name: str) -> float:
        return float(self.dimensions.get(name, 0.0))


@dataclass(frozen=True)
class RefinementRequest:
    """传给注入修订器的纯数据请求。"""

    round_index: int
    narrative: str
    options: tuple[Any, ...]
    scorecard: ScoreCard
    modular_context: Any
    contracts: Any


@dataclass(frozen=True)
class RefinementEvent:
    """单轮审计事件；accepted=False 表示自动回滚到此前最佳稿。"""

    round_index: int
    call_index: int
    accepted: bool
    reason: str
    before_total: float
    candidate_total: float | None = None
    regressed_dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefinementResult:
    """有界修订结果，始终返回执行期间见过的最佳且未退化稿。"""

    narrative: str
    options: tuple[Any, ...]
    scorecard: ScoreCard
    initial_scorecard: ScoreCard
    rounds: int
    calls: int
    stop_reason: str
    events: tuple[RefinementEvent, ...] = ()


JudgeFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]
ModelFn = Callable[[RefinementRequest], Any]
ClockFn = Callable[[], float]


def score_turn(
    narrative: str,
    options: Sequence[Any] | None,
    modular_context: Any,
    contracts: Any,
    baseline: ScoreCard | None = None,
    *,
    judge_fn: JudgeFn | None = None,
    dimension_weights: dict[str, float] | None = None,
) -> ScoreCard:
    """对完整回合做确定性九维评分，并可选择性融合有证据的注入裁判分。

    ``judge_fn`` 不会被默认创建。裁判返回形态为
    ``{"scores": {维度: 0..100}, "evidence": {维度: [正文原文引句]}}``；
    每个被评分维度必须给出至少一条候选稿中的逐字证据，否则整份裁判结果拒收。
    
    v2.0.4: ``dimension_weights`` 可覆盖默认权重（free 阶段降低 anchors 权重）。
    """
    story = str(narrative or "")
    option_items = tuple(options or ())
    corpus = _candidate_corpus(story, option_items)
    terms = _collect_requirements(modular_context, contracts)
    issues: list[QualityIssue] = []

    format_score = _score_format(story, issues)
    substance_score = _score_substance(story, issues)
    options_score = _score_options(option_items, issues)
    contract_score = _score_contracts(corpus, terms["required"], terms["forbidden"], issues)
    world_score = _score_coverage("world_context", corpus, terms["world"], issues)
    anchor_score = _score_coverage("anchors", story, terms["anchors"], issues, require_any=True)
    character_score = _score_coverage("characters", story, terms["characters"], issues, require_any=True)
    style_score = _score_style(story, issues)
    continuity_score = _score_continuity(story, modular_context, terms, issues)
    state_score = _score_state(story, modular_context, issues)

    dimensions = {
        "format": format_score,
        "substance": substance_score,
        "options": options_score,
        "contracts": contract_score,
        "world_context": world_score,
        "anchors": anchor_score,
        "characters": character_score,
        "style": style_score,
        "continuity": continuity_score,
        "state": state_score,
    }
    judge_audit: dict[str, Any] = {"used": False, "status": "not_requested"}
    if judge_fn is not None:
        dimensions, judge_audit = _apply_judge(
            dimensions, story, option_items, modular_context, contracts, judge_fn, issues)

    dimensions = {key: _bounded(value) for key, value in dimensions.items()}
    weights = dimension_weights if dimension_weights is not None else DIMENSION_WEIGHTS
    total = round(sum(dimensions[key] * weights[key] for key in weights), 2)
    hard_errors = [issue for issue in issues if issue.severity == "error"]
    baseline_delta = None
    if baseline is not None:
        baseline_delta = {
            key: round(dimensions[key] - baseline.dimension(key), 2)
            for key in DIMENSION_WEIGHTS
        }
    audit = {
        "version": QUALITY_GATE_VERSION,
        "input_digest": _digest(story, option_items, modular_context, contracts),
        "rule_issue_count": sum(1 for item in issues if item.source == "rule"),
        "hard_error_count": len(hard_errors),
        "judge": judge_audit,
        "baseline_delta": baseline_delta,
        "term_counts": {key: len(value) for key, value in terms.items()},
    }
    return ScoreCard(
        dimensions=dimensions,
        total=total,
        issues=tuple(issues),
        passed=total >= PASS_SCORE and not hard_errors,
        audit=audit,
    )


def bounded_refine(
    narrative: str,
    options: Sequence[Any] | None,
    modular_context: Any,
    contracts: Any,
    model_fn: ModelFn,
    *,
    baseline: ScoreCard | None = None,
    judge_fn: JudgeFn | None = None,
    max_rounds: int = MAX_REFINEMENT_ROUNDS,
    max_calls: int = MAX_REFINEMENT_CALLS,
    max_seconds: float = MAX_REFINEMENT_SECONDS,
    epsilon: float = DEFAULT_EPSILON,
    min_improvement: float = DEFAULT_MIN_IMPROVEMENT,
    early_pass_score: float | None = None,
    clock_fn: ClockFn = time.monotonic,
    dimension_weights: dict[str, float] | None = None,
) -> RefinementResult:
    """在硬边界内迭代修订，并实施 keep-best、逐维非退化与失败回滚。
    
    v2.0.4: ``dimension_weights`` 可覆盖默认权重（free 阶段降低 anchors 权重）。

    边界参数会被夹紧到 ``3 轮 / 8 次注入调用 / 180 秒``。修订器每轮收到
    :class:`RefinementRequest`，可返回 ``{"narrative": ..., "options": [...]}``、
    ``(narrative, options)``，或仅返回新正文字符串（沿用当前选项；自由输入
    回合选项为空也合法）。候选稿任一维比当前最佳稿下降超过 ``epsilon``
    即拒绝；总分没有至少 ``min_improvement`` 的提升也不替换最佳稿。异常、
    空稿、超时和不合形态均回滚。连续两轮无提升即停。

    ``early_pass_score``：初稿已通过且总分达到该阈值时直接跳过修订轮
    （强模型/干净初稿快速通道，零额外模型调用）。

    v2.0.3 特赦轮：初稿带 error 级硬伤时机械分已被污染（脚手架逐字命中
    必含词反拿高分），首个有效候选免受分维无回退门与提升阈值约束；首个
    候选被采纳后基线即真实文本，防反作用门照常生效。
    """
    rounds_limit = max(0, min(int(max_rounds), MAX_REFINEMENT_ROUNDS))
    calls_limit = max(0, min(int(max_calls), MAX_REFINEMENT_CALLS))
    seconds_limit = max(0.0, min(float(max_seconds), MAX_REFINEMENT_SECONDS))
    epsilon = max(0.0, float(epsilon))
    min_improvement = max(0.0, float(min_improvement))
    start = clock_fn()
    calls = 0

    def can_call() -> bool:
        return calls < calls_limit and clock_fn() - start < seconds_limit

    def call_judge(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        if not can_call():
            raise _CallBudgetExhausted("注入调用预算已耗尽")
        calls += 1
        return judge_fn(payload) if judge_fn is not None else {}

    initial_judge = call_judge if judge_fn is not None and can_call() else None
    initial = score_turn(
        narrative, options, modular_context, contracts, baseline=baseline,
        judge_fn=initial_judge, dimension_weights=dimension_weights,
    )
    best_story = str(narrative or "")
    best_options = tuple(options or ())
    best_score = initial
    events: list[RefinementEvent] = []
    no_improvement = 0
    stop_reason = "round_limit"
    rounds_run = 0
    # v2.0.3 特赦轮：初稿带 error 级硬伤（如蓝图脚手架混入）时，机械维度
    # 分数已被污染（脚手架逐字命中必含词/锚点词反而拿高分），无资格受
    # 分维无回退门保护——首个候选只要求总分不低于当前基线即可采纳，
    # 采纳后基线换成真实文本、后续轮恢复全部防反作用门。
    amnesty = any(issue.severity == "error" for issue in initial.issues)

    if rounds_limit == 0:
        stop_reason = "round_limit"
    elif initial.passed and early_pass_score is not None and initial.total >= early_pass_score:
        stop_reason = "early_pass"
    elif not can_call():
        stop_reason = "call_limit" if calls >= calls_limit else "time_limit"

    for round_index in range(1, rounds_limit + 1):
        if stop_reason == "early_pass":
            break  # 快速通道：初稿已达标，零额外模型调用（break 跳过 for-else）
        if not can_call():
            stop_reason = "call_limit" if calls >= calls_limit else "time_limit"
            break
        rounds_run = round_index
        request = RefinementRequest(
            round_index=round_index,
            narrative=best_story,
            options=best_options,
            scorecard=best_score,
            modular_context=modular_context,
            contracts=contracts,
        )
        calls += 1
        try:
            raw_candidate = model_fn(request)
        except Exception as exc:  # 注入边界：失败必须留审计并回滚
            events.append(RefinementEvent(
                round_index, calls, False, f"model_error:{type(exc).__name__}", best_score.total))
            no_improvement += 1
        else:
            if clock_fn() - start >= seconds_limit:
                events.append(RefinementEvent(
                    round_index, calls, False, "time_limit", best_score.total))
                stop_reason = "time_limit"
                break
            candidate = _coerce_candidate(raw_candidate, best_options)
            if candidate is None:
                events.append(RefinementEvent(
                    round_index, calls, False, "invalid_candidate", best_score.total))
                no_improvement += 1
            else:
                candidate_story, candidate_options = candidate
                candidate_judge = call_judge if judge_fn is not None and can_call() else None
                candidate_score = score_turn(
                    candidate_story, candidate_options, modular_context, contracts,
                    baseline=best_score, judge_fn=candidate_judge, dimension_weights=dimension_weights,
                )
                regressed = tuple(
                    key for key in (dimension_weights or DIMENSION_WEIGHTS)
                    if candidate_score.dimension(key) < best_score.dimension(key) - epsilon
                )
                round_amnesty = amnesty and not any(event.accepted for event in events)
                if regressed and not round_amnesty:
                    events.append(RefinementEvent(
                        round_index, calls, False, "dimension_regression", best_score.total,
                        candidate_score.total, regressed))
                    no_improvement += 1
                elif (not round_amnesty
                      and candidate_score.total < best_score.total + min_improvement):
                    events.append(RefinementEvent(
                        round_index, calls, False, "no_improvement", best_score.total,
                        candidate_score.total))
                    no_improvement += 1
                else:
                    events.append(RefinementEvent(
                        round_index, calls, True,
                        "amnesty_accept" if round_amnesty else "improved",
                        best_score.total, candidate_score.total))
                    best_story = candidate_story
                    best_options = candidate_options
                    best_score = candidate_score
                    no_improvement = 0
        if no_improvement >= 2:
            stop_reason = "two_no_improvement_rounds"
            break
    else:
        stop_reason = "round_limit"

    return RefinementResult(
        narrative=best_story,
        options=best_options,
        scorecard=best_score,
        initial_scorecard=initial,
        rounds=rounds_run,
        calls=calls,
        stop_reason=stop_reason,
        events=tuple(events),
    )


class _CallBudgetExhausted(RuntimeError):
    pass


def _score_format(story: str, issues: list[QualityIssue]) -> float:
    if not story.strip():
        issues.append(QualityIssue("format", "empty_narrative", "正文为空", "error"))
        return 0.0
    score = 100.0
    for code, pattern, message in _FORMAT_PATTERNS:
        match = pattern.search(story)
        if match:
            issues.append(QualityIssue("format", code, message, "error", (match.group(0)[:80],)))
            score -= 28.0
    option_lines = _OPTION_LINE_RE.findall(story)
    if len(option_lines) >= 4:
        issues.append(QualityIssue(
            "format", "option_block_residue", "正文混入字母选项块", "error",
            tuple(line.strip() for line in option_lines[:4])))
        score -= 35.0
    return _bounded(score)


def _score_substance(story: str, issues: list[QualityIssue]) -> float:
    text = story.strip()
    if not text:
        issues.append(QualityIssue("substance", "empty_content", "正文没有可评分内容", "error"))
        return 0.0
    score = 100.0
    if len(text) < 40:
        issues.append(QualityIssue("substance", "too_short", "正文过短，缺少完整场景信息"))
        score -= min(55.0, (40 - len(text)) * 1.5)
    sentences = [part.strip() for part in _WORD_SPLIT_RE.split(text) if part.strip()]
    normalized = [_normalize(part) for part in sentences if _normalize(part)]
    duplicate_count = len(normalized) - len(set(normalized))
    if duplicate_count:
        issues.append(QualityIssue(
            "substance", "repeated_sentence", "正文存在整句重复填充", "error",
            tuple(_duplicates(sentences)[:3])))
        score -= min(55.0, duplicate_count * 22.0)
    repeated_chunk = _repeated_chunk(text)
    if repeated_chunk:
        issues.append(QualityIssue(
            "substance", "repeated_filler", "正文存在高频重复片段，疑似填充", "error",
            (repeated_chunk,)))
        score -= 30.0
    filler_hits = tuple(term for term in _FILLER_PHRASES if term in text)
    if len(filler_hits) >= 2:
        issues.append(QualityIssue(
            "substance", "generic_filler", "正文套话密度偏高", "warning", filler_hits[:4]))
        score -= min(25.0, len(filler_hits) * 6.0)
    return _bounded(score)


def _score_options(options: Sequence[Any], issues: list[QualityIssue]) -> float:
    if not options:
        # AI-only 原则：模型不可用时本回合合法无选项（保留自由输入），
        # 该维按中性处理，不因「无选项」惩罚正文质量分。
        issues.append(QualityIssue("options", "empty_options", "本回合无选项（自由输入模式）", "info"))
        return 100.0
    score = 100.0
    texts: list[tuple[int, str]] = []
    for index, item in enumerate(options, 1):
        text = _option_text(item)
        if not text:
            issues.append(QualityIssue(
                "options", "empty_option", f"第 {index} 个选项为空", "error"))
            score -= 20.0
            continue
        texts.append((index, text))
        ai_match = _AI_ORIGIN_RE.search(text)
        if ai_match:
            issues.append(QualityIssue(
                "options", "ai_origin", f"第 {index} 个选项暴露 AI/模型来源", "error",
                (ai_match.group(0),)))
            score -= 30.0
    for left in range(len(texts)):
        for right in range(left + 1, len(texts)):
            first_index, first = texts[left]
            second_index, second = texts[right]
            ratio = difflib.SequenceMatcher(None, _normalize(first), _normalize(second)).ratio()
            if ratio >= 0.82:
                issues.append(QualityIssue(
                    "options", "duplicate_options",
                    f"第 {first_index} 与第 {second_index} 个选项疑似重复（相似度 {ratio:.2f}）",
                    "error", (first[:80], second[:80])))
                score -= 24.0
    return _bounded(score)


def _score_contracts(
    corpus: str,
    required: Sequence[str],
    forbidden: Sequence[str],
    issues: list[QualityIssue],
) -> float:
    score = 100.0
    missing = tuple(term for term in required if term not in corpus)
    hits = tuple(term for term in forbidden if term in corpus)
    if missing:
        issues.append(QualityIssue(
            "contracts", "missing_required_terms", "缺少合约必含词", "error", missing))
        score -= min(80.0, 80.0 * len(missing) / max(1, len(required)))
    if hits:
        issues.append(QualityIssue(
            "contracts", "forbidden_terms", "命中合约禁用词", "error", hits))
        score -= min(100.0, 55.0 + 15.0 * (len(hits) - 1))
    return _bounded(score)


def _score_coverage(
    dimension: str,
    corpus: str,
    terms: Sequence[str],
    issues: list[QualityIssue],
    *,
    require_any: bool = False,
) -> float:
    if not terms:
        return 100.0
    matched = tuple(term for term in terms if term in corpus)
    missing = tuple(term for term in terms if term not in corpus)
    coverage = len(matched) / len(terms)
    if require_any and not matched:
        code = "missing_anchor" if dimension == "anchors" else "missing_character"
        label = "锚点词" if dimension == "anchors" else "角色名"
        issues.append(QualityIssue(dimension, code, f"正文未命中任何{label}", "error", tuple(terms)))
        return 0.0
    if missing:
        label = {"world_context": "世界/上下文词", "anchors": "锚点词", "characters": "角色名"}[dimension]
        issues.append(QualityIssue(
            dimension, "partial_coverage", f"{label}覆盖不完整", "warning", missing))
    return _bounded(45.0 + 55.0 * coverage if matched else 20.0 * coverage)


def _score_style(story: str, issues: list[QualityIssue]) -> float:
    text = story.strip()
    if not text:
        return 0.0
    score = 100.0
    sentences = [part.strip() for part in _WORD_SPLIT_RE.split(text) if part.strip()]
    if len(sentences) >= 3:
        lengths = [len(part) for part in sentences]
        mean = sum(lengths) / len(lengths)
        variance = sum((length - mean) ** 2 for length in lengths) / len(lengths)
        if math.sqrt(variance) < 2.0:
            issues.append(QualityIssue("style", "uniform_sentence_length", "句长过于整齐，节奏机械"))
            score -= 15.0
        starts = [part[:4] for part in sentences if len(part) >= 4]
        if starts and len(starts) - len(set(starts)) >= 2:
            issues.append(QualityIssue("style", "repeated_sentence_opening", "多个句子以相同短语开头"))
            score -= 18.0
    if len(text) >= 80 and not _SENSORY_OR_ACTION_RE.search(text):
        issues.append(QualityIssue("style", "abstract_only", "长正文缺少动作或感官细节"))
        score -= 22.0
    if len(text) >= 140 and not _DIALOGUE_RE.search(text):
        issues.append(QualityIssue("style", "no_dialogue", "较长场景没有直接对白", "info"))
        score -= 5.0
    return _bounded(score)


def _score_state(story: str, modular_context: Any,
                 issues: list[QualityIssue]) -> float:
    """校验主角状态硬事实；区分不可逆状态与可演进状态。
    
    只拦截明确的历史否定，允许合理演进。
    """
    facts = _terms_from_keys(modular_context, ("state_hard_facts", "hard_facts"))
    if not facts or not story.strip():
        return 100.0
    score = 100.0
    
    for fact in facts:
        text = str(fact or "").strip()
        if not text:
            continue
        
        # 1. 死亡状态：不可逆，检查复活类明确违规
        if "已死亡" in text or text.startswith("主角已死亡"):
            revival_markers = ("复活", "活过来", "重生", "起死回生", "死而复生", "苏醒过来")
            if any(marker in story for marker in revival_markers):
                issues.append(QualityIssue(
                    "state", "death_reversal", "主角已死亡状态被违规复活（需特殊设定支撑）",
                    "error", (next((s for s in _WORD_SPLIT_RE.split(story) 
                                   if any(m in s for m in revival_markers)), story[:80]),)))
                score -= 60.0
        
        # 2. 重伤状态：可康复但需过程，拦截"并未受伤/毫发无伤"类直接否定
        elif "重伤" in text and text.startswith("主角当前身体状态：重伤"):
            direct_negation = ("并未受伤", "毫发无伤", "完全没有伤", "身体完好", "从未受伤")
            if any(marker in story for marker in direct_negation):
                issues.append(QualityIssue(
                    "state", "injury_negation", "重伤状态被直接否定（未经康复过程）",
                    "error", (next((s for s in _WORD_SPLIT_RE.split(story) 
                                   if any(m in s for m in direct_negation)), story[:80]),)))
                score -= 45.0
        
        # 3. 位置：可移动但需铺垫，检查"从未到过/并非在"类历史否定
        elif "当前位置" in text:
            location = text.split("：", 1)[-1].split("；")[0].strip()
            negation_patterns = (f"从未到过{location}", f"并非在{location}", f"不在{location}", 
                               f"{location}并非", "从未来过", "原本不在")
            if location and any(pattern in story for pattern in negation_patterns):
                issues.append(QualityIssue(
                    "state", "location_negation", f"位置状态被历史否定：{location}",
                    "error", (next((s for s in _WORD_SPLIT_RE.split(story) 
                                   if any(p in s for p in negation_patterns)), story[:80]),)))
                score -= 35.0
        
        # 4. 物品/装备/技能：可消耗/卸下，但不得写成"从未拥有"
        elif text.startswith(("主角当前持有物品：", "主角当前装备：", "主角已掌握能力：")):
            history_negation = ("从未拥有", "原本没有", "从未装备", "从未学过", "不会使用", "从未掌握")
            if any(marker in story for marker in history_negation):
                issues.append(QualityIssue(
                    "state", "asset_history_negation", "物品/装备/技能历史被否定",
                    "error", (next((s for s in _WORD_SPLIT_RE.split(story) 
                                   if any(m in s for m in history_negation)), story[:80]),)))
                score -= 35.0
        
        # 5. 关系：可演进但不得改写相识史
        elif text.startswith("主角既成关系："):
            relation_negation = ("从未相识", "素不相识", "从未见过", "原本不认识", "从来不认识")
            if any(marker in story for marker in relation_negation):
                issues.append(QualityIssue(
                    "state", "relationship_history_negation", "既成关系历史被否定",
                    "error", (next((s for s in _WORD_SPLIT_RE.split(story) 
                                   if any(m in s for m in relation_negation)), story[:80]),)))
                score -= 30.0
    
    return _bounded(score)


def _score_continuity(
    story: str,
    modular_context: Any,
    terms: Mapping[str, tuple[str, ...]],
    issues: list[QualityIssue],
) -> float:
    if not story.strip():
        return 0.0
    score = 100.0
    previous = _first_text(modular_context, ("previous_narrative", "prior_narrative", "recent_narrative"))
    continuity_terms = _unique((*terms["world"], *terms["anchors"], *terms["characters"]))
    if previous:
        previous_terms = tuple(term for term in continuity_terms if term in previous)
        if previous_terms and not any(term in story for term in previous_terms):
            issues.append(QualityIssue(
                "continuity", "context_disconnect", "正文与前文已知实体完全脱节", "warning",
                previous_terms[:8]))
            score -= 35.0
    abrupt_hits = tuple(marker for marker in _ABRUPT_MARKERS if marker in story)
    if len(abrupt_hits) >= 2:
        issues.append(QualityIssue(
            "continuity", "abrupt_transitions", "场景跳转标记过密", "warning", abrupt_hits))
        score -= 18.0
    for term in (*terms["anchors"], *terms["required"]):
        for match in re.finditer(re.escape(term), story):
            start = max(0, match.start() - 8)
            end = min(len(story), match.end() + 8)
            negation = _NEGATION_RE.search(story[start:end])
            if negation:
                issues.append(QualityIssue(
                    "continuity", "negated_fact", f"关键事实「{term}」附近出现否定表述",
                    "error", (story[start:end],)))
                score -= 45.0
                break
    return _bounded(score)


def _apply_judge(
    dimensions: dict[str, float],
    story: str,
    options: tuple[Any, ...],
    modular_context: Any,
    contracts: Any,
    judge_fn: JudgeFn,
    issues: list[QualityIssue],
) -> tuple[dict[str, float], dict[str, Any]]:
    payload = {
        "narrative": story,
        "options": list(options),
        "modular_context": modular_context,
        "contracts": contracts,
        "dimensions": dict(dimensions),
    }
    try:
        response = judge_fn(payload)
    except Exception as exc:
        issues.append(QualityIssue(
            "style", "judge_error", "注入裁判执行失败，已忽略", "warning",
            (type(exc).__name__,), "judge"))
        return dimensions, {"used": False, "status": "error", "error_type": type(exc).__name__}
    if not isinstance(response, Mapping):
        issues.append(QualityIssue(
            "style", "invalid_judge_response", "注入裁判返回形态无效，已忽略",
            "warning", source="judge"))
        return dimensions, {"used": False, "status": "invalid_shape"}
    scores = response.get("scores")
    evidence_map = response.get("evidence")
    if not isinstance(scores, Mapping) or not isinstance(evidence_map, Mapping) or not scores:
        issues.append(QualityIssue(
            "style", "invalid_judge_response", "注入裁判缺少 scores/evidence，已忽略",
            "warning", source="judge"))
        return dimensions, {"used": False, "status": "invalid_shape"}
    candidate_corpus = _candidate_corpus(story, options)
    accepted: dict[str, tuple[float, tuple[str, ...]]] = {}
    unsupported: list[str] = []
    for raw_dimension, raw_score in scores.items():
        dimension = str(raw_dimension)
        if dimension not in dimensions:
            unsupported.append(dimension)
            continue
        raw_evidence = evidence_map.get(dimension)
        evidence = _as_terms(raw_evidence)
        if (not evidence or any(quote not in candidate_corpus for quote in evidence)
                or not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool)):
            unsupported.append(dimension)
            continue
        accepted[dimension] = (_bounded(float(raw_score)), evidence)
    if unsupported or not accepted:
        issues.append(QualityIssue(
            "style", "unsupported_judge_evidence",
            "注入裁判证据无法由候选稿逐字支持，整份裁判结果已拒绝",
            "warning", tuple(unsupported), "judge"))
        return dimensions, {
            "used": False,
            "status": "unsupported_evidence",
            "rejected_dimensions": unsupported,
        }
    blended = dict(dimensions)
    for dimension, (judge_score, evidence) in accepted.items():
        blended[dimension] = round(dimensions[dimension] * 0.8 + judge_score * 0.2, 2)
        issues.append(QualityIssue(
            dimension, "judge_evidence", "已融合有逐字证据支持的注入裁判分",
            "info", evidence, "judge"))
    return blended, {"used": True, "status": "accepted", "dimensions": sorted(accepted)}


def _collect_requirements(modular_context: Any, contracts: Any) -> dict[str, tuple[str, ...]]:
    required = _terms_from_keys(contracts, (
        "required_terms", "must_include", "must_terms", "required", "必含词"))
    forbidden = _terms_from_keys(contracts, (
        "forbidden_terms", "forbidden", "ban_terms", "雷区词", "禁用词"))
    world = _unique((
        *_terms_from_keys(modular_context, ("world_terms", "context_terms", "lore_terms", "世界词")),
        *_terms_from_keys(contracts, ("world_terms", "context_terms")),
    ))
    anchors = _unique((
        *_terms_from_keys(modular_context, ("anchor_terms", "anchors", "锚点词")),
        *_terms_from_keys(contracts, ("anchor_terms", "anchors", "锚点词")),
    ))
    characters = _unique((
        *_terms_from_keys(modular_context, (
            "character_names", "roster_names", "active_names", "characters", "角色名")),
        *_terms_from_keys(contracts, ("character_names", "must_mention", "characters")),
    ))
    return {
        "required": required,
        "forbidden": forbidden,
        "world": world,
        "anchors": anchors,
        "characters": characters,
    }


def _terms_from_keys(value: Any, keys: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    wanted = set(keys)
    found: list[str] = []
    for key, item in value.items():
        if str(key) in wanted:
            found.extend(_as_terms(item))
        if isinstance(item, Mapping):
            found.extend(_terms_from_keys(item, keys))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for child in item:
                if isinstance(child, Mapping):
                    found.extend(_terms_from_keys(child, keys))
    return _unique(found)


def _as_terms(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in re.split(r"[,，、;；|\n]", value) if part.strip())
    if isinstance(value, Mapping):
        name = value.get("name") or value.get("term") or value.get("text")
        return (str(name).strip(),) if name and str(name).strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        terms: list[str] = []
        for item in value:
            terms.extend(_as_terms(item))
        return _unique(terms)
    text = str(value).strip()
    return (text,) if text else ()


def _first_text(value: Any, keys: Sequence[str]) -> str:
    if not isinstance(value, Mapping):
        return ""
    for key in keys:
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _candidate_corpus(story: str, options: Sequence[Any]) -> str:
    return "\n".join((story, *(_option_text(item) for item in options)))


def _option_text(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("text") or item.get("label") or item.get("content") or "").strip()
    return str(item or "").strip()


def _coerce_candidate(raw: Any, current_options: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]] | None:
    if isinstance(raw, str):
        story, options = raw.strip(), current_options
    elif isinstance(raw, Mapping):
        story = str(raw.get("narrative") or raw.get("text") or "").strip()
        option_value = raw.get("options", current_options)
        if isinstance(option_value, (str, bytes)) or not isinstance(option_value, Sequence):
            return None
        options = tuple(option_value)
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 2:
        story = str(raw[0] or "").strip()
        if isinstance(raw[1], (str, bytes)) or not isinstance(raw[1], Sequence):
            return None
        options = tuple(raw[1])
    else:
        return None
    # 选项允许为空（自由输入回合）：只要求正文非空，纯正文修订同样可验收。
    if not story:
        return None
    return story, options


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", str(text or "")).lower()


def _duplicates(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        normalized = _normalize(item)
        if normalized in seen and item not in duplicates:
            duplicates.append(item)
        seen.add(normalized)
    return duplicates


def _repeated_chunk(text: str, width: int = 12) -> str:
    sentences = [part.strip() for part in _WORD_SPLIT_RE.split(text) if part.strip()]
    sentence_counts: dict[str, int] = {}
    for sentence in sentences:
        normalized_sentence = _normalize(sentence)
        if normalized_sentence:
            sentence_counts[normalized_sentence] = sentence_counts.get(normalized_sentence, 0) + 1
    repeated_sentence = next(
        (sentence for sentence in sentences if sentence_counts.get(_normalize(sentence), 0) >= 3),
        "",
    )
    if repeated_sentence:
        return repeated_sentence[:width * 3]
    normalized = re.sub(r"\s+", "", text)
    if len(normalized) < width * 3:
        return ""
    counts: dict[str, int] = {}
    for index in range(0, len(normalized) - width + 1):
        chunk = normalized[index:index + width]
        counts[chunk] = counts.get(chunk, 0) + 1
    repeated = [chunk for chunk, count in counts.items() if count >= 3]
    return repeated[0] if repeated else ""


def _unique(items: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _digest(story: str, options: Sequence[Any], modular_context: Any, contracts: Any) -> str:
    payload = json.dumps(
        [story, list(options), modular_context, contracts], ensure_ascii=False,
        sort_keys=True, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "QUALITY_GATE_VERSION", "MAX_REFINEMENT_ROUNDS", "MAX_REFINEMENT_CALLS",
    "MAX_REFINEMENT_SECONDS", "DEFAULT_EPSILON", "DEFAULT_MIN_IMPROVEMENT",
    "PASS_SCORE", "DIMENSION_WEIGHTS", "QualityIssue", "ScoreCard",
    "RefinementRequest", "RefinementEvent", "RefinementResult", "score_turn",
    "bounded_refine",
]
