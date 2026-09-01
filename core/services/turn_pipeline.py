# -*- coding: utf-8 -*-
"""回合试卷管线中台门面（重构 M3，docs/REFACTOR_PLAN.md §1/§3/§11）。

把「一回合正文」从单卷长文改造为**试卷化填空 + 双线并发**：

  Wave A 导演卷（1 次）  → 蓝图（分段施工图 + 6 颗选项种子）
  Wave B 段卷 ∥ 选项卷   → 单层扁平并发（严禁嵌套 run_parallel）
  段级批改 → 逐空重填 ≤2 → 确定性兜底段
  组装 → format_gate → TurnResult

分层红线（standards/01-architecture.md）：本模块是 services 门面，只编排
engine 机制与 core.prompts 文案；不碰 app/server/UI 状态，不做 IO，模型
经 ``model_fn`` 注入（缺省走 distill 通道并占用回合优先级并发额度）。

档位覆盖：MVP 支持 setup/climax 两个 stage 的既有试卷；``free`` 阶段或
缺卷时返回 :data:`LEGACY` 信号，由调用方走旧单卷路径（不静默降级）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from core.engine import (
    agent_refill,
    papers,
    parallel,
    structured,
    turn_blueprint,
    turn_composer,
    turn_grader,
)
from core.engine.distill import distill_model
from core.prompts import render
from core.services import options_service
from core.services import answer_polish_service

Model = Callable[[str], Any]

#: 本回合不适用试卷管线（free 阶段或缺卷）——调用方须走 legacy 单卷路径。
LEGACY = "legacy"

#: 段卷重填预算上限（每空独立，与 papers 的 agent 档位无关）。
REFILL_ATTEMPTS = 2


class TurnUpstreamError(RuntimeError):
    """传输层全线失败（导演卷与段卷都没拿到任何可用答卷）。

    与「模型答卷不合格」区分：不合格走批改重填与确定性兜底，绝不抛错；
    只有连接/额度/鉴权级失败才抛此异常，由 app 层回滚整回合。
    """


@dataclass
class TurnResult:
    """一回合的管线产物（形状与 app 既有回合状态键一一对应）。"""

    narrative: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)
    log_line: str = ""
    scene_validation: dict[str, Any] = field(default_factory=dict)
    agent_meta: dict[str, Any] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)
    blueprint: Optional[turn_blueprint.Blueprint] = None
    paper_key: str = ""
    options_source: str = ""


def _text(value: Any, limit: int = 400) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _segment_contracts(paper: papers.Paper,
                       plan: turn_blueprint.Blueprint) -> list[turn_grader.SegmentContract]:
    """把试卷段窗口 + 蓝图段合约合成批改用的 SegmentContract 列表。

    段数以**试卷**为准（试卷是契约来源）；蓝图段不足时按空合约补齐，
    多余的蓝图段忽略——模型不能通过多写段绕过试卷的窗口约束。
    """
    contracts: list[turn_grader.SegmentContract] = []
    anchor_stage_slot = papers.anchor_slot_for(paper.stage)
    for index, seg in enumerate(paper.segments):
        plan_seg = plan.segments[index] if index < len(plan.segments) else None
        must_include = list(getattr(plan_seg, "must_include", []) or [])
        must_mention = list(getattr(plan_seg, "must_mention", []) or [])
        # 锚点收束段：把蓝图锚点计划的动作/结果词交给批改器做落地校验。
        anchor_terms: list[str] = []
        require_climax = False
        if anchor_stage_slot and anchor_stage_slot in (seg.slots or ()):
            anchor_plan = plan.anchor_plan or {}
            anchor_terms = [
                _text(term, 40)
                for term in (list(anchor_plan.get("action_terms") or [])
                             + list(anchor_plan.get("result_terms") or []))
                if _text(term, 40)
            ]
            require_climax = paper.stage == "climax"
        contracts.append(turn_grader.SegmentContract(
            index=index + 1,
            role=seg.role,
            window=tuple(seg.window),
            must_include=must_include,
            must_mention=must_mention,
            anchor_terms=anchor_terms,
            anchor_require_climax=require_climax,
        ))
    return contracts


def build_segment_prompt(paper: papers.Paper, plan: turn_blueprint.Blueprint,
                         contract: turn_grader.SegmentContract, *,
                         action: str = "", context_blocks: str = "") -> str:
    """装配单个段卷提示词（段间事件互斥靠 EXCLUDED_EVENTS 显式排除）。"""
    index = contract.index - 1
    plan_seg = plan.segments[index] if index < len(plan.segments) else None
    events = list(getattr(plan_seg, "events", []) or [])
    excluded: list[str] = []
    for other_index, other in enumerate(plan.segments):
        if other_index == index:
            continue
        excluded.extend(list(getattr(other, "events", []) or []))
    low, high = contract.window
    anchor_plan = plan.anchor_plan or {}
    anchor_requirement = "（本段不承担锚点落地）"
    if contract.anchor_terms:
        anchor_requirement = (
            "本段必须让锚点落地：动作词 %s；结果词 %s；因果句式「%s」。"
            % ("、".join(anchor_plan.get("action_terms") or []) or "（无）",
               "、".join(anchor_plan.get("result_terms") or []) or "（无）",
               _text(anchor_plan.get("causal_phrase"), 60) or "因此")
        )
        if contract.anchor_require_climax:
            anchor_requirement += "本段为收束段：事件、结果与因果三要素必须齐全。"
    return render(
        "paper_segment.md",
        SEGMENT_ID=str(contract.index),
        SEGMENT_ROLE=contract.role,
        WINDOW="%d–%d" % (low, high),
        BLUEPRINT_BRIEF="节拍：%s；目标：%s；冲突：%s" % (
            plan.beat or "（未给）", plan.goal or "（未给）", plan.conflict or "（未给）"),
        EVENTS="、".join(events) or "（按段职责自行铺陈）",
        EXCLUDED_EVENTS="、".join(excluded) or "（无）",
        MUST_INCLUDE="、".join(contract.must_include) or "（无硬性必含词）",
        MUST_MENTION="、".join(contract.must_mention) or "（无点名要求）",
        ANCHOR_REQUIREMENT=anchor_requirement,
        ACTION=_text(action, 300) or "（玩家自由行动）",
        CONTEXT=str(context_blocks or "").strip()[-1200:] or "（开局首轮，无前文）",
    )


def _fallback_segment(paper: papers.Paper,
                      plan: turn_blueprint.Blueprint) -> Callable[[Any], str]:
    """确定性兜底段工厂：零模型，按窗口下限铺陈蓝图素材填满窗口。"""

    def _make(contract: Any) -> str:
        index = int(getattr(contract, "index", 0) or 0)
        window = tuple(getattr(contract, "window", (0, 0)) or (0, 0))
        role = str(getattr(contract, "role", "") or "推进")
        plan_seg = plan.segments[index - 1] if 0 < index <= len(plan.segments) else None
        events = list(getattr(plan_seg, "events", []) or []) or [plan.beat or "局面推进"]
        mentions = list(getattr(contract, "must_mention", []) or [])
        pieces = [f"（{role}）"]
        if mentions:
            pieces.append("；".join(f"{name}就地应对" for name in mentions) + "。")
        pieces.append("；".join(events) + "。")
        for term in list(getattr(contract, "must_include", []) or []):
            pieces.append(f"{term}随之显形。")
        for term in list(getattr(contract, "anchor_terms", []) or []):
            pieces.append(f"{term}因此落定。")
        text = "".join(pieces)
        low = int(window[0] or 0)
        if low and len(text) < low:
            filler = plan.cliffhanger or plan.beat or "局面继续推进"
            while len(text) < low:
                text += filler + "。"
        high = int(window[1] or 0)
        return text[:high] if high else text

    return _make


def _director_blueprint(model: Model, paper: papers.Paper, *, action: str,
                        context_blocks: str, active_members: Sequence[Any],
                        anchor_text: str, state: Mapping[str, Any],
                        attempts: int) -> tuple[turn_blueprint.Blueprint, dict[str, Any]]:
    """跑导演卷；任何失败（传输/解析/交叉校验）都落到机械兜底蓝图。"""
    names = [
        str((item.get("name") if isinstance(item, Mapping) else item) or "").strip()
        for item in (active_members or ())
    ]
    names = [name for name in names if name]
    anchor_terms = turn_blueprint.extract_anchor_terms(anchor_text)
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    abilities = memory.get("abilities") if isinstance(memory.get("abilities"), Mapping) else {}
    gf_box = abilities.get("golden_finger") if isinstance(abilities.get("golden_finger"), Mapping) else {}
    ripple = state.get("last_ripple") if isinstance(state.get("last_ripple"), Mapping) else {}
    prompt = turn_blueprint.build_director_prompt(
        paper_label=paper.label, stage=paper.stage,
        segment_count=len(paper.segments), target_chars=paper.target_chars,
        action=action, context_tail=context_blocks,
        active_names=names, anchor_text=anchor_text,
        world_beats_hint=_text(state.get("nemesis_rumor"), 200),
        ripple_hint="%s 有效积势 %s/%s" % (
            ripple.get("level") or "L0", ripple.get("effective_total") or 0,
            ripple.get("threshold") or 0),
        gf_hint=_text(gf_box.get("name") or state.get("golden_finger"), 120),
        persona_hint=_text(state.get("persona"), 120))
    # 传输层失败同样落机械兜底蓝图：导演卷是增值环节，不得因它掐死整回合；
    # 段卷才是回合正文的生命线（只有段卷全线失败才抛 TurnUpstreamError）。
    try:
        data, meta = structured.structured_call(
            model, prompt, turn_blueprint.DIRECTOR_SPECS, attempts=attempts)
    except Exception as exc:  # noqa: BLE001
        data, meta = None, {"transport_error": str(exc)}
    if data:
        plan, errors = turn_blueprint.parse_blueprint(
            data, segment_count=len(paper.segments),
            active_names=names, anchor_terms=anchor_terms)
        if plan is not None and not errors:
            return plan, {"origin": "model", "meta": meta}
        meta = dict(meta or {})
        meta["blueprint_errors"] = list(errors or [])
    return (
        turn_blueprint.synthesize_blueprint(
            segment_roles=[(seg.role, tuple(seg.window)) for seg in paper.segments],
            action=action, anchor_terms=anchor_terms, active_names=names,
            gf_hint=_text(gf_box.get("name") or state.get("golden_finger"), 30),
            persona_hint=_text(state.get("persona"), 30)),
        {"origin": "synthesized", "meta": meta},
    )


def run_turn(state: Mapping[str, Any], client, model: str,
             request_kwargs: dict | None = None, provider: str = "deepseek", *,
             message: str = "", system_prompt: str = "", context_blocks: str = "",
             active_members: Optional[Sequence[Any]] = None, anchor_text: str = "",
             factors: Optional[Sequence[Any]] = None, model_fn: Optional[Model] = None,
             tier: Optional[int] = None,
             attempts: int = 2) -> TurnResult | str:
    """跑完一回合试卷管线；返回 :class:`TurnResult` 或 :data:`LEGACY` 信号。

    - ``tier`` 缺省取 ``state["paper_tier"]``，再退回按 story_richness 映射；
    - ``free`` 阶段或缺卷 → 返回 :data:`LEGACY`（调用方走旧单卷路径）；
    - 模型不合格一律走批改重填/兜底，不抛错；只有传输层全线失败才抛
      :class:`TurnUpstreamError`。
    """
    snapshot = dict(state or {})
    resolved_tier = int(tier or snapshot.get("paper_tier") or 0)
    if resolved_tier < 1:
        resolved_tier = papers.map_legacy_richness(snapshot.get("story_richness"))
    # 碎锚判定走 break_anchor 唯一入口（app 全链路同源）：relay 接通或
    # 碎锚任务完成后 stage=free，本管线不接管（返回 LEGACY 走旧单卷）。
    from core.engine import break_anchor  # 局部导入：避免包级循环
    try:
        shattered = bool(break_anchor.shattered_from(snapshot))
    except Exception:  # noqa: BLE001 判定失败按未碎锚处理（不误降级）
        shattered = False
    stage = papers.stage_for(
        int(snapshot.get("chapter_round") or 1),
        int(snapshot.get("turn_budget") or 0),
        shattered)
    if stage not in ("setup", "climax"):
        return LEGACY
    try:
        paper = papers.get_paper(resolved_tier, stage)
    except ValueError:
        # 档位越界/试卷缺失：不静默降级为别的档，交回 legacy 单卷路径。
        return LEGACY
    if paper is None:
        return LEGACY

    budgeted = parallel.budget_model(
        model_fn or (lambda p: distill_model(client, model, p, request_kwargs, provider)),
        parallel.PRIORITY_TURN)

    # —— Wave A：导演卷（1 次，失败落机械兜底蓝图）——
    plan, plan_meta = _director_blueprint(
        budgeted, paper, action=message, context_blocks=context_blocks,
        active_members=active_members or (), anchor_text=anchor_text,
        state=snapshot, attempts=attempts)
    contracts = _segment_contracts(paper, plan)

    # —— Wave B：段卷 ∥ 选项卷（单层扁平并发；选项卷内部不得再并发）——
    segment_jobs = [
        (lambda prompt=build_segment_prompt(
            paper, plan, contract, action=message, context_blocks=context_blocks):
         budgeted(prompt))
        for contract in contracts
    ]
    factors_block = "\n".join(
        f"- {seed.get('factor')}：{seed.get('direction')}"
        for seed in (plan.option_seeds or ())
        if isinstance(seed, Mapping)
    )

    def _options_job():
        return options_service.generate_options(
            client, model, request_kwargs, provider, action=message,
            factors_block=factors_block, context_tail=context_blocks,
            narrative="", factors=factors or [], model_fn=budgeted,
            attempts=attempts)

    jobs = list(segment_jobs) + [_options_job]
    assert len(jobs) <= parallel.HARD_LIMIT, "Wave B 作业数不得超过并发硬上限"
    results = parallel.run_parallel(jobs, parallel.PRIORITY_TURN)
    segment_results = results[:len(segment_jobs)]
    options_result = results[len(segment_jobs)]

    drafts = [
        str(getattr(item, "value", "") or "") if getattr(item, "ok", False) else ""
        for item in segment_results
    ]
    if not any(drafts):
        raise TurnUpstreamError("段卷全线失败：模型服务不可用")

    # —— 段级批改 → 逐空重填 ≤2 → 确定性兜底段 ——
    refilled = agent_refill.run_refill_loop(
        contracts, drafts, model=budgeted, attempts=REFILL_ATTEMPTS,
        fallback_factory=_fallback_segment(paper, plan))
    segments = [str(text or "") for text in refilled["answers"]]

    options_payload = (
        getattr(options_result, "value", None)
        if getattr(options_result, "ok", False) else None) or {}
    options = list(options_payload.get("options") or [])
    options_source = str(options_payload.get("source") or "none")
    if not options:
        options = _seed_options(plan)
        options_source = "blueprint_seeds"

    display = turn_composer.compose_display(segments, options)
    narrative = str(display.get("narrative") or "\n\n".join(segments))
    gate = turn_grader.format_gate(narrative)
    format_fallback = False
    if not gate.get("valid"):
        # 整体格式门失败说明至少一个段混入 JSON/围栏/系统标记。不能把同一批
        # 污染段重新拼一遍；改为对**污染段**逐段检测，命中的段换成确定性
        # 兜底段，再重组并重跑唯一整回合门禁。
        make_fallback = _fallback_segment(paper, plan)
        repaired_segments: list[str] = []
        for contract, segment in zip(contracts, segments):
            segment_gate = turn_grader.format_gate(segment)
            repaired_segments.append(
                segment if segment_gate.get("valid") else make_fallback(contract))
        segments = repaired_segments
        display = turn_composer.compose_display(segments, options)
        narrative = str(display.get("narrative") or "\n\n".join(segments))
        gate = turn_grader.format_gate(narrative)
        format_fallback = True

    # —— 后处理整合卷：不设质量门禁，只负责把答卷零散结构整合成自然正文。
    #     采用流程化 prompt（事实锁定→接缝整合→文学润色→输出整理）；失败、
    #     空返回或格式污染均安全回退初步组装稿，不阻断回合、不改写状态。
    polish_meta: dict[str, Any] = {"enabled": True, "used": False}
    quest_box = snapshot.get("quest") if isinstance(snapshot.get("quest"), Mapping) else {}
    break_box = snapshot.get("break_anchor") if isinstance(snapshot.get("break_anchor"), Mapping) else {}
    memory = snapshot.get("state_memory") if isinstance(snapshot.get("state_memory"), Mapping) else {}
    mechanism_parts = [
        "锚点阶段=%s；锚点状态=%s" % (paper.stage, _text((snapshot.get("scene_validation") or {}).get("anchor"), 120)),
        "角色状态=在场角色按角色卷推进，禁止改写角色底色",
        "金手指=%s；冷却/代价以已生效状态为准" % _text(snapshot.get("golden_finger") or "未设定", 80),
        "涟漪=%s；相容K=%s" % (_text((snapshot.get("last_ripple") or {}).get("level"), 30),
                              _text(snapshot.get("last_compatibility_k"), 20)),
        "宿敌=%s" % ("已启用，公开信息受限" if snapshot.get("nemesis") else "未启用"),
        "玩家铁律=已由上游相关性选择后注入的内容必须保留，不得被剧情否定",
        "记忆地点=%s；当前目标=%s" % (
            _text((memory.get("location") or {}).get("name"), 60),
            _text((memory.get("goals") or {}).get("current"), 100)),
    ]
    if quest_box.get("status") == "active":
        mechanism_parts.append("任务 active：目标=%s；要求=%s；必须让本回合可观察推进或明确受阻" % (
            _text(quest_box.get("goal"), 120), _text(quest_box.get("requirements"), 180)))
    else:
        mechanism_parts.append("任务 inactive：不要虚构任务推进")
    if break_box.get("status") == "active":
        stages = break_box.get("stages") if isinstance(break_box.get("stages"), list) else []
        try:
            stage_index = int(break_box.get("current_stage") or 0)
        except (TypeError, ValueError):
            stage_index = 0
        stage = stages[stage_index] if 0 <= stage_index < len(stages) and isinstance(stages[stage_index], Mapping) else {}
        mechanism_parts.append("碎锚任务 active：阶段=%s；要求=%s；必须让本回合可观察推进或明确受阻" % (
            _text(stage.get("title"), 80), _text(stage.get("requirement"), 160)))
    else:
        mechanism_parts.append("碎锚任务 inactive：不要虚构碎锚阶段")
    mechanism_context = "\n".join(mechanism_parts)
    polish = answer_polish_service.polish_answer(
        narrative, client=client, model=model, request_kwargs=request_kwargs,
        provider=provider, blueprint=plan,
        anchor_terms=turn_blueprint.extract_anchor_terms(anchor_text),
        active_members=active_members or (), quest_break=mechanism_context,
        window=(int(paper.min_chars), int(paper.max_chars)), model_fn=budgeted)
    polish_meta.update({"used": polish.used, "reason": polish.reason,
                        "meta": dict(polish.meta or {})})
    if polish.used:
        narrative = polish.text
        display = turn_composer.compose_display([narrative], options)
        gate = turn_grader.format_gate(narrative)

    log_draft = plan.log_draft or {}
    log_line = turn_composer.render_log_line(
        int(snapshot.get("round") or 0),
        _text(log_draft.get("player") or message, 60),
        _text(log_draft.get("golden_finger"), 60),
        _text(log_draft.get("nemesis"), 60),
        _text(log_draft.get("world"), 60),
        _text(log_draft.get("beat"), 60),
        int(snapshot.get("progress") or 0))

    agent_meta = {
        "paper": paper.key,
        "paper_tier": paper.tier,
        "paper_stage": paper.stage,
        "blueprint_origin": plan.origin,
        "segments": agent_refill.refill_budget_meta(refilled["per_slot"]),
        "format_gate": gate,
        "format_fallback": format_fallback,
        "polish": polish_meta,
        "director": plan_meta.get("meta") or {},
    }
    return TurnResult(
        narrative=narrative,
        options=options,
        log_line=log_line,
        scene_validation=_scene_validation(refilled, contracts, narrative, paper),
        agent_meta=agent_meta,
        display=display,
        blueprint=plan,
        paper_key=paper.key,
        options_source=options_source,
    )


def _seed_options(plan: turn_blueprint.Blueprint) -> list[dict[str, Any]]:
    """选项卷失败时的零模型兜底：直接把蓝图种子转成 A–F 选项。"""
    from core import engine as _engine

    items: list[dict[str, Any]] = []
    for index, seed in enumerate(plan.option_seeds or ()):
        if index >= len(_engine.OPTION_KEYS) or not isinstance(seed, Mapping):
            break
        text = _text(seed.get("direction"), 60)
        if not text:
            continue
        items.append({
            "key": _engine.OPTION_KEYS[index],
            "text": text,
            "preview": _text(seed.get("preview"), 60),
            "factor": str(seed.get("factor") or "").strip() or "金手指",
            "factors": [],
        })
    return items


def _scene_validation(refilled: Mapping[str, Any],
                      contracts: Sequence[turn_grader.SegmentContract],
                      narrative: str, paper: papers.Paper) -> dict[str, Any]:
    """把段级批改结果压成 app 既有的 {length, interaction, anchor} 形状。"""
    per_slot = list(refilled.get("per_slot") or ())
    chars = len(narrative or "")
    low = int(paper.min_chars) if hasattr(paper, "min_chars") else 0
    high = int(paper.max_chars) if hasattr(paper, "max_chars") else 0
    if not low or not high:
        span = sum(int(c.window[1]) for c in contracts)
        low, high = int(span * 0.8), int(span * 1.2)
    mentions = sorted({name for c in contracts for name in (c.must_mention or ())})
    anchor_rows = [
        row for row, contract in zip(per_slot, contracts) if contract.anchor_terms
    ]
    anchor_ok = all(bool(row.get("ok")) for row in anchor_rows) if anchor_rows else True
    anchor_status = ("fulfilled" if paper.stage == "climax" and anchor_ok
                     else "partial" if anchor_rows and anchor_ok
                     else "pending" if anchor_rows else "mentioned")
    return {
        "length": {"valid": low <= chars <= high, "chars": chars,
                   "minimum": low, "maximum": high,
                   "shortfall": max(0, low - chars), "excess": max(0, chars - high)},
        "interaction": {"valid": all(name in narrative for name in mentions),
                        "required": bool(mentions), "active_names": mentions,
                        "mentioned_names": [n for n in mentions if n in narrative]},
        "anchor": {"valid": anchor_ok,
                   "status": anchor_status,
                   "segments": len(anchor_rows)},
        "segments": per_slot,
    }


__all__ = ["LEGACY", "REFILL_ATTEMPTS", "TurnResult", "TurnUpstreamError",
           "build_segment_prompt", "run_turn"]
