"""Durable operation lifecycle journal for API consumers.

The journal is intentionally small and dependency-free.  It stores redacted JSONL
records, gives every event a monotonic sequence, and supports idempotent client
requests and reconnect/replay.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import StreamEvent, redact_secrets

_TERMINAL = {"done", "error", "cancel"}


@dataclass(frozen=True)
class Operation:
    operation_id: str
    client_request_id: str | None
    status: str
    seq: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "client_request_id": self.client_request_id,
            "status": self.status,
            "seq": self.seq,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class OperationJournal:
    """Thread-safe append-only operation journal with optional JSONL persistence."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._events: dict[str, list[StreamEvent]] = {}
        self._operations: dict[str, Operation] = {}
        self._requests: dict[str, str] = {}
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                event = StreamEvent.from_dict(item) if hasattr(StreamEvent, "from_dict") else StreamEvent(
                    item["type"], item.get("data", {}), item.get("operation_id"), item.get("seq"), item.get("client_request_id")
                )
                self._events.setdefault(event.operation_id or "", []).append(event)
                if event.operation_id:
                    self._apply_loaded(event)
        except (OSError, ValueError, TypeError, KeyError):
            # A damaged tail must not make the API unusable; valid prior records remain.
            return

    def _apply_loaded(self, event: StreamEvent) -> None:
        oid = event.operation_id
        assert oid is not None
        data = event.data
        old = self._operations.get(oid)
        created = old.created_at if old else float(data.get("created_at", event.data.get("at", 0.0)))
        client = event.client_request_id or (old.client_request_id if old else data.get("client_request_id"))
        self._operations[oid] = Operation(oid, client, event.type, event.seq or 0, created, float(data.get("at", time.time())))
        if client:
            self._requests[str(client)] = oid

    def start(self, client_request_id: str | None = None, *, operation_id: str | None = None,
              data: Mapping[str, Any] | None = None) -> Operation:
        """Create or return an operation; the client request id is idempotency key."""
        with self._lock:
            if client_request_id is not None and client_request_id in self._requests:
                return self._operations[self._requests[client_request_id]]
            oid = operation_id or uuid.uuid4().hex
            now = time.time()
            op = Operation(oid, client_request_id, "ack", 0, now, now)
            self._operations[oid] = op
            if client_request_id is not None:
                self._requests[client_request_id] = oid
            self._append_locked(StreamEvent("ack", dict(data or {}), oid, 1, client_request_id))
            return self._operations[oid]

    create = start

    def append(self, operation_id: str, event_type: str, data: Mapping[str, Any] | Any = None) -> StreamEvent:
        """Append one lifecycle event. Supported types: ack, progress, heartbeat,
        checkpoint, done, error, and cancel."""
        event_type = str(event_type).lower()
        if event_type not in {"ack", "progress", "heartbeat", "checkpoint", "done", "error", "cancel"}:
            raise ValueError(f"unsupported operation event: {event_type}")
        with self._lock:
            if operation_id not in self._operations:
                raise KeyError(operation_id)
            op = self._operations[operation_id]
            if op.status in _TERMINAL:
                return self._events[operation_id][-1]
            event = StreamEvent(event_type, data if isinstance(data, Mapping) else {"value": data}, operation_id, op.seq + 1, op.client_request_id)
            self._append_locked(event)
            return event

    emit = append

    def ack(self, operation_id: str, data: Mapping[str, Any] | Any = None) -> StreamEvent:
        return self.append(operation_id, "ack", data)

    def progress(self, operation_id: str, data: Mapping[str, Any] | Any = None) -> StreamEvent:
        return self.append(operation_id, "progress", data)

    def heartbeat(self, operation_id: str, data: Mapping[str, Any] | Any = None) -> StreamEvent:
        return self.append(operation_id, "heartbeat", data)

    def checkpoint(self, operation_id: str, data: Mapping[str, Any] | Any = None) -> StreamEvent:
        return self.append(operation_id, "checkpoint", data)

    def done(self, operation_id: str, data: Mapping[str, Any] | Any = None) -> StreamEvent:
        return self.append(operation_id, "done", data)

    def error(self, operation_id: str, data: Mapping[str, Any] | Any = None) -> StreamEvent:
        return self.append(operation_id, "error", data)

    def _append_locked(self, event: StreamEvent) -> None:
        clean = redact_secrets(event.data)
        event = StreamEvent(event.type, clean if isinstance(clean, dict) else {"value": clean}, event.operation_id, event.seq, event.client_request_id)
        self._events.setdefault(event.operation_id or "", []).append(event)
        self._apply_loaded(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")

    def replay(self, operation_id: str, after_seq: int = 0) -> list[StreamEvent]:
        with self._lock:
            return [e for e in self._events.get(operation_id, ()) if (e.seq or 0) > after_seq]

    def status(self, operation_id: str) -> Operation:
        with self._lock:
            return self._operations[operation_id]

    def cancel(self, operation_id: str, reason: str | None = None) -> StreamEvent:
        return self.append(operation_id, "cancel", {"reason": reason} if reason else {})

    def events(self, operation_id: str) -> Iterable[StreamEvent]:
        return self.replay(operation_id)


__all__ = ["Operation", "OperationJournal"]
