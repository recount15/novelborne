# -*- coding: utf-8 -*-
"""全进程共享的 API 动态并发控制器（重构 M0 基础件）。

设计目标（重构方案 §八）：
- **硬上限 10**：任何时刻在途模型请求不超过 ``HARD_LIMIT``，配置只能收窄；
- **动态自适应（AIMD）**：连续成功缓慢加窗，限流/超时乘性退避并进入冷却，
  冷却期满逐步恢复；
- **「达不到目标并发绝不报错」**：控制器只决定同时放行几个，超额任务排队
  等待；并发不足本身永远不构成失败原因，单任务失败走各步既有的重试/兜底
  阶梯；低并发供应商自动收敛，降速不降级；
- **优先级调度**：开局蒸馏 > 回合内波次 > 后台蒸馏，等待者按优先级授予额度。

控制器不感知 SDK/传输层：调用方用 :func:`slot` 包住单次模型调用，或用
:func:`budget_model` 构造带额度的模型包装（后台蒸馏/开局流水线共用）。
"""
from __future__ import annotations

import contextlib
import functools
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Optional, Sequence

#: 并发硬上限（重构方案规定：任何配置不得突破）。
HARD_LIMIT = 10

#: 优先级：数值越小越优先获得额度。
PRIORITY_OPENING = 0      # 开局蒸馏流水线（开局未开打，可给满额度）
PRIORITY_TURN = 10        # 回合内波次（导演卷/段卷/润色/结算）
PRIORITY_BACKGROUND = 20  # 后台蒸馏等低优先级通道

_DEFAULT_TARGET = 6
_INCREASE_EVERY = 3       # 连续成功 N 次后加窗 +1（加性缓慢增）
_COOLDOWN_SECONDS = 15.0  # 限流退避冷却期；期内维持低窗，期满逐步恢复
_WAIT_TICK = 0.05         # 等待者周期性重查间隔（秒）

_RATE_LIMIT_MARKERS = (
    "429", "rate limit", "ratelimit", "rate_limit", "too many requests",
    "quota", "限流", "频率超",
)


def is_rate_limit_error(exc: BaseException) -> bool:
    """按异常文本识别限流/配额类错误（跨 SDK、跨供应商的宽容判定）。"""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


class _Ticket:
    """一个等待额度凭证；``key`` 决定被授予的先后（优先级 + 到达序）。"""

    __slots__ = ("priority", "seq", "granted")

    def __init__(self, priority: int, seq: int) -> None:
        self.priority = priority
        self.seq = seq
        self.granted = False

    def key(self) -> tuple[int, int]:
        return (self.priority, self.seq)


class SlotToken:
    """``slot()`` 产出的可标记凭证：调用方在限流/失败时打标记供释放结算。"""

    __slots__ = ("success", "rate_limited")

    def __init__(self) -> None:
        self.success = True
        self.rate_limited = False


