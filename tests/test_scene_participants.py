# -*- coding: utf-8 -*-
"""P4：场景 NPC 闲聊名单生成（app 层确定性资格判定）。"""
from __future__ import annotations

import json

import pytest

from core.app import _scene_participant_names


@pytest.fixture
def book(tmp_path):
    anchor_dir = tmp_path / "anchors"
    anchor_dir.mkdir()
    (anchor_dir / "0001.json").write_text(json.dumps({
        "title": "第一章", "characters": ["张三", "李四", "苏叶"],
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _state(book, **over):
    state = {
        "distill_key": str(book),
        "current_chapter": 1,
        "persona": "主角甲",
        "companions": [{"name": "苏叶"}],
        "heroines": [],
    }
    state.update(over)
    return state


def test_only_narrative_mentioned_anchors_enter_roster(book):
    state = _state(book)
    names = _scene_participant_names(state, "张三在城门口拦住了去路。", [])
    assert names == ["张三"], "正文未提及的锚点人物不得进入闲聊名单"


def test_roster_and_protagonist_are_excluded(book):
    state = _state(book)
    names = _scene_participant_names(
        state, "苏叶与张三同行，主角甲压阵。", [{"name": "苏叶"}])
    assert "苏叶" not in names and "主角甲" not in names


def test_missing_anchor_file_returns_empty(book):
    state = _state(book, current_chapter=9)
    assert _scene_participant_names(state, "张三出现。", []) == []


def test_limit_caps_roster(book):
    (book / "anchors" / "0001.json").write_text(json.dumps({
        "characters": [{"name": f"人物{i}"} for i in range(20)],
    }, ensure_ascii=False), encoding="utf-8")
    state = _state(book)
    body = "、".join(f"人物{i}" for i in range(20))
    assert len(_scene_participant_names(state, body, [])) == 8


def test_recollection_mention_does_not_enter_roster(book):
    """D11：仅被回忆句提及的锚点人物不算在场。"""
    state = _state(book)
    names = _scene_participant_names(
        state, "张三在城门口拦住了去路。他想起当年李四也曾在此驻马。", [])
    assert "李四" not in names, "回忆中提及的离场角色不得进入闲聊名单"
    assert "张三" in names


def test_relayed_rumor_mention_does_not_enter_roster(book):
    """D11：转述/传闻句提及不算在场。"""
    state = _state(book)
    names = _scene_participant_names(
        state, "张三在城门口拦住了去路。传闻李四早已离开北境。", [])
    assert "李四" not in names


def test_present_mention_in_own_sentence_enters(book):
    state = _state(book)
    names = _scene_participant_names(state, "李四在城门口与张三对峙。", [])
    assert "李四" in names and "张三" in names


def test_hard_state_death_excludes_even_when_present(book):
    """D11：高置信生死/在场断言（死亡/离场）压过正文提及。"""
    from core.services import character_state_service
    state = _state(book)
    dead = character_state_service.add_evidence(
        None, key="alive", value="已死亡", confidence=1.0)
    state["character_states"] = {"李四": dead}
    names = _scene_participant_names(state, "李四在城门口与张三对峙。", [])
    assert "李四" not in names, "死亡断言必须压过正文提及"
    assert "张三" in names


def test_get_roster_filters_dead_scene_npc():
    """D11：chat 侧名册合并场景 NPC 时同样执行硬门禁（纵深防御）。"""
    from core.services import chat_service, character_state_service
    dead = character_state_service.add_evidence(
        None, key="生死", value="已身亡", confidence=1.0)
    state = {"active_members": [{"name": "苏叶"}],
             "scene_participants": ["李四", "张三"],
             "character_states": {"李四": dead}}
    roster = [item["name"] for item in chat_service.get_roster(state)]
    assert "李四" not in roster
    assert "张三" in roster and "苏叶" in roster
