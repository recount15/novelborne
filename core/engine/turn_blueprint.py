# -*- coding: utf-8 -*-
"""回合蓝图机制（重构 M3，docs/REFACTOR_PLAN.md §1 Wave A 导演卷 / §10）。

蓝图 = 一回合的分段施工图（Wave A 导演卷产物）：回目级 beat/goal/conflict、
段合约（id/role/window/events/must_include/must_mention，段间事件互斥）、
锚点计划（stage + 动作/结果/因果词，词必须取自传入锚点文本）、涟漪收束、
世界节拍、悬念钩子、LOG 草稿与 6 个选项种子（4 金手指 + 2 性格）。

机制层红线：模型注入 callable（``str -> str``，离线可测）、不读用户配置、
不做 IO、不依赖 app/fate_engine。三层入口：
- :func:`build_director_prompt`：装配导演卷提示词（纯拼装）；
- :func:`parse_blueprint`：校验 + 交叉校验模型 JSON（中文错误清单）；
- :func:`synthesize_blueprint`：从 state 素材确定性合成机械兜底蓝图
  （零模型，任何模型失败时保底）。

选项种子的 ``factor`` 口径与 ``turn_grader.OPTION_FACTORS``（金手指/性格/剧情）
一致；分布约束「4 金手指 + 2 性格」与试卷 ``options.factor_split`` 对齐。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from core.engine.structured import FieldSpec, spec_prompt

Model = Callable[[str], Any]

# —— 蓝图口径常量（与试卷库/批改器同源约定）——
OPTION_SEED_COUNT = 6                    # 选项种子恰 6 颗
GF_SEEDS = 4                             # 金手指向种子数
PERSONA_SEEDS = 2                        # 性格向种子数
ALLOWED_FACTORS = ("金手指", "性格", "剧情")
BLUEPRINT_FACTORS = ("金手指", "性格")   # 蓝图种子只允许这两类（分布恰 4+2）
EVENT_OVERLAP_LIMIT = 0                  # 段间事件互斥：共现事件数上限（= 严禁共现）
OPTION_SEED_TEXT_MAX = 60
_TERM_STRIP_RE = re.compile(r"[，。；、！？\s\"'「」『』（）()：:，]+")
ANCHOR_JSON_KEYS = frozenset({
    "chapter", "title", "summary", "events", "characters", "world",
    "foreshadowing", "quotes", "ripple",
})

# 导演卷输出的声明式字段规格（FieldSpec 复用 structured 的规格渲染）。
DIRECTOR_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("beat", "str", required=True, min_len=2, max_len=60,
              hint="本回合节拍：一句话概括本回合核心事件"),
    FieldSpec("goal", "str", required=True, min_len=2, max_len=60,
              hint="本回合目标：主角想达成什么"),
    FieldSpec("conflict", "str", required=True, min_len=2, max_len=60,
              hint="本回合冲突：阻碍主角的力量或困境"),
    # segments/option_seeds 是**对象数组**：structured.validate 的 "list" 语义是
    # 字符串数组（逐项要求非空字符串），会把合法对象判成「必须是非空字符串」。
    # 故声明 "any"，数组形状与逐项结构由 parse_blueprint 自己严格校验。
    FieldSpec("segments", "any", required=True,
              hint="分段施工图数组（条数与试卷段数一致），每项为对象，含 "
                   "id/role/window/events/must_include/must_mention"),
    FieldSpec("anchor_plan", "dict", required=True,
              hint="锚点计划：stage/action_terms/result_terms/causal_phrase"),
    FieldSpec("ripple_resolution", "str", required=True, min_len=1, max_len=80,
              hint="涟漪与代价的收束方式（无涟漪时写「无」）"),
    FieldSpec("world_beats", "list", required=False, default=[],
              hint="世界节拍：世界书/传闻/势力动向（每项一句）"),
    FieldSpec("cliffhanger", "str", required=True, min_len=1, max_len=60,
              hint="悬念钩子：指向下回合可承接事件"),
    FieldSpec("log_draft", "dict", required=True,
              hint="日志草稿：player/golden_finger/world/beat 各一句"),
    FieldSpec("option_seeds", "any", required=True,
              hint="恰 6 颗选项种子的数组：4 金手指 + 2 性格；每项为对象，含 "
                   "factor/direction/preview"),
)


def _clean_text(value: Any, limit: int = 60) -> str:
    """压平为单行并截断（蓝图字段都是短句，超长即截断不报错）。"""
    return " ".join(str(value or "").strip().split())[:limit]


def _clean_list(value: Any, limit: int = 40, max_items: int = 12) -> list[str]:
    """清洗字符串数组：压平每项、去空、限量（蓝图词表都是短词）。"""
    items: list[str] = []
    for raw in value or ():
        text = _clean_text(raw, limit)
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def extract_anchor_terms(anchor_text: str, limit: int = 8) -> list[str]:
    """从锚点文本确定性抽取剧情证据词表（中文字串切分，≥2 字、按长度去重）。

    ``anchor_text`` 通常是 app 重新序列化的九字段 JSON；必须先解析并只取
    ``title/summary/events/characters/foreshadowing/quotes/ripple`` 的**值**，
    不能把 JSON 键名（如 ``foreshadowing``/``quotes``）误当作剧情词。解析
    失败时才退化为纯文本切分。
    """
    raw_values: list[str] = []
    text = str(anchor_text or "")
    try:
        from core.engine.structured import extract_json

        data = extract_json(text)
        if isinstance(data, Mapping):
            for key in ("title", "summary", "events", "characters",
                        "foreshadowing", "quotes", "world", "ripple"):
                value = data.get(key)
                if isinstance(value, (list, tuple)):
                    raw_values.extend(str(item) for item in value)
                elif value:
                    raw_values.append(str(value))
    except (TypeError, ValueError):
        raw_values = [text]
    candidates = [term for value in raw_values for term in _TERM_STRIP_RE.split(value)
                 if len(term) >= 2 and term not in ANCHOR_JSON_KEYS]
    terms: list[str] = []
    for term in sorted(set(candidates), key=len, reverse=True):
        if any(term in kept or kept in term for kept in terms):
            continue
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


@dataclass
class SegmentPlan:
    """蓝图分段施工项：对应试卷一个段空的填空要求。"""

    id: str
    role: str
    window: tuple[int, int]
    events: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)


@dataclass
class Blueprint:
    """一回合的完整蓝图（导演卷产物 / 机械兜底产物，同一形状）。"""

    beat: str
    goal: str
    conflict: str
    segments: list[SegmentPlan]
    anchor_plan: dict[str, Any] = field(default_factory=dict)
    ripple_resolution: str = ""
    world_beats: list[str] = field(default_factory=list)
    cliffhanger: str = ""
    log_draft: dict[str, str] = field(default_factory=dict)
    option_seeds: list[dict[str, str]] = field(default_factory=list)
    origin: str = "model"   # model（导演卷）/ synthesized（机械兜底）

    def segment(self, segment_id: str) -> Optional[SegmentPlan]:
        """按 id 取段（段卷提交时的对位查找）。"""
        for plan in self.segments:
            if plan.id == segment_id:
                return plan
        return None


def _normalize_segments(raw_segments: Sequence[Any], expected_count: int,
                        errors: list[str]) -> list[dict[str, Any]]:
    """归一并校验 segments 数组（形状 + 段数一致 + 事件互斥），错误写进 errors。"""
    if expected_count and len(raw_segments) != expected_count:
        errors.append(
            f"segments 段数必须与试卷一致（{expected_count} 段，当前 {len(raw_segments)} 段）")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_segments, 1):
        if not isinstance(raw, Mapping):
            errors.append(f"segments 第 {index} 段必须是对象（含 id/role/window/events）")
            continue
        seg_id = _clean_text(raw.get("id"), 30) or f"seg{index}"
        role = _clean_text(raw.get("role"), 30) or f"第{index}段"
        window = raw.get("window")
        if (not isinstance(window, (list, tuple)) or len(window) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in window)):
            errors.append(f"segments 第 {index} 段 window 必须是 [下界, 上界] 整数对")
            window = (0, 0)
        events = _clean_list(raw.get("events"))
        if not events:
            errors.append(f"segments 第 {index} 段 events 不得为空（每段至少一个专属事件）")
        normalized.append({
            "id": seg_id, "role": role, "window": (int(window[0]), int(window[1])),
            "events": events,
            "must_include": _clean_list(raw.get("must_include")),
            "must_mention": _clean_list(raw.get("must_mention")),
        })
    # 段间事件互斥：同一事件文本出现在多段即违规
    seen: dict[str, int] = {}
    for index, seg in enumerate(normalized, 1):
        for event in seg["events"]:
            if event in seen and seen[event] != index:
                errors.append(
                    f"事件「{event}」同时分给了第 {seen[event]} 段与第 {index} 段（段间事件必须互斥）")
            else:
                seen.setdefault(event, index)
    return normalized


def _normalize_anchor_plan(raw: Any, anchor_terms: Sequence[str],
                           errors: list[str]) -> dict[str, Any]:
    """归一 anchor_plan：词表必须 ⊆ 传入锚点词表（不得编造词）。"""
    if not isinstance(raw, Mapping):
        errors.append("anchor_plan 必须是对象（stage/action_terms/result_terms/causal_phrase）")
        return {}
    allowed = {term for term in (anchor_terms or ()) if term}
    plan: dict[str, Any] = {}
    stage = _clean_text(raw.get("stage"), 20) or "setup"
    plan["stage"] = stage if stage in ("setup", "climax", "free") else "setup"
    for key in ("action_terms", "result_terms"):
        terms = _clean_list(raw.get(key))
        unknown = [term for term in terms if allowed and term not in allowed]
        if unknown:
            errors.append(
                f"anchor_plan.{key} 含锚点文本之外的词：「{'」、「'.join(unknown[:5])}」"
                "（词必须取自锚点文本）")
        plan[key] = terms
    plan["causal_phrase"] = _clean_text(raw.get("causal_phrase"), 60)
    return plan


def _normalize_option_seeds(raw_seeds: Sequence[Any], errors: list[str]) -> list[dict[str, str]]:
    """归一 option_seeds：恰 6 颗、factor ∈ 金手指/性格、字段非空。"""
    seeds: list[dict[str, str]] = []
    for index, raw in enumerate(raw_seeds or (), 1):
        if not isinstance(raw, Mapping):
            errors.append(f"option_seeds 第 {index} 颗必须是对象（factor/direction/preview）")
            continue
        factor = _clean_text(raw.get("factor"), 10)
        if factor not in BLUEPRINT_FACTORS:
            errors.append(
                f"option_seeds 第 {index} 颗 factor 必须是金手指/性格（当前「{factor or '缺失'}」）")
        direction = _clean_text(raw.get("direction"), OPTION_SEED_TEXT_MAX)
        if not direction:
            errors.append(f"option_seeds 第 {index} 颗 direction 不得为空")
        seeds.append({
            "factor": factor, "direction": direction,
            "preview": _clean_text(raw.get("preview"), 60),
        })
    if len(seeds) != OPTION_SEED_COUNT:
        errors.append(f"option_seeds 必须恰 {OPTION_SEED_COUNT} 颗（当前 {len(seeds)} 颗）")
        return seeds
    gf_count = sum(1 for seed in seeds if seed["factor"] == "金手指")
    persona_count = sum(1 for seed in seeds if seed["factor"] == "性格")
    if (gf_count, persona_count) != (GF_SEEDS, PERSONA_SEEDS):
        errors.append(
            f"option_seeds 因素分布必须为 {GF_SEEDS} 金手指 + {PERSONA_SEEDS} 性格"
            f"（当前 {gf_count} 金手指 + {persona_count} 性格）")
    return seeds


def parse_blueprint(raw: Any, *, segment_count: int = 0,
                    active_names: Sequence[str] = (),
                    anchor_terms: Sequence[str] = ()) -> tuple[Optional[Blueprint], list[str]]:
    """校验并归一导演卷 JSON：返回 ``(Blueprint, 中文错误清单)``。

    校验分三层：形状（必填/类型）→ 归一（压平/截断）→ 交叉校验（段数一致、
    事件互斥、角色名 ⊆ 传入 active_names、option_seeds 恰 6 且 4+2）。
    有任一错误即返回 ``(None, errors)``，由调用方走 :func:`synthesize_blueprint`。
    """
    errors: list[str] = []
    if not isinstance(raw, Mapping):
        return None, ["导演卷输出必须是 JSON 对象"]
    for key in ("beat", "goal", "conflict", "segments", "anchor_plan",
                "ripple_resolution", "cliffhanger", "log_draft", "option_seeds"):
        if key not in raw or raw[key] is None:
            errors.append(f"导演卷缺少必填字段 {key}")
    # 数组形状自校验：DIRECTOR_SPECS 里 segments/option_seeds 声明为 any
    # （structured.validate 的 list 语义是字符串数组，会把合法对象项判成
    # 「必须是非空字符串」），所以对象数组的形状必须在这里自己把关。
    for key in ("segments", "option_seeds"):
        if not isinstance(raw.get(key), list):
            errors.append(f"{key} 必须是数组")
    if not isinstance(raw.get("world_beats", []), list):
        errors.append("world_beats 必须是数组")
    if errors:
        return None, errors

    segments = _normalize_segments(raw.get("segments") or [], segment_count, errors)
    anchor_plan = _normalize_anchor_plan(raw.get("anchor_plan"), anchor_terms, errors)
    option_seeds = _normalize_option_seeds(raw.get("option_seeds") or (), errors)

    names = {str(name or "").strip() for name in (active_names or ()) if str(name or "").strip()}
    for index, seg in enumerate(segments, 1):
        unknown = [name for name in seg["must_mention"] if names and name not in names]
        if unknown:
            errors.append(
                f"segments 第 {index} 段 must_mention 含未传入的角色名："
                f"「{'」、「'.join(unknown[:5])}」（须取自本回合在场角色）")

    log_raw = raw.get("log_draft")
    log_draft: dict[str, str] = {}
    if isinstance(log_raw, Mapping):
        for key in ("player", "golden_finger", "world", "beat"):
            log_draft[key] = _clean_text(log_raw.get(key), 60)
    else:
        errors.append("log_draft 必须是对象（player/golden_finger/world/beat）")

    if errors:
        return None, errors
    return Blueprint(
        beat=_clean_text(raw.get("beat")),
        goal=_clean_text(raw.get("goal")),
        conflict=_clean_text(raw.get("conflict")),
        segments=[SegmentPlan(
            id=seg["id"], role=seg["role"], window=seg["window"],
            events=seg["events"], must_include=seg["must_include"],
            must_mention=seg["must_mention"]) for seg in segments],
        anchor_plan=anchor_plan,
        ripple_resolution=_clean_text(raw.get("ripple_resolution"), 80),
        world_beats=_clean_list(raw.get("world_beats")),
        cliffhanger=_clean_text(raw.get("cliffhanger")),
        log_draft=log_draft,
        option_seeds=option_seeds,
        origin="model",
    ), []


def build_director_prompt(*, paper_label: str, stage: str, segment_count: int,
                          target_chars: int, action: str, context_tail: str,
                          active_names: Sequence[str] = (),
                          anchor_text: str = "",
                          system_brief: str = "",
                          world_beats_hint: str = "",
                          ripple_hint: str = "", gf_hint: str = "",
                          persona_hint: str = "",
                          quest_hint: str = "") -> str:
    """装配导演卷提示词（@@KEY@@ 占位符由 core.prompts.render 渲染）。

    机制层只做参数拼装；文案本体在 ``assets/prompts/paper_director.md``。
    ``system_brief`` 为作品设定与系统规则摘要（v2.0.3 注入，防跨书乱入）。
    ``quest_hint`` 为进行中任务块（v2.0.4 注入，蓝图节拍须考虑任务时限）。
    """
    from core.prompts import render  # 局部导入：assets 数据加载层，机制层允许

    names = "、".join(str(name or "").strip() for name in (active_names or ())
                      if str(name or "").strip()) or "（本回合无在场角色）"
    anchor_block = str(anchor_text or "").strip()[:600] or "（本回合无锚点）"
    stage_labels = {"setup": "铺垫（锚点允许 pending/mentioned/partial）",
                    "climax": "收束（锚点必须 fulfilled 落地）",
                    "free": "自由（锚点仅供参考，不强制收束）"}
    return render(
        "paper_director.md",
        PAPER_LABEL=str(paper_label or "试卷"),
        STAGE=stage_labels.get(stage, stage),
        SEGMENT_COUNT=int(segment_count),
        TARGET_CHARS=int(target_chars),
        ACTION=str(action or "（玩家自由行动）").strip()[:300],
        CONTEXT=str(context_tail or "").strip()[-2400:] or "（开局首轮，无前文）",
        ACTIVE_NAMES=names,
        SYSTEM=str(system_brief or "").strip()[:3000] or "（未提供作品设定摘要）",
        ANCHOR_TEXT=anchor_block,
        ANCHOR_TERMS="、".join(extract_anchor_terms(anchor_text)) or "（无锚点词）",
        WORLD_BEATS=str(world_beats_hint or "").strip()[:200] or "（无世界书/传闻命中）",
        RIPPLE=str(ripple_hint or "").strip()[:300] or "（无涟漪压力）",
        QUEST=str(quest_hint or "").strip()[:400] or "（无进行中任务：不要虚构任务推进）",
        GF=str(gf_hint or "").strip()[:120] or "（金手指未激活或未设定）",
        PERSONA=str(persona_hint or "").strip()[:120] or "（未设定性格）",
        FORMAT_BLOCK=spec_prompt(DIRECTOR_SPECS),
        GF_SEEDS=GF_SEEDS,
        PERSONA_SEEDS=PERSONA_SEEDS,
    )


def synthesize_blueprint(*, segment_roles: Sequence[tuple[str, tuple[int, int]]],
                         action: str, anchor_terms: Sequence[str] = (),
                         active_names: Sequence[str] = (),
                         gf_hint: str = "", persona_hint: str = "") -> Blueprint:
    """机械兜底蓝图：从 state 素材确定性合成（零模型，任何模型失败时保底）。

    ``segment_roles`` 为 ``(role, window)`` 列表（来自试卷 Paper.segments）；
    必含词取锚点词与玩家行动关键词，保证兜底段可构造（含必含词的最小合成句）。
    """
    terms = [str(term or "").strip() for term in (anchor_terms or ()) if str(term or "").strip()]
    names = [str(name or "").strip() for name in (active_names or ())
             if str(name or "").strip()]
    action_text = _clean_text(action, 40) or "推进当前局面"
    gf_text = _clean_text(gf_hint, 30) or "金手指"
    persona_text = _clean_text(persona_hint, 30) or "主角心性"
    anchor_head = terms[0] if terms else "当前锚点"

    segments: list[SegmentPlan] = []
    for index, item in enumerate(segment_roles or (), 1):
        role, window = (item if isinstance(item, (tuple, list)) else (item, (100, 300)))
        low = max(60, int(window[0]) // 2)
        high = max(low + 60, int(window[1]))
        include: list[str] = []
        if index == 1 and terms:
            include.append(terms[0])
        if index == len(segment_roles) and terms:
            include.append(terms[-1])
        segments.append(SegmentPlan(
            id=f"seg{index}", role=str(role or f"第{index}段"), window=(low, high),
            events=[f"第{index}段主事件：{action_text}（段 {index}）"],
            must_include=include, must_mention=names[:2] if index == 1 else [],
        ))

    option_seeds: list[dict[str, str]] = []
    for i in range(GF_SEEDS):
        option_seeds.append({
            "factor": "金手指",
            "direction": f"以{gf_text}的另一种用法应对：{action_text}（变体 {i + 1}）",
            "preview": "触发对应代价或冷却",
        })
    for i in range(PERSONA_SEEDS):
        option_seeds.append({
            "factor": "性格",
            "direction": f"依{persona_text}行事：{action_text}（性格 {i + 1}）",
            "preview": "关系或局势出现相应变化",
        })

    return Blueprint(
        beat=f"围绕「{anchor_head}」推进回合",
        goal=f"回应玩家行动：{action_text}",
        conflict="局面阻力与代价",
        segments=segments,
        anchor_plan={
            "stage": "setup",
            "action_terms": terms[:2],
            "result_terms": terms[:1],
            "causal_phrase": f"{anchor_head} 因此落定" if terms else "事件因此落定",
        },
        ripple_resolution="按当前涟漪等级结算代价",
        world_beats=[],
        cliffhanger=f"{anchor_head} 的后续影响尚未显形",
        log_draft={
            "player": action_text,
            "golden_finger": gf_text,
            "world": anchor_head,
            "beat": f"围绕{anchor_head}推进",
        },
        option_seeds=option_seeds,
        origin="synthesized",
    )


__all__ = [
    "Blueprint", "SegmentPlan", "DIRECTOR_SPECS",
    "OPTION_SEED_COUNT", "GF_SEEDS", "PERSONA_SEEDS", "BLUEPRINT_FACTORS",
    "ALLOWED_FACTORS", "OPTION_SEED_TEXT_MAX",
    "build_director_prompt", "parse_blueprint", "synthesize_blueprint",
    "extract_anchor_terms",
]
