# -*- coding: utf-8 -*-
"""测试桥 SSE 流式形状回归（B 类·测试器缺陷 PB-003 重归因依据）。

产品用 OpenAI SDK 流式解析（core/fate_engine.py: chunk.choices[0].delta.content），
因此测试桥 stream=True 时每个分块必须携带 choices[0].delta.content；
曾经把内容放在 choices[0].message.content 导致 SDK 逐块拿到空内容、
开局/回合 acc 恒为空串，进而触发「开局未生成有效状态」假失败。
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

ANSWER = "第一段核对内容。\nA. 选项一\nB. 选项二\nC. 选项三\nD. 选项四\nE. 选项五\nF. 选项六"


@pytest.fixture()
def bridge(tmp_path):
    """把桥的队列根指到本测试独立目录（导入亦延后，避免默认目录写副作用）。"""
    old = os.environ.get("FATE_MASS_QUEUE_DIR")
    os.environ["FATE_MASS_QUEUE_DIR"] = str(tmp_path / "queue")
    from tools.mass_test import bridge_server
    mod = importlib.reload(bridge_server)
    yield mod
    if old is None:
        os.environ.pop("FATE_MASS_QUEUE_DIR", None)
    else:
        os.environ["FATE_MASS_QUEUE_DIR"] = old


def _complete_when_pending(mod, content: str) -> None:
    """后台补完线程：等 pending 出现 → 写 done → 唤醒等待者（与 queue_cli 同协议）。"""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pend = sorted(mod.PENDING.glob("*.json"))
        if pend:
            task = json.loads(pend[0].read_text(encoding="utf-8"))
            (mod.DONE / f"{task['id']}.json").write_text(
                json.dumps({"id": task["id"], "content": content}, ensure_ascii=False),
                encoding="utf-8")
            mod.WAITER.notify(task["id"])
            return
        time.sleep(0.02)
    raise AssertionError("桥未在限时内产生 pending 任务")


def test_stream_chunks_use_delta_content(bridge):
    client = TestClient(bridge.app)
    th = threading.Thread(target=_complete_when_pending, args=(bridge, ANSWER))
    th.start()
    resp = client.post("/v1/chat/completions", json={
        "model": "zcode-1", "stream": True,
        "messages": [{"role": "user", "content": "开局核对"}]})
    th.join(10)
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.startswith("data: ")]
    assert lines and lines[-1] == "data: [DONE]"
    chunks = [json.loads(l[len("data: "):]) for l in lines[:-1]]
    assert chunks, "流式响应必须至少有一个分块"
    pieces = []
    for i, chunk in enumerate(chunks):
        choice = chunk["choices"][0]
        assert "delta" in choice, (
            f"第 {i} 块缺少 choices[0].delta：OpenAI 流式协议要求 delta.content，"
            "放在 message.content 会被 SDK 解析为空")
        pieces.append(str(choice["delta"].get("content") or ""))
        expected_finish = "stop" if i == len(chunks) - 1 else None
        assert choice.get("finish_reason") == expected_finish
    assert "".join(pieces) == ANSWER, "流式分块拼接必须还原完整回答"


def test_non_stream_response_keeps_message_content(bridge):
    client = TestClient(bridge.app)
    th = threading.Thread(target=_complete_when_pending, args=(bridge, ANSWER))
    th.start()
    resp = client.post("/v1/chat/completions", json={
        "model": "zcode-1", "stream": False,
        "messages": [{"role": "user", "content": "花名册"}]})
    th.join(10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == ANSWER
