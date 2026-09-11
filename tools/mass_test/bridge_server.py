# -*- coding: utf-8 -*-
"""本地模型桥：OpenAI 兼容端点 → 磁盘任务队列 → 由 ZCode 消费产出回答。

流程：测试实例把 chat.completions 请求 POST 到本桥；桥把提示词写入
queue/pending/<id>.json 并同步等待；ZCode 用 queue_cli 把 pending 领走
（running/），产出的回答以 {"id","content"} 写回 done/；桥轮询到后按
OpenAI 协议返回（stream=True 时以 SSE 分块）。

仅绑定 127.0.0.1；无外部出站请求；凭据不参与（本桥无鉴权，只服务本机测试）。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
# 队列根可用 FATE_MASS_QUEUE_DIR 覆盖：并行 run 各用独立队列命名空间，防串任务。
QUEUE_ROOT = Path(os.getenv(
    "FATE_MASS_QUEUE_DIR", str(ROOT / "var" / "mass_test" / "bridge_queue")))
PENDING = QUEUE_ROOT / "pending"
RUNNING = QUEUE_ROOT / "running"
DONE = QUEUE_ROOT / "done"
for d in (PENDING, RUNNING, DONE):
    d.mkdir(parents=True, exist_ok=True)

# 桥同步等待窗口：内容生产源（ZCode）按批消费，窗口须覆盖批处理周期。
DEFAULT_WAIT_SECONDS = float(os.getenv("BRIDGE_WAIT_SECONDS", "900"))


class ChatMessage(BaseModel):
    role: str
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    model: str = "zcode-1"
    messages: list[ChatMessage]
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False


def _tag_for(messages: list[dict[str, Any]]) -> str:
    """按提示词特征打标签，方便消费端分诊。"""
    text = ""
    for m in messages:
        text += str(m.get("content") or "") + "\n"
    if "锚点" in text and "events" in text:
        return "prepare_block"
    if '"tool"' in text or "search_docs" in text:
        return "copilot"
    if "A-F" in text or "选项" in text:
        return "turn_or_opening"
    return "generic"


app = FastAPI(title="mass-test model bridge", docs_url=None, redoc_url=None)


class _Waiter:
    """done 目录轮询（进程内事件加速）。"""

    def __init__(self) -> None:
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def wait_done(self, task_id: str, timeout: float) -> dict[str, Any] | None:
        event = threading.Event()
        with self._lock:
            self._events[task_id] = event
        try:
            path = DONE / f"{task_id}.json"
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if path.is_file():
                    try:
                        return json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                if event.wait(0.5):
                    event.clear()
            return None
        finally:
            with self._lock:
                self._events.pop(task_id, None)

    def notify(self, task_id: str) -> None:
        with self._lock:
            event = self._events.get(task_id)
        if event is not None:
            event.set()


WAITER = _Waiter()


def _openai_response(task_id: str, model: str, content: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{task_id}", "object": "chat.completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _enqueue(body: ChatCompletionRequest) -> tuple[str, dict[str, Any]]:
    task_id = uuid.uuid4().hex
    messages = [m.model_dump() for m in body.messages]
    task = {"id": task_id, "ts": time.time(), "model": body.model,
            "messages": messages, "max_tokens": body.max_tokens,
            "temperature": body.temperature, "tag": _tag_for(messages)}
    (PENDING / f"{task_id}.json").write_text(
        json.dumps(task, ensure_ascii=False), encoding="utf-8")
    return task_id, task


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "zcode-1", "object": "model"}]}


@app.post("/v1/chat/completions")
def chat_completions(body: ChatCompletionRequest) -> Any:
    task_id, task = _enqueue(body)
    done = WAITER.wait_done(task_id, DEFAULT_WAIT_SECONDS)
    if done is None:
        # 超时：把任务标记为过期留证，返回网关超时。
        expired = RUNNING / f"{task_id}.json"
        target = PENDING / f"{task_id}.json"
        source = expired if expired.is_file() else target
        if source.is_file():
            source.rename(QUEUE_ROOT / "running" / f"{task_id}.expired")
        raise HTTPException(status_code=504, detail="bridge wait timeout")
    content = str(done.get("content") or "")
    if not body.stream:
        return JSONResponse(_openai_response(task_id, body.model, content))
    chunks = [content[i:i + 80] for i in range(0, len(content), 80)] or [""]

    def sse():
        # 消费端是 OpenAI SDK（core/fate_engine.py 读 chunk.choices[0].delta.content），
        # 分块必须走 delta 形状；放在 message.content 会被解析为空串。
        for index, piece in enumerate(chunks):
            delta: dict[str, Any] = ({"role": "assistant", "content": piece}
                                     if index == 0 else {"content": piece})
            payload = {
                "id": f"chatcmpl-{task_id}", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": body.model,
                "choices": [{"index": 0, "delta": delta,
                             "finish_reason": "stop" if index == len(chunks) - 1 else None}],
            }
            yield b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n"
        yield b"data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.get("/v1/queue/stats")
def stats() -> dict[str, Any]:
    def count(directory: Path) -> int:
        return sum(1 for _ in directory.glob("*.json"))
    oldest = None
    pending = sorted(PENDING.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if pending:
        oldest = round(time.time() - pending[0].stat().st_mtime, 1)
    return {"pending": count(PENDING), "running": count(RUNNING), "done": count(DONE),
            "oldest_pending_age_sec": oldest}


def run(port: int = 21590) -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    run()