class ConcurrencyController:
    """AIMD 动态并发控制器：等待绝不因并发不足而失败。"""

    def __init__(self, target: int = _DEFAULT_TARGET) -> None:
        self._target = max(1, min(HARD_LIMIT, int(target)))
        self._limit = self._target
        self._inflight = 0
        self._waiters: set = set()
        self._seq = 0
        self._cv = threading.Condition()
        self._cooldown_until = 0.0
        self._successes = 0
        self._events: deque = deque(maxlen=64)

    # ---- 配置 ------------------------------------------------------------
    @property
    def target(self) -> int:
        return self._target

    @property
    def limit(self) -> int:
        return self._limit

    def configure(self, target: int) -> None:
        """调整目标并发（收窄立即生效；放大受 AIMD 窗口逐步爬升）。"""
        with self._cv:
            self._target = max(1, min(HARD_LIMIT, int(target)))
            self._limit = min(self._limit, self._target)
            self._cv.notify_all()

    # ---- 额度获取/释放 ------------------------------------------------------
    def acquire(self, priority: int = PRIORITY_TURN,
                timeout: Optional[float] = None) -> None:
        """阻塞获取一个并发额度。

        并发不足时按优先级排队等待，**绝不因「达不到目标并发」抛错**；仅当
        调用方显式传入 ``timeout`` 且等待超时才抛 ``TimeoutError``。
        """
        with self._cv:
            ticket = _Ticket(priority, self._seq)
            self._seq += 1
            self._waiters.add(ticket)
            deadline = None if timeout is None else time.monotonic() + timeout
            try:
                while not ticket.granted:
                    self._pump_locked()
                    if ticket.granted:
                        return
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("等待 API 并发额度超时（显式 timeout）")
                        self._cv.wait(min(_WAIT_TICK, max(0.005, remaining)))
                    else:
                        self._cv.wait(_WAIT_TICK)
            except BaseException:
                self._waiters.discard(ticket)
                raise

    def release(self, success: bool = True, rate_limited: bool = False) -> None:
        with self._cv:
            self._inflight = max(0, self._inflight - 1)
            if rate_limited:
                new_limit = max(1, self._limit // 2)
                if new_limit != self._limit:
                    self._record("backoff", self._limit, new_limit)
                self._limit = new_limit
                self._cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
                self._successes = 0
            elif success:
                self._successes += 1
                if self._limit < self._target and self._successes >= _INCREASE_EVERY:
                    self._successes = 0
                    self._record("increase", self._limit, self._limit + 1)
                    self._limit += 1
            self._cv.notify_all()

    def _pump_locked(self) -> None:
        """冷却到期恢复额度 + 把空闲额度授予优先级最高的等待者。"""
        now = time.monotonic()
        if self._cooldown_until and now >= self._cooldown_until:
            self._cooldown_until = 0.0
            if self._limit < self._target:
                self._record("recover", self._limit, self._limit + 1)
                self._limit += 1
        while self._inflight < self._limit and self._waiters:
            nxt = min(self._waiters, key=_Ticket.key)
            self._waiters.discard(nxt)
            nxt.granted = True
            self._inflight += 1

    def slot(self, priority: int = PRIORITY_TURN,
             timeout: Optional[float] = None) -> "_SlotContext":
        """实例级便捷入口：``with controller.slot(): ...``。"""
        return _SlotContext(self, priority, timeout)

    # ---- 观测 --------------------------------------------------------------
    def stats(self) -> dict:
        with self._cv:
            return {
                "target": self._target,
                "limit": self._limit,
                "inflight": self._inflight,
                "waiting": len(self._waiters),
                "cooldown_remaining": round(
                    max(0.0, self._cooldown_until - time.monotonic()), 1),
                "events": [dict(item) for item in self._events],
            }

    def _record(self, kind: str, old: int, new: int) -> None:
        self._events.append(
            {"kind": kind, "from": old, "to": new, "at": round(time.time(), 3)})


class _SlotContext:
    """``with slot(priority=...) as token:`` —— token 可标记限流/失败。"""

    def __init__(self, controller: ConcurrencyController, priority: int,
                 timeout: Optional[float]) -> None:
        self._controller = controller
        self._priority = priority
        self._timeout = timeout
        self.token = SlotToken()

    def __enter__(self) -> SlotToken:
        self._controller.acquire(self._priority, self._timeout)
        return self.token

    def __exit__(self, exc_type, exc, tb) -> bool:
        rate_limited = self.token.rate_limited
        success = self.token.success and not rate_limited and exc is None
        self._controller.release(success=success, rate_limited=rate_limited)
        return False


def slot(priority: int = PRIORITY_TURN, timeout: Optional[float] = None,
         controller: Optional[ConcurrencyController] = None) -> "_SlotContext":
    """获取一次模型调用的并发额度（上下文管理器；默认全局控制器）。"""
    return (controller or api_controller).slot(priority, timeout)


def _default_target_from_env() -> int:
    raw = str(os.environ.get("FATE_API_CONCURRENCY") or "").strip()
    if not raw:
        return _DEFAULT_TARGET
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_TARGET


#: 进程级共享控制器单例：开局蒸馏、回合波次、后台蒸馏统一从这里取额度。
api_controller = ConcurrencyController(_default_target_from_env())


# ---- 线程级优先级与模型包装 -------------------------------------------------

_priority_local = threading.local()


@contextlib.contextmanager
def priority_scope(priority: int):
    """临时切换当前线程的默认优先级（如开局同步蒸馏提到 PRIORITY_OPENING）。"""
    previous = getattr(_priority_local, "priority", None)
    _priority_local.priority = priority
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_priority_local, "priority")
            except AttributeError:
                pass
        else:
            _priority_local.priority = previous


