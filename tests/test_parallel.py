# -*- coding: utf-8 -*-
"""engine.parallel 动态并发控制器单测（重构 M0）。

覆盖：目标钳制、排队不报错、优先级授予顺序、AIMD 退避与恢复、
budget_model 限流标记与幂等包装、run_parallel 顺序与异常捕获。
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import parallel  # noqa: E402


class TestControllerBasics(unittest.TestCase):
    def test_target_clamped_to_hard_limit(self):
        controller = parallel.ConcurrencyController(target=99)
        self.assertEqual(controller.target, parallel.HARD_LIMIT)
        limited = parallel.ConcurrencyController(target=0)
        self.assertEqual(limited.target, 1)

    def test_queue_never_errors_when_concurrency_short(self):
        controller = parallel.ConcurrencyController(target=1)
        seen = []
        lock = threading.Lock()

        def worker(index):
            with controller.slot():
                with lock:
                    current = controller.stats()["inflight"]
                    seen.append(current)
                time.sleep(0.02)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        # 目标并发 1：所有任务排队完成，峰值在途 == 1，绝不抛错。
        self.assertEqual(len(seen), 6)
        self.assertEqual(max(seen), 1)

    def test_explicit_timeout_raises_timeout(self):
        controller = parallel.ConcurrencyController(target=1)
        controller.acquire()
        with self.assertRaises(TimeoutError):
            controller.acquire(timeout=0.05)
        controller.release()

    def test_priority_grants_higher_first(self):
        controller = parallel.ConcurrencyController(target=1)
        controller.acquire()  # 主线程占住唯一额度
        granted = []
        lock = threading.Lock()

        def waiter(priority, tag):
            controller.acquire(priority=priority)
            with lock:
                granted.append(tag)
            controller.release()

        low = threading.Thread(target=waiter, args=(parallel.PRIORITY_BACKGROUND, "low"))
        low.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and controller.stats()["waiting"] < 1:
            time.sleep(0.01)
        self.assertGreaterEqual(controller.stats()["waiting"], 1)
        high = threading.Thread(target=waiter, args=(parallel.PRIORITY_OPENING, "high"))
        high.start()
        time.sleep(0.05)
        controller.release()  # 释放后高优先级先获额度
        low.join(timeout=5)
        high.join(timeout=5)
        self.assertEqual(granted, ["high", "low"])


class TestAimd(unittest.TestCase):
    def test_backoff_halves_and_recovers(self):
        controller = parallel.ConcurrencyController(target=6)
        self.assertEqual(controller.limit, 6)
        controller.acquire()
        controller.release(rate_limited=True)
        self.assertEqual(controller.limit, 3)
        controller.acquire()
        controller.release(rate_limited=True)
        self.assertEqual(controller.limit, 1)
        # 冷却到期后 pump 恢复 +1。
        with controller._cv:
            controller._cooldown_until = time.monotonic() - 1
        controller.acquire()
        self.assertEqual(controller.limit, 2)
        controller.release()

    def test_slow_increase_on_success(self):
        controller = parallel.ConcurrencyController(target=4)
        controller.acquire()
        controller.release(rate_limited=True)   # 4 -> 2
        controller.acquire()
        controller.release(rate_limited=True)   # 2 -> 1
        with controller._cv:
            controller._cooldown_until = time.monotonic() - 1
        for _ in range(parallel._INCREASE_EVERY):
            controller.acquire()
            self.assertLessEqual(controller.limit, controller.target)
            controller.release()
        # 冷却恢复 + 成功加窗应至少回到 2。
        self.assertGreaterEqual(controller.limit, 2)


class TestBudgetModel(unittest.TestCase):
    def setUp(self):
        self._saved = parallel.api_controller
        self.controller = parallel.ConcurrencyController(target=4)
        parallel.api_controller = self.controller

    def tearDown(self):
        parallel.api_controller = self._saved

    def test_rate_limit_marks_backoff(self):
        def boom(prompt):
            raise ValueError("Error code: 429 too many requests")

        wrapped = parallel.budget_model(boom, parallel.PRIORITY_BACKGROUND)
        with self.assertRaises(ValueError):
            wrapped("p")
        self.assertEqual(self.controller.limit, 2)
        stats = self.controller.stats()
        self.assertTrue(any(e["kind"] == "backoff" for e in stats["events"]))

    def test_budget_model_idempotent(self):
        def calm(prompt):
            return "ok"

        once = parallel.budget_model(calm)
        twice = parallel.budget_model(once)
        self.assertIs(once, twice)
        self.assertEqual(once("p"), "ok")

    def test_priority_scope_switches_priority(self):
        def calm(prompt):
            return "ok"

        wrapped = parallel.budget_model(calm, parallel.PRIORITY_BACKGROUND)
        with parallel.priority_scope(parallel.PRIORITY_OPENING):
            self.assertEqual(parallel.current_priority(), parallel.PRIORITY_OPENING)
            self.assertEqual(wrapped("p"), "ok")
        # 退出作用域后回到默认（无线程局部优先级时取 PRIORITY_TURN）。
        self.assertEqual(parallel.current_priority(), parallel.PRIORITY_TURN)


class TestRunParallel(unittest.TestCase):
    def test_order_preserved_and_errors_captured(self):
        def boom():
            raise RuntimeError("job failed")

        results = parallel.run_parallel([lambda: 1, boom, lambda: 3])
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].value, 1)
        self.assertFalse(results[1].ok)
        self.assertIsInstance(results[1].error, RuntimeError)
        self.assertTrue(results[2].ok)
        self.assertEqual(results[2].value, 3)

    def test_is_rate_limit_error(self):
        self.assertTrue(parallel.is_rate_limit_error(ValueError("429 Too Many Requests")))
        self.assertTrue(parallel.is_rate_limit_error(RuntimeError("rate limit exceeded")))
        self.assertFalse(parallel.is_rate_limit_error(ValueError("bad json")))


if __name__ == "__main__":
    unittest.main()
