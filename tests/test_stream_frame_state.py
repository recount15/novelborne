"""流式帧状态收尾（D01）：帧必须使用公开投影快照，禁止与会话状态别名。"""
from types import SimpleNamespace

from core.server import _apply_frame_state

# 占位敏感值：仅用于验证投影会剔除敏感键，不是任何真实凭据。
占位 = "占位敏感值"


def _session_with_internal_state() -> SimpleNamespace:
    return SimpleNamespace(state={
        "revision": 5,
        "system": "内部 system prompt",
        "api_key": 占位,
        "request_kwargs": {"api_key": 占位},
        "nemesis_private": {"plot": "私密"},
        "chat": [{"role": "assistant", "content": "正文"}],
        "options": ["A", "B"],
    })


def _run(data, session, *, durable_seen, stage, operation, usable_previous=False):
    _apply_frame_state(data, session, stage=stage, durable_seen=durable_seen,
                       operation=operation, usable_previous=usable_previous)


def test_durable_frame_is_public_snapshot_not_alias():
    session = _session_with_internal_state()
    data = {"state": session.state}
    _run(data, session, durable_seen=True, stage="committed", operation="start")
    frame = data["state"]
    assert frame is not session.state
    assert frame["revision"] == 5
    assert frame["save_stage"] == "committed"
    assert frame["game_ready"] is True
    # 修改帧不得写回会话状态
    frame["save_stage"] = "probe"
    assert "save_stage" not in session.state


def test_durable_frame_strips_internal_fields():
    session = _session_with_internal_state()
    data = {"state": session.state}
    _run(data, session, durable_seen=True, stage="committed", operation="start")
    frame = data["state"]
    assert "system" not in frame
    assert "api_key" not in frame
    assert "request_kwargs" not in frame
    assert "nemesis_private" not in frame


def test_streaming_frame_keeps_increment_and_flags():
    session = _session_with_internal_state()
    incremental = {"chat": [{"role": "assistant", "content": "片段"}]}
    data = {"state": incremental}
    _run(data, session, durable_seen=False, stage="streaming", operation="start")
    assert data["state"]["save_stage"] == "streaming"
    assert data["state"]["game_ready"] is False
    # 增量帧不被投影改写成完整状态（避免用部分状态计算聚合值）
    assert "relay_active" not in data["state"]


def test_message_streaming_usable_previous_is_game_ready():
    session = _session_with_internal_state()
    data = {"state": {"chat": []}}
    _run(data, session, durable_seen=False, stage="streaming",
         operation="message", usable_previous=True)
    assert data["state"]["game_ready"] is True


def test_non_dict_state_is_untouched():
    session = _session_with_internal_state()
    data = {"state": None}
    _run(data, session, durable_seen=True, stage="committed", operation="start")
    assert data["state"] is None
