"""网络故障注入测试：超时、429/5xx、断线、熔断、半开探测、部分失败汇合。

覆盖 resilient_gateway.ResilientCall 的真实故障行为，全部为离线 mock，
不依赖真实 provider。
"""
from __future__ import annotations

import threading
import time
import unittest

from core.services.resilient_gateway import CircuitOpen, ResilientCall


class FakeClock:
    """可控时钟：支持线程安全推进。"""

    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


class FlakyProvider:
    """按脚本抛错的 provider：每次 call 弹出一个异常或返回值。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.script.pop(0) if self.script else RuntimeError("exhausted")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ResilientGatewayFaultInjectionTests(unittest.TestCase):
    def test_success_after_transient_429(self):
        """429 一次后成功：应重试并返回结果，熔断不打开。"""
        clock = FakeClock()
        gw = ResilientCall(failures=3, cooldown=30.0, clock=clock, sleep=lambda s: clock.advance(s))
        provider = FlakyProvider([RuntimeError("429 rate limit"), "ok"])
        self.assertEqual(gw.call(provider, attempts=3), "ok")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(gw.errors, 0)

    def test_non_retryable_error_fails_fast(self):
        """不可重试错误（如 401 认证失败）不应消耗重试次数。"""
        clock = FakeClock()
        gw = ResilientCall(failures=3, cooldown=30.0, clock=clock, sleep=lambda s: clock.advance(s))
        provider = FlakyProvider([RuntimeError("401 unauthorized")])
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=3, retryable=lambda e: "401" not in str(e))
        self.assertEqual(provider.calls, 1)

    def test_circuit_opens_after_consecutive_failures(self):
        """连续失败达到阈值：熔断打开，后续调用直接拒绝不再触达 provider。"""
        clock = FakeClock()
        gw = ResilientCall(failures=3, cooldown=30.0, clock=clock, sleep=lambda s: clock.advance(s))
        provider = FlakyProvider([RuntimeError("503")] * 5)
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=1)
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=1)
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=1)
        self.assertEqual(provider.calls, 3)
        # 熔断窗口内的新请求不触达 provider
        with self.assertRaises(CircuitOpen):
            gw.call(provider, attempts=1)
        self.assertEqual(provider.calls, 3)

    def test_circuit_closes_after_cooldown_via_probe(self):
        """冷却期过后允许一次半开探测：探测成功则熔断关闭。"""
        clock = FakeClock()
        gw = ResilientCall(failures=2, cooldown=30.0, clock=clock, sleep=lambda s: clock.advance(s))
        provider = FlakyProvider([RuntimeError("timeout"), RuntimeError("timeout"), "recovered", "recovered"])
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=1)
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=1)
        with self.assertRaises(CircuitOpen):
            gw.call(provider, attempts=1)
        clock.advance(31.0)  # 冷却结束，放行一次探测
        self.assertEqual(gw.call(provider, attempts=1), "recovered")
        # 熔断已关闭：再次正常调用
        self.assertEqual(gw.call(provider, attempts=1), "recovered")

    def test_half_open_probe_rejects_parallel_calls(self):
        """探测进行中：并发请求被拒绝，不重复触达 provider。"""
        clock = FakeClock()
        gw = ResilientCall(failures=1, cooldown=30.0, clock=clock, sleep=lambda s: clock.advance(s))
        slow = threading.Event()

        def slow_fn():
            slow.wait(timeout=5)
            return "late"

        provider = FlakyProvider([RuntimeError("500")])
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=1)
        clock.advance(31.0)
        # 第一个线程发起半开探测并挂起
        result_holder: dict = {}

        def probe():
            try:
                result_holder["value"] = gw.call(slow_fn, attempts=1)
            except Exception as exc:  # pragma: no cover
                result_holder["error"] = exc

        thread = threading.Thread(target=probe)
        thread.start()
        time.sleep(0.1)  # 等探测进入
        with self.assertRaises(CircuitOpen):
            gw.call(provider, attempts=1)
        slow.set()
        thread.join(timeout=5)
        self.assertEqual(result_holder.get("value"), "late")

    def test_backoff_is_bounded(self):
        """退避封顶：attempts=4 时总 sleep 不超过 0.25+0.5+1.0=1.75s 的上限。"""
        clock = FakeClock()
        sleeps: list[float] = []
        gw = ResilientCall(failures=99, cooldown=0.0, clock=clock, sleep=sleeps.append)
        provider = FlakyProvider([RuntimeError("502")] * 4)
        with self.assertRaises(RuntimeError):
            gw.call(provider, attempts=4)
        self.assertEqual(len(sleeps), 3)
        self.assertTrue(all(s <= 2.0 for s in sleeps))
        self.assertEqual(sleeps, [0.25, 0.5, 1.0])

    def test_success_resets_error_counter(self):
        """成功后错误计数清零：零星失败不会累积成熔断。"""
        clock = FakeClock()
        gw = ResilientCall(failures=3, cooldown=30.0, clock=clock, sleep=lambda s: clock.advance(s))
        script = [RuntimeError("503"), "ok", RuntimeError("503"), "ok", RuntimeError("503"), "ok", "ok"]
        provider = FlakyProvider(script)
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                gw.call(provider, attempts=1)
            self.assertEqual(gw.call(provider, attempts=1), "ok")
        self.assertEqual(gw.errors, 0)
        # 零星失败从未达到连续 3 次阈值：不应熔断
        self.assertEqual(gw.call(provider, attempts=1), "ok")

    def test_partial_parallel_failure_isolated(self):
        """并行任务部分失败：成功任务保留，失败任务独立标记，不互相污染。"""
        results = {"done": [], "failed": []}

        def make_task(i: int):
            def task():
                if i % 2 == 0:
                    raise RuntimeError(f"task {i} failed")
                return f"task {i} ok"
            return task

        clock = FakeClock()
        threads: list[threading.Thread] = []
        lock = threading.Lock()
        for i in range(6):
            gw = ResilientCall(failures=1, cooldown=0.0, clock=clock, sleep=lambda s: None)

            def run(index=i, gateway=gw):
                try:
                    value = gateway.call(make_task(index), attempts=1)
                    with lock:
                        results["done"].append(value)
                except Exception as exc:
                    with lock:
                        results["failed"].append(str(exc))

            threads.append(threading.Thread(target=run))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(len(results["done"]), 3)
        self.assertEqual(len(results["failed"]), 3)
        self.assertTrue(all("failed" in f for f in results["failed"]))


if __name__ == "__main__":
    unittest.main()
