# -*- coding: utf-8 -*-
"""事实来源、三愿码授权与请求预算的纯契约层（施工卡 C01）。

本模块只定义数据结构与分类规则，不导入应用层、不落盘、不改变任何生成
行为。它是《NARRATIVE_READER_WISH_CONSTRUCTION_PLAN》§2 的代码化：

- 原著事实（canon）只来自宿主解析的原文证据（含 source_hash）；
- 三愿码（authorized）是玩家显式授权的本局改编，授权记录挂在既有
  ``state.ledger.cheat.directives`` 行的 ``authorization`` 子键上——
  不新建平行事实库；
- 本局事件（event）只来自已提交的 story_ledger 行；
- 人物猜测（hypothesis）与普通描写（decoration）不因重复出现升级；
- 旧数据来源不明（unknown_legacy）只如实分类，绝不伪造历史授权。

核心不变量：**来源升级必须携带明确凭据（授权回执或已提交事件）；
重复、摘要压缩、导出加工永远不构成升级依据。**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

FACT_CONTRACT_VERSION = "1"

# ---------------------------------------------------------------- 来源类别

#: 事实来源类别（计划 §2.1）。类别之间只允许通过 upgrade_kind 的显式凭据迁移。
KIND_CANON = "canon"                  # 原著事实：宿主解析的原文证据
KIND_AUTHORIZED = "authorized"        # 玩家授权设定：三愿码/开局配置/金手指
KIND_EVENT = "event"                  # 本局已提交事件
KIND_HYPOTHESIS = "hypothesis"        # 人物猜测、未确认推断
KIND_DECORATION = "decoration"        # 普通描写：无世界规则效力
KIND_UNKNOWN_LEGACY = "unknown_legacy"  # 来源不明的旧数据

PROVENANCE_KINDS = (
    KIND_CANON, KIND_AUTHORIZED, KIND_EVENT,
    KIND_HYPOTHESIS, KIND_DECORATION, KIND_UNKNOWN_LEGACY,
)

#: 授权子类型：三愿码 / 开局配置 / 普通金手指 / 永久增补（授权路径不同，不合并）。
AUTHORIZED_ORIGINS = ("wish", "opening", "golden_finger", "relay")

#: 授权范围域：原著事实只属于 source/book 域；愿望与本局事件只属于 session 域。
SCOPE_SOURCE = "source"
SCOPE_SESSION = "session"


class ContractError(ValueError):
    """契约校验失败；code 供上层分类为硬错误（A 类），不得静默放行。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(_text(x) for x in value)


# ---------------------------------------------------------------- 授权记录

#: 授权状态机：draft -> active -> consumed；needs_clarification 不落效果不扣次；
#: superseded/rolled_back 为终态。
WISH_STATUSES = ("draft", "active", "needs_clarification", "consumed",
                 "superseded", "rolled_back")

#: directives 行内挂载授权记录的键名（旧行无此键 → 来源按 unknown_legacy 处理）。
AUTHORIZATION_KEY = "authorization"


