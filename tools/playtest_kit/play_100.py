#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Novelborne 100-turn real playtest for the HCYA source novel.

All credentials are read from environment variables and are never written to
reports, JSONL logs, request snapshots, or the playtest monitor state.

Required environment variables:
    FATE_API_KEY      model API key (NOVELBORNE_API_KEY is also accepted)
Optional:
    FATE_BASE         Novelborne API base (default http://127.0.0.1:8010)
    FATE_BASE_URL     OpenAI-compatible endpoint (NOVELBORNE_BASE_URL is also accepted)
    FATE_MODEL        model id returned by /api/models/fetch
    FATE_TXT          source TXT path

Outputs:
    outputs/playtest_100_play100_report.json
    outputs/playtest_100_play100_rounds.jsonl
    outputs/playtest_100_play100_features.jsonl
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = "http://127.0.0.1:8010"
# Credentials, endpoints, and source manuscripts are runtime inputs only.
# A verified run gets a distinct artifact prefix so the historical failure is preserved.
RUN_TAG = os.environ.get("FATE_RUN_TAG") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
DEFAULT_BASE_URL = ""
DEFAULT_TXT = ""
OUTPUT_DIR = ROOT / "outputs"
REPORT_PATH = OUTPUT_DIR / f"playtest_100_play100_verified_{RUN_TAG}_report.json"
ROUND_LOG_PATH = OUTPUT_DIR / f"playtest_100_play100_verified_{RUN_TAG}_rounds.jsonl"
FEATURE_LOG_PATH = OUTPUT_DIR / f"playtest_100_play100_verified_{RUN_TAG}_features.jsonl"
STORY_OPTIONS_PATH = OUTPUT_DIR / f"playtest_100_play100_verified_{RUN_TAG}_story_options.jsonl"
OPTION_KEYS = ("A", "B", "C", "D", "E", "F")
SECRET_KEYS = {"api_key", "_api_key", "authorization", "request_kwargs", "system", "persona_text", "nemesis_private"}


def sanitize_public_text(value: Any, api_key: str = "") -> Any:
    """Remove private keys while retaining complete narrative text."""
    if isinstance(value, dict):
        return {
            str(key): sanitize_public_text(item, api_key)
            for key, item in value.items()
            if str(key).lower() not in SECRET_KEYS
        }
    if isinstance(value, list):
        return [sanitize_public_text(item, api_key) for item in value]
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]") if api_key else value
    return value


