# -*- coding: utf-8 -*-
"""施工卡 C01：事实来源契约、三愿码授权记录与请求预算的纯结构测试。

关键反例来自既有缺陷行为：
- directives.parse_registration 会把白名单过滤后的空 affected 静默扩为
  全局铁律（WILDCARD）——授权记录必须拒绝这种范围扩大；
- 旧指令行没有任何授权回执——只能分类为 unknown_legacy，绝不能伪造授权
  或升为 canon。
"""
import pytest

from core.engine import fact_contract as fc
from core.state_schema import TRANSACTIONAL_KEYS
from core.api.save_contract import classify_state


def _wish(**overrides) -> fc.WishAuthorizationRecord:
    base = dict(
        authorization_id="auth-1", request_id="req-1",
        raw_text="我希望获得能看见台球走位线的能力",
        authorized_origin="wish", status="active",
        accepted_interpretation="本局内主角获得走位线视觉能力",
        target_ids=("char:player",),
    )
    base.update(overrides)
    return fc.WishAuthorizationRecord(**base)


# ------------------------------------------------ 授权记录与范围安全

def test_wish_record_rejects_empty_target_widening():
    """无已解析对象、无未解析原文、又非显式全局 → 拒绝，绝不静默通配。"""
    with pytest.raises(fc.ContractError) as exc:
        _wish(target_ids=(), unresolved_target_text=())
    assert exc.value.code == "wish_empty_target_widening"


def test_wish_record_requires_explicit_global_grant():
    """global_authority 必须携带玩家显式全局凭据。"""
    with pytest.raises(fc.ContractError) as exc:
        _wish(global_authority=True)
    assert exc.value.code == "wish_global_without_grant"
    receipt = {"user_global_grant": True}
    assert _wish(global_authority=True, receipt=receipt).global_authority is True


def test_unresolved_targets_do_not_become_global():
    """解析不了的对象保留原文，不扩大范围——愿望本身仍可登记。"""
    record = _wish(target_ids=(), unresolved_target_text=("那位没名字的老头",))
    assert record.unresolved_target_text == ("那位没名字的老头",)
    assert record.global_authority is False


def test_active_wish_requires_interpretation():
    with pytest.raises(fc.ContractError) as exc:
        _wish(accepted_interpretation="  ")
    assert exc.value.code == "wish_interpretation_required"


def test_wish_record_roundtrip():
    record = _wish(explicit_limits=("只在台球厅生效",), effect_refs=("effect-1",))
    restored = fc.WishAuthorizationRecord.from_row_value(record.to_row_value())
    assert restored == record


def test_from_row_invalid_returns_none():
    assert fc.WishAuthorizationRecord.from_row_value(None) is None
    # authorization_id 缺失的畸形记录按旧数据处理，不抛异常、不伪造。
    assert fc.WishAuthorizationRecord.from_row_value({"status": "active"}) is None


# ------------------------------------------------ 既有存储的分类视图

def test_legacy_directive_row_is_unknown_legacy_never_canon():
    row = {"id": 3, "kind": "wish", "fact_norm": "主角会飞", "origin": "model",
           "affected": ["全局"], "scope": "world"}
    view = fc.classify_directive_row(row)
    assert view.kind == fc.KIND_UNKNOWN_LEGACY
    assert view.authority == "unknown"


def test_active_wish_classifies_authorized():
    row = {"kind": "wish", "authorization": _wish().to_row_value()}
    view = fc.classify_directive_row(row)
    assert (view.kind, view.authority) == (fc.KIND_AUTHORIZED, "player")
    # 未生效状态（draft / needs_clarification）没有授权效力。
    draft = fc.classify_directive_row(
        {"authorization": _wish(status="needs_clarification").to_row_value()})
    assert draft.kind == fc.KIND_DECORATION


def test_source_evidence_requires_source_hash():
    canon = fc.classify_source_evidence(
        {"book_id": "b1", "source_hash": "h1", "chapter_no": 3})
    assert (canon.kind, canon.authority) == (fc.KIND_CANON, "source")
    broken = fc.classify_source_evidence({"book_id": "b1", "quote": "……"})
    assert broken.kind == fc.KIND_UNKNOWN_LEGACY


def test_ledger_row_classification():
    committed = fc.classify_story_ledger_row(
        {"committed": True, "narrative": "他推开了当铺的门。"})
    assert (committed.kind, committed.authority) == (fc.KIND_EVENT, "committed")
    migrated = fc.classify_story_ledger_row({"migration_uncertain": True})
    assert migrated.kind == fc.KIND_UNKNOWN_LEGACY
    draft = fc.classify_story_ledger_row({"narrative": "也许有黑手党。"})
    assert draft.kind == fc.KIND_DECORATION


# ------------------------------------------------ 升级规则

def test_canon_cannot_be_produced_at_runtime():
    for current in (fc.KIND_HYPOTHESIS, fc.KIND_AUTHORIZED, fc.KIND_EVENT,
                    fc.KIND_DECORATION, fc.KIND_UNKNOWN_LEGACY):
        with pytest.raises(fc.ContractError):
            fc.upgrade_kind(current, fc.KIND_CANON,
                            authorization_receipt={"x": 1}, committed_evidence="e1")


