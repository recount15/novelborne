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
    allowed = _allowed_terms(state)
    # 先迁移旧存档并置幂等标记，再写新账本/双写 legacy 键：否则下一回合
    # select_for_turn 会把本次刚双写的 wish_facts/relay_facts 再迁移成「全局」
    # 条目，导致未命中的局部铁律也被全量注入。
    directives.migrate_legacy(state)
    prompt = directives.build_registration_prompt(
        clean_text, kind=kind, known=directives.active_directives(state),
        roster=_roster_names(state), worldbook=_worldbook_terms(state))
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


def grant_wish(state: dict, question: str, *, client=None, model: str = "",
               request_kwargs: dict | None = None, provider: str = "deepseek",
               api_key: str | None = None,
               model_fn: Optional[Model] = None) -> dict[str, Any]:
    """三愿：机制护栏 → 结构化登记 → **登记成功才扣费**。

    返回 ``{"row", "granted", "rejected", "remaining", "meta"}``。
    ``granted`` 是给玩家看的铁律落地文本（结构化 fact_norm 或兜底原文）。
    校验类失败抛 :class:`DirectiveClientError`（不扣费）。
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

    row, meta = _register_structured(
        state, clean, kind=directives.KIND_WISH,
        model=_budgeted(client, model, request_kwargs, provider, model_fn),
        api_key=api_key)
    # 原子扣费点：登记已落账本，最后才消耗次数（顺序不可颠倒）。
    engine.cheat_code.consume(state)
    remaining = engine.cheat_code.remaining_wishes(state)
    # 兼容旧存档/旧读取方：wish_facts 继续双写（不删旧键，双读期）。
    state.setdefault("wish_facts", []).append(
        {"wish": clean, "granted": row.get("fact_norm"),
         "round": int(state.get("round") or 0), "directive_id": row.get("id")})
    return {"row": row, "granted": str(row.get("fact_norm") or clean),
            "rejected": rejected, "remaining": remaining, "meta": meta}


def append_relay_fact(state: dict, question: str, *, client=None, model: str = "",
                      request_kwargs: dict | None = None, provider: str = "deepseek",
                      api_key: str | None = None,
                      model_fn: Optional[Model] = None) -> dict[str, Any]:
    """永久增补：机制护栏 → 结构化登记（无扣费）。

    返回 ``{"row", "text", "rejected", "count", "meta"}``。
    """
    try:
        clean, rejected = directives.mechanism_guard(question)
    except ValueError as exc:
        raise DirectiveClientError(str(exc)) from exc
    if not clean:
        raise DirectiveClientError(
            "增补在剥离机制诉求后为空——增补只能修改世界观与剧情，不能修改游戏机制。")

    row, meta = _register_structured(
        state, clean, kind=directives.KIND_RELAY,
        model=_budgeted(client, model, request_kwargs, provider, model_fn),
        api_key=api_key)
    # 兼容旧读取方：relay_facts 继续双写。
    engine.cheat_code.record_relay_fact(
        state, {"fact": clean, "text": row.get("fact_norm"),
                "round": int(state.get("round") or 0), "directive_id": row.get("id")})
    return {"row": row, "text": str(row.get("fact_norm") or clean),
            "rejected": rejected,
            "count": len(directives.active_directives(state)), "meta": meta}


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
