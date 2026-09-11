# -*- coding: utf-8 -*-
"""B01 回归：mass_test lanes 的流帧解包必须遵循服务端 NDJSON 协议。

服务端帧格式（core/server.py _stream_response）：
    {"type": "state", "data": {"chat": [...], "state": <游戏状态>, "session_id": ...}}
- 游戏状态在 data["state"]，不在 data 本身；
- session_id 只在正式提交后写入 data 顶层；
- 正式状态帧 save_stage 为 opening/committed，中间帧为 streaming；
- 正式选项契约 = save_contract.valid_options：A–F 恰六项、顺序一致、文本非空。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.mass_test.lanes import (  # noqa: E402
    durable_state, final_state, session_id_of, six_options_ok, stream_error,
)

SIX = [{"key": k, "text": f"行动{k}"} for k in "ABCDEF"]


def _frames_with_durable():
    return [
        {"type": "state", "data": {"chat": [], "state": {"save_stage": "streaming"},
                                   "status": "生成中"}},
        {"type": "state", "data": {"chat": [{"role": "assistant", "content": "正文"}],
                                   "state": {"save_stage": "committed", "options": SIX},
                                   "session_id": "sess-1", "operation": "start"}},
    ]


def test_final_state_returns_inner_state_not_outer_data():
    frames = _frames_with_durable()
    state = final_state(frames)
    # 旧实现返回外层 data，导致 state["save_stage"]/state["options"] 永远缺失。
    assert state.get("save_stage") == "committed"
    assert len(state.get("options") or []) == 6
    assert "chat" not in state


def test_final_state_prefers_last_durable_frame():
    frames = _frames_with_durable() + [
        {"type": "state", "data": {"chat": [], "state": {"save_stage": "streaming"}}},
    ]
    assert durable_state(frames).get("save_stage") == "committed"
    # final_state 仍返回最后帧（含 streaming 现场），durable_state 返回正式帧。
    assert final_state(frames).get("save_stage") == "streaming"


def test_final_state_empty_and_error_frames():
    assert final_state([]) == {}
    assert durable_state([]) == {}
    frames = [{"type": "error", "data": {"message": "开局失败，请检查设定后重试"}}]
    assert final_state(frames) == {}
    assert stream_error(frames).startswith("开局失败")


def test_session_id_from_top_level_data():
    frames = _frames_with_durable()
    assert session_id_of(frames) == "sess-1"
    # 未正式提交（无顶层 session_id）时不得伪造。
    frames_pending = [f for f in frames if "session_id" not in f["data"]]
    assert session_id_of(frames_pending) == ""


def test_six_options_contract():
    assert six_options_ok({"options": SIX})
    assert not six_options_ok({"options": SIX[:5]})          # 缺项
    assert not six_options_ok({"options": list(reversed(SIX))})  # 乱序
    assert not six_options_ok({"options": SIX[:5] + [{"key": "F", "text": " "}]}), "空文本"
    assert not six_options_ok({"options": "six"})
    assert not six_options_ok({})

if __name__ == "__main__":
    raise SystemExit(0)
