# -*- coding: utf-8 -*-
"""探针：basic_agent 会话首回合事件全文抓取。

复用 verify_mode_matrix 的辅助完成上传→window 准备→开局（类Agent），
然后 POST 一条玩家消息并打印全部事件（chat/delta/state 字段），
用于核对「零模型调用但回合 committed」的成因。凭据读 MOCK_API_KEY。
"""
import os
import sys

import requests

sys.path.insert(0, __file__.rsplit("\\", 2)[0])
from tools.verify_mode_matrix import (  # noqa: E402
    build_synthetic_book, stream_events, upload_book, wait_preparation)

APP = "http://127.0.0.1:21594"
MOCK = "http://127.0.0.1:21593"
API_KEY = os.environ.get("MOCK_API_KEY", "")

session = requests.Session()
book = upload_book(session, APP, "probe_ba.txt", build_synthetic_book())
book_id = book["book_id"]
print("book:", book_id)

job = session.post(f"{APP}/api/books/{book_id}/preparation-jobs", json={
    "idempotency_key": f"probe-ba-{int(__import__('time').time())}",
    "mode": "window", "target_chapter": 1,
    "provider": "custom", "base_url": MOCK, "api_key": API_KEY,
    "model": "mock-model",
}, timeout=60)
print("prep create:", job.status_code)
job_id = job.json().get("job", {}).get("job_id") or job.json().get("job_id")
ready, _ = wait_preparation(session, APP, job_id)
print("prep status:", ready.get("status"))

intent = session.post(f"{APP}/api/books/{book_id}/chapter-start",
                      json={"chapter_no": 1}, timeout=30).json()
selection = intent.get("scene_selection")

start = {
    "provider": "custom", "base_url": MOCK, "api_key": API_KEY,
    "model": "mock-model", "mode": "基础模式",
    "book_id": book_id, "preparation_mode": "window", "preparation_job_id": job_id,
    "target_chapter": 1, "difficulty": "D4 普通",
    "golden_finger": "系统流（面板/任务/抽奖）",
    "persona_preset": "自定义（在下方文本框描述）",
    "persona_custom": "冷静谨慎，重证据，先谋后动。",
    "role": "李青", "protagonist_gender": "male",
    "paper_tier": 2, "story_agent_mode": True,
    "scene_selection": selection, "convergence": "较高",
}
events = stream_events(session, "POST", APP + "/api/sessions/start", start)
sid = None
for ev in events:
    data = ev.get("data") or {}
    sid = data.get("session_id") or sid
    if ev.get("type") == "state":
        s = data.get("state") or {}
        chat = data.get("chat") or data.get("chatbot") or []
        last = chat[-1].get("content") if chat else ""
        print("[start]", ev.get("type"), "stage=", s.get("save_stage"), "round=", s.get("round"),
              "agent_mode=", s.get("agent_mode"), "story_agent_mode=", s.get("story_agent_mode"),
              "opts=", [o.get("key") for o in (s.get("options") or [])],
              "| last:", str(last)[:80].replace("\n", " "))
print("sid:", sid)

turn = stream_events(session, "POST", f"{APP}/api/sessions/{sid}/messages", {"message": "A"})
for ev in turn:
    data = ev.get("data") or {}
    if ev.get("type") == "state":
        s = data.get("state") or {}
        chat = data.get("chat") or data.get("chatbot") or []
        last = chat[-1].get("content") if chat else ""
        print("[turn]", ev.get("type"), "stage=", s.get("save_stage"), "round=", s.get("round"),
              "opts=", [o.get("key") for o in (s.get("options") or [])],
              "gate=", s.get("scene_gate"), "reason=", str(s.get("scene_gate_reason"))[:60],
              "| last:", str(last)[:200].replace("\n", " "))
    else:
        print("[turn]", ev.get("type"), str(data)[:200])