@dataclass(frozen=True)
class WishAuthorizationRecord:
    """三愿码授权记录（计划 §2.2）。

    授权者是玩家；``origin`` 为 "wish"（模型/回退只是解析器，不是授权者）。
    ``raw_text`` 是玩家原话的不可变副本，``accepted_interpretation`` 是系统
    确认的解释——两者分离，防止模型解释悄然扩大授权。
    """

    authorization_id: str
    request_id: str                 # 幂等键：同请求重复提交只扣一次
    raw_text: str
    authorized_origin: str          # AUTHORIZED_ORIGINS 之一
    status: str = "draft"
    accepted_interpretation: str = ""
    target_ids: tuple[str, ...] = ()          # 已解析对象（实体/锚点 id）
    unresolved_target_text: tuple[str, ...] = ()  # 解析不了的对象原文，绝不扩为全局
    explicit_limits: tuple[str, ...] = ()     # 仅玩家明确提出或确认的限制
    global_authority: bool = False            # 仅原愿望明确全局才可为 True
    effect_refs: tuple[str, ...] = ()
    receipt: dict[str, Any] = field(default_factory=dict)  # 扣次/回执（宿主填）
    schema_version: str = FACT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("authorization_id", "request_id", "raw_text"):
            if not _text(getattr(self, name)):
                raise ContractError("wish_" + name)
        if self.authorized_origin not in AUTHORIZED_ORIGINS:
            raise ContractError("wish_origin")
        if self.status not in WISH_STATUSES:
            raise ContractError("wish_status")
        # 全局授权必须携带玩家显式全局凭据；默认绝不放行全局铁律。
        if self.global_authority and self.receipt.get("user_global_grant") is not True:
            raise ContractError("wish_global_without_grant")
        # 无已解析对象、无未解析对象原文、又非显式全局 = 授权范围空洞，
        # 禁止静默升级为全局通配。唯一豁免：回执如实标记 targets_unresolved
        # （对象未解析、按原文生效、未授予全局）——这是诚实留痕，不是扩权。
        if not self.global_authority and not self.target_ids and not self.unresolved_target_text:
            if self.receipt.get("targets_unresolved") is not True:
                raise ContractError("wish_empty_target_widening")
        # 生效/已消耗状态必须有已确认解释，否则授权内容无从界定。
        if self.status in ("active", "consumed") and not _text(self.accepted_interpretation):
            raise ContractError("wish_interpretation_required")

    # ---- directives 行兼容序列化 ------------------------------------
    def to_row_value(self) -> dict[str, Any]:
        return {
            "authorization_id": self.authorization_id,
            "request_id": self.request_id,
            "raw_text": self.raw_text,
            "authorized_origin": self.authorized_origin,
            "status": self.status,
            "accepted_interpretation": self.accepted_interpretation,
            "target_ids": list(self.target_ids),
            "unresolved_target_text": list(self.unresolved_target_text),
            "explicit_limits": list(self.explicit_limits),
            "global_authority": self.global_authority,
            "effect_refs": list(self.effect_refs),
            "receipt": dict(self.receipt),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_row_value(cls, value: Any) -> "WishAuthorizationRecord | None":
        """从 directives 行读取授权记录；旧行无此键返回 None，不伪造回执。"""
        if not isinstance(value, Mapping):
            return None
        try:
            return cls(
                authorization_id=str(value.get("authorization_id") or ""),
                request_id=str(value.get("request_id") or ""),
                raw_text=str(value.get("raw_text") or ""),
                authorized_origin=str(value.get("authorized_origin") or "wish"),
                status=str(value.get("status") or "draft"),
                accepted_interpretation=str(value.get("accepted_interpretation") or ""),
                target_ids=tuple(str(x) for x in value.get("target_ids") or ()),
                unresolved_target_text=tuple(str(x) for x in value.get("unresolved_target_text") or ()),
                explicit_limits=tuple(str(x) for x in value.get("explicit_limits") or ()),
                global_authority=value.get("global_authority") is True,
                effect_refs=tuple(str(x) for x in value.get("effect_refs") or ()),
                receipt=dict(value.get("receipt") or {}),
                schema_version=str(value.get("schema_version") or FACT_CONTRACT_VERSION),
            )
        except ContractError:
            return None


def wish_authorization_of(row: Mapping[str, Any]) -> WishAuthorizationRecord | None:
    """读取指令行的授权记录（便捷口）。"""
    return WishAuthorizationRecord.from_row_value(row.get(AUTHORIZATION_KEY))


# ---------------------------------------------------------------- 事实视图

@dataclass(frozen=True)
class FactView:
    """对既有存储记录的来源判定结果（只读视图，不回写）。"""
    kind: str
    scope: str                       # SCOPE_SOURCE / SCOPE_SESSION
    authority: str                   # "source" / "player" / "committed" / "none" / "unknown"
    note: str = ""


def classify_source_evidence(record: Mapping[str, Any]) -> FactView:
    """宿主解析的原文证据 → canon。无 source_hash 的证据不配 canon。"""
    if _text(record.get("source_hash")) and _text(record.get("book_id")):
        return FactView(KIND_CANON, SCOPE_SOURCE, "source")
    return FactView(KIND_UNKNOWN_LEGACY, SCOPE_SOURCE, "unknown",
                    "evidence_without_source_hash")


def classify_directive_row(row: Mapping[str, Any]) -> FactView:
    """指令行 → authorized（有生效授权）或 unknown_legacy（旧行无回执）。

    指令行永远不可能分类为 canon：愿望改变的是本局，不是原书。
    """
    record = wish_authorization_of(row)
    if record is not None and record.status in ("active", "consumed"):
        return FactView(KIND_AUTHORIZED, SCOPE_SESSION, "player")
    if record is not None:
        # 有记录但未生效（draft/needs_clarification/终态）——尚无授权效力。
        return FactView(KIND_DECORATION, SCOPE_SESSION, "none",
                        "authorization_status_" + record.status)
    return FactView(KIND_UNKNOWN_LEGACY, SCOPE_SESSION, "unknown",
                    "legacy_directive_without_receipt")


def classify_story_ledger_row(row: Mapping[str, Any]) -> FactView:
    """story_ledger 行 → event（已提交）/ unknown_legacy（迁移不确定）/
    decoration（未提交草稿——只是文本，无事实效力）。"""
    if row.get("migration_uncertain"):
        return FactView(KIND_UNKNOWN_LEGACY, SCOPE_SESSION, "unknown",
                        "migration_uncertain")
    committed = row.get("committed") is True or row.get("status", row.get("save_stage")) == "committed"
    if committed:
        return FactView(KIND_EVENT, SCOPE_SESSION, "committed")
    return FactView(KIND_DECORATION, SCOPE_SESSION, "none", "uncommitted_draft")


# ---------------------------------------------------------------- 升级规则

#: 允许的显式来源迁移表；表外一律禁止。重复/观察次数不作为入参存在——
#: 这本身就是契约：升级必须携带凭据，而不是出现得足够多。
_KIND_UPGRADES = {
    (KIND_HYPOTHESIS, KIND_AUTHORIZED),   # 玩家补授权确认某猜测
    (KIND_HYPOTHESIS, KIND_EVENT),        # 猜测被已提交事件证实
    (KIND_UNKNOWN_LEGACY, KIND_AUTHORIZED),  # 玩家事后补授权（新回执）
    (KIND_UNKNOWN_LEGACY, KIND_EVENT),    # 旧记录被已提交事件证实
    (KIND_DECORATION, KIND_EVENT),        # 描写中的动作被提交为事件
    (KIND_AUTHORIZED, KIND_EVENT),        # 愿望效果兑现为本局事件
}


def upgrade_kind(current: str, proposed: str, *, authorization_receipt: Any = None,
                 committed_evidence: Any = None) -> str:
    """凭据驱动的来源迁移；返回升级后的 kind，非法迁移抛 ContractError。

    - canon 只能由宿主原文解析产生，任何运行时迁移都不产生 canon；
    - authorized 永不升为 canon（导出/加工结果不得反写为原著事实）；
    - receipt/evidence 必须非空才允许相应迁移，否则维持原类别。
    """
    if current not in PROVENANCE_KINDS or proposed not in PROVENANCE_KINDS:
        raise ContractError("kind_unknown")
    if current == proposed:
        return current
    if proposed == KIND_CANON:
        raise ContractError("canon_runtime_upgrade_forbidden")
    if (current, proposed) not in _KIND_UPGRADES:
        raise ContractError("kind_upgrade_forbidden")
    if proposed == KIND_AUTHORIZED and not authorization_receipt:
        raise ContractError("upgrade_requires_authorization")
    if proposed == KIND_EVENT and not committed_evidence:
        raise ContractError("upgrade_requires_committed_evidence")
    return proposed


# ---------------------------------------------------------------- 场景与任务

EVENT_STATES = ("not_started", "active", "completed", "changed_by_player", "unavailable")

TASK_STATUSES = ("pending", "active", "partial", "completed",
                 "failed", "unavailable", "needs_review")


@dataclass(frozen=True)
class SceneCard:
    """当前场景约束卡（计划 §2.3；由 C05 填充与消费）。"""
    scene_id: str
    source_ref: str                  # 原文锚定（章节/事件引用）
    current_objective: str           # 本轮唯一局部目标
    participants: tuple[str, ...] = ()
    character_motives: dict[str, str] = field(default_factory=dict)
    prerequisites: tuple[str, ...] = ()
    event_states: dict[str, str] = field(default_factory=dict)
    authorized_deviations: tuple[str, ...] = ()   # 授权 id 引用
    player_action: str = ""
    forbidden_early_reveals: tuple[str, ...] = ()
    optional_threads: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _text(self.scene_id) or not _text(self.source_ref):
            raise ContractError("scene_identity")
        if not _text(self.current_objective):
            raise ContractError("scene_objective_required")
        if not _strings(list(self.participants)) or not _strings(list(self.prerequisites)) \
                or not _strings(list(self.forbidden_early_reveals)) \
                or not _strings(list(self.optional_threads)):
            raise ContractError("scene_string_lists")
        for who, motive in self.character_motives.items():
            if not _text(who) or not _text(motive):
                raise ContractError("scene_motive_schema")
        for name, state in self.event_states.items():
            if not _text(name) or state not in EVENT_STATES:
                raise ContractError("scene_event_state")
        # 原创支线软上限（计划 §3.2 初始 1 条，结构性硬顶 4 条防滥用）。
        if len(self.optional_threads) > 4:
            raise ContractError("scene_thread_limit")


@dataclass(frozen=True)
class TaskProjection:
    """任务投影（计划 §2.3；C06 对接 quest 权威结算）。"""
    task_id: str
    origin_refs: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    completion_predicates: tuple[str, ...] = ()
    evidence_event_ids: tuple[str, ...] = ()   # 只能引用已提交事件
    status: str = "pending"
    settlement_id: str = ""                     # 权威结算一次的凭据

    def __post_init__(self) -> None:
        if not _text(self.task_id):
            raise ContractError("task_identity")
        if self.status not in TASK_STATUSES:
            raise ContractError("task_status")
        for name in ("origin_refs", "prerequisites", "completion_predicates",
                     "evidence_event_ids"):
            if not _strings(list(getattr(self, name))):
                raise ContractError("task_string_lists")
        # F12/F15 结构性前置：完成/部分完成必须绑定已提交事件证据；
        # 意图、计划、尝试、否定都不构成完成。
        if self.status in ("completed", "partial") and not self.evidence_event_ids:
            raise ContractError("task_completion_requires_evidence")
        if self.status == "completed" and not _text(self.settlement_id):
            raise ContractError("task_completion_requires_settlement")


# ---------------------------------------------------------------- 请求预算

#: 回合请求终态（计划 §4.2）。needs_clarification 不是挂起中的服务器任务。
TURN_TERMINAL_STATUSES = ("committed", "committed_with_warnings",
                          "needs_clarification", "failed_recoverable", "cancelled")


@dataclass(frozen=True)
class RequestBudgetProfile:
    """请求级预算（计划 §4.2 初始工程配置；C04 接线，本卡只定数据）。

    数值是待验收冻结的初始配置：总模型尝试含网络重试、校验与修复；
    单次超时不得超过剩余预算由 C04 的 deadline 传递实现。
    """
    max_repair_passes: int = 2
    max_model_attempts: int = 6
    deadline_seconds: float = 180.0
    reader_max_attempts: int = 3
    reader_deadline_seconds: float = 60.0

    def __post_init__(self) -> None:
        if type(self.max_repair_passes) is not int or not 0 <= self.max_repair_passes <= 4:
            raise ContractError("budget_max_repair_passes")
        for name in ("max_model_attempts", "reader_max_attempts"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 64:
                raise ContractError("budget_" + name)
        for name in ("deadline_seconds", "reader_deadline_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not value > 0 or value != value \
                    or value in (float("inf"), float("-inf")):
                raise ContractError("budget_" + name)

    def cluster_policy_kwargs(self) -> dict[str, Any]:
        """映射到 agent_cluster BudgetPolicy 的构造参数（C04 使用）。"""
        return {"max_calls": max(self.max_model_attempts, 1),
                "deadline_seconds": float(self.deadline_seconds)}


def _default_request_budget() -> RequestBudgetProfile:
    """部署级预算覆盖（过渡入口）：state["request_budget"] 的 UI/存档接线
    尚未落地（C04 只定数据），而冻结默认值会在慢网关/弱模型上把智能体
    编队锁死在 180 秒墙钟里（真机验收实测 job_deadline_exceeded）。
    仅识别显式设置的环境变量；不设置时默认值逐字节不变。非法取值按
    契约直接抛错——预算配置错必须响，不得静默回退。"""
    import os
    overrides: dict[str, Any] = {}
    for name, key in (("FATE_REQUEST_BUDGET_SECONDS", "deadline_seconds"),
                      ("FATE_READER_BUDGET_SECONDS", "reader_deadline_seconds")):
        raw = os.environ.get(name)
        if raw:
            overrides[key] = float(raw)
    return RequestBudgetProfile(**overrides)


DEFAULT_REQUEST_BUDGET = _default_request_budget()
