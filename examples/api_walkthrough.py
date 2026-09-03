#!/usr/bin/env python3
"""Novelborne API 全流程示例（v2.1.0）。

演示：连接测试 → 基础模式开局 → 角色闲聊 → 推进一回合 → 存档。
API key 从环境变量 NOVELBORNE_API_KEY 读取，绝不写死。

用法:
    export NOVELBORNE_API_KEY=sk-...
    export NOVELBORNE_BASE_URL=https://your-endpoint.example/v1   # 自定义服务商
    python examples/api_walkthrough.py
"""
import json
import os
import sys

import requests

BASE = os.environ.get("NOVELBORNE_BASE", "http://127.0.0.1:8000")
API_KEY = os.environ.get("NOVELBORNE_API_KEY", "")
BASE_URL = os.environ.get("NOVELBORNE_BASE_URL", "")
SID = "api_walkthrough_demo"


def stream(url: str, body: dict):
    """读取 NDJSON 流，返回 (最后 state, 错误, 草稿字数)。"""
    last, err, draft_chars, first_delta = None, None, 0, None
    with requests.post(url, json=body, stream=True, timeout=1800) as r:
        r.raise_for_status()
        for raw in r.iter_lines():
            if not raw:
                continue
            ev = json.loads(raw)
            t, d = ev.get("type"), ev.get("data") or {}
            if t == "error":
                err = d.get("message")
            elif t == "delta":
                draft_chars += len(d.get("delta") or "")
                first_delta = first_delta or True
            elif t == "state":
                last = d
    return last, err, draft_chars


def main() -> None:
    if not API_KEY:
        sys.exit("请先设置 NOVELBORNE_API_KEY 环境变量")

    # ① 健康检查
    health = requests.get(f"{BASE}/api/health", timeout=30).json()
    print("[1] 服务版本:", health.get("version"))

    # ② 测试模型连接
    test = requests.post(f"{BASE}/api/models/test", json={
        "provider": "custom" if BASE_URL else "deepseek",
        "base_url": BASE_URL or None,
        "api_key": API_KEY,
    }, timeout=60).json()
    print("[2] 连接测试:", test.get("message") or test)

    # ③ 开局（基础模式）
    state, err, _ = stream(f"{BASE}/api/sessions/start", {
        "session_id": SID, "provider": "custom" if BASE_URL else "deepseek",
        "base_url": BASE_URL or None, "api_key": API_KEY,
        "mode": "基础模式", "fragment": "现代都市", "role": "大学生",
        "difficulty": "简单", "convergence": "较高", "story_richness": 800,
        "golden_finger": "学霸系统",
        "companion_roster": [{"name": "小明", "voice": "开朗外向"}],
        "heroine_roster": [{"name": "小红", "voice": "文静温柔"}],
        "heroine_mode": "单女主",
    })
    assert state and state["state"].get("game_ready"), f"开局失败: {err}"
    print("[3] 开局成功 options =", len(state["state"].get("options") or []))

    # ④ 角色闲聊（不推进剧情、不消耗回合）
    roster = requests.get(f"{BASE}/api/chat/roster",
                          params={"session_id": SID}, timeout=30).json()["roster"]
    print("[4] 可聊天角色:", [x["name"] for x in roster])
    if roster:
        reply = requests.post(f"{BASE}/api/chat/send",
                              params={"session_id": SID},
                              json={"character_name": roster[0]["name"],
                                    "message": "你好，今天感觉怎么样？"},
                              timeout=600).json()
        print(f"    {roster[0]['name']}：{reply['reply'][:60]}…")
        print("    usage:", reply.get("usage"))

    # ⑤ 推进一回合（享受 v2.1.0 流式草稿）
    opts = state["state"].get("options") or []
    msg = (f"选择{opts[0]['key']}：{opts[0]['text']}" if opts
           else "我走进教室，环顾四周。")
    state, err, draft_chars = stream(
        f"{BASE}/api/sessions/{SID}/messages", {"message": msg})
    assert state, f"回合失败: {err}"
    print(f"[5] 回合完成 草稿流 {draft_chars} 字 round =", state["state"].get("round"))

    # ⑥ 存档
    requests.post(f"{BASE}/api/sessions/{SID}/save",
                  json={"save_id": "latest"}, timeout=60)
    print("[6] 已存档。完整接口文档见 docs/API.md")


if __name__ == "__main__":
    main()
