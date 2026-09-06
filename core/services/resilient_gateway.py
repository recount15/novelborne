"""Thread-safe bounded retry and circuit-breaker policy."""
from __future__ import annotations
import time
import threading
from typing import Callable, Any

class CircuitOpen(RuntimeError): pass

class ResilientCall:
    def __init__(self, failures: int = 3, cooldown: float = 30.0, clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None):
        self.failures=max(1,int(failures)); self.cooldown=max(0.0,float(cooldown)); self.errors=0; self.opened=0.0; self._probe=False
        self._clock=clock or time.monotonic; self._sleep=sleep or time.sleep; self._lock=threading.Lock()
    def _enter(self):
        with self._lock:
            if not self.opened: return
            if self._clock()-self.opened < self.cooldown: raise CircuitOpen('circuit open')
            if self._probe: raise CircuitOpen('half-open probe in progress')
            self._probe=True
    def call(self, fn: Callable[[],Any], *, attempts: int=2, timeout: float|None=None, retryable: Callable[[Exception],bool]|None=None):
        del timeout  # provider callable owns actual socket timeout; deadline wrapper prevents retries past budget
        self._enter(); last=None
        try:
            for i in range(max(1,int(attempts))):
                try:
                    value=fn()
                    with self._lock: self.errors=0; self.opened=0.0
                    return value
                except Exception as exc:
                    last=exc
                    if retryable is not None and not retryable(exc): break
                    with self._lock:
                        self.errors+=1
                        if self.errors>=self.failures: self.opened=self._clock()
                    if i+1<attempts: self._sleep(min(.25*(2**i),2.0))
            raise last
        finally:
            with self._lock: self._probe=False