class Recorder:
    def __init__(self, *, base: str, endpoint: str, model: str, txt: Path, rounds: int) -> None:
        self.started_at = time.time()
        self.base = base
        self.endpoint = endpoint
        self.model = model
        self.txt = txt
        self.rounds_planned = rounds
        self.session_id: str | None = None
        self.checks: list[dict[str, Any]] = []
        self.rounds: list[dict[str, Any]] = []
        self.features: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.story_options: list[dict[str, Any]] = []
        self.starting_turns = 0
        self.starting_round: int | None = None
        self.run_complete = False
        self.final_save_ok = False
        self.final_load_ok = False
        self.final_export_ok = False
        self._key = os.environ.get("FATE_API_KEY") or os.environ.get("NOVELBORNE_API_KEY", "")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ROUND_LOG_PATH.write_text("", encoding="utf-8")
        FEATURE_LOG_PATH.write_text("", encoding="utf-8")
        STORY_OPTIONS_PATH.write_text("", encoding="utf-8")

    def _clean(self, value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "…"
        if isinstance(value, dict):
            return {
                str(k): ("[REDACTED]" if str(k).lower() in SECRET_KEYS else self._clean(v, depth + 1))
                for k, v in value.items()
                if str(k).lower() not in SECRET_KEYS
            }
        if isinstance(value, list):
            return [self._clean(v, depth + 1) for v in value[:100]]
        if isinstance(value, str):
            text = value.replace(self._key, "[REDACTED]") if self._key else value
            return text[:800]
        return value

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        item = {"name": name, "ok": bool(ok), "detail": self._clean(detail), "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.checks.append(item)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {str(item['detail'])[:180]}", flush=True)
        return bool(ok)

    def error(self, name: str, detail: Any) -> None:
        item = {"name": name, "detail": self._clean(detail), "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.errors.append(item)
        self._write(FEATURE_LOG_PATH, {"kind": "error", **item})

    def _write(self, path: Path, item: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self._clean(item), ensure_ascii=False, default=str) + "\n")

    def _write_full(self, path: Path, item: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")

    def round(self, item: dict[str, Any]) -> None:
        item = {"kind": "round", **item, "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
        item = self._clean(item)
        self.rounds.append(item)
        self._write(ROUND_LOG_PATH, item)
        print(f"[ROUND {item.get('requested_round')}/{self.rounds_planned}] "
              f"state_round={item.get('state_round')} chapter={item.get('chapter')} "
              f"gate={item.get('scene_gate')} options={item.get('option_count')} "
              f"{item.get('elapsed_sec')}s", flush=True)

    def story_option(self, item: dict[str, Any]) -> None:
        """Persist the complete generated scene and A-F options separately."""
        safe = {
            "kind": "story_options",
            "state_round": item.get("state_round"),
            "chapter": item.get("chapter"),
            "chapter_round": item.get("chapter_round"),
            "action": item.get("action"),
            "choice": item.get("choice"),
            "body": str(item.get("body") or ""),
            "options": [
                {"key": str(row.get("key") or ""), "text": str(row.get("text") or "")}
                for row in (item.get("options") or [])
                if isinstance(row, dict)
            ],
        }
        safe["body_chars"] = len(safe["body"])
        safe["option_count"] = len(safe["options"])
        safe = sanitize_public_text(safe, self._key)
        self.story_options.append(safe)
        self._write_full(STORY_OPTIONS_PATH, safe)

    def feature(self, round_no: int, name: str, method: str, path: str, status: int,
                ok: bool, detail: Any = None) -> None:
        item = {"kind": "feature_probe", "round": round_no, "name": name,
                "method": method, "path": path, "status": status, "ok": bool(ok),
                "detail": self._clean(detail), "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.features.append(item)
        self._write(FEATURE_LOG_PATH, item)
        self.check(f"功能:{name}", ok, {"status": status, "detail": detail})


    def report(self, *, config: dict[str, Any], final_state: dict[str, Any] | None,
               export_summary: dict[str, Any] | None = None) -> dict[str, Any]:
        passed = sum(1 for item in self.checks if item["ok"])
        report = {
            "schema": "novelborne.playtest.v2",
            "purpose": "100回合真实游戏与前端功能探查",
            "config": self._clean(config),
            "session_id": self.session_id,
            "source_novel": self.txt.name,
            "started_at": self.started_at,
            "ended_at": time.time(),
            "elapsed_sec": round(time.time() - self.started_at, 1),
            "rounds_planned": self.rounds_planned,
            "rounds_completed": sum(
                1 for item in self.rounds
                if item.get("successful_progress") is True
            ),
            "run_complete": bool(self.run_complete),
            "final_save_ok": bool(self.final_save_ok),
            "final_load_ok": bool(self.final_load_ok),
            "final_export_ok": bool(self.final_export_ok),
            "checks_total": len(self.checks),
            "checks_passed": passed,
            "checks_failed": len(self.checks) - passed,
            "checks": self.checks,
            "feature_probes": self.features,
            "round_log": self.rounds,
            "errors": self.errors,
            "final_state_summary": state_summary(final_state),
            "export_summary": self._clean(export_summary),
            "story_options_summary": {
                "path": str(STORY_OPTIONS_PATH),
                "rounds": len(self.story_options),
                "sha256": file_sha256(STORY_OPTIONS_PATH),
            },
            "logs": {"rounds": str(ROUND_LOG_PATH), "features": str(FEATURE_LOG_PATH)},
        }
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return report


def narrative_only(text: str) -> str:
    """Keep the generated scene while removing hidden blocks and trailing options."""
    content = re.sub(r"<<<(?:LOG|ARCHIVE)>>>.*?<<<END>>>", "", str(text or ""), flags=re.S)
    lines = content.splitlines()
    option_start = None
    for index, line in enumerate(lines):
        if re.match(r"^\s*[A-F]\s*[.、．:：)）-]\s*\S", line, flags=re.I):
            option_start = index
            break
    if option_start is not None and option_start >= max(1, len(lines) // 3):
        content = "\n".join(lines[:option_start])
    return content.strip()


def options_for_summary(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {"key": str(item.get("key") or "").strip().upper(),
         "text": str(item.get("text") or "").strip()}
        for item in value
        if isinstance(item, dict)
    ]


def states_equivalent(live: dict[str, Any], restored: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    fields = ("round", "current_chapter", "chapter_round", "save_stage", "scene_gate")
    differences = {
        key: {"live": live.get(key), "restored": restored.get(key)}
        for key in fields if live.get(key) != restored.get(key)
    }
    live_options = options_for_summary(live.get("options"))
    restored_options = options_for_summary(restored.get("options"))
    if live_options != restored_options:
        differences["options"] = {"live": live_options, "restored": restored_options}
    live_history = live.get("history") if isinstance(live.get("history"), list) else []
    restored_history = restored.get("history") if isinstance(restored.get("history"), list) else []
    if len(live_history) != len(restored_history):
        differences["history_length"] = {"live": len(live_history), "restored": len(restored_history)}
    return not differences, differences


def endpoint_identity(endpoint: str) -> str:
    return hashlib.sha256(str(endpoint or "").encode("utf-8")).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items() if str(k).lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [safe_json(v) for v in value[:100]]
    return value


def state_summary(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    usage = state.get("agent_meta", {}).get("usage") if isinstance(state.get("agent_meta"), dict) else {}
    return {
        "game_ready": state.get("game_ready"),
        "save_stage": state.get("save_stage"),
        "round": state.get("round"),
        "current_chapter": state.get("current_chapter"),
        "chapter_round": state.get("chapter_round"),
        "paper_tier": state.get("paper_tier"),
        "paper_family": state.get("paper_family"),
        "agent_mode": state.get("agent_mode"),
        "story_agent_mode": state.get("story_agent_mode"),
        "story_richness": state.get("story_richness"),
        "option_count": len(valid_options(state.get("options")) and state.get("options") or []),
        "scene_gate": state.get("scene_gate"),
        "convergence": safe_json(state.get("convergence_state")),
        "quest": safe_json(state.get("quest")),
        "break_anchor": safe_json(state.get("break_anchor")),
        "usage": safe_json(usage),
        "tok_last": state.get("tok_last"),
    }


def valid_options(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 6:
        return False
    for expected, item in zip(OPTION_KEYS, value):
        if not isinstance(item, dict):
            return False
        if str(item.get("key") or "").strip().upper() != expected:
            return False
        if not str(item.get("text") or "").strip():
            return False
    return True


def http(base: str, method: str, path: str, body: Any = None, *, timeout: int = 300) -> tuple[int, Any]:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return response.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, {"__http_error__": exc.read().decode("utf-8", "replace")[:1200]}
    except Exception as exc:  # noqa: BLE001
        return -1, {"__error__": str(exc)[:1200]}


def stream(base: str, path: str, body: dict[str, Any], *, timeout: int = 1800) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base.rstrip("/") + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    events: list[dict[str, Any]] = []
    last_state: dict[str, Any] | None = None
    error: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                events.append(event)
                if event.get("type") == "error":
                    error = str((event.get("data") or {}).get("message") or "未知错误")[:1200]
                if event.get("type") == "state":
                    state = (event.get("data") or {}).get("state")
                    if isinstance(state, dict):
                        last_state = state
    except urllib.error.HTTPError as exc:
        error = exc.read().decode("utf-8", "replace")[:1200]
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:1200]
    return events, last_state, error


def upload_novel(base: str, txt: Path, session_id: str) -> tuple[int, Any]:
    boundary = "----novelborne100" + uuid.uuid4().hex
    file_bytes = txt.read_bytes()
    filename = txt.name.replace("\r", "").replace("\n", "")
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{session_id}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nnovel\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: text/plain\r\n\r\n".encode("utf-8"),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    request = urllib.request.Request(base.rstrip("/") + "/api/uploads", data=b"".join(parts), method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"__http_error__": exc.read().decode("utf-8", "replace")[:1200]}
    except Exception as exc:  # noqa: BLE001
        return -1, {"__error__": str(exc)[:1200]}


def last_assistant(events: list[dict[str, Any]]) -> str:
    result = ""
    for event in events:
        if event.get("type") != "state":
            continue
        chat = (event.get("data") or {}).get("chat") or []
        if isinstance(chat, list) and chat and isinstance(chat[-1], dict) and chat[-1].get("role") == "assistant":
            result = str(chat[-1].get("content") or "")
    return result


def decide(endpoint: str, api_key: str, model: str, options: list[dict[str, Any]], context: str) -> str:
    if not options:
        return ""
    text = "\n".join(f"{o.get('key')}. {o.get('text')}" for o in options)
    prompt = ("你是互动小说玩家。根据最近剧情，在 A-F 行动中选择最合适的一项。"
              "只回复一个大写字母，不要解释。\n\n最近剧情：\n" + context[-1400:] +
              "\n\n行动选项：\n" + text)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 10, "temperature": 0.2}
    request = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = str(((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").upper()
        for letter in re.findall(r"[A-F]", content):
            if any(str(item.get("key")) == letter for item in options):
                return letter
    except Exception:
        pass
    return str(options[0].get("key") or "A")


def option_action(options: list[dict[str, Any]], letter: str) -> str:
    item = next((row for row in options if row.get("key") == letter), None)
    return f"选择{letter}：{item.get('text')}" if item else "自由行动：继续推进当前目标"


def state_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"available": False}
    keys = ("round", "current_chapter", "chapter_round", "history", "state_memory", "quest", "convergence_state", "save_stage")
    changed = [key for key in keys if before.get(key) != after.get(key)]
    return {"available": True, "changed_keys": changed, "round_before": before.get("round"), "round_after": after.get("round")}


def probe(rec: Recorder, base: str, round_no: int, name: str, method: str, path: str,
          body: Any = None, *, expected: Any = None, timeout: int = 300) -> Any:
    status, payload = http(base, method, path, body, timeout=timeout)
    ok = status == 200 if expected is None else bool(expected(status, payload))
    rec.feature(round_no, name, method, path, status, ok, safe_json(payload if isinstance(payload, dict) else str(payload)[:300]))
    return payload


def probe_state_isolation(rec: Recorder, base: str, session_id: str, character: str, round_no: int) -> None:
    before_status, before_payload = http(base, "GET", f"/api/sessions/{session_id}/state", timeout=90)
    before = before_payload.get("state") if isinstance(before_payload, dict) else None
    status, reply = http(base, "POST", f"/api/chat/send?session_id={session_id}",
                         {"character_name": character, "message": "今天的情况怎么样？"}, timeout=900)
    after_status, after_payload = http(base, "GET", f"/api/sessions/{session_id}/state", timeout=90)
    after = after_payload.get("state") if isinstance(after_payload, dict) else None
    delta = state_delta(before, after)
    chat_ok = status == 200 and isinstance(reply, dict) and bool(reply.get("reply"))
    rec.feature(round_no, "角色闲聊生成", "POST", "/api/chat/send", status, chat_ok,
                {"character": character, "reply_length": len(str((reply or {}).get("reply") or "")),
                 "usage": safe_json((reply or {}).get("usage"))})
    isolated = delta.get("available") and not any(key in delta.get("changed_keys", []) for key in
                                                   ("round", "current_chapter", "history", "state_memory", "quest", "convergence_state", "save_stage"))
    rec.feature(round_no, "角色闲聊状态隔离", "GET", "/api/sessions/{id}/state", after_status,
                bool(after_status == 200 and isolated), delta)


def run(config: dict[str, Any], rec: Recorder) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    base = config["base"]
    api_key = config["api_key"]
    endpoint = config["base_url"]
    provider = config["provider"]
    model = config["model"]
    rounds_total = config["rounds"]
    txt = Path(config["txt"])

    status, health = http(base, "GET", "/api/health", timeout=60)
    rec.check("健康检查", status == 200 and isinstance(health, dict) and str(health.get("version")) == "2.1.0", health)
    boot = probe(rec, base, 0, "bootstrap 配置", "GET", "/api/bootstrap",
                 expected=lambda code, data: code == 200 and isinstance(data, dict) and bool(data.get("paper_tiers")))
    status, models = http(base, "POST", "/api/models/fetch", {"provider": provider, "base_url": endpoint, "api_key": api_key}, timeout=180)
    model_list = models.get("models") if isinstance(models, dict) else []
    rec.check("模型列表请求", status == 200 and isinstance(model_list, list) and model in model_list,
              {"status": status, "model_found": model in model_list if isinstance(model_list, list) else False,
               "models_count": len(model_list) if isinstance(model_list, list) else 0})
    status, model_test = http(base, "POST", "/api/models/test",
                              {"provider": provider, "base_url": endpoint, "api_key": api_key, "model": model}, timeout=180)
    rec.check("模型连接测试", status == 200 and isinstance(model_test, dict) and model_test.get("ok") is True,
              {"status": status, "message": (model_test or {}).get("message") if isinstance(model_test, dict) else ""})
    probe(rec, base, 0, "金手指推荐", "POST", "/api/golden-fingers/recommend",
          {"world": "都市校园异能", "persona": "苟道（稳健发育、保命优先）", "difficulty": "D4 普通", "nemesis_d": 4.0},
          expected=lambda code, data: code == 200 and isinstance(data, dict) and bool(data.get("choices")))
    probe(rec, base, 0, "金手指设计器选项", "GET", "/api/gf-designer/options",
          expected=lambda code, data: code == 200)
    probe(rec, base, 0, "角色设计器 schema", "GET", "/api/character-designer/schema",
          expected=lambda code, data: code == 200)
    probe(rec, base, 0, "角色池", "GET", "/api/characters/pool",
          expected=lambda code, data: code == 200)
    probe(rec, base, 0, "角色库", "GET", "/api/character-library",
          expected=lambda code, data: code == 200)
    probe(rec, base, 0, "局域网信息", "GET", "/api/lan-info",
          expected=lambda code, data: code == 200)
    probe(rec, base, 0, "开局问题", "POST", "/api/setup/questions",
          {"purpose": "pre_game_setup", "prepared_script": {"script": "现代都市开局"}},
          expected=lambda code, data: code == 200 and isinstance(data, dict) and isinstance(data.get("questions"), list))

    session_id = "play100_" + uuid.uuid4().hex[:16]
    rec.session_id = session_id
    upload_status, upload = upload_novel(base, txt, session_id)
    upload_id = ((upload or {}).get("upload") or {}).get("upload_id") if isinstance(upload, dict) else None
    rec.check("TXT 上传并绑定同一 session", upload_status == 200 and isinstance(upload, dict) and upload.get("session_id") == session_id and bool(upload_id),
              {"status": upload_status, "session_id_match": isinstance(upload, dict) and upload.get("session_id") == session_id,
               "filename": txt.name, "bytes": txt.stat().st_size})
    if not upload_id:
        return None, None

    start_body = {
        "session_id": session_id, "provider": provider, "base_url": endpoint, "api_key": api_key, "model": model,
        "thinking_mode": "auto", "thinking_param": "", "mode": "强化模式", "novel_upload_id": upload_id,
        "role": "漂泊者", "timepoint": "故事开篇", "difficulty": "D4 普通",
        "golden_finger": "残卷箴言（洞悉一丝先机）", "persona_preset": "苟道（稳健发育、保命优先）",
        "distill_enabled": True, "companion_roster": [{"name": "苏叶", "skill": "情报", "background": "同行的观测者", "participation": 8}],
        "heroine_roster": [{"name": "周桐", "skill": "医术", "background": "药铺掌柜", "participation": 7}], "heroine_mode": "单女主",
        "enable_nemesis": True, "nemesis_select": "幕后掮客（暗中操纵货物失踪案）", "convergence": "较高",
        "story_richness": 1000, "paper_tier": 5, "story_agent_mode": True,
    }
    started = time.time()
    events, state, stream_error = stream(base, "/api/sessions/start", start_body, timeout=3600)
    rec.check("强化鸿篇类 Agent 开局", stream_error is None and isinstance(state, dict),
              {"error": stream_error, "events": len(events), "elapsed_sec": round(time.time() - started, 1)})
    if isinstance(state, dict):
        rec.check("开局配置契约", str(state.get("mode") or "").startswith("强化") and int(state.get("paper_tier") or 0) == 5
                  and bool(state.get("agent_mode") or state.get("story_agent_mode")),
                  {"mode": state.get("mode"), "paper_tier": state.get("paper_tier"), "paper_family": state.get("paper_family"),
                   "agent_mode": state.get("agent_mode"), "story_agent_mode": state.get("story_agent_mode"),
                   "richness": state.get("story_richness")})
    if not isinstance(state, dict):
        return None, None

    def send(message: str, timeout: int = 1800) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
        return stream(base, f"/api/sessions/{session_id}/messages", {"message": message}, timeout=timeout)

    _, state, error = send("确认金手指", timeout=1200)
    rec.check("强化模式确认金手指", error is None and isinstance(state, dict) and state.get("gf_confirmed") is True,
              {"error": error, "gf_confirmed": state.get("gf_confirmed") if isinstance(state, dict) else None})
    _, state, error = send("确认开局", timeout=1800)
    rec.check("强化模式确认开局", error is None and isinstance(state, dict) and state.get("scene_gate") is True,
              {"error": error, "opening_confirmed": state.get("opening_confirmed") if isinstance(state, dict) else None,
               "round": state.get("round") if isinstance(state, dict) else None})
    if not isinstance(state, dict):
        return None, None

    books = probe(rec, base, 0, "原著作品库", "GET", "/api/books",
                  expected=lambda code, data: code == 200 and isinstance(data, dict))
    book_id = None
    if isinstance(books, dict) and books.get("books"):
        book_id = books["books"][0].get("book_id")
        if book_id:
            encoded_book_id = urllib.parse.quote(str(book_id), safe="")
            probe(rec, base, 0, "原著章节目录", "GET", f"/api/books/{encoded_book_id}", expected=lambda code, data: code == 200)
            probe(rec, base, 0, "原著阅读器首章", "GET", f"/api/books/{encoded_book_id}/chapters/1", expected=lambda code, data: code == 200)
            probe(rec, base, 0, "原著锚点阅读", "GET", f"/api/books/{encoded_book_id}/chapters/1/anchors", expected=lambda code, data: code == 200)
            probe(rec, base, 0, "原著搜索", "GET", f"/api/books/{encoded_book_id}/search?{urllib.parse.urlencode({'q': '第一章'})}", expected=lambda code, data: code in (200, 404))
            probe(rec, base, 0, "原著准备", "POST", f"/api/books/{encoded_book_id}/prepare", expected=lambda code, data: code == 200)
    probe(rec, base, 0, "闲聊角色名册", "GET", f"/api/chat/roster?session_id={session_id}",
          expected=lambda code, data: code == 200 and isinstance(data, dict) and isinstance(data.get("roster"), list))
    probe(rec, base, 0, "状态读取", "GET", f"/api/sessions/{session_id}/state",
          expected=lambda code, data: code == 200 and isinstance(data, dict) and isinstance(data.get("state"), dict))

    quest_done: dict[str, bool] = {"short": False, "medium": False, "long": False}
    autoplay_letter: str | None = None
    previous_pos: float | None = None
    current_state = state
    successful_turns = 0

    while successful_turns < rounds_total:
        requested_round = successful_turns + 1
        # requested_round is the next real gameplay turn.  It advances only
        # after the server returns a committed state with round +1.
        before_round = int(current_state.get("round") or 0) if isinstance(current_state, dict) else 0
        target_round = before_round + 1
        if target_round == 2:
            roster_status, roster_payload = http(base, "GET", f"/api/chat/roster?session_id={session_id}", timeout=90)
            roster = roster_payload.get("roster") if isinstance(roster_payload, dict) else []
            rec.feature(2, "角色闲聊 roster 与当前角色过滤", "GET", "/api/chat/roster", roster_status,
                        roster_status == 200 and isinstance(roster, list) and bool(roster), {"count": len(roster) if isinstance(roster, list) else 0,
                        "names": [str(row.get("name")) for row in (roster or [])[:8] if isinstance(row, dict) and row.get("name")]})
            if roster and isinstance(roster[0], dict) and roster[0].get("name"):
                probe_state_isolation(rec, base, session_id, str(roster[0]["name"]), requested_round)
            ui_payload = {"session_id": session_id, "ui_state": {"probe": "playtest_100", "round": requested_round, "chat_open": True}}
            probe(rec, base, requested_round, "UI 状态保存", "POST", "/api/session/ui-state", ui_payload,
                  expected=lambda code, data: code == 200)
            probe(rec, base, requested_round, "UI 状态恢复", "GET", f"/api/session/ui-state?session_id={session_id}",
                  expected=lambda code, data: code == 200 and isinstance(data, dict))
        if target_round == 3:
            probe(rec, base, requested_round, "结构化问题批量处理", "POST", f"/api/sessions/{session_id}/questions/batch",
                  {"questions": [{"id": "probe", "answer_type": "single_choice", "question": "探查", "choices": [{"id": "safe", "label": "稳健"}]}], "max_concurrency": 1},
                  expected=lambda code, data: code == 200)
        if target_round in (4, 20, 40):
            kind = {4: "short", 20: "medium", 40: "long"}[target_round]
            status, payload = http(base, "POST", f"/api/sessions/{session_id}/quests/offer", {"kind": kind, "difficulty": 0.45 + target_round / 1000}, timeout=900)
            quest = payload.get("quest") if isinstance(payload, dict) else None
            rec.feature(target_round, f"任务 {kind} offer", "POST", "/api/sessions/{id}/quests/offer", status,
                        status == 200 and isinstance(quest, dict), {"title": (quest or {}).get("title") if isinstance(quest, dict) else None,
                        "deadline_span": (quest or {}).get("deadline_span") if isinstance(quest, dict) else None})
            if status == 200 and isinstance(quest, dict):
                accept_status, accepted = http(base, "POST", f"/api/sessions/{session_id}/quests/accept", timeout=300)
                rec.feature(target_round, f"任务 {kind} accept", "POST", "/api/sessions/{id}/quests/accept", accept_status,
                            accept_status == 200, {"status": (accepted or {}).get("quest", {}).get("status") if isinstance(accepted, dict) else None})
                quest_done[kind] = accept_status == 200
        if target_round == 10:
            status, payload = http(base, "POST", f"/api/sessions/{session_id}/autoplay-choice", timeout=900)
            autoplay_letter = payload.get("choice") if isinstance(payload, dict) else None
            rec.feature(target_round, "托管单回合选择", "POST", "/api/sessions/{id}/autoplay-choice", status,
                        status == 200 and autoplay_letter in OPTION_KEYS, {"choice": autoplay_letter})
        if target_round == 12:
            probe(rec, base, requested_round, "不懂就问规则答疑", "POST", f"/api/sessions/{session_id}/ask",
                  {"question": "本局当前的收束力和回合推进规则是什么？"},
                  expected=lambda code, data: code == 200 and isinstance(data, dict))
        if target_round == 15:
            save_id = "play100-checkpoint-15"
            save_status, save_payload = http(base, "POST", f"/api/sessions/{session_id}/save", {"save_id": save_id}, timeout=180)
            rec.feature(target_round, "存档", "POST", "/api/sessions/{id}/save", save_status,
                        save_status == 200 and isinstance(save_payload, dict) and save_payload.get("saved") is True,
                        {"save_id": save_id})
            load_status, loaded = http(base, "POST", "/api/saves/load", {"save_id": save_id}, timeout=180)
            loaded_state = loaded.get("state") if isinstance(loaded, dict) else None
            rec.feature(target_round, "独立读档", "POST", "/api/saves/load", load_status,
                        load_status == 200 and isinstance(loaded_state, dict) and valid_options(loaded_state.get("options")),
                        state_summary(loaded_state))
            list_status, saves = http(base, "GET", "/api/saves", timeout=90)
            rec.feature(target_round, "存档列表", "GET", "/api/saves", list_status,
                        list_status == 200 and isinstance(saves, dict), {"count": len(saves.get("saves") or []) if isinstance(saves, dict) else 0})
        if target_round == 60:
            roster_status, roster_payload = http(base, "GET", f"/api/chat/roster?session_id={session_id}", timeout=90)
            roster = roster_payload.get("roster") if isinstance(roster_payload, dict) else []
            if roster and isinstance(roster[0], dict) and roster[0].get("name"):
                probe_state_isolation(rec, base, session_id, str(roster[0]["name"]), requested_round)
        if target_round == 10:
            # First feature entry point for break-anchor starts here. Probe decline
            # before acceptance when an offer is available, then re-offer and accept.
            offer_status, offer = http(base, "POST", f"/api/sessions/{session_id}/break-anchor/offer", timeout=1200)
            break_box = offer.get("break_anchor") if isinstance(offer, dict) else None
            rec.feature(target_round, "碎锚 offer", "POST", "/api/sessions/{id}/break-anchor/offer", offer_status,
                        offer_status == 200 and isinstance(break_box, dict), {"status": (break_box or {}).get("status") if isinstance(break_box, dict) else None,
                        "stage": (break_box or {}).get("stage") if isinstance(break_box, dict) else None})
            if offer_status == 200 and isinstance(break_box, dict) and break_box.get("status") == "offered":
                decline_status, declined = http(base, "POST", f"/api/sessions/{session_id}/break-anchor/decline", timeout=300)
                declined_box = declined.get("break_anchor") if isinstance(declined, dict) else None
                rec.feature(target_round, "碎锚 decline 状态回收", "POST", "/api/sessions/{id}/break-anchor/decline", decline_status,
                            decline_status == 200, {"status": (declined_box or {}).get("status") if isinstance(declined_box, dict) else None})
                retry_status, retry_offer = http(base, "POST", f"/api/sessions/{session_id}/break-anchor/offer", timeout=1200)
                retry_box = retry_offer.get("break_anchor") if isinstance(retry_offer, dict) else None
                rec.feature(target_round, "碎锚再次 offer", "POST", "/api/sessions/{id}/break-anchor/offer", retry_status,
                            retry_status == 200 and isinstance(retry_box, dict), {"status": (retry_box or {}).get("status") if isinstance(retry_box, dict) else None})
                if retry_status == 200 and isinstance(retry_box, dict) and retry_box.get("status") == "offered":
                    accept_status, accepted = http(base, "POST", f"/api/sessions/{session_id}/break-anchor/accept", timeout=300)
                    accepted_box = accepted.get("break_anchor") if isinstance(accepted, dict) else None
                    rec.feature(target_round, "碎锚 accept", "POST", "/api/sessions/{id}/break-anchor/accept", accept_status,
                                accept_status == 200, {"status": (accepted_box or {}).get("status") if isinstance(accepted_box, dict) else None})
        if target_round == 11:
            # Cheats are deliberately tested only through /ask, never through action/chat.
            # Test sequence: 1. Arm wishes (WHOSLOMSTING), 2. Grant wish, 3. Three more wishes (THREEMORE), 4. God mode (IDDQD)
            
            # Step 1: 武装愿望系统
            code_status, code_payload = http(base, "POST", f"/api/sessions/{session_id}/ask",
                                             {"question": "UUDDLLRRBABAWHOSLOMSTING"}, timeout=900)
            rec.feature(target_round, "作弊码武装", "POST", "/api/sessions/{id}/ask", code_status,
                        code_status == 200 and isinstance(code_payload, dict) and code_payload.get("wish_armed") is True,
                        {"wish_armed": (code_payload or {}).get("wish_armed") if isinstance(code_payload, dict) else None})
            
            # Step 2: 愿望兑现测试
            wish_status, wish_payload = http(base, "POST", f"/api/sessions/{session_id}/ask",
                                             {"question": "让主角获得一枚疗伤丹药"}, timeout=900)
            rec.feature(target_round, "作弊码愿望兑现", "POST", "/api/sessions/{id}/ask", wish_status,
                        wish_status == 200 and isinstance(wish_payload, dict) and wish_payload.get("wish_granted") is True,
                        {"wish_granted": (wish_payload or {}).get("wish_granted") if isinstance(wish_payload, dict) else None})
            
            # Step 3: 三愿补充（THREEMORE）
            three_status, three_payload = http(base, "POST", f"/api/sessions/{session_id}/ask",
                                               {"question": "THREEMORE"}, timeout=900)
            rec.feature(target_round, "作弊码三愿补充", "POST", "/api/sessions/{id}/ask", three_status,
                        three_status == 200 and isinstance(three_payload, dict) and three_payload.get("cheat_activated") is True,
                        {"cheat_activated": (three_payload or {}).get("cheat_activated") if isinstance(three_payload, dict) else None,
                         "remaining_wishes": (three_payload or {}).get("remaining_wishes") if isinstance(three_payload, dict) else None})
            
            # Step 4: 一次性打通（IDDQD）
            god_status, god_payload = http(base, "POST", f"/api/sessions/{session_id}/ask",
                                           {"question": "IDDQD"}, timeout=900)
            rec.feature(target_round, "作弊码一次性打通", "POST", "/api/sessions/{id}/ask", god_status,
                        god_status == 200 and isinstance(god_payload, dict) and god_payload.get("cheat_activated") is True,
                        {"cheat_activated": (god_payload or {}).get("cheat_activated") if isinstance(god_payload, dict) else None,
                         "god_mode": (god_payload or {}).get("god_mode") if isinstance(god_payload, dict) else None})

        options = current_state.get("options") if isinstance(current_state, dict) else []
        if not valid_options(options):
            rec.check(f"第{requested_round}回合输入前 A-F 合法", False, {"options": safe_json(options)})
            rec.error(f"第{requested_round}回合输入前门禁失败", "当前 durable state 缺少完整 A-F，已停止实验")
            break
            
        if target_round == 10 and autoplay_letter in OPTION_KEYS:
            choice = autoplay_letter
        else:
            choice = decide(endpoint, api_key, model, options, last_assistant(events) if 'events' in locals() else "")
        action = option_action(options, choice)
        started_turn = time.time()
        events, next_state, turn_error = send(action)
        if turn_error:
            rec.error(f"第{requested_round}回合首次请求", turn_error)
            events, next_state, turn_error = send("自由行动：继续推进当前目标")
            rec.check(f"第{requested_round}回合重试", turn_error is None and isinstance(next_state, dict), turn_error or "")
        elapsed = round(time.time() - started_turn, 1)
        after_state = next_state if isinstance(next_state, dict) else current_state
        after_round = int(after_state.get("round") or 0) if isinstance(after_state, dict) else before_round
        after_options = after_state.get("options") if isinstance(after_state, dict) else []
        successful_progress = (
            turn_error is None
            and isinstance(next_state, dict)
            and after_round == before_round + 1
            and next_state.get("save_stage") == "committed"
            and next_state.get("scene_gate") is True
            and valid_options(after_options)
        )
        if not successful_progress:
            rec.round({
                "requested_round": requested_round,
                "state_round": after_round,
                "action": action,
                "turn_elapsed_sec": elapsed,
                "scene_gate": after_state.get("scene_gate") if isinstance(after_state, dict) else None,
                "save_stage": after_state.get("save_stage") if isinstance(after_state, dict) else None,
                "option_count": len(after_options) if isinstance(after_options, list) else 0,
                "options_valid_af": valid_options(after_options),
                "successful_progress": False,
                "request_error": turn_error or (
                    f"回合未严格推进：before={before_round}, after={after_round}"
                ),
            })
            rec.check(f"第{requested_round}回合推进", False, {
                "before_round": before_round,
                "state_round": after_round,
                "error": turn_error,
            })
            rec.error(f"第{requested_round}回合未完成", {
                "before_round": before_round,
                "after_round": after_round,
                "error": turn_error or "状态未严格推进或未通过提交门禁",
            })
            break

        current_state = next_state
        successful_turns += 1
        length = ((current_state.get("scene_validation") or {}).get("length")
                  if isinstance(current_state.get("scene_validation"), dict) else {}) or {}
        convergence = current_state.get("convergence_state") or {}
        position = convergence.get("position") if isinstance(convergence, dict) else None
        drift = None
        if isinstance(position, (int, float)) and previous_pos is not None:
            drift = round(abs(float(position) - previous_pos), 5)
        if isinstance(position, (int, float)):
            previous_pos = float(position)
        usage = ((current_state.get("agent_meta") or {}).get("usage")
                 if isinstance(current_state.get("agent_meta"), dict) else {}) or {}
        options_after = current_state.get("options") or []
        text = last_assistant(events)
        narrative = narrative_only(text)
        options_public = options_for_summary(options_after)
        round_item = {
            "requested_round": requested_round,
            "state_round": current_state.get("round"),
            "before_round": before_round,
            "chapter": current_state.get("current_chapter"),
            "chapter_round": current_state.get("chapter_round"),
            "turn_budget": current_state.get("turn_budget"),
            "action": action,
            "choice": choice,
            "scene_gate": current_state.get("scene_gate"),
            "scene_gate_reason": str(current_state.get("scene_gate_reason") or "")[:240],
            "save_stage": current_state.get("save_stage"),
            "durable_state": current_state.get("save_stage") == "committed" and current_state.get("scene_gate") is True,
            "option_count": len(options_after),
            "options_valid_af": valid_options(options_after),
            "option_keys": [str(item.get("key")) for item in options_after if isinstance(item, dict)],
            "length": safe_json(length),
            "body_chars": len(narrative),
            "body_preview": narrative.replace("\n", " ")[:180],
            "convergence": safe_json(convergence),
            "convergence_drift": drift,
            "quest": safe_json(current_state.get("quest")),
            "break_anchor": safe_json(current_state.get("break_anchor")),
            "compression": safe_json(current_state.get("compression_record")),
            "active_members": [str(row.get("name")) for row in (current_state.get("active_members") or []) if isinstance(row, dict)],
            "side_chat_characters": list((current_state.get("side_chats") or {}).keys()) if isinstance(current_state.get("side_chats"), dict) else [],
            "usage": safe_json(usage),
            "tok_last": current_state.get("tok_last"),
            "tok_in": current_state.get("tok_in"),
            "tok_out": current_state.get("tok_out"),
            "turn_elapsed_sec": elapsed,
            "request_error": turn_error,
            "successful_progress": True,
        }
        rec.round(round_item)
        rec.story_option({**round_item, "body": narrative, "options": options_public})
        rec.check(f"第{requested_round}回合推进", True,
                  {"before_round": before_round, "state_round": current_state.get("round")})
        rec.check(f"第{requested_round}回合门禁", current_state.get("scene_gate") is True,
                  current_state.get("scene_gate_reason"))
        rec.check(f"第{requested_round}回合完整 A-F", valid_options(options_after),
                  [str(item.get("key")) for item in options_after if isinstance(item, dict)])
        if target_round == 10:
            autoplay_letter = None
        if target_round % 10 == 0:
            progress = probe(rec, base, target_round, "锚点蒸馏进度", "GET", f"/api/sessions/{session_id}/distill/progress",
                             expected=lambda code, data: code == 200)
            if isinstance(progress, dict):
                rec.rounds[-1]["distill_progress"] = {key: progress.get(key) for key in ("current", "total", "status", "summary")}

    run_complete = (
        successful_turns == rounds_total
        and isinstance(current_state, dict)
        and rec.starting_round is not None
        and int(current_state.get("round") or -1) == rec.starting_round + rounds_total
        and current_state.get("save_stage") == "committed"
        and current_state.get("scene_gate") is True
        and valid_options(current_state.get("options"))
        and len(rec.story_options) == rounds_total
    )
    rec.run_complete = bool(run_complete)
    final_export: dict[str, Any] | None = None
    if not run_complete:
        rec.error("终局校验", {
            "successful_turns": successful_turns,
            "planned": rounds_total,
            "starting_round": rec.starting_round,
            "final_round": current_state.get("round") if isinstance(current_state, dict) else None,
            "story_options": len(rec.story_options),
        })
        return current_state, None

    final_save_id = f"play100-final-{RUN_TAG}"
    save_status, saved = http(base, "POST", f"/api/sessions/{session_id}/save", {"save_id": final_save_id}, timeout=300)
    save_ok = save_status == 200 and isinstance(saved, dict) and saved.get("saved") is True
    rec.final_save_ok = bool(save_ok)
    rec.feature(int(current_state.get("round") or 0), "最终存档", "POST", "/api/sessions/{id}/save", save_status,
                save_ok, {"save_id": final_save_id})

    loaded_state: dict[str, Any] | None = None
    load_status = -1
    if save_ok:
        load_status, loaded = http(base, "POST", "/api/saves/load", {"save_id": final_save_id}, timeout=300)
        loaded_state = loaded.get("state") if isinstance(loaded, dict) else None
        same_state, differences = states_equivalent(current_state, loaded_state or {})
        rec.final_load_ok = bool(
            load_status == 200
            and isinstance(loaded_state, dict)
            and same_state
            and loaded_state.get("save_stage") == "committed"
            and valid_options(loaded_state.get("options"))
        )
        rec.feature(int(current_state.get("round") or 0), "最终独立读档", "POST", "/api/saves/load", load_status,
                    rec.final_load_ok, {"save_id": final_save_id, "differences": differences})
    else:
        rec.final_load_ok = False
        rec.feature(int(current_state.get("round") or 0), "最终独立读档", "POST", "/api/saves/load", -1,
                    False, {"save_id": final_save_id, "reason": "final save failed"})

    if rec.final_save_ok and rec.final_load_ok:
        export_status, exported = http(base, "POST", f"/api/sessions/{session_id}/export-novel", {"style": "faithful"}, timeout=3600)
        final_export = exported if isinstance(exported, dict) else None
        chapters = final_export.get("chapters") if isinstance(final_export, dict) else []
        full_text = str(final_export.get("full_text") or "") if isinstance(final_export, dict) else ""
        export_ok = (
            export_status == 200
            and isinstance(final_export, dict)
            and isinstance(chapters, list)
            and bool(chapters)
            and len(full_text) > 500
            and not any(marker in full_text for marker in ("<<<LOG>>>", "<<<ARCHIVE>>>", "（系统提示", "引擎日志"))
        )
        rec.final_export_ok = bool(export_ok)
        rec.feature(int(current_state.get("round") or 0), "小说导出", "POST", "/api/sessions/{id}/export-novel", export_status,
                    export_ok, {"chapters": len(chapters) if isinstance(chapters, list) else 0,
                                "full_text_chars": len(full_text),
                                "story_options_rounds": len(rec.story_options)})
    else:
        rec.final_export_ok = False
        rec.feature(int(current_state.get("round") or 0), "小说导出", "POST", "/api/sessions/{id}/export-novel", -1,
                    False, {"reason": "final save/load validation failed"})
    return current_state, final_export


def parse_args(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Novelborne HCYA 100回合真实实测")
    parser.add_argument("--base", default=os.environ.get("FATE_BASE", DEFAULT_BASE))
    parser.add_argument("--base-url", default=os.environ.get("FATE_BASE_URL") or os.environ.get("NOVELBORNE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--provider", default=os.environ.get("FATE_PROVIDER", "custom"))
    parser.add_argument("--model", default=os.environ.get("FATE_MODEL", "gpt-5.6-sol"))
    parser.add_argument("--txt", default=os.environ.get("FATE_TXT", DEFAULT_TXT))
    parser.add_argument("--rounds", type=int, default=100)
    args = parser.parse_args(argv)
    return {"base": args.base, "base_url": args.base_url, "provider": args.provider, "model": args.model, "txt": args.txt,
            "rounds": max(1, min(100, args.rounds)),
            "api_key": os.environ.get("FATE_API_KEY") or os.environ.get("NOVELBORNE_API_KEY", "")}


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    txt = Path(config["txt"])
    if not config["api_key"]:
        print("缺少 FATE_API_KEY：真实 100 回合实测未启动。", file=sys.stderr)
        return 2
    if not txt.is_file():
        print(f"原著 TXT 不存在：{txt}", file=sys.stderr)
        return 2
    rec = Recorder(base=config["base"], endpoint=config["base_url"], model=config["model"], txt=txt, rounds=config["rounds"])
    public_config = {"base": config["base"], "provider": config["provider"], "base_url": config["base_url"], "model": config["model"],
                     "thinking_mode": "auto", "rounds": config["rounds"], "mode": "强化模式", "paper_tier": 5,
                     "paper_label": "鸿篇", "story_agent_mode": True, "story_richness": 1000,
                     "api_key_present": True, "api_key_masked": "***"}
    final_state: dict[str, Any] | None = None
    export_summary: dict[str, Any] | None = None
    try:
        final_state, exported = run(config, rec)
        if isinstance(exported, dict):
            export_summary = {"chapters": len(exported.get("chapters") or []), "full_text_chars": len(exported.get("full_text") or ""),
                              "manifest": safe_json(exported.get("manifest"))}
    except Exception as exc:  # noqa: BLE001
        rec.error("运行异常", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
        print(traceback.format_exc(), file=sys.stderr)
    report = rec.report(config=public_config, final_state=final_state, export_summary=export_summary)
    final_round = int(final_state.get("round") or 0) if isinstance(final_state, dict) else 0
    success = (
        report["run_complete"]
        and report["final_save_ok"]
        and report["final_load_ok"]
        and report["final_export_ok"]
        and report["rounds_completed"] == config["rounds"]
        and final_round == (rec.starting_round or 0) + config["rounds"]
        and report["checks_failed"] == 0
    )
    print(json.dumps({"report": str(REPORT_PATH), "round_log": str(ROUND_LOG_PATH), "feature_log": str(FEATURE_LOG_PATH),
                      "story_options": str(STORY_OPTIONS_PATH),
                      "rounds_completed": report["rounds_completed"], "checks_passed": report["checks_passed"],
                      "checks_failed": report["checks_failed"], "run_complete": report["run_complete"],
                      "final_save_ok": report["final_save_ok"], "final_load_ok": report["final_load_ok"],
                      "final_export_ok": report["final_export_ok"], "success": success}, ensure_ascii=False), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