def test_repetition_is_not_an_upgrade_path():
    """API 不存在"出现次数"入参；无凭据的迁移一律拒绝，类别保持原样。"""
    assert fc.upgrade_kind(fc.KIND_HYPOTHESIS, fc.KIND_HYPOTHESIS) == fc.KIND_HYPOTHESIS
    with pytest.raises(fc.ContractError):
        fc.upgrade_kind(fc.KIND_HYPOTHESIS, fc.KIND_AUTHORIZED)
    with pytest.raises(fc.ContractError):
        fc.upgrade_kind(fc.KIND_DECORATION, fc.KIND_EVENT)


def test_upgrades_require_credentials():
    assert fc.upgrade_kind(fc.KIND_HYPOTHESIS, fc.KIND_AUTHORIZED,
                           authorization_receipt={"authorization_id": "a1"}) \
        == fc.KIND_AUTHORIZED
    assert fc.upgrade_kind(fc.KIND_UNKNOWN_LEGACY, fc.KIND_EVENT,
                           committed_evidence="event-17") == fc.KIND_EVENT
    with pytest.raises(fc.ContractError):
        fc.upgrade_kind(fc.KIND_HYPOTHESIS, fc.KIND_EVENT)


# ------------------------------------------------ 场景卡与任务投影

def _scene(**overrides) -> fc.SceneCard:
    base = dict(scene_id="s1", source_ref="ch7:offset1200",
                current_objective="化解课堂冲突并决定是否赴约",
                participants=("杨明", "陈梦妍"),
                event_states={"课堂冲突": "active"})
    base.update(overrides)
    return fc.SceneCard(**base)


def test_scene_card_contract():
    card = _scene()
    assert card.event_states["课堂冲突"] == "active"
    with pytest.raises(fc.ContractError):
        _scene(current_objective=" ")
    with pytest.raises(fc.ContractError):
        _scene(event_states={"课堂冲突": "已完成但没证据"})
    with pytest.raises(fc.ContractError):
        _scene(optional_threads=("a", "b", "c", "d", "e"))


def test_task_completion_requires_committed_evidence_and_settlement():
    ok = fc.TaskProjection(task_id="t1", status="completed",
                           evidence_event_ids=("event-9",), settlement_id="settle-1")
    assert ok.status == "completed"
    with pytest.raises(fc.ContractError):
        fc.TaskProjection(task_id="t1", status="completed")  # 无证据不得完成
    with pytest.raises(fc.ContractError):
        fc.TaskProjection(task_id="t1", status="completed",
                          evidence_event_ids=("event-9",))  # 无结算凭据
    # 计划/意图状态不需要证据。
    assert fc.TaskProjection(task_id="t1", status="pending").status == "pending"


# ------------------------------------------------ 请求预算

def test_request_budget_defaults_finite():
    profile = fc.DEFAULT_REQUEST_BUDGET
    kwargs = profile.cluster_policy_kwargs()
    assert kwargs["max_calls"] == 6 and kwargs["deadline_seconds"] == 180.0
    assert fc.RequestBudgetProfile(max_model_attempts=0).cluster_policy_kwargs()["max_calls"] == 1


def test_request_budget_env_override(monkeypatch):
    """部署级预算覆盖（过渡入口）：显式设置环境变量才生效，默认逐字节不变；
    非法取值必须响（float 转换抛 ValueError），不得静默回退。"""
    monkeypatch.setenv("FATE_REQUEST_BUDGET_SECONDS", "1500")
    monkeypatch.setenv("FATE_READER_BUDGET_SECONDS", "240")
    profile = fc._default_request_budget()
    assert profile.deadline_seconds == 1500.0
    assert profile.reader_deadline_seconds == 240.0
    assert profile.max_model_attempts == 6
    monkeypatch.setenv("FATE_REQUEST_BUDGET_SECONDS", "not-a-number")
    with pytest.raises(ValueError):
        fc._default_request_budget()


def test_request_budget_rejects_nonfinite():
    with pytest.raises(fc.ContractError):
        fc.RequestBudgetProfile(deadline_seconds=float("inf"))
    with pytest.raises(fc.ContractError):
        fc.RequestBudgetProfile(deadline_seconds=0)
    with pytest.raises(fc.ContractError):
        fc.RequestBudgetProfile(max_model_attempts=-1)
    with pytest.raises(fc.ContractError):
        fc.RequestBudgetProfile(max_repair_passes=5)


# ------------------------------------------------ 与既有机制的兼容证明

def test_authorization_fields_survive_save_classification():
    """save_contract 不认识 authorization 子键也不得拒绝——向后兼容证明。"""
    state = {
        "system": "规则", "save_stage": "committed",
        "options": [{"key": k, "text": f"行动{k}"} for k in "ABCDEF"],
        "ledger": {"cheat": {"directives": [
            {"id": 1, "kind": "wish", "fact_norm": "……",
             "authorization": _wish().to_row_value()},
        ]}},
    }
    assert classify_state(state) == "committed"


def test_directive_storage_remains_transactional():
    """授权记录挂在 ledger.cheat 下；ledger 必须一直在事务键集合内。"""
    assert "ledger" in TRANSACTIONAL_KEYS
