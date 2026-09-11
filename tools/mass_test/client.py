# -*- coding: utf-8 -*-
"""并行测试 HTTP 客户端：请求实时日志 + 全局限流 + 响应质量检测三合一。

每个测试实例一个 MassClient；线程安全，可被多条 lane 并发使用。
所有响应文本自动过 quality_lint，发现即写 quality.jsonl 并入问题台账。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from .live_log import JsonlLogger
from .problems import ProblemLedger
from .quality_lint import lint_payload
from .throttle import GlobalThrottle

# 本地测试桥凭据占位（非真实凭据，桥不鉴权；仅为满足应用端字段长度校验）。
BRIDGE_DUMMY_KEY = "bridge-local-noauth"


class MassClient:
    def __init__(self, base: str, loggers: dict[str, JsonlLogger],
                 ledger: ProblemLedger, throttle: GlobalThrottle, *,
                 lane: str = "", timeout: float = 900.0):
        self.base = base.rstrip("/")
        self.loggers = loggers
        self.ledger = ledger
        self.throttle = throttle
        self.lane = lane
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------- 基础请求
    def request(self, method: str, path: str, *, json: Any = None,
                data: Any = None, files: Any = None, params: Any = None,
                headers: dict[str, str] | None = None,
                timeout: float | None = None,
                quality: bool = True, _retries: int = 2) -> requests.Response:
        started = time.monotonic()
        with self.throttle:
            try:
                response = self.session.request(
                    method, self.base + path, json=json, data=data,
                    files=files, params=params, headers=headers,
                    timeout=timeout or self.timeout)
            except requests.exceptions.RequestException:
                if _retries <= 0:
                    raise
                # DEF-E-05：带请求体的请求非幂等（如导出 POST），读超时重发会
                # 撞上仍在运行的上一个处理器（409）并让服务端重复执行。
                # 只有幂等请求（安全方法且无请求体）才自动重试。
                if json is not None or data is not None or files is not None \
                        or method.upper() not in ("GET", "HEAD", "OPTIONS"):
                    raise
                self.throttle.backoff_after()
                return self.request(method, path, json=json, data=data,
                                    files=files, params=params, headers=headers,
                                    timeout=timeout,
                                    quality=quality, _retries=_retries - 1)
        took = round(time.monotonic() - started, 3)
        self.loggers["http"].write(
            "http", lane=self.lane, method=method, path=path,
            status=response.status_code, took=took)
        if response.status_code == 429:
            self.throttle.backoff_after(float(response.headers.get("retry-after", 2)))
            if _retries > 0:
                return self.request(method, path, json=json, data=data,
                                    files=files, params=params, headers=headers,
                                    timeout=timeout,
                                    quality=quality, _retries=_retries - 1)
        if quality and _is_json(response):
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if payload is not None:
                self._lint(payload, f"{method} {path}")
        leak = self.loggers["http"].find_secret(response.text[:5000], f"{method} {path}")
        if leak:
            self.ledger.report("P0", "响应中出现 API Key 原文", lane=self.lane,
                               where=leak["where"], excerpt=leak["excerpt"])
        return response

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path, **kwargs)

    # ------------------------------------------------------------- NDJSON 流
    def ndjson(self, method: str, path: str, body: Any = None) -> list[dict[str, Any]]:
        """读 NDJSON 流式端点：逐帧写 stream.jsonl + 质检 + 拼接 delta。"""
        frames: list[dict[str, Any]] = []
        started = time.monotonic()
        with self.throttle:
            with self.session.request(method, self.base + path, json=body,
                                      stream=True, timeout=self.timeout) as response:
                if response.status_code != 200:
                    self.loggers["http"].write(
                        "http", lane=self.lane, method=method, path=path,
                        status=response.status_code, took=0, error="non-200 stream")
                    response.raise_for_status()
                # 显式按 UTF-8 解码：requests 对无 charset 的 NDJSON 会用
                # chardet 猜编码，中文 UTF-8 常被误判为 GBK 造成假乱码。
                response.encoding = "utf-8"
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except ValueError:
                        self.ledger.report("P2", "NDJSON 流出现非 JSON 行",
                                           lane=self.lane, where=path, excerpt=line[:200])
                        continue
                    frames.append(frame)
                    self.loggers["stream"].write("frame", lane=self.lane,
                                                 path=path, frame=frame)
                    self._lint(frame, f"{method} {path} stream")
        self.loggers["http"].write(
            "http", lane=self.lane, method=method, path=path,
            status=200, took=round(time.monotonic() - started, 3), stream=True)
        return frames

    # ------------------------------------------------------------- 内部
    def _lint(self, payload: Any, where: str) -> None:
        findings = lint_payload(payload, where)
        if findings:
            for item in findings:
                self.loggers["quality"].write("finding", lane=self.lane, **item)
                severity = "P2" if item["type"] in ("mojibake", "json_leak") else "P3"
                self.ledger.report(
                    severity,
                    {"mojibake": "文本乱码", "json_leak": "正文吐 JSON",
                     "english_run": "中文语境吐英文串"}[item["type"]],
                    lane=self.lane, where=item.get("where", where),
                    excerpt=item.get("excerpt", ""), detail=item)


def _is_json(response: requests.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return "json" in content_type