def current_priority(default: int = PRIORITY_TURN) -> int:
    return getattr(_priority_local, "priority", default)


def budget_model(fn: Callable[[str], Any],
                 default_priority: int = PRIORITY_BACKGROUND) -> Callable[[str], Any]:
    """把单 prompt 模型调用包进并发额度；重复包装直接返回原函数。

    优先级取当前线程的 :func:`priority_scope`，缺省 ``default_priority``
    （后台蒸馏等异步通道应传 ``PRIORITY_BACKGROUND``）。限流类异常会在
    释放额度时触发 AIMD 退避；异常本身原样上抛，由调用方兜底。
    """
    if getattr(fn, "_budgeted", False):
        return fn

    @functools.wraps(fn)
    def wrapped(prompt: str) -> Any:
        with slot(priority=current_priority(default_priority)) as token:
            try:
                return fn(prompt)
            except BaseException as exc:
                token.success = False
                if is_rate_limit_error(exc):
                    token.rate_limited = True
                raise

    wrapped._budgeted = True  # type: ignore[attr-defined]
    return wrapped


# ---- 并行作业执行 ------------------------------------------------------------

class JobResult:
    """单个并行作业的结果：``value`` 或 ``error`` 二者其一；绝不整体抛错。"""

    __slots__ = ("value", "error")

    def __init__(self, value: Any = None, error: Optional[BaseException] = None) -> None:
        self.value = value
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None

    def __repr__(self) -> str:  # pragma: no cover 调试辅助
        if self.ok:
            return f"JobResult(ok, {self.value!r:.80})"
        return f"JobResult(error, {self.error!r})"


_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=HARD_LIMIT, thread_name_prefix="api-parallel")
        return _executor


def run_parallel(jobs: Sequence[Callable[[], Any]],
                 priority: Optional[int] = None) -> List[JobResult]:
    """并行执行若干零参作业，按提交顺序返回 :class:`JobResult` 列表。

    线程池只提供执行载体；**并发额度由各作业内部自行获取**（作业体内的
    模型调用应经 :func:`budget_model` 或 :func:`slot`）。单作业异常被捕获
    进对应 ``JobResult.error``，本函数绝不整体抛错。
    """
    futures = [_pool().submit(job) for job in jobs]
    results: List[JobResult] = []
    for future in futures:
        try:
            results.append(JobResult(value=future.result()))
        except BaseException as exc:  # noqa: BLE001 单作业失败不拖垮整批
            results.append(JobResult(error=exc))
    return results


def shutdown_pool() -> None:
    """进程收尾时关闭共享线程池（测试隔离用）。"""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
            _executor = None


__all__ = [
    "HARD_LIMIT", "PRIORITY_OPENING", "PRIORITY_TURN", "PRIORITY_BACKGROUND",
    "ConcurrencyController", "SlotToken", "api_controller",
    "is_rate_limit_error", "slot", "priority_scope", "current_priority",
    "budget_model", "JobResult", "run_parallel", "shutdown_pool",
]
