# -*- coding: utf-8 -*-
"""探针：basic_agent 开局失败的 live 事件全文抓取。

复用 verify_mode_matrix 的上传辅助，POST 基础模式+类Agent 开局请求，
打印每个 NDJSON 事件的 chat/delta/error 内容，定位 on_start 内层异常
（服务端包装只回 generic「开局未生成有效状态」）。

模型凭据从环境变量 MOCK_API_KEY 读取（本地 mock 的占位值，非真实凭据）。
"""
import os
import sys

import requests

sys.path.insert(0, __file__.rsplit("\\", 2)[0])
from tools.verify_mode_matrix import build_synthetic_book, stream_events, upload_book  # noqa: E402

APP = "http://127.0.0.1:21594"
MOCK = "http://127.0.0.1:21593"
API_KEY = os.environ.get("MOCK_API_KEY", "")

session = requests.Session()
content = build_synthetic_book()
book = upload_book(session, APP, "probe_basic_agent.txt", content)
print("upload ->", book)

body = {
    "provider": "custom",
    "base_url": MOCK,
    "api_key": API_KEY,
    "model": "mock-model",
    "mode": "基础模式",
    "book_id": book.get("book_id"),
    "preparation_mode": "window",
    "golden_finger": "系统流（面板/任务/抽奖）",
    "persona_custom": "冷静谨慎，重证据",
    "role": "李青",
    "paper_tier": 2,
    "story_agent_mode": True,
}
events = stream_events(session, "POST", APP + "/api/sessions/start", body)
for ev in events:
    etype = ev.get("type")
    data = ev.get("data") or {}
    chat = data.get("chat") or data.get("chatbot") or []
    delta = data.get("delta") or ""
    err = data.get("error") or data.get("message") or ""
    print("==", etype, "| op:", data.get("operation"), "| stage:", data.get("save_stage"),
          "| round:", data.get("round"))
    if isinstance(chat, list):
        for m in chat[-3:]:
            print("   chat[-]:", str(m.get("content"))[:300].replace("\n", " "))
    if delta:
        print("   delta:", str(delta)[:300].replace("\n", " "))
    if err and etype in ("error", "parse_error"):
        print("   ERROR:", str(err)[:500])
