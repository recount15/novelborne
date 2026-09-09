# -*- coding: utf-8 -*-
"""铁律账本中台门面（重构 M5，docs/REFACTOR_PLAN.md §7）。

把两条作弊码（三愿 WISH / 永久增补 RELAY）的产物从「自由文本全量堆积」
改造为**结构化铁律账本 + 相关性选择注入**：

  ask 通路（愿望/增补）→ 登记卷 structured_call → 严格校验 → 写
  ledger.cheat.directives（附 superseded 仲裁）→ 即时落盘（调用方负责）
  回合通路（app 注入点）→ select_for_turn → 命中才注入，未命中不注入

分层红线：本模块是 services 门面，只编排 engine 机制（directives /
cheat_code / structured / parallel），不感知 HTTP，不做 IO（落盘由调用方
的 _persist_cheat_state 负责）。

**三愿原子扣费顺序（不可调整）**：登记（结构化或兜底）成功之后才
``cheat_code.consume``——顺序颠倒会出现「扣了愿望次数但铁律没登记」。
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Optional, Sequence

from core import engine
from core.engine import directives, parallel, structured
from core.engine.distill import distill_model

Model = Callable[[str], Any]

#: 登记卷重试上限（与 structured_call 默认一致）。
REGISTER_ATTEMPTS = 2


class DirectiveClientError(Exception):
    """请求侧错误（空文本/超长/机制护栏剥空）：端点映射 HTTP 400。"""


class DirectiveUpstreamError(Exception):
    """模型侧错误（登记卷与兜底都拿不到可用铁律）：端点映射 HTTP 502。"""


def _mask(text: str, api_key: str | None) -> str:
    """防泄露：结构化字段同样要做 API Key 替换（与 ask_service 口径一致）。"""
    value = str(text or "")
    if api_key and api_key in value:
        value = value.replace(api_key, "***")
    return value


def _mask_entry(entry: Mapping[str, Any], api_key: str | None) -> dict[str, Any]:
    row = dict(entry)
    row["fact_norm"] = _mask(row.get("fact_norm"), api_key)
    row["affected"] = [_mask(item, api_key) for item in (row.get("affected") or ())]
    row["conflicts"] = [_mask(item, api_key) for item in (row.get("conflicts") or ())]
    return row


def _roster_names(state: Mapping[str, Any]) -> list[str]:
    """本局全阵容名字（affected 白名单来源之一）。"""
    names: list[str] = []
    for key in ("companions", "heroines", "active_members"):
        for item in (state.get(key) or ()):
            name = str((item.get("name") if isinstance(item, Mapping) else item) or "").strip()
            if name and name not in names:
                names.append(name)
    private = state.get("nemesis_private")
    if isinstance(private, Mapping):
        name = str(private.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    persona = str(state.get("persona") or "").strip()
    if persona and persona not in names:
        names.append(persona)
    return names


def _member_card(state: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    """按名字取名册成员卡片（角色状态 core 投影来源）。"""
    for key in ("companions", "heroines", "active_members"):
        for item in (state.get(key) or ()):
            if isinstance(item, Mapping) and str(item.get("name") or "").strip() == name:
                return item
    private = state.get("nemesis_private")
    if isinstance(private, Mapping) and str(private.get("name") or "").strip() == name:
        return private
    return None


def _snapshot_directives(state: dict) -> list[dict[str, Any]]:
    cheat = (state.get("ledger") or {}).get("cheat") if isinstance(state.get("ledger"), Mapping) else {}
    rows = cheat.get(directives.LEDGER_KEY) if isinstance(cheat, Mapping) else None
    return copy.deepcopy(rows) if isinstance(rows, list) else []


def _restore_directives(state: dict, snapshot: list[dict[str, Any]]) -> None:
    ledger = state.get("ledger")
    if not isinstance(ledger, dict):
        ledger = {}
        state["ledger"] = ledger
    cheat = ledger.get("cheat")
    if not isinstance(cheat, dict):
        cheat = {}
        ledger["cheat"] = cheat
    cheat[directives.LEDGER_KEY] = copy.deepcopy(snapshot)


def _snapshot_effect_domains(state: Mapping[str, Any]) -> tuple[Optional[dict], Optional[list]]:
    """事务前快照类型化效果域（character_states / wish_effects），供整体回滚。"""
    char_states = state.get("character_states")
    effects = state.get("wish_effects")
    return (copy.deepcopy(char_states) if isinstance(char_states, dict) else None,
            copy.deepcopy(effects) if isinstance(effects, list) else None)


def _restore_effect_domains(state: dict, snapshot: tuple[Optional[dict], Optional[list]]) -> None:
    """回滚 character_states / wish_effects 到事务前快照（None 表示键原本不存在）。"""
    char_states, effects = snapshot
    if char_states is None:
        state.pop("character_states", None)
    else:
        state["character_states"] = char_states
    if effects is None:
        state.pop("wish_effects", None)
    else:
        state["wish_effects"] = effects


def _apply_directive_effects(state: dict, row: Mapping[str, Any], text: str) -> list[str]:
    """把铁律登记行落成类型化状态效果（纯内存；失败抛 ValueError/TypeError）。

    - affected 中能对上名册的实体：写 ``character_states[name]`` 的证据断言
      （统一角色状态服务，置信 1.0——铁律是世界级硬事实，来源=账本行）；
    - 全部铁律：追加 ``state["wish_effects"]``（愿望/增补兑现状态显示与
      前端消费的唯一数据源）。
    """
    from core.services import character_state_service

    if not str(text or "").strip():
        raise ValueError("铁律正文为空，无法落状态效果")
    round_no = int(state.get("round") or 0)
    scope = str(row.get("scope") or "world")
    directive_id = row.get("id")
    from core.services.role_context_projection import explicit_role_ids
    ids = explicit_role_ids(state)
    roster = set(ids or _roster_names(state))
    char_states = state.setdefault("character_states", {})
    if not isinstance(char_states, dict):
        char_states = {}
        state["character_states"] = char_states
    touched: list[str] = []
    for name in row.get("affected") or ():
        name = str(name or "").strip()
        if not name or name not in roster:
            continue
        current = char_states.get(name)
        card = None if ids else _member_card(state, name)
        if not isinstance(current, Mapping):
            current = character_state_service.blank_character_state(
                character_state_service.core_personality_projection(card))
        char_states[name] = character_state_service.add_evidence(
            current, key=f"wish_{scope}", value=str(text),
            confidence=1.0,
            provenance={"source": "directive", "kind": row.get("kind"),
                        "directive_id": directive_id, "scope": scope},
            round_no=round_no)
        if name not in touched:
            touched.append(name)
    effects = state.get("wish_effects")
    if not isinstance(effects, list):
        effects = []
        state["wish_effects"] = effects
    effects.append({
        "directive_id": directive_id,
        "kind": str(row.get("kind") or directives.KIND_WISH),
        "scope": scope,
        "affected": [str(item) for item in (row.get("affected") or ())],
        "characters_touched": touched,
        "fact": str(text),
        "round": round_no,
    })
    return touched


def _worldbook_terms(state: Mapping[str, Any], limit: int = 40) -> list[str]:
    """世界书/地点/目标词表（affected 白名单来源之二）。"""
    terms: list[str] = []
    for item in (state.get("lore_hits") or ()):
        text = str(item or "").strip()
        if text and text not in terms:
            terms.append(text)
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    location = memory.get("location") if isinstance(memory.get("location"), Mapping) else {}
    for value in (location.get("name"), location.get("region")):
        text = str(value or "").strip()
        if text and text not in terms:
            terms.append(text)
    goals = memory.get("goals") if isinstance(memory.get("goals"), Mapping) else {}
    for goal in (goals.get("current") or ())[:4]:
        text = str(goal or "").strip()[:24]
        if text and text not in terms:
            terms.append(text)
    work = str(state.get("work") or state.get("novel_name") or "").strip().strip("《》")
    if work and work not in terms:
        terms.append(work)
    return terms[:limit]


def _allowed_terms(state: Mapping[str, Any]) -> list[str]:
    return _roster_names(state) + _worldbook_terms(state)


def _budgeted(client, model: str, request_kwargs: dict | None, provider: str,
              model_fn: Optional[Model]) -> Model:
    """把模型调用纳入回合优先级并发额度（ask 通路此前绕开了额度控制）。"""
    raw = model_fn or (lambda prompt: distill_model(
        client, model, prompt, request_kwargs, provider))
    return parallel.budget_model(raw, parallel.PRIORITY_TURN)


def _register_structured(state: dict, clean_text: str, *, kind: str, model: Model,
                         api_key: str | None,
                         attempts: int = REGISTER_ATTEMPTS) -> tuple[dict[str, Any], dict[str, Any]]:
    """登记卷 → 校验 → 写账本；任何失败落自由文本兜底条目（origin=fallback）。

    返回 ``(账本行, meta)``；``meta["origin"]`` ∈ model/fallback。
    """
    from core.services.role_context_projection import project_role_context
    roles = project_role_context(state)
    if not roles["ok"]:
        raise DirectiveClientError("role_projection_failed: " + ",".join(roles["omissions"]))
    allowed = (_worldbook_terms(state) + roles["target_ids"]
               if roles["rich"] else _allowed_terms(state))
    # 先迁移旧存档并置幂等标记，再写新账本/双写 legacy 键：否则下一回合
    # select_for_turn 会把本次刚双写的 wish_facts/relay_facts 再迁移成「全局」
    # 条目，导致未命中的局部铁律也被全量注入。
    directives.migrate_legacy(state)
    prompt = directives.build_registration_prompt(
        clean_text, kind=kind, known=directives.active_directives(state),
        roster=roles["target_ids"] if roles["rich"] else _roster_names(state),
        worldbook=_worldbook_terms(state))
    prompt += "\n\n" + roles["block"] + (
        "\n角色 affected 必须使用上列显式 character_id；显示名不是写入标识。"
        if roles["rich"] else "\n旧名册标签不构成角色事实证据。")
    entry: dict[str, Any] | None = None
    meta: dict[str, Any] = {}
    try:
        data, meta = structured.structured_call(
            model, prompt, directives.REGISTER_SPECS, attempts=attempts)
    except Exception as exc:  # noqa: BLE001  传输层失败：落兜底条目，不掐死许愿
        data, meta = None, {"transport_error": str(exc)}
    if data:
        entry, errors = directives.parse_registration(data, allowed=allowed)
        if errors:
            meta = dict(meta or {})
            meta["register_errors"] = list(errors)
    origin = "model"
    if entry is None:
        entry = directives.fallback_entry(clean_text, kind=kind)
        origin = "fallback"
    entry = _mask_entry(entry, api_key)
    row = directives.register(
        state, entry, kind=kind,
        round_no=int(state.get("round") or 0), raw=_mask(clean_text, api_key))
    superseded = directives.mark_superseded(state, row)
    meta = dict(meta or {})
    meta.update({"origin": origin, "superseded": superseded,
                 "directive_id": row.get("id")})
    return row, meta


def _duplicate_directive(state: dict, clean_text: str, kind: str) -> bool:
    """同文铁律已生效则拒绝重复登记（重试不重复生效、不重复扣费）。"""
    wanted = str(clean_text or "").strip()
    if not wanted:
        return False
    for row in directives.active_directives(state):
        if str(row.get("kind") or "") != kind:
            continue
        if str(row.get("fact_norm") or "").strip() == wanted or \
                str(row.get("raw") or "").strip() == wanted:
            return True
    return False


def grant_wish(state: dict, question: str, *, client=None, model: str = "",
               request_kwargs: dict | None = None, provider: str = "deepseek",
               api_key: str | None = None,
               model_fn: Optional[Model] = None) -> dict[str, Any]:
    """三愿：机制护栏 → 查重 → 结构化登记 → 类型化状态效果 → **原子扣费**。

    登记行（含 scope/affected）先落类型化角色状态断言与 ``wish_effects``，
    全部成功才 ``cheat_code.consume``；类型化落库失败回滚账本行并抛
    :class:`DirectiveClientError`（不扣费）。重复愿望拒绝且不扣费。

    返回 ``{"row", "granted", "rejected", "remaining", "characters_touched", "meta"}``。
    ``granted`` 是给玩家看的铁律落地文本（结构化 fact_norm 或兜底原文）。
    """
    try:
        clean, rejected = directives.mechanism_guard(question)
    except ValueError as exc:
        raise DirectiveClientError(str(exc)) from exc
    if not clean:
        raise DirectiveClientError(
            "愿望在剥离机制诉求后为空——铁律只能修改世界观与剧情，不能修改游戏机制。")
    if not engine.cheat_code.is_armed(state):
        raise DirectiveClientError("作弊许愿未被激活或次数已耗尽")
    if _duplicate_directive(state, clean, directives.KIND_WISH):
        raise DirectiveClientError("相同愿望已生效——重复许愿不再扣次数，也未产生新效果")

    snapshot = _snapshot_directives(state)
    effect_domains = _snapshot_effect_domains(state)
    row, meta = _register_structured(
        state, clean, kind=directives.KIND_WISH,
        model=_budgeted(client, model, request_kwargs, provider, model_fn),
        api_key=api_key)
    granted = str(row.get("fact_norm") or clean)
    try:
        # 类型化兑现：登记行落角色状态断言 + wish_effects（原子扣费点之前）。
        touched = _apply_directive_effects(state, row, granted)
    except (TypeError, ValueError) as exc:
        _restore_directives(state, snapshot)
        _restore_effect_domains(state, effect_domains)
        raise DirectiveClientError(f"愿望状态兑现失败（未扣次数）：{exc}") from exc
    # 原子扣费点：登记与状态兑现都已落成，最后才消耗次数（顺序不可颠倒）。
    engine.cheat_code.consume(state)
    remaining = engine.cheat_code.remaining_wishes(state)
    # 兼容旧存档/旧读取方：wish_facts 继续双写（不删旧键，双读期）。
    state.setdefault("wish_facts", []).append(
        {"wish": clean, "granted": granted,
         "round": int(state.get("round") or 0), "directive_id": row.get("id")})
    return {"row": row, "granted": granted,
            "rejected": rejected, "remaining": remaining,
            "characters_touched": touched, "meta": meta}


def append_relay_fact(state: dict, question: str, *, client=None, model: str = "",
                      request_kwargs: dict | None = None, provider: str = "deepseek",
                      api_key: str | None = None,
                      model_fn: Optional[Model] = None) -> dict[str, Any]:
    """永久增补：机制护栏 → 查重 → 结构化登记 → 类型化状态效果（无扣费）。

    返回 ``{"row", "text", "rejected", "count", "characters_touched", "meta"}``。
    """
    try:
        clean, rejected = directives.mechanism_guard(question)
    except ValueError as exc:
        raise DirectiveClientError(str(exc)) from exc
    if not clean:
        raise DirectiveClientError(
            "增补在剥离机制诉求后为空——增补只能修改世界观与剧情，不能修改游戏机制。")
    if _duplicate_directive(state, clean, directives.KIND_RELAY):
        raise DirectiveClientError("相同增补已生效——重复增补不产生新效果")

    snapshot = _snapshot_directives(state)
    effect_domains = _snapshot_effect_domains(state)
    row, meta = _register_structured(
        state, clean, kind=directives.KIND_RELAY,
        model=_budgeted(client, model, request_kwargs, provider, model_fn),
        api_key=api_key)
    text = str(row.get("fact_norm") or clean)
    try:
        touched = _apply_directive_effects(state, row, text)
    except (TypeError, ValueError) as exc:
        _restore_directives(state, snapshot)
        _restore_effect_domains(state, effect_domains)
        raise DirectiveClientError(f"增补状态兑现失败：{exc}") from exc
    # 兼容旧读取方：relay_facts 继续双写。
    engine.cheat_code.record_relay_fact(
        state, {"fact": clean, "text": text,
                "round": int(state.get("round") or 0), "directive_id": row.get("id")})
    return {"row": row, "text": text,
            "rejected": rejected,
            "count": len(directives.active_directives(state)),
            "characters_touched": touched, "meta": meta}


def select_for_turn(state: dict, *, anchor_words: Sequence[str] = (),
                    present_members: Sequence[Any] = (),
                    locations: Sequence[str] = (),
                    limit: int = 8) -> dict[str, Any]:
    """回合注入口：惰性迁移 → 相关性选择 → 装配注入块。

    返回 ``{"block", "selected", "migrated", "total"}``；未命中任何铁律时
    ``block`` 为空串（解决 relay 无限累积撑大 system 的问题）。
    """
    migrated = directives.migrate_legacy(state)
    selected = directives.select_relevant(
        state, anchor_words=anchor_words, present_members=present_members,
        locations=locations, limit=limit)
    return {
        "block": directives.build_directives_block(selected),
        "selected": selected,
        "migrated": migrated,
        "total": len(directives.active_directives(state)),
    }


def activate_relay(state: dict) -> dict[str, Any]:
    """永久通路激活：接通 → 碎锚 → 停蒸馏池 → 状态文案（收编自 ask_service）。

    落盘由调用方负责（本模块不做 IO）。返回 ``{"anchors_shattered_from"}``。
    """
    from core.services import registries  # 局部导入：中立注册表层

    engine.cheat_code.relay_activate(state)
    shatter: dict[str, Any] = {}
    try:
        shatter = engine.break_anchor.shatter_now(state) or {}
    except Exception:  # noqa: BLE001  碎锚失败不阻断通路激活（维持既有语义）
        shatter = {}
    try:
        registries.distillers.stop_all()
    except Exception:  # noqa: BLE001  蒸馏停止失败不阻断通路激活
        pass
    state["distill_status"] = "锚点已全部失效，后续蒸馏停止"
    return {"anchors_shattered_from": shatter.get("anchors_shattered_from", 0)}


__all__ = [
    "REGISTER_ATTEMPTS", "DirectiveClientError", "DirectiveUpstreamError",
    "grant_wish", "append_relay_fact", "select_for_turn", "activate_relay",
]
