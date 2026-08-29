"""碎锚机制：积势进度、多阶段碎锚任务、成功降锚 / 失败冷却。

纯计算、无 IO、不调用模型。模型只在集成层调用；本模块提供
``offer_prompt`` / ``parse_offer`` / ``template_stages``，parse 失败由集成层
回退到模板，不把坏 JSON 写入状态。

碎锚占用 ``state["break_anchor"]``，与普通 ``state["quest"]`` 并存，互不占用。
成功把目标章写入 ``state["broken_anchors"]``，并把最早成功章写入
``state["anchors_shattered_from"]``：自该章起（含）之后的主线锚点全部降为
hint（不再收束），集成层同时停止后续锚点蒸馏。
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

BREAK_THRESHOLDS: dict[str, int] = {"一般": 4, "较高": 7, "极高": 10}
STAGE_COUNTS: dict[str, int] = {"一般": 2, "较高": 3, "极高": 4}
# 一般时限更短（阶段数 ×3），较高/极高为 ×4。
DEADLINE_MULT: dict[str, int] = {"一般": 3, "较高": 4, "极高": 4}
DEFAULT_TIER = "较高"
COOLDOWN_ROUNDS = 8
_ACTIVE_STATUSES = {"offered", "active"}
_VERBS = tuple("查证夺挡谈立")
_FENCE_PATTERN = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)

# 模板层兜底：条数按档位截取，每条 requirement 都含可观察动词，且不点名具体角色。
_STAGE_BLUEPRINTS: tuple[tuple[str, str], ...] = (
    ("探查裂隙", "查清{loc}关于「{title}」尚未锁死的关键线索"),
    ("对证事实", "证明确认「{title}」的履约路径仍可改写"),
    ("谈妥见证", "谈妥在场同伴对「{title}」的公开见证口径"),
    ("立下反证", "立下可观察、可复核的反证，阻止「{title}」被强制履约"),
)


def _normalize_tier(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in BREAK_THRESHOLDS else DEFAULT_TIER


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _tier(state: Mapping[str, Any] | None) -> str:
    state = state if isinstance(state, Mapping) else {}
    conv = state.get("convergence_state")
    conv = conv if isinstance(conv, Mapping) else {}
    return _normalize_tier(conv.get("effective"))


def _current_round(state: Mapping[str, Any] | None, override: Any = None) -> int:
    if override is not None:
        return _int(override, 0)
    state = state if isinstance(state, Mapping) else {}
    return _int(state.get("round"), 0)


def _current_chapter(state: Mapping[str, Any] | None) -> int:
    state = state if isinstance(state, Mapping) else {}
    return _int(state.get("current_chapter"), 1)


def _current_anchor(state: Mapping[str, Any] | None) -> dict[str, Any]:
    state = state if isinstance(state, Mapping) else {}
    timeline = state.get("anchor_timeline")
    timeline = timeline if isinstance(timeline, Mapping) else {}
    raw = timeline.get("current")
    return _normalize_anchor(raw if isinstance(raw, Mapping) else {})


def _normalize_anchor(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = raw if isinstance(raw, Mapping) else {}
    return {
        "chapter": _int(raw.get("chapter"), 0),
        "title": str(raw.get("title") or "").strip(),
        "summary": str(raw.get("summary") or "").strip(),
    }


def _box(state: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, Mapping):
        return {}
    box = state.get("break_anchor")
    return box if isinstance(box, dict) else {}


def _latest_ripple(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    state = state if isinstance(state, Mapping) else {}
    ripples = state.get("ripples")
    if not isinstance(ripples, list):
        return None
    for item in reversed(ripples):
        if isinstance(item, dict):
            return item
    return None


def _momentum_total(state: Mapping[str, Any] | None) -> int:
    ripple = _latest_ripple(state)
    if ripple is None:
        return 0
    raw = ripple.get("effective_total", ripple.get("total", 0))
    return max(0, _int(raw, 0))


def _write_momentum(state: dict[str, Any], total: int) -> int:
    total = max(0, int(total))
    ripples = state.get("ripples")
    if not isinstance(ripples, list):
        ripples = []
        state["ripples"] = ripples
    latest = None
    for item in reversed(ripples):
        if isinstance(item, dict):
            latest = item
            break
    if latest is None:
        latest = {}
        ripples.append(latest)
    latest["effective_total"] = total
    latest["total"] = total
    return total


def _names(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _has_verb(text: Any) -> bool:
    content = str(text or "")
    return any(verb in content for verb in _VERBS)


def _deadline_span(tier: str) -> int:
    tier = _normalize_tier(tier)
    return STAGE_COUNTS[tier] * DEADLINE_MULT[tier]


def idle_box() -> dict[str, Any]:
    return {
        "status": "idle",
        "target_anchor": {"chapter": 0, "title": "", "summary": ""},
        "stages": [],
        "current_stage": 0,
        "momentum_spent": 0,
        "offered_round": 0,
        "accepted_round": 0,
        "deadline_round": 0,
        "cooldown_until": 0,
        "momentum_deducted": False,
        "tier": DEFAULT_TIER,
        "deadline_span": 0,
    }


def momentum_bar(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """积势进度条：total / threshold / ratio∈[0,1] / ready / tier。"""
    total = _momentum_total(state)
    tier = _tier(state)
    threshold = BREAK_THRESHOLDS[tier]
    ratio = 0.0 if threshold <= 0 else max(0.0, min(1.0, total / float(threshold)))
    return {
        "total": total,
        "threshold": threshold,
        "ratio": ratio,
        "ready": total >= threshold,
        "tier": tier,
    }


def can_offer(state: Mapping[str, Any] | None) -> bool:
    """积势达标、无 offered/active 碎锚、已过冷却、且尚未全局碎锚时，才允许派发。"""
    box = _box(state)
    if box.get("status") in _ACTIVE_STATUSES:
        return False
    from_chapter = shattered_from(state)
    if from_chapter and _current_chapter(state) >= from_chapter:
        return False  # 锚点已全部失效，无可再碎
    current = _current_round(state)
    cooldown_until = _int(box.get("cooldown_until"), 0)
    if cooldown_until and current < cooldown_until:
        return False
    return bool(momentum_bar(state)["ready"])


def template_stages(
    anchor: Mapping[str, Any] | None,
    persona_hint: Any = "",
    location: Any = "",
    companions: Any = None,
    difficulty: Any = DEFAULT_TIER,
) -> dict[str, Any]:
    """无模型时的合格碎锚 offer。永远返回可被 ``new_offer`` 消费的结构。"""
    del persona_hint  # 模板不改写底色，只保证阶段数与可观察动词。
    del companions
    tier = _normalize_tier(difficulty)
    count = STAGE_COUNTS[tier]
    target = _normalize_anchor(anchor)
    title = target["title"] or "当前锚点"
    loc = str(location or "").strip() or "现场"
    stages = []
    for index, (stage_title, req) in enumerate(_STAGE_BLUEPRINTS[:count], start=1):
        stages.append({
            "id": index,
            "title": stage_title,
            "requirement": req.format(title=title, loc=loc),
            "status": "pending",
        })
    return {"target_anchor": target, "stages": stages}


def offer_prompt(context: Mapping[str, Any] | None) -> str:
    """碎锚派发子智能体提示词，要求严格 JSON。"""
    ctx = dict(context or {})
    tier = _normalize_tier(ctx.get("tier") or ctx.get("convergence") or ctx.get("difficulty"))
    count = _int(ctx.get("stage_count"), STAGE_COUNTS[tier])
    count = STAGE_COUNTS[tier] if count not in STAGE_COUNTS.values() else count
    anchor = ctx.get("anchor") or ctx.get("target_anchor") or ctx.get("current_anchor") or {}
    anchor = anchor if isinstance(anchor, Mapping) else {}
    chapter = anchor.get("chapter", ctx.get("chapter", ""))
    title = str(anchor.get("title") or ctx.get("anchor_title") or "").strip() or "未知"
    summary = str(anchor.get("summary") or ctx.get("anchor_summary") or "").strip() or "（无摘要）"
    persona = str(ctx.get("persona_hint") or ctx.get("persona") or "").strip() or "无（保持 persona 基线）"
    goal = str(ctx.get("goal") or f"打碎锚点「{title}」，使其仅作叙事提示").strip()
    location = str(ctx.get("location") or "").strip() or "未知"
    companions = "、".join(_names(ctx.get("companions"))) or "无"
    bar = ctx.get("momentum_bar") if isinstance(ctx.get("momentum_bar"), Mapping) else {}
    total = bar.get("total", ctx.get("momentum_total", 0))
    threshold = bar.get("threshold", ctx.get("threshold", BREAK_THRESHOLDS[tier]))
    ratio = bar.get("ratio", ctx.get("ratio"))
    if ratio is None:
        try:
            ratio = min(1.0, max(0.0, float(total) / float(threshold or 1)))
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = 0.0
    try:
        ratio_text = f"{float(ratio):.2f}"
    except (TypeError, ValueError):
        ratio_text = "0.00"
    span = _deadline_span(tier)
    registered = "、".join(_names(ctx.get("registered_names") or ctx.get("companions"))) or "（仅当前登记角色）"
    return (
        "【碎锚任务】你是碎锚设计子智能体。请依据当前锚点与主角近期倾向，"
        f"设计一个 {count} 阶段碎锚任务，并以严格 JSON 输出，不要输出任何其他文字。\n"
        f"当前锚点：第 {chapter} 章「{title}」——{summary}；"
        f"主角近期倾向：{persona}；目标：{goal}；"
        f"地点：{location}；在场同伴：{companions}；"
        f"收束力档：{tier}；积势比：{ratio_text}（积势 {total}/{threshold}）。\n"
        "输出 JSON 形状："
        "{\"target_anchor\": {\"chapter\": 章号, \"title\": \"标题\", \"summary\": \"摘要\"}, "
        "\"stages\": [{\"id\": 1, \"title\": \"阶段名\", \"requirement\": \"可观察完成条件\"}]}\n"
        f"硬性要求：stages 必须恰好 {count} 条且按序递进，不可跳步；"
        "每条 requirement 必须含可观察动词（查/证/夺/挡/谈/立）且非空；"
        "target_anchor 必须是当前锚点，不得改指向其他章节；"
        f"任务须在 {span} 回合内可完成；"
        f"不得点名未登记角色（可出现的名字：{registered}）；"
        "不得改写角色底色 / 口头禅 / 底线。"
    )


def _extract_json(text: str) -> Any:
    content = str(text or "").strip()
    if not content:
        raise ValueError("碎锚 offer 为空")
    fenced = _FENCE_PATTERN.search(content)
    if fenced:
        content = fenced.group(1).strip()
    else:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("碎锚 offer 中找不到 JSON 对象")
        content = content[start:end + 1]
    try:
        return json.loads(content)
    except ValueError as exc:
        raise ValueError(f"碎锚 offer 不是合法 JSON: {exc}") from exc


def _normalize_stages(raw: Any, expected: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("stages 必须是非空列表")
    if expected is not None and len(raw) != expected:
        raise ValueError(f"stages 必须恰好 {expected} 条（实际 {len(raw)}）")
    if expected is None and not (2 <= len(raw) <= 4):
        raise ValueError("stages 数量必须为 2–4")
    stages: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"stages[{index}] 必须是对象")
        title = str(item.get("title") or "").strip()
        requirement = str(item.get("requirement") or "").strip()
        if not title:
            raise ValueError(f"stages[{index}].title 不能为空")
        if not requirement:
            raise ValueError(f"stages[{index}].requirement 不能为空")
        if not _has_verb(requirement):
            raise ValueError(
                f"stages[{index}].requirement 必须含可观察动词（查/证/夺/挡/谈/立）")
        status = str(item.get("status") or "pending").strip() or "pending"
        if status not in ("pending", "done"):
            status = "pending"
        stage_id = item.get("id", index)
        try:
            stage_id = int(stage_id)
        except (TypeError, ValueError):
            stage_id = index
        stages.append({
            "id": stage_id,
            "title": title,
            "requirement": requirement,
            "status": status,
        })
    return stages


def parse_offer(text: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """解析模型碎锚 offer。形状或规则不合格一律抛 ValueError，由集成层回退模板。"""
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("碎锚 offer 必须是 JSON 对象")
    ctx = context if isinstance(context, Mapping) else {}
    tier = _normalize_tier(ctx.get("tier") or ctx.get("convergence") or ctx.get("difficulty"))
    expected = STAGE_COUNTS[tier] if ctx else None
    if ctx.get("stage_count") is not None:
        expected = _int(ctx.get("stage_count"), expected or 0) or expected
    stages = _normalize_stages(data.get("stages"), expected)
    target = data.get("target_anchor")
    if target is None:
        target = {"chapter": ctx.get("chapter", 0), "title": ctx.get("anchor_title", ""),
                  "summary": ctx.get("anchor_summary", "")}
    target = _normalize_anchor(target if isinstance(target, Mapping) else {})
    current = ctx.get("anchor") or ctx.get("current_anchor")
    if isinstance(current, Mapping):
        current_chapter = _int(current.get("chapter"), 0)
        if current_chapter and target["chapter"] and target["chapter"] != current_chapter:
            raise ValueError("target_anchor 必须是当前锚点")
        if not target["title"]:
            target["title"] = str(current.get("title") or "").strip()
        if not target["summary"]:
            target["summary"] = str(current.get("summary") or "").strip()
        if not target["chapter"]:
            target["chapter"] = current_chapter
    registered = set(_names(ctx.get("registered_names") or ctx.get("companions")))
    if registered:
        forbidden = set(_names(ctx.get("forbidden_names")))
        blob = " ".join(stage["requirement"] + stage["title"] for stage in stages)
        for name in forbidden:
            if name and name in blob and name not in registered:
                raise ValueError(f"不得点名未登记角色: {name}")
    return {"target_anchor": target, "stages": stages}


def new_offer(state: dict[str, Any], offer: Mapping[str, Any],
              current_round: int | None = None) -> dict[str, Any]:
    """写入碎锚 offer（status=offered）。不修改 ``state["quest"]``。"""
    if not isinstance(state, dict):
        raise ValueError("state 必须是字典")
    if not can_offer(state):
        raise ValueError("当前不能发起碎锚（积势不足、已有进行中碎锚、或仍在冷却）")
    offer = dict(offer or {})
    tier = _tier(state)
    stages = _normalize_stages(offer.get("stages"), STAGE_COUNTS[tier])
    target = offer.get("target_anchor")
    target = _normalize_anchor(target if isinstance(target, Mapping) else _current_anchor(state))
    if not target["chapter"]:
        target = _current_anchor(state)
        target["chapter"] = target["chapter"] or _current_chapter(state)
    current = _current_round(state, current_round)
    spent = BREAK_THRESHOLDS[tier]
    previous = _box(state)
    box = {
        "status": "offered",
        "target_anchor": target,
        "stages": stages,
        "current_stage": 0,
        "momentum_spent": spent,
        "offered_round": current,
        "accepted_round": 0,
        "deadline_round": 0,
        "cooldown_until": _int(previous.get("cooldown_until"), 0),
        "momentum_deducted": False,
        "tier": tier,
        "deadline_span": _deadline_span(tier),
    }
    state["break_anchor"] = box
    return box


def _deduct_if_needed(state: dict[str, Any], box: dict[str, Any]) -> None:
    """碎锚不再扣除积势（2026-08-30 设计变更）。

    碎锚现在是**全局碎锚**：成功后该章及之后的主线锚点全部失效，不存在
    「下一次碎锚」，积势因此不再是碎锚的消耗品，只为剧情推进服务。积势达标
    仍是 ``can_offer`` 的发起门禁（未变），但接受/推进不再扣势，失败也无从
    退还。``momentum_spent`` 归零仅保留字段供前端展示与存档兼容。
    """
    box["momentum_spent"] = 0
    box["momentum_deducted"] = False


def accept(state: dict[str, Any]) -> dict[str, Any]:
    """接受当前 offer：offered -> active，记下时限（不扣积势，见 _deduct_if_needed）。"""
    if not isinstance(state, dict):
        raise ValueError("state 必须是字典")
    box = _box(state)
    if box.get("status") != "offered":
        raise ValueError(f"当前碎锚状态为 {box.get('status')!r}，不能接受")
    current = _current_round(state)
    span = _int(box.get("deadline_span"), _deadline_span(box.get("tier") or _tier(state)))
    box["status"] = "active"
    box["accepted_round"] = current
    box["deadline_round"] = current + span
    _deduct_if_needed(state, box)
    state["break_anchor"] = box
    return box


def _validate_verdict(verdict: Any) -> dict[str, Any]:
    if not isinstance(verdict, Mapping):
        raise ValueError("verdict 必须是 {completed: bool, evidence: str} 字典")
    completed = verdict.get("completed")
    evidence = verdict.get("evidence")
    if not isinstance(completed, bool):
        raise ValueError("verdict.completed 必须是布尔值")
    if not isinstance(evidence, str):
        raise ValueError("verdict.evidence 必须是字符串")
    return {"completed": completed, "evidence": evidence.strip()}


def apply_success(state: dict[str, Any], current_round: int | None = None) -> dict[str, Any]:
    """碎锚成功：目标章写入 broken_anchors（去重），确保已扣积势，status=completed。

    同时落下「全局碎锚」标记 anchors_shattered_from：自该章起（含）之后的
    主线锚点全部失效不再收束，后续锚点蒸馏也随之停止（最早一次生效）。
    """
    if not isinstance(state, dict):
        raise ValueError("state 必须是字典")
    box = _box(state) or idle_box()
    if box.get("status") == "completed":
        state.setdefault("broken_anchors", [])
        return box
    _deduct_if_needed(state, box)
    target = _normalize_anchor(box.get("target_anchor"))
    chapter = target["chapter"] or _current_chapter(state)
    broken = state.get("broken_anchors")
    broken = [x for x in broken] if isinstance(broken, list) else []
    seen: set[int] = set()
    cleaned: list[int] = []
    for item in broken:
        number = _int(item, 0)
        if number and number not in seen:
            seen.add(number)
            cleaned.append(number)
    if chapter and chapter not in seen:
        cleaned.append(chapter)
    state["broken_anchors"] = cleaned
    if chapter:
        previous_from = _int(state.get("anchors_shattered_from"), 0)
        state["anchors_shattered_from"] = min(previous_from, chapter) if previous_from else chapter
    current = _current_round(state, current_round)
    box["status"] = "completed"
    box["cooldown_until"] = current + COOLDOWN_ROUNDS
    box["target_anchor"] = target
    state["break_anchor"] = box
    return box


def apply_fail(state: dict[str, Any], current_round: int | None = None) -> dict[str, Any]:
    """碎锚失败：进入 cooldown（不涉及积势——碎锚已不扣势，无从退还）。

    旧档兼容：历史存档里 momentum_deducted=True 的会话仍退还其记账的一半，
    避免玩家在版本切换点白扣一次。
    """
    if not isinstance(state, dict):
        raise ValueError("state 必须是字典")
    box = _box(state) or idle_box()
    if box.get("status") in ("failed", "cooldown") and box.get("cooldown_until"):
        return box
    if box.get("momentum_deducted"):
        # 仅旧档路径：新流程 _deduct_if_needed 不再置 True。
        spent = max(0, _int(box.get("momentum_spent"), 0))
        refund = spent // 2
        if refund:
            _write_momentum(state, _momentum_total(state) + refund)
        box["momentum_deducted"] = False
    current = _current_round(state, current_round)
    box["status"] = "failed"
    box["cooldown_until"] = current + COOLDOWN_ROUNDS
    state["break_anchor"] = box
    return box


def settle_stage(state: dict[str, Any], current_round: int,
                 verdict: Mapping[str, Any]) -> dict[str, Any]:
    """按阶段独立结算，一次只推进一个 pending 阶段，不跳步。

    - completed=True：当前阶段 done，current_stage +1；全部完成后 ``apply_success``。
    - 未完成且 current_round > deadline_round：``apply_fail``（超时）。
    - 否则保持 active。
    """
    verdict = _validate_verdict(verdict)
    box = _box(state)
    if box.get("status") != "active":
        return {"status": box.get("status") or "idle", "changed": False}
    current = _int(current_round, 0)
    stages = box.get("stages") if isinstance(box.get("stages"), list) else []
    index = max(0, _int(box.get("current_stage"), 0))

    if verdict["completed"]:
        if index < len(stages) and isinstance(stages[index], dict):
            stages[index]["status"] = "done"
        box["current_stage"] = index + 1
        box.setdefault("progress", []).append(
            {"round": current, "stage": index, "evidence": verdict["evidence"], "result": "done"})
        state["break_anchor"] = box
        if box["current_stage"] >= len(stages):
            apply_success(state, current)
            return {"status": "completed", "changed": True, "current_stage": box["current_stage"]}
        return {"status": "active", "changed": True, "current_stage": box["current_stage"]}

    deadline = box.get("deadline_round")
    if deadline is not None and current > _int(deadline, 0):
        box.setdefault("progress", []).append(
            {"round": current, "stage": index, "evidence": verdict["evidence"],
             "result": "failed_timeout"})
        state["break_anchor"] = box
        apply_fail(state, current)
        return {"status": "failed", "changed": True}

    box.setdefault("progress", []).append(
        {"round": current, "stage": index, "evidence": verdict["evidence"], "result": "ongoing"})
    state["break_anchor"] = box
    return {"status": "active", "changed": False, "current_stage": index}


def shattered_from(state: Mapping[str, Any] | None) -> int:
    """全局碎锚起始章：自该章（含）起锚点全部失效；0 表示尚未全局碎锚。"""
    state = state if isinstance(state, Mapping) else {}
    return _int(state.get("anchors_shattered_from"), 0)


def shatter_now(state: dict[str, Any], chapter: Any = None) -> dict[str, Any]:
    """立即全局碎锚（特权通道，如 RELAY 作弊码）：不走阶段机、不扣积势。

    自当前章（含）起主线锚点全部失效不再收束；进行中的碎锚任务直接记为完成。
    返回 {"chapter", "anchors_shattered_from", "already"}；already=True 表示此前已碎。
    """
    if not isinstance(state, dict):
        raise ValueError("state 必须是字典")
    already = bool(shattered_from(state))
    target_chapter = _int(chapter, 0) or _current_chapter(state)
    if target_chapter:
        broken = state.get("broken_anchors")
        broken = [x for x in broken] if isinstance(broken, list) else []
        seen: set[int] = set()
        cleaned: list[int] = []
        for item in broken:
            number = _int(item, 0)
            if number and number not in seen:
                seen.add(number)
                cleaned.append(number)
        if target_chapter not in seen:
            cleaned.append(target_chapter)
        state["broken_anchors"] = cleaned
        previous_from = shattered_from(state)
        state["anchors_shattered_from"] = min(previous_from, target_chapter) if previous_from else target_chapter
    box = _box(state)
    if box.get("status") in _ACTIVE_STATUSES:
        box["status"] = "completed"
        state["break_anchor"] = box
    return {
        "chapter": target_chapter,
        "anchors_shattered_from": _int(state.get("anchors_shattered_from"), 0),
        "already": already,
    }


def is_anchor_broken(state: Mapping[str, Any] | None, chapter: int) -> bool:
    state = state if isinstance(state, Mapping) else {}
    target = _int(chapter, 0)
    if not target:
        return False
    from_chapter = shattered_from(state)
    if from_chapter and target >= from_chapter:
        return True
    broken = state.get("broken_anchors")
    if not isinstance(broken, list):
        return False
    for item in broken:
        if _int(item, 0) == target:
            return True
    return False


def overlay_anchor_check(state: Mapping[str, Any] | None, chapter: int,
                         check: Mapping[str, Any] | None) -> dict[str, Any]:
    """门禁接入辅助：已碎锚点 valid 强制 True，status 原样保留并加 hint_only。"""
    result = dict(check) if isinstance(check, Mapping) else {}
    if is_anchor_broken(state, chapter):
        result["valid"] = True
        result["hint_only"] = True
    else:
        result.setdefault("hint_only", False)
    return result


def public_snapshot(state: Mapping[str, Any] | None) -> dict[str, Any]:
    box = _box(state)
    bar = momentum_bar(state)
    chapter = _current_chapter(state)
    target = box.get("target_anchor") if isinstance(box.get("target_anchor"), Mapping) else {}
    target_chapter = _int(target.get("chapter"), chapter)
    current = _current_round(state)
    cooldown_until = _int(box.get("cooldown_until"), 0)
    broken = state.get("broken_anchors") if isinstance(state, Mapping) else []
    broken = [int(x) for x in broken if _int(x, 0)] if isinstance(broken, list) else []
    from_chapter = shattered_from(state)
    return {
        "status": str(box.get("status") or "idle"),
        "target_anchor": dict(target) if target else {"chapter": 0, "title": "", "summary": ""},
        "stages": list(box.get("stages") or []),
        "current_stage": _int(box.get("current_stage"), 0),
        "momentum_spent": _int(box.get("momentum_spent"), 0),
        "offered_round": _int(box.get("offered_round"), 0),
        "accepted_round": _int(box.get("accepted_round"), 0),
        "deadline_round": _int(box.get("deadline_round"), 0),
        "cooldown_until": cooldown_until,
        "in_cooldown": bool(cooldown_until and current < cooldown_until),
        "momentum_bar": bar,
        "broken_anchors": broken,
        "anchors_shattered_from": from_chapter,
        "shattered": bool(from_chapter and chapter >= from_chapter),
        "hint_only": is_anchor_broken(state, target_chapter or chapter),
        "can_offer": can_offer(state),
    }


__all__ = [
    "BREAK_THRESHOLDS", "STAGE_COUNTS", "DEADLINE_MULT", "DEFAULT_TIER",
    "momentum_bar", "can_offer", "template_stages", "offer_prompt", "parse_offer",
    "new_offer", "accept", "settle_stage", "apply_success", "apply_fail",
    "is_anchor_broken", "shattered_from", "shatter_now", "public_snapshot", "overlay_anchor_check", "idle_box",
]
