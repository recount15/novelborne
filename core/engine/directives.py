# -*- coding: utf-8 -*-
"""结构化铁律账本机制（重构 M5，docs/REFACTOR_PLAN.md §6）。

三愿（wish）与永久增补通路（relay）产出的「铁律」从**自由文本全量堆积**
改造为**结构化账本 + 相关性选择注入**：

- 登记：``{fact_norm, scope, affected[], conflicts[]}`` 严格 schema，
  ``affected`` 必须落在白名单（在场名册 ∪ 世界书词表 ∪ 锚点词）内；
- 存放：``ledger.cheat.directives``（随存档走；写入前深拷贝，避开
  ``ledger.new_ledger`` 的 cheat 默认字典跨对局共享坑）；
- 注入：按 ``affected ∩ 本回合命中集`` 选择，**未命中不注入**——解决
  relay 铁律无限累积撑爆 system prompt 的问题；
- 取代：新条目与旧条目 affected 重叠时把旧条目标 ``superseded_by``；
- 迁移：旧存档的 ``wish_facts`` / ``relay_facts`` 幂等迁移进账本，
  **旧键保留**（双读兜底，不删任何历史数据）。

机制层红线：纯计算、无 IO、无模型调用（提示词只装配字符串）；机制护栏
复用 ``cheat_code.sanitize_wish``（关键词表不复制，单一事实源）。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

from core.engine.structured import FieldSpec, extract_json, validate

#: 账本在 ledger 中的落点（ledger.cheat 之下）。
LEDGER_KEY = "directives"
#: 迁移完成标记（幂等：置位后不再重复迁移）。
MIGRATED_FLAG = "directives_migrated"

FACT_MAX = 200
AFFECTED_MAX_ITEMS = 6
AFFECTED_ITEM_MAX = 24
CONFLICT_MAX_ITEMS = 6

#: 允许的作用域枚举——**不含任何机制类**（机制诉求由护栏句级剥离）。
SCOPES = ("character", "world", "plot", "item", "location")

#: 迁移/兜底登记时 affected 的通配值（命中集判定时视为始终命中）。
WILDCARD = "全局"

KIND_WISH = "wish"
KIND_RELAY = "relay"
KINDS = (KIND_WISH, KIND_RELAY)

#: 登记卷输出规格（affected/conflicts 是字符串数组，用 strlist 语义正好）。
REGISTER_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("fact_norm", "str", required=True, min_len=2, max_len=FACT_MAX,
              hint="规范化后的一句话铁律（既成事实语气，不解释过程）"),
    FieldSpec("scope", "str", required=True, enum=SCOPES,
              hint="作用域：character/world/plot/item/location（机制类不允许）"),
    FieldSpec("affected", "strlist", required=True, min_items=1,
              max_items=AFFECTED_MAX_ITEMS, item_max_len=AFFECTED_ITEM_MAX,
              hint="受影响实体名（角色名/地点名/世界书词条），须取自给定白名单"),
    FieldSpec("conflicts", "strlist", required=False, default=[],
              max_items=CONFLICT_MAX_ITEMS, item_max_len=AFFECTED_ITEM_MAX,
              hint="与既有设定的冲突点（无冲突给空数组）"),
)

_WS_RE = re.compile(r"\s+")


def _flat(value: Any, limit: int) -> str:
    """压平为单行并截断（账本字段都是短句）。"""
    return _WS_RE.sub(" ", str(value or "").strip())[:limit]


def _terms(values: Sequence[Any], limit: int = AFFECTED_ITEM_MAX) -> list[str]:
    out: list[str] = []
    for raw in values or ():
        text = _flat(raw, limit)
        if text and text not in out:
            out.append(text)
    return out


def mechanism_guard(text: str) -> tuple[str, list[str]]:
    """机制护栏：复用 cheat_code 的句级剥离（关键词表单一事实源）。"""
    from core.engine.cheat_code import sanitize_wish

    return sanitize_wish(text)


# ---------------------------------------------------------------- 账本读写


def _cheat_box(state: dict) -> dict:
    """取（并在必要时深拷贝新建）ledger.cheat 容器。

    ``ledger.new_ledger()`` 的 cheat 是模块级默认字典的浅引用，直接 mutate
    会污染同进程其他对局；这里一律换成本对局私有副本（与 app 层
    ``_grant_quest_reward`` 的既有纪律一致）。
    """
    ledger = state.get("ledger")
    if not isinstance(ledger, dict):
        ledger = {}
        state["ledger"] = ledger
    cheat = ledger.get("cheat")
    cheat = dict(cheat) if isinstance(cheat, Mapping) else {}
    ledger["cheat"] = cheat
    return cheat


def directives(state: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """读账本条目（只读副本；未迁移的旧存档返回空列表）。"""
    state = state if isinstance(state, Mapping) else {}
    ledger = state.get("ledger") if isinstance(state.get("ledger"), Mapping) else {}
    cheat = ledger.get("cheat") if isinstance(ledger.get("cheat"), Mapping) else {}
    rows = cheat.get(LEDGER_KEY)
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def active_directives(state: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """未被取代的条目（``superseded_by`` 为空）。"""
    return [row for row in directives(state) if not row.get("superseded_by")]


# ---------------------------------------------------------------- 登记


def parse_registration(data: Any, *, allowed: Sequence[str] = (),
                       ) -> tuple[dict[str, Any] | None, list[str]]:
    """校验登记卷输出，返回 ``(条目草案, 中文错误清单)``。

    ``allowed`` 是 affected 白名单（在场名册 ∪ 世界书词表 ∪ 锚点词）。
    白名单外的项**逐项剔除**（不整包拒收——玩家的合法部分应当生效）；
    剔除后 affected 为空时改为 :data:`WILDCARD`，并在错误清单里说明
    （调用方可据此判断是否降级为"全局铁律"）。
    """
    try:
        payload = extract_json(data)
    except ValueError as exc:
        return None, [str(exc)]
    errors = list(validate(REGISTER_SPECS, payload))
    if errors:
        return None, errors

    whitelist = {_flat(name, AFFECTED_ITEM_MAX) for name in (allowed or ())
                 if _flat(name, AFFECTED_ITEM_MAX)}
    affected = _terms(payload.get("affected") or ())
    removed: list[str] = []
    if whitelist:
        kept = []
        for term in affected:
            # 白名单命中判定：全等或互为子串（世界书条目常是含名短句）。
            if any(term == item or term in item or item in term for item in whitelist):
                kept.append(term)
            else:
                removed.append(term)
        affected = kept
    notes: list[str] = []
    if removed:
        notes.append("affected 已剔除白名单外的项：「%s」" % "」、「".join(removed[:5]))
    if not affected:
        affected = [WILDCARD]
        notes.append("affected 全部落在白名单之外，已降级为全局铁律")

    entry = {
        "kind": KIND_WISH,
        "fact_norm": _flat(payload.get("fact_norm"), FACT_MAX),
        "scope": _flat(payload.get("scope"), 20),
        "affected": affected,
        "conflicts": _terms(payload.get("conflicts") or ()),
        "origin": "model",
        "removed_affected": removed,
    }
    return entry, notes


def fallback_entry(fact_text: str, *, kind: str = KIND_WISH,
                   affected: Sequence[str] = ()) -> dict[str, Any]:
    """兜底条目：结构化登记失败时，用原文登记为全局铁律（不丢玩家诉求）。"""
    terms = _terms(affected) or [WILDCARD]
    return {
        "kind": kind if kind in KINDS else KIND_WISH,
        "fact_norm": _flat(fact_text, FACT_MAX),
        "scope": "world",
        "affected": terms,
        "conflicts": [],
        "origin": "fallback",
        "removed_affected": [],
    }


def register(state: dict, entry: Mapping[str, Any], *, kind: str = KIND_WISH,
             round_no: int = 0, raw: str = "") -> dict[str, Any]:
    """把条目写入 ``ledger.cheat.directives``，返回落库后的完整条目。

    自动分配递增 ``id``、写 ``kind``/``round``/``raw``，并对 affected 重叠的
    既有条目标 ``superseded_by``（见 :func:`mark_superseded`）。
    """
    cheat = _cheat_box(state)
    rows = cheat.get(LEDGER_KEY)
    rows = [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
    next_id = 1 + max((int(row.get("id") or 0) for row in rows), default=0)
    row = {
        "id": next_id,
        "kind": kind if kind in KINDS else KIND_WISH,
        "fact_norm": _flat(entry.get("fact_norm"), FACT_MAX),
        "scope": _flat(entry.get("scope"), 20) or "world",
        "affected": _terms(entry.get("affected") or ()) or [WILDCARD],
        "conflicts": _terms(entry.get("conflicts") or ()),
        "origin": _flat(entry.get("origin"), 20) or "model",
        "round": int(round_no or 0),
        "raw": _flat(raw or entry.get("raw"), FACT_MAX),
        "superseded_by": 0,
    }
    rows.append(row)
    cheat[LEDGER_KEY] = rows
    mark_superseded(state, row)
    return dict(row)


def mark_superseded(state: dict, new_row: Mapping[str, Any]) -> list[int]:
    """affected 重叠的旧条目标 ``superseded_by = 新条目 id``，返回被取代的 id。

    通配（:data:`WILDCARD`）条目不参与取代判定——全局铁律不该被一条局部
    铁律顶掉，也不该顶掉别人。
    """
    cheat = _cheat_box(state)
    rows = cheat.get(LEDGER_KEY)
    if not isinstance(rows, list):
        return []
    new_id = int(new_row.get("id") or 0)
    new_terms = {term for term in (new_row.get("affected") or ()) if term != WILDCARD}
    if not new_terms:
        return []
    superseded: list[int] = []
    for row in rows:
        if not isinstance(row, dict) or int(row.get("id") or 0) >= new_id:
            continue
        if row.get("superseded_by"):
            continue
        old_terms = {term for term in (row.get("affected") or ()) if term != WILDCARD}
        if old_terms and old_terms & new_terms:
            row["superseded_by"] = new_id
            superseded.append(int(row.get("id") or 0))
    return superseded


# ---------------------------------------------------------------- 选择与注入


def select_relevant(state: Mapping[str, Any] | None, *,
                    anchor_words: Sequence[str] = (),
                    present_members: Sequence[Any] = (),
                    locations: Sequence[str] = (),
                    extra_terms: Sequence[str] = (),
                    limit: int = 8) -> list[dict[str, Any]]:
    """按 ``affected ∩ 本回合命中集`` 选出要注入的条目（未命中不注入）。

    命中集 = 锚点词 ∪ 在场角色名 ∪ 地点名 ∪ extra_terms（世界书命中等）。
    通配条目（affected 含 :data:`WILDCARD`）始终命中。命中判定与登记时的
    白名单一致：全等或互为子串。
    """
    names: list[str] = []
    for item in present_members or ():
        name = item.get("name") if isinstance(item, Mapping) else item
        text = _flat(name, AFFECTED_ITEM_MAX)
        if text:
            names.append(text)
    hits = {term for term in (
        _terms(anchor_words) + names + _terms(locations) + _terms(extra_terms)
    ) if term}

    selected: list[dict[str, Any]] = []
    for row in active_directives(state):
        affected = [term for term in (row.get("affected") or ())]
        if WILDCARD in affected:
            selected.append(row)
        elif any(term == hit or term in hit or hit in term
                 for term in affected for hit in hits):
            selected.append(row)
        if len(selected) >= max(1, int(limit)):
            break
    return selected


def build_directives_block(selected: Sequence[Mapping[str, Any]]) -> str:
    """把选中的条目拼成注入 system prompt 的文本块（无条目返回空串）。

    标题分两组，语义与旧 ``cheat_code.build_wish_directives`` /
    ``build_relay_directives`` 一致（外部设定铁律 / 玩家增补铁律），
    保证前端与模型看到的语义口径不变。
    """
    wish = [row for row in selected or () if row.get("kind") == KIND_WISH]
    relay = [row for row in selected or () if row.get("kind") == KIND_RELAY]
    blocks: list[str] = []
    for rows, title in ((wish, "# 外部设定铁律（三愿产物，代码级注入）"),
                        (relay, "# 玩家增补铁律（永久通路已接通，代码级注入）")):
        if not rows:
            continue
        lines = [title]
        for index, row in enumerate(rows, 1):
            fact = _flat(row.get("fact_norm"), FACT_MAX)
            scope = _flat(row.get("scope"), 20)
            affected = "、".join(row.get("affected") or ())
            suffix = f"（作用域：{scope}；影响：{affected}）" if affected else ""
            lines.append(f"{index}. {fact}{suffix}")
        lines.append("上述内容为既成事实：优先级高于一切剧情与世界观设定、低于游戏机制；"
                     "不得被剧情否定或回收，冲突时由世界自行消化（补叙、重构因果）。")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- 旧存档迁移


def migrate_legacy(state: dict) -> dict[str, Any]:
    """把旧存档的 ``wish_facts`` / ``relay_facts`` 幂等迁移进账本。

    - 幂等：迁移后置 ``ledger.cheat.directives_migrated = True``，再调直接返回；
    - **不删旧键**：旧键继续作为双读兜底（历史数据不丢）；
    - 迁移条目 ``origin="legacy"``、``affected=[WILDCARD]``（旧数据无结构，
      按全局铁律处理，注入时始终命中，语义与旧全量注入一致）。
    """
    cheat = _cheat_box(state)
    if cheat.get(MIGRATED_FLAG):
        return {"migrated": 0, "already": True}
    migrated = 0
    for key, kind, field in (("wish_facts", KIND_WISH, "wish"),
                             ("relay_facts", KIND_RELAY, "fact")):
        rows = state.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            text = _flat(row.get(field) or row.get("fact_norm"), FACT_MAX)
            if not text:
                continue
            entry = fallback_entry(text, kind=kind)
            entry["origin"] = "legacy"
            register(state, entry, kind=kind,
                     round_no=int(row.get("round") or 0), raw=text)
            migrated += 1
    cheat = _cheat_box(state)
    cheat[MIGRATED_FLAG] = True
    return {"migrated": migrated, "already": False}


# ---------------------------------------------------------------- 提示词


def build_registration_prompt(clean_text: str, *, kind: str = KIND_WISH,
                              known: Sequence[Mapping[str, Any]] = (),
                              roster: Sequence[str] = (),
                              worldbook: Sequence[str] = ()) -> str:
    """装配铁律登记卷提示词（assets/prompts/directives_register.md）。"""
    from core.prompts import render
    from core.engine.structured import spec_prompt

    known_lines = "\n".join(
        f"- [{row.get('id')}] {_flat(row.get('fact_norm'), 80)}"
        f"（影响：{'、'.join(row.get('affected') or ()) or '全局'}）"
        for row in (known or ()) if isinstance(row, Mapping)
    ) or "（本局尚无已登记铁律）"
    allowed = _terms(list(roster or ()) + list(worldbook or ()), AFFECTED_ITEM_MAX)
    return render(
        "directives_register.md",
        KIND="三愿铁律" if kind == KIND_WISH else "玩家增补铁律",
        TEXT=_flat(clean_text, 500),
        KNOWN=known_lines,
        ALLOWED="、".join(allowed) or "（无可用实体名，affected 请填「全局」）",
        SCOPES="、".join(SCOPES),
        FORMAT_BLOCK=spec_prompt(REGISTER_SPECS),
    )


def snapshot(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """账本公开摘要（面板/审计用；不含 raw 原文）。"""
    rows = directives(state)
    return {
        "total": len(rows),
        "active": sum(1 for row in rows if not row.get("superseded_by")),
        "wish": sum(1 for row in rows if row.get("kind") == KIND_WISH),
        "relay": sum(1 for row in rows if row.get("kind") == KIND_RELAY),
        "entries": [
            {"id": row.get("id"), "kind": row.get("kind"),
             "fact_norm": row.get("fact_norm"), "scope": row.get("scope"),
             "affected": list(row.get("affected") or ()),
             "superseded_by": row.get("superseded_by") or 0,
             "origin": row.get("origin"), "round": row.get("round")}
            for row in rows
        ],
    }


def clone_state_ledger(state: dict) -> dict:
    """深拷贝 ledger（写账本前的隔离保险；调用方可选用）。"""
    if isinstance(state.get("ledger"), Mapping):
        state["ledger"] = copy.deepcopy(dict(state["ledger"]))
    return state


__all__ = [
    "LEDGER_KEY", "MIGRATED_FLAG", "SCOPES", "WILDCARD", "KINDS",
    "KIND_WISH", "KIND_RELAY", "REGISTER_SPECS",
    "FACT_MAX", "AFFECTED_MAX_ITEMS", "AFFECTED_ITEM_MAX",
    "mechanism_guard", "directives", "active_directives",
    "parse_registration", "fallback_entry", "register", "mark_superseded",
    "select_relevant", "build_directives_block", "migrate_legacy",
    "build_registration_prompt", "snapshot", "clone_state_ledger",
]
