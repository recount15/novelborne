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
    modular_context,
    papers,
    parallel,
    protagonist_state,
    quest,
    structured,
    turn_blueprint,
    turn_composer,
    turn_grader,
)
# 注意：engine 包级 ``quality_gate`` 名字被 gf_designer 的同名函数（惰性导出）
# 占用，质量门模块必须按全路径导入。
from core.engine.quality_gate import (
    DIMENSION_WEIGHTS,
    QUALITY_GATE_VERSION,
    RefinementRequest,
    ScoreCard,
    bounded_refine,
    has_scaffold,
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


def _mechanism_context(snapshot: Mapping[str, Any], paper: papers.Paper,
                       quest_box: Mapping[str, Any], break_box: Mapping[str, Any],
                       memory: Mapping[str, Any]) -> str:
    """润色卷机制素材块（v2.0.4 抽函数：任务三态+完成回响，可单测）。"""
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
    # v2.0.4 任务完成回响：判定完成后 2 回合内，润色卷可见「已发放奖励」
    # 事实，后续叙事自然提及成果；奖励由系统发放，正文不得重复入账。
    if quest_box.get("status") == "completed":
        settled = quest_box.get("last_settlement") if isinstance(quest_box.get("last_settlement"), Mapping) else {}
        try:
            gap = int(snapshot.get("round") or 0) - int(settled.get("round") or 0)
        except (TypeError, ValueError):
            gap = 99
        if 0 <= gap <= 2:
            granted = "、".join(str(g) for g in (settled.get("granted") or [])) or "已入账"
            mechanism_parts.append(
                "任务已完成（%s），奖励已由系统发放（%s）：本回合叙事可自然提及这段成果，"
                "不得把该任务当作未完成或让角色重复领取奖励" % (
                    _text(quest_box.get("title"), 60), _text(granted, 120)))
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
    return "\n".join(mechanism_parts)


def build_segment_prompt(paper: papers.Paper, plan: turn_blueprint.Blueprint,
                         contract: turn_grader.SegmentContract, *,
                         action: str = "", context_blocks: str = "",
                         world_block: str = "") -> str:
    """装配单个段卷提示词（段间事件互斥靠 EXCLUDED_EVENTS 显式排除）。

    ``world_block`` 为作品设定 + 近期回合摘要的压缩块（v2.0.3 注入：
    段卷此前只有 1200 字前情尾巴，正文极易混入其他作品的设定）。
    """
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
        WORLD=str(world_block or "").strip()[:2600] or "（未提供作品设定摘要：按前情梗概书写）",
        CONTEXT=str(context_blocks or "").strip()[-2400:] or "（开局首轮，无前文）",
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
                        attempts: int, system_brief: str = "",
                        quest_hint: str = "") -> tuple[turn_blueprint.Blueprint, dict[str, Any]]:
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
        system_brief=system_brief,
        quest_hint=quest_hint,
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
             scene_excerpt: str = "",
             tier: Optional[int] = None,
             attempts: int = 2,
             on_draft: Optional[Any] = None) -> TurnResult | str:
    """跑完一回合试卷管线；返回 :class:`TurnResult` 或 :data:`LEGACY` 信号。

    - ``tier`` 缺省取 ``state["paper_tier"]``，再退回按 story_richness 映射；
    - ``system_prompt``（作品设定+系统规则）与近期回合摘要注入导演卷/段卷
      （v2.0.3：此前是死参数，四子卷全部裸跑导致跨书乱入）；
    - ``scene_excerpt`` 为当前章原文节选，注入选项卷（AI 情景模拟）；
    - ``free`` 阶段或缺卷 → 返回 :data:`LEGACY`（调用方走旧单卷路径）；
    - 模型不合格一律走批改重填/兜底，不抛错；只有传输层全线失败才抛
      :class:`TurnUpstreamError`。
    
    v2.0.4: 初始化回合级 Token 累加器（阶段 E）。
    """
    # v2.0.4 Token 计量：初始化回合级累加器
    from core.engine import token_accounting
    token_accounting.init_turn_usage()
    
    snapshot = dict(state or {})
    resolved_tier = int(tier or snapshot.get("paper_tier") or 0)
    if resolved_tier < 1:
        resolved_tier = papers.map_legacy_richness(snapshot.get("story_richness"))
    # 碎锚判定走 break_anchor 唯一入口（app 全链路同源）：relay 接通或
    # 碎锚任务完成后 stage=free，本管线接管（v2.0.4 放行 free 阶段）。
    from core.engine import break_anchor, free_stage  # 局部导入：避免包级循环
    try:
        shattered = bool(break_anchor.shattered_from(snapshot))
    except Exception:  # noqa: BLE001 判定失败按未碎锚处理（不误降级）
        shattered = False
    stage = papers.stage_for(
        int(snapshot.get("chapter_round") or 1),
        int(snapshot.get("turn_budget") or 0),
        shattered)
    # v2.0.4: free 阶段放行，使用专属试卷和质量门变体
    if stage not in ("setup", "climax", "free"):
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

    # —— v2.0.3 生成侧上下文：作品设定摘要 + 近期 ≤10 回合摘要（单源组装）——
    # v2.0.4：进行中任务块并入段卷 world_block（正文生成时可见任务，推进
    # 不再只靠结算时模型事后追认）。
    recent_digest = modular_context.build_recent_digest(snapshot)
    quest_block = quest.quest_context_block(snapshot)
    state_facts = protagonist_state.hard_facts_text(snapshot)
    world_block = "\n".join(part for part in (
        "【作品设定与系统规则】" if str(system_prompt or "").strip() else "",
        str(system_prompt or "").strip()[:1500],
        state_facts,
        "【近期回合摘要】" if recent_digest else "",
        recent_digest[:1100],
        quest_block,
    ) if part).strip()
    context_audit = {
        "system_brief_chars": len(str(system_prompt or "").strip()),
        "recent_digest_chars": len(recent_digest),
        "world_block_chars": len(world_block),
        "scene_excerpt_chars": len(str(scene_excerpt or "").strip()),
        "quest_block_chars": len(quest_block),
        "state_facts_chars": len(state_facts),
    }

    # —— Wave A：导演卷（1 次，失败落机械兜底蓝图）——
    plan, plan_meta = _director_blueprint(
        budgeted, paper, action=message, context_blocks=context_blocks,
        active_members=active_members or (), anchor_text=anchor_text,
        state=snapshot, attempts=attempts, system_brief=str(system_prompt or ""),
        quest_hint=quest_block)
    contracts = _segment_contracts(paper, plan)

    # —— Wave B：段卷 ∥ 选项卷（单层扁平并发；选项卷内部不得再并发）——
    segment_jobs = [
        (lambda prompt=build_segment_prompt(
            paper, plan, contract, action=message, context_blocks=context_blocks,
            world_block=world_block):
         budgeted(prompt))
        for contract in contracts
    ]
    factors_block = "\n".join(
        f"- {seed.get('factor')}：{seed.get('direction')}"
        for seed in (plan.option_seeds or ())
        if isinstance(seed, Mapping)
    )
    # v2.0.5 S6：导演蓝图节拍注入选项卷——选项不再对本回合走向一无所知；
    # 同时并发两卷（视角差异化：剧情走向优先 / 玩家处境优先），批改择优。
    blueprint_brief = "；".join(filter(None, (
        f"节拍：{plan.beat}" if plan.beat else "",
        f"回合目标：{plan.goal}" if plan.goal else "",
        f"核心冲突：{plan.conflict}" if plan.conflict else "",
        f"悬念钩子：{plan.cliffhanger}" if plan.cliffhanger else "",
    )))

    def _options_job(variant: int = 0):
        return options_service.generate_options(
            client, model, request_kwargs, provider, action=message,
            factors_block=factors_block, context_tail=context_blocks,
            scene=str(scene_excerpt or ""), narrative="", factors=factors or [],
            model_fn=budgeted, attempts=attempts,
            blueprint_brief=blueprint_brief, variant=variant)

    jobs = list(segment_jobs) + [lambda: _options_job(0), lambda: _options_job(1)]
    assert len(jobs) <= parallel.HARD_LIMIT, "Wave B 作业数不得超过并发硬上限"
    # v2.0.5 S1：段卷完成即推流。草稿按完成序即时回调 on_draft（App 侧把它
    # 作为增量推给前端），后处理完成后再以终稿整体替换——keep-best 保证终稿
    # 不低于草稿，用户首字节等待从「全管线结束」提前到「首个段卷完成」。
    segment_count = len(segment_jobs)
    if on_draft is not None:
        _draft_seen: list[bool] = [False] * segment_count
        _draft_buf: list[str] = [""] * segment_count
        for index, item in parallel.iter_parallel_completed(
                list(segment_jobs), parallel.PRIORITY_TURN):
            if getattr(item, "ok", False) and str(getattr(item, "value", "") or "").strip():
                _draft_seen[index] = True
                _draft_buf[index] = str(item.value)
                try:
                    on_draft("\n\n".join(
                        text for seen, text in zip(_draft_seen, _draft_buf) if seen))
                except Exception:  # noqa: BLE001 推流失败绝不影响回合
                    pass
        results = parallel.run_parallel(
            list(segment_jobs) + [lambda: _options_job(0), lambda: _options_job(1)],
            parallel.PRIORITY_TURN)
    else:
        results = parallel.run_parallel(jobs, parallel.PRIORITY_TURN)
    segment_results = results[:segment_count]
    options_candidates = results[segment_count:]

    def _pick_options(cands: Sequence[Any]) -> dict[str, Any]:
        """两卷择优：grade 全过者优先；否则取 warnings/错误更少、条目更全者。"""
        best: dict[str, Any] = {}
        best_cost = None
        for item in cands:
            payload = (getattr(item, "value", None)
                       if getattr(item, "ok", False) else None) or {}
            if not payload.get("options"):
                continue
            items = list(payload["options"])
            graded = turn_grader.grade_options(items)
            cost = (0 if graded.ok else 1,
                    len(graded.errors or payload.get("warnings") or []),
                    -len(items))
            if best_cost is None or cost < best_cost:
                best, best_cost = payload, cost
        return best

    options_payload = _pick_options(options_candidates)

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

    options = list(options_payload.get("options") or [])
    options_source = str(options_payload.get("source") or "none")
    # v2.0.3 AI-only 硬性原则：选项卷失败不再以蓝图种子/模板句兜底展示，
    # options 为空即保留玩家自由输入（前端照常渲染无选项回合）。

    display = turn_composer.compose_display(segments, options)
    narrative = str(display.get("narrative") or "\n\n".join(segments))
    gate = turn_grader.format_gate(narrative)
    # v2.0.3 修正（kimi-k3 实测）：组装格式门失败时旧逻辑把污染段换成
    # 确定性兜底段——兜底段本身就是蓝图脚手架（“X就地应对/Y随之显形”），
    # 等于用更糟的机械文本替换污染文本。现在只记标记不动正文：格式硬伤
    # 交由润色卷与质量门修复，终检防线兜底（仍不合格→LEGACY 单卷路径）。
    format_fallback = not bool(gate.get("valid"))

    # —— 后处理整合卷：不设质量门禁，只负责把答卷零散结构整合成自然正文。
    #     采用流程化 prompt（事实锁定→接缝整合→文学润色→输出整理）；失败、
    #     空返回或格式污染均安全回退初步组装稿，不阻断回合、不改写状态。
    #     v2.0.5 S5：双卷并发 best-of-2——两份润色候选并行生成后由质量门
    #     规则层打分择优，取代单候选串行迭代；耗时 ≈ 单卷，质量取上界。
    polish_meta: dict[str, Any] = {"enabled": True, "used": False}
    quest_box = snapshot.get("quest") if isinstance(snapshot.get("quest"), Mapping) else {}
    break_box = snapshot.get("break_anchor") if isinstance(snapshot.get("break_anchor"), Mapping) else {}
    memory = snapshot.get("state_memory") if isinstance(snapshot.get("state_memory"), Mapping) else {}
    mechanism_context = _mechanism_context(snapshot, paper, quest_box, break_box, memory)
    _polish_kwargs = dict(
        client=client, model=model, request_kwargs=request_kwargs,
        provider=provider, blueprint=plan,
        anchor_terms=turn_blueprint.extract_anchor_terms(anchor_text),
        active_members=active_members or (), quest_break=mechanism_context,
        window=(int(paper.min_chars), int(paper.max_chars)), model_fn=budgeted)

    def _polish_cost(result: Any) -> tuple[int, int]:
        # 排序键：未用（回退原稿）劣后；用卷时按格式门错误数升序。
        gate = turn_grader.format_gate(result.text or "")
        return (0 if result.used else 1, len(gate.get("errors") or []))

    _polish_jobs = [
        (lambda: answer_polish_service.polish_answer(narrative, **_polish_kwargs)),
        (lambda: answer_polish_service.polish_answer(narrative, **_polish_kwargs)),
    ]
    _polish_results = parallel.run_parallel(_polish_jobs, parallel.PRIORITY_TURN)
    _polish_candidates = [
        item.value for item in _polish_results if getattr(item, "ok", False)
    ] or [answer_polish_service.PolishResult(narrative, False, "parallel_failed")]
    polish = min(_polish_candidates, key=_polish_cost)
    polish_meta.update({"used": polish.used, "reason": polish.reason,
                        "meta": dict(polish.meta or {}),
                        "best_of": len(_polish_candidates)})
    if polish.used:
        narrative = polish.text
        display = turn_composer.compose_display([narrative], options)
        gate = turn_grader.format_gate(narrative)

    # —— v2.0.3 质量门 + 有界多轮修订（keep-best/分维无回退门/预算熔断，
    #     全程审计入 agent_meta.postprocess；任何失败均回退润色稿不阻断）——
    post_meta = _run_quality_gate(
        snapshot, budgeted, narrative=narrative, options=options,
        system_prompt=str(system_prompt or ""), world_block=world_block,
        anchor_text=anchor_text, active_members=active_members or (),
        contracts=contracts, window=(int(paper.min_chars), int(paper.max_chars)),
        stage=paper.stage)
    if post_meta.get("adopted"):
        narrative = str(post_meta["narrative"])
        options = list(post_meta["options"])
        display = turn_composer.compose_display([narrative], options)
        gate = turn_grader.format_gate(narrative)

    # —— v2.0.3 终检防线（AI-only 对正文同样成立）：蓝图脚手架与格式硬伤
    #     绝不出厂。润色卷和质量门都没能救回时，整回合降级 LEGACY 单卷
    #     路径重新流式生成自然正文；单卷也失败则由调用方如实报错回滚。
    if has_scaffold(narrative) or not gate.get("valid", True):
        return LEGACY

    log_draft = plan.log_draft or {}
    log_line = turn_composer.render_log_line(
        int(snapshot.get("round") or 0),
        _text(log_draft.get("player") or message, 60),
        _text(log_draft.get("golden_finger"), 60),
        _text(log_draft.get("nemesis"), 60),
        _text(log_draft.get("world"), 60),
        _text(log_draft.get("beat"), 60),
        int(snapshot.get("progress") or 0))

    # v2.0.4 Token 计量：回合结束收集分项 usage
    usage_data = token_accounting.get_turn_usage()
    if usage_data:
        usage_breakdown = {
            "total": usage_data["total_tokens"],
            "prompt": usage_data["prompt_tokens"],
            "completion": usage_data["completion_tokens"],
            "director": usage_data.get("director", 0),
            "segments": usage_data.get("segments", 0),
            "options": usage_data.get("options", 0),
            "polish": usage_data.get("polish", 0),
            "gate": usage_data.get("gate", 0),
            "quest": usage_data.get("quest", 0),
            "chat": usage_data.get("chat", 0),
            "other": usage_data.get("other", 0),
        }
    else:
        usage_breakdown = {}
    
    agent_meta = {
        "paper": paper.key,
        "paper_tier": paper.tier,
        "paper_stage": paper.stage,
        "blueprint_origin": plan.origin,
        "segments": agent_refill.refill_budget_meta(refilled["per_slot"]),
        "format_gate": gate,
        "format_fallback": format_fallback,
        "polish": polish_meta,
        "postprocess": post_meta,
        "context_audit": context_audit,
        "director": plan_meta.get("meta") or {},
        "usage": usage_breakdown,  # v2.0.4 Token 分项
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


def _run_quality_gate(snapshot: Mapping[str, Any], model: Model, *, narrative: str,
                      options: Sequence[Any], system_prompt: str, world_block: str,
                      anchor_text: str, active_members: Sequence[Any],
                      contracts: Sequence[turn_grader.SegmentContract],
                      window: tuple[int, int],
                      stage: str = "") -> dict[str, Any]:
    """润色稿上的有界多轮质量修订（v2.0.3 硬性原则 2/3）。

    - 打分：quality_gate 九维规则评分（格式/实质/选项/合约必含词/世界词/
      锚点/角色/风格/前情衔接），裁判分仅在能给出逐字证据时融合；
    - 修订：每轮 = 风格迁移重写（带语体指纹 + 近期摘要 + 违规清单，
      最小干预只动违规处），keep-best + 分维无回退门 + 失败回滚 +
      连续无改善终止 + 调用/时限熔断（≤2 轮 / ≤4 次调用 / ≤120s）；
    - 防反作用：修订稿须再过 format_gate 与字数窗，否则整轮回退润色稿；
    - 初稿已通过且总分 ≥85 直接跳过（强模型快速通道，零额外调用）。
    任何异常都不阻断回合——返回审计元数据，adopted=False 表示未采纳。
    """
    audit: dict[str, Any] = {
        "version": QUALITY_GATE_VERSION, "enabled": True,
        "adopted": False, "used": False, "error": "",
    }
    try:
        names = [
            str((item.get("name") if isinstance(item, Mapping) else item) or "").strip()
            for item in (active_members or ())
        ]
        memory = snapshot.get("state_memory") if isinstance(snapshot.get("state_memory"), Mapping) else {}
        location = memory.get("location") if isinstance(memory.get("location"), Mapping) else {}
        must_include = [term for contract in contracts
                        for term in (contract.must_include or []) if term]
        mctx = {
            "anchor_terms": turn_blueprint.extract_anchor_terms(anchor_text),
            "character_names": [name for name in names if name],
            "required_terms": must_include[:12],
            "world_terms": [
                _text(location.get("name"), 40),
                _text(snapshot.get("golden_finger"), 40),
            ],
            "state_hard_facts": protagonist_state.hard_facts(snapshot),
            "recent_narrative": modular_context.build_recent_digest(snapshot)[-800:],
        }
        modular_text = modular_context.build_modular_context(
            snapshot, system_prompt=system_prompt, anchor_text=anchor_text,
            active_members=active_members)[:3500]

        def _rewrite(request: RefinementRequest) -> str:
            issues = [
                f"[{issue.dimension}/{issue.code}/{issue.severity}] {issue.message}"
                + (f"（证据：{'；'.join(issue.evidence[:2])}）" if issue.evidence else "")
                for issue in request.scorecard.issues[:12]
            ]
            return str(model(render(
                "quality_rewrite.md",
                NARRATIVE=request.narrative,
                ISSUES="\n".join(issues) or "（无具体违规：做一次向原文风格收敛的润色）",
                WORLD=str(world_block or "").strip()[:2000] or "（未提供作品设定摘要）",
                MODULAR=modular_text or "（未提供模块化档案）",
                ROUND=request.round_index)))

        def _judge(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            raw = model(render(
                "quality_judge.md",
                NARRATIVE=payload.get("narrative") or "",
                OPTIONS="\n".join(
                    "- %s" % (item.get("text") if isinstance(item, Mapping) else item)
                    for item in (payload.get("options") or ()))[:800] or "（本回合无选项）",
                DIMENSIONS="、".join(DIMENSION_WEIGHTS)))
            data = structured.extract_json(str(raw or ""))
            if not isinstance(data, Mapping):
                raise ValueError("裁判返回非 JSON 对象")
            if "scores" not in data or "evidence" not in data:
                raise ValueError("裁判返回缺少 scores/evidence")
            return data

        # v2.0.4: free 阶段使用降级权重
        weights = None
        if stage == "free":
            from core.engine import free_stage
            weights = free_stage.FREE_DIMENSION_WEIGHTS

        result = bounded_refine(
            narrative, options, mctx, None, _rewrite,
            judge_fn=_judge, max_rounds=2, max_calls=4, max_seconds=120.0,
            early_pass_score=85.0, dimension_weights=weights)
        audit.update({
            "used": result.calls > 0,
            "initial": _scorecard_summary(result.initial_scorecard),
            "final": _scorecard_summary(result.scorecard),
            "rounds": result.rounds, "calls": result.calls,
            "stop_reason": result.stop_reason,
            "events": [
                {"round": event.round_index, "call": event.call_index,
                 "accepted": event.accepted, "reason": event.reason,
                 "before": event.before_total, "candidate": event.candidate_total,
                 "regressed": list(event.regressed_dimensions)}
                for event in result.events
            ],
            "issues": [
                {"dimension": issue.dimension, "code": issue.code,
                 "severity": issue.severity, "message": issue.message}
                for issue in result.scorecard.issues[:10]
            ],
        })
        candidate = str(result.narrative or "").strip()
        low, high = window
        gate = turn_grader.format_gate(candidate)
        # 防反作用终检：修订稿必须过格式门与字数窗（上限放宽 20% 容差），
        # 否则整轮弃用，保留润色稿。v2.0.3 修正（kimi-k3 实测）：keep-best
        # 原样保留初稿时 total>=initial 恒真，曾把「什么都没修好」记成
        # adopted=true——adopted 必须以「确有候选被采纳」为前提，未采纳
        # 时记录拒因（no_accepted_candidate / format_gate / window）。
        accepted_any = any(event.accepted for event in result.events)
        audit["reject_reason"] = ""
        if not candidate or not accepted_any:
            audit["reverted"] = True
            audit["reject_reason"] = "no_accepted_candidate"
        elif result.scorecard.total < result.initial_scorecard.total:
            audit["reverted"] = True
            audit["reject_reason"] = "total_regression"
        elif not gate.get("valid"):
            audit["reverted"] = True
            audit["reject_reason"] = "format_gate"
        elif not (low * 0.8 <= len(candidate) <= high * 1.2):
            audit["reverted"] = True
            audit["reject_reason"] = "window"
            audit["window_detail"] = {"chars": len(candidate),
                                      "low": low, "high": high}
        else:
            audit.update({"adopted": True, "narrative": candidate,
                          "options": list(result.options)})
    except Exception as exc:  # noqa: BLE001 质量门自身失败绝不阻断回合
        audit["error"] = f"{type(exc).__name__}: {exc}"
    return audit


def _scorecard_summary(card: ScoreCard) -> dict[str, Any]:
    return {
        "total": card.total,
        "passed": card.passed,
        "dimensions": dict(card.dimensions),
        "hard_errors": sum(1 for issue in card.issues if issue.severity == "error"),
    }


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
