# -*- coding: utf-8 -*-
"""限流护栏：全局并发信号量 + 429/超时指数退避。"""
from __future__ import annotations

import random
import threading
import time


class GlobalThrottle:
    def __init__(self, max_concurrency: int = 6):
        self._sem = threading.Semaphore(max_concurrency)
        self._lock = threading.Lock()
        self._next_window = 0.0  # 全局退避窗口：任何 lane 撞 429 都让全体缓一缓。

    def acquire(self) -> None:
        while True:
            with self._lock:
                wait = self._next_window - time.monotonic()
            if wait <= 0:
                break
            time.sleep(min(wait, 5.0))
        self._sem.acquire()

    def release(self) -> None:
        self._sem.release()

    def backoff_after(self, retry_after: float | None = None) -> None:
        """撞限流后设置全局退避窗口（指数 + 抖动，封顶 60 秒）。"""
        base = retry_after if retry_after and retry_after > 0 else 2.0
        with self._lock:
            candidate = time.monotonic() + min(60.0, base * 2) + random.uniform(0, 1.0)
            self._next_window = max(self._next_window, candidate)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        self.release()
        return False
