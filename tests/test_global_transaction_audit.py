# -*- coding: utf-8 -*-
"""C10 全域事务和兼容审计：新增可变字段回滚/revision/幂等/持久化 + 兼容面。

验收（计划 §C10）：
- 数据库提交失败零部分效果（对 C01–C09 全部新增可变字段验证）；
- 已持久化后镜像失败仍显示/恢复正确状态（outbox 投递新字段完整）；
- 旧 UI 请求兼容（save/load/roster 不带新参数仍按原语义工作）；
- 不改变源 hash、原文引文及正式选项协议。

四路径（基础/强化 × 智能体开/关）共用同一提交边界（_stream_response 的
request_id+expected_revision 存档与 on_send 全量快照回滚），由本文件的
TurnTransaction/持久化钉测试 + 既有 test_turn_pipeline / test_story_agent_integration /
test_choice_agent_integration / test_v3_agent_cluster / test_turn_budget 共同覆盖。
"""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core import server
from core.api import save_contract
from core.engine import directives, fact_contract, persistence
from core.engine.turn_transaction import TurnTransaction
from core.state_schema import TRANSACTIONAL_KEYS

WISH_RAW = "我希望北墙的裂痕后面真的有一条暗渠"
WISH_FACT = "北墙裂痕后有暗渠"


def rich_state() -> dict:
    """开局合法态 + C01–C09 新增可变字段（授权、账本、任务、摘要、场景）。"""
    state: dict = {
        "system": "rules", "mode": "强化模式", "save_stage": "opening", "round": 0,
        "history": [], "story_ledger": [],
        "task_registry": [], "task_progress_log": [],
        "chapter_arc_plan": {"chapter": 1},
        "handoff": {"wish_authorizations": [{"authorization_id": "auth-1"}]},
        "compression_record": {"round": 0, "fidelity": "ok"},
        "compression_due_round": 10,
        "scene_participants": ["张三"],
        "character_states": {"李四": {"body": {"gender": "female"}}},
        "quest": {"kind": "寻物", "status": "accepted", "settlement_id": "settle-1"},
    }
    entry, _ = directives.parse_registration(
        {"fact_norm": WISH_FACT, "scope": "world", "affected": ["北墙"], "conflicts": []},
        allowed=())
    record = fact_contract.WishAuthorizationRecord(
        authorization_id="auth-1", request_id="req-1", raw_text=WISH_RAW,
        authorized_origin="wish", status="active",
        accepted_interpretation=WISH_FACT, target_ids=("北墙",))
    directives.register(state, entry, kind="wish", raw=WISH_RAW,
                        authorization=record.to_row_value())
    return state


def committed_round_row(round_no: int = 1) -> dict:
    return {"turn_id": f"round-{round_no}", "round": round_no, "chapter": 1,
            "chapter_round": round_no, "narrative": f"第{round_no}回合正文。",
            "options": [], "committed": True}


# ---------------------------------------------------------------- 注册面（红）

def test_turn_mutable_keys_are_registered():
    """审计发现：回合内写入但未登记 TRANSACTIONAL_KEYS 的键必须补齐。"""
    missing = [key for key in (
        "handoff",                    # C09 接手包（_compress_context 回合内写）
        "compression_record",         # C09 压缩记录（回合内写）
        "compression_due_round",      # 摘要到期标记（回合末写、下回合开头消费）
        "scene_participants",         # 场景 NPC 闲聊名单（回合末派生）
        "character_states",           # 回合内角色状态记忆更新
    ) if key not in TRANSACTIONAL_KEYS]
    assert missing == [], f"回合可变键未登记事务面: {missing}"


# ---------------------------------------------------------------- 事务钉

def test_commit_failure_zero_partial_effects_on_new_fields(tmp_path):
    """数据库提交失败：候选中的全部新增可变字段不得留下任何部分效果。"""
    original = rich_state()
    target = copy.deepcopy(original)
    tx = TurnTransaction(target)
    tx.advance("PLANNED")
    tx.candidate["round"] = 1
    tx.candidate["story_ledger"] = [committed_round_row(1)]
    tx.candidate["task_registry"] = [{"task_id": "t1", "status": "active"}]
    tx.candidate["handoff"] = {"wish_authorizations": [{"authorization_id": "auth-1"}],
                               "round": 1}
    tx.candidate["compression_due_round"] = 10
    tx.candidate["scene_participants"] = ["王五"]
    tx.advance("GENERATED")
    tx.advance("VALIDATED")
    with patch.object(persistence, "_db_save",
                      side_effect=sqlite3.OperationalError("fault")):
        with pytest.raises(sqlite3.OperationalError):
            tx.commit(root=tmp_path, session_id="audit", request_id="r1", target=target)
    assert tx.status == "FAILED"
    assert target == original, "提交失败后目标状态必须与事务前逐字节一致"


