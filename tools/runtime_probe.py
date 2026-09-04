#!/usr/bin/env python3
"""实验运行期间的实时功能探测

在 100 回合实验进行时，持续探测：
- 任务系统（task_service）
- 存档功能（save/load）
- 会话状态（session state）
- 角色状态（character state）
"""
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


BASE_URL = "http://127.0.0.1:8010"
SESSION_ID = None  # 从日志中提取


def http_get(path: str) -> tuple[int, dict | list | None]:
    """发送 GET 请求"""
    try:
        req = Request(f"{BASE_URL}{path}", headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return resp.status, data
    except HTTPError as e:
        try:
            error_data = json.loads(e.read().decode('utf-8'))
            return e.code, error_data
        except Exception:
            return e.code, None
    except (URLError, TimeoutError):
        return -1, None


def http_post(path: str, body: dict) -> tuple[int, dict | list | None]:
    """发送 POST 请求"""
    try:
        req = Request(
            f"{BASE_URL}{path}",
            data=json.dumps(body).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return resp.status, data
    except HTTPError as e:
        try:
            error_data = json.loads(e.read().decode('utf-8'))
            return e.code, error_data
        except Exception:
            return e.code, None
    except (URLError, TimeoutError):
        return -1, None


def extract_session_id() -> str | None:
    """从最新的 features.jsonl 中提取 session_id"""
    try:
        features_files = sorted(Path("outputs").glob("playtest_*_features.jsonl"))
        if not features_files:
            return None
        
        latest = features_files[-1]
        with latest.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if "session_id" in entry.get("detail", {}):
                        return entry["detail"]["session_id"]
                except Exception:
                    continue
        return None
    except Exception:
        return None


def probe_tasks(session_id: str) -> dict:
    """探测任务系统"""
    status, data = http_get(f"/api/sessions/{session_id}/tasks")
    return {
        "endpoint": "/api/sessions/{id}/tasks",
        "status": status,
        "available": status == 200,
        "task_count": len(data.get("tasks", [])) if isinstance(data, dict) else 0,
        "error": data.get("detail") if status != 200 else None,
    }


def probe_saves(session_id: str) -> dict:
    """探测存档功能"""
    # 尝试保存
    save_id = f"probe_{int(time.time())}"
    save_status, save_data = http_post(
        f"/api/sessions/{session_id}/save",
        {"save_id": save_id}
    )
    save_ok = save_status == 200 and isinstance(save_data, dict) and save_data.get("saved")
    
    # 如果保存成功，尝试读取
    load_ok = False
    if save_ok:
        load_status, load_data = http_post("/api/saves/load", {"save_id": save_id})
        load_ok = load_status == 200 and isinstance(load_data, dict) and load_data.get("state")
    
    return {
        "save_endpoint": "/api/sessions/{id}/save",
        "load_endpoint": "/api/saves/load",
        "save_status": save_status,
        "load_status": load_status if save_ok else -1,
        "save_ok": save_ok,
        "load_ok": load_ok,
    }


def probe_session_state(session_id: str) -> dict:
    """探测会话状态"""
    status, data = http_get(f"/api/sessions/{session_id}/state")
    
    if status == 200 and isinstance(data, dict):
        state = data.get("state", {})
        return {
            "endpoint": "/api/sessions/{id}/state",
            "status": status,
            "available": True,
            "current_round": state.get("round"),
            "mode": state.get("mode"),
            "save_stage": state.get("save_stage"),
            "has_options": bool(state.get("options")),
        }
    
    return {
        "endpoint": "/api/sessions/{id}/state",
        "status": status,
        "available": False,
        "error": data.get("detail") if isinstance(data, dict) else None,
    }


def probe_character_state(session_id: str) -> dict:
    """探测角色状态"""
    status, data = http_get(f"/api/sessions/{session_id}/character-states")
    
    return {
        "endpoint": "/api/sessions/{id}/character-states",
        "status": status,
        "available": status == 200,
        "character_count": len(data) if isinstance(data, list) else 0,
        "error": data.get("detail") if status != 200 and isinstance(data, dict) else None,
    }


def probe_directives(session_id: str) -> dict:
    """探测指令系统"""
    status, data = http_post(
        f"/api/sessions/{session_id}/directives",
        {"question": "测试探测", "context": {}}
    )
    
    return {
        "endpoint": "/api/sessions/{id}/directives",
        "status": status,
        "available": status == 200,
        "has_answer": bool(data.get("answer")) if isinstance(data, dict) else False,
    }


def main():
    global SESSION_ID
    
    print("=" * 70)
    print("实验运行期间功能实时探测")
    print("=" * 70)
    
    # 提取 session_id
    print("\n正在提取 session_id...")
    for attempt in range(10):
        SESSION_ID = extract_session_id()
        if SESSION_ID:
            print(f"✓ Session ID: {SESSION_ID}")
            break
        print(f"  尝试 {attempt + 1}/10...")
        time.sleep(5)
    
    if not SESSION_ID:
        print("✗ 无法提取 session_id，等待实验生成输出文件")
        sys.exit(1)
    
    probe_count = 0
    
    while True:
        probe_count += 1
        timestamp = time.strftime("%H:%M:%S")
        
        print(f"\n{'=' * 70}")
        print(f"探测 #{probe_count} - {timestamp}")
        print(f"{'=' * 70}")
        
        # 探测会话状态
        print("\n[1/5] 会话状态...")
        state_result = probe_session_state(SESSION_ID)
        print(f"  状态: {'✓' if state_result['available'] else '✗'}")
        if state_result['available']:
            print(f"  当前回合: {state_result.get('current_round')}")
            print(f"  模式: {state_result.get('mode')}")
            print(f"  保存阶段: {state_result.get('save_stage')}")
            print(f"  有选项: {state_result.get('has_options')}")
        
        # 探测任务系统
        print("\n[2/5] 任务系统...")
        task_result = probe_tasks(SESSION_ID)
        print(f"  状态: {'✓' if task_result['available'] else '✗'}")
        print(f"  任务数: {task_result['task_count']}")
        
        # 探测存档功能
        print("\n[3/5] 存档功能...")
        save_result = probe_saves(SESSION_ID)
        print(f"  保存: {'✓' if save_result['save_ok'] else '✗'}")
        print(f"  读取: {'✓' if save_result['load_ok'] else '✗'}")
        
        # 探测角色状态
        print("\n[4/5] 角色状态...")
        char_result = probe_character_state(SESSION_ID)
        print(f"  状态: {'✓' if char_result['available'] else '✗'}")
        print(f"  角色数: {char_result['character_count']}")
        
        # 探测指令系统
        print("\n[5/5] 指令系统...")
        dir_result = probe_directives(SESSION_ID)
        print(f"  状态: {'✓' if dir_result['available'] else '✗'}")
        
        # 写入探测日志
        probe_entry = {
            "probe_id": probe_count,
            "timestamp": timestamp,
            "session_state": state_result,
            "tasks": task_result,
            "saves": save_result,
            "character_state": char_result,
            "directives": dir_result,
        }
        
        log_file = Path("outputs") / f"runtime_probes_{SESSION_ID}.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(probe_entry, ensure_ascii=False) + "\n")
        
        print(f"\n探测结果已记录到: {log_file}")
        print(f"下次探测将在 30 秒后进行...")
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n探测已停止")
        sys.exit(0)
