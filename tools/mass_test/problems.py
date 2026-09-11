# -*- coding: utf-8 -*-
"""问题台账：结构化记录执行过程中的所有问题（JSONL 实时落盘 + 汇总导出）。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class ProblemLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] = []

    def report(self, severity: str, title: str, *, lane: str = "",
               where: str = "", detail: Any = None, excerpt: str = "",
               evidence: str = "", repro: str = "", suspect: str = "") -> dict[str, Any]:
        assert severity in SEVERITY_ORDER, severity
        item = {
            "id": f"PB-{len(self._items) + 1:03d}", "severity": severity,
            "title": title, "lane": lane, "where": where,
            "detail": detail, "excerpt": excerpt[:400], "evidence": evidence,
            "repro": repro, "suspect": suspect,
            "ts": round(time.time(), 3),
        }
        with self._lock:
            self._items.append(item)
            line = _json_line(item)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return item

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._items, key=lambda i: SEVERITY_ORDER[i["severity"]])

    def counts_by_severity(self) -> dict[str, int]:
        counts = {level: 0 for level in SEVERITY_ORDER}
        for item in self.all():
            counts[item["severity"]] += 1
        return counts


def _json_line(item: dict[str, Any]) -> str:
    import json
    return json.dumps(item, ensure_ascii=False, default=str)