def test_mirror_failure_recovers_new_fields(tmp_path):
    """已持久化后镜像失败：revision 已定、恢复路径完整携带新字段。"""
    state = rich_state()
    state["round"] = 1
    state["story_ledger"] = [committed_round_row(1)]
    state["task_registry"] = [{"task_id": "t1", "status": "active"}]
    with patch.object(persistence, "_atomic_json", side_effect=OSError("mirror down")):
        persistence.save_state(state, root=tmp_path, session_id="audit",
                               request_id="r1", expected_revision=0)
    assert state["revision"] == 1, "数据库权威提交已成功，镜像失败不撤销 revision"
    # 恢复状态显示正确：列表/元数据走数据库，不依赖镜像。
    meta = persistence.save_metadata("latest", root=tmp_path, session_id="audit")
    assert meta is not None and meta["round"] == 1
    # 镜像故障移除后 outbox 补投，JSON 镜像与严格读档都完整携带新字段。
    assert persistence.flush_outbox(tmp_path) == 1
    mirror = json.loads((tmp_path / "saves" / "latest-audit.json").read_text(encoding="utf-8"))
    for key in ("story_ledger", "task_registry", "handoff", "compression_due_round",
                "scene_participants", "character_states", "quest"):
        assert key in mirror["state"], f"镜像缺失新字段 {key}"
    restored = persistence.load_state_strict("latest", root=tmp_path, session_id="audit")
    assert restored["story_ledger"][0]["turn_id"] == "round-1"
    assert restored["handoff"]["wish_authorizations"][0]["authorization_id"] == "auth-1"
    assert restored["task_registry"][0]["task_id"] == "t1"
    assert restored["compression_due_round"] == 10
    rows = directives.active_directives(restored)
    assert fact_contract.classify_directive_row(rows[0]).kind == "authorized"


# ---------------------------------------------------------------- 兼容面钉

@pytest.fixture
def book(tmp_path):
    root = tmp_path / "books" / "demo"
    (root / "chapters").mkdir(parents=True)
    (root / "chapter_index.json").write_text(json.dumps({"book_id": "demo", "chapters": [
        {"idx": 1, "title": "One", "chars": 9}, {"idx": 2, "title": "Two", "chars": 9}]}),
        encoding="utf-8")
    (root / "chapters" / "0001.txt").write_text("past fact", encoding="utf-8")
    (root / "chapters" / "0002.txt").write_text("next fact", encoding="utf-8")
    # roster 走 read_reader_source：按 book_index.json 的逐章 checksum 校验原文。
    from core.engine.book_index import checksum
    rows = []
    for number, text in ((1, "past fact"), (2, "next fact")):
        rows.append({"chapter_no": number, "chars": len(text),
                     "source": {"checksum": checksum(text)}})
    (root / "book_index.json").write_text(
        json.dumps({"book_id": "demo", "chapters": rows}), encoding="utf-8")
    return root


def test_old_ui_save_and_load_requests_still_work(tmp_path, monkeypatch):
    """旧 UI 请求形状（无新参数）在 save/list/load 全链按原语义工作。"""
    monkeypatch.setattr(server.fe, "WRITABLE_DIR", str(tmp_path))
    session = server.sessions.create("audit-old-ui")
    session.state = rich_state()
    try:
        client = TestClient(server.app)
        saved = client.post("/api/sessions/audit-old-ui/save", json={"save_id": "manual-1"})
        assert saved.status_code == 200
        assert saved.json()["saved"] is True
        # 旧 UI 不带 save_id 的默认请求：latest 被存为会话隔离档后，元数据仍须如实可查。
        auto = client.post("/api/sessions/audit-old-ui/save", json={})
        assert auto.status_code == 200
        assert auto.json()["metadata"] is not None, \
            "默认 latest 存档按会话隔离落库，save_metadata 查询必须做同一归一"
        listing = client.get("/api/saves").json()["saves"]
        assert any(item["save_id"] == "manual-1" for item in listing)
        loaded = client.post("/api/saves/load", json={"save_id": "manual-1"})
        assert loaded.status_code == 200
        body = loaded.json()
        assert body["save_id"] == "manual-1"
        # 公开态脱敏但不吞新字段：玩家能看到任务与愿望剩余等投影。
        assert body["state"]["task_registry"] == []
        assert "wish_remaining" in body["state"]
    finally:
        server.sessions.clear()


def test_old_ui_roster_defaults_to_original_and_book_unchanged(book, tmp_path, monkeypatch):
    """旧 UI 不带 view 的 roster 请求默认原著视图，且书目文件保持只读。"""
    monkeypatch.setattr(server, "_resolve_book_dir", lambda bid: book)
    monkeypatch.setattr(server.fe, "WRITABLE_DIR", str(tmp_path / "var"))
    # 重置服务缓存：隔离运行时下角色库为空 → 名单如实报 preparation_required。
    monkeypatch.setattr(server, "_reader_chat", None)
    before = {str(p): p.read_bytes() for p in book.rglob("*") if p.is_file()}
    client = TestClient(server.app)
    response = client.get("/api/books/demo/reader-chat/roster?chapter_no=1")
    assert response.status_code == 200
    roster = response.json()
    assert roster["book_id"] == "demo"
    assert roster["status"] in ("ready", "preparation_required")
    assert roster.get("cutoff") is None or roster["cutoff"]["chapter_no"] == 1
    assert before == {str(p): p.read_bytes() for p in book.rglob("*") if p.is_file()}, \
        "原著查询不得改写任何书目文件（原文引文完整性）"


def test_source_hash_and_option_protocol_unchanged(book):
    """源 hash 由内容决定且稳定；正式选项协议保持 A-F 有序。"""
    from core.services import reader_start_service as reader
    first = reader.read_book_source(book, 1)
    assert first["source_hash"] == reader.read_book_source(book, 1)["source_hash"]
    (book / "chapters" / "0001.txt").write_text("edited", encoding="utf-8")
    assert reader.read_book_source(book, 1)["source_hash"] != first["source_hash"], \
        "原文变化必须改变 source_hash（防旧证据冒充新书）"
    assert save_contract.OPTION_KEYS == ("A", "B", "C", "D", "E", "F")
    assert save_contract.valid_options([{"key": k, "text": "行动" + k} for k in "ABCDEF"])
    assert not save_contract.valid_options([{"key": k, "text": "行动" + k}
                                            for k in "ABCDE"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
