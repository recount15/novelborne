from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from core.api.contracts import StreamEvent
from core.api.operations import OperationJournal


class TestStreamEventCompatibility(unittest.TestCase):
    def test_legacy_and_extended_shapes(self):
        legacy = StreamEvent.from_dict({"type": "message", "data": {"content": "x"}})
        self.assertEqual(legacy.to_dict(), {"type": "message", "data": {"content": "x"}})
        event = StreamEvent("progress", {"percent": 25}, "op", 2, "req")
        self.assertEqual(event.to_dict()["seq"], 2)


class TestOperationJournal(unittest.TestCase):
    def test_lifecycle_sequence_replay_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OperationJournal(Path(tmp) / "ops.jsonl")
            op = journal.start("request-1", data={"api_key": "secret", "x": 1})
            self.assertEqual(op.status, "ack")
            journal.append(op.operation_id, "progress", {"step": 1, "access_token": "secret"})
            journal.append(op.operation_id, "heartbeat", {"alive": True})
            journal.append(op.operation_id, "checkpoint", {"chapter": 2})
            done = journal.append(op.operation_id, "done", {"result": "ok"})
            self.assertEqual([e.seq for e in journal.replay(op.operation_id)], [1, 2, 3, 4, 5])
            self.assertEqual([e.seq for e in journal.replay(op.operation_id, after_seq=3)], [4, 5])
            self.assertNotIn("access_token", journal.replay(op.operation_id)[1].data)
            self.assertEqual(journal.status(op.operation_id).status, "done")
            self.assertIs(journal.start("request-1"), journal.status(op.operation_id))
            self.assertEqual(journal.append(op.operation_id, "progress").seq, done.seq)
            restored = OperationJournal(Path(tmp) / "ops.jsonl")
            self.assertEqual(restored.status(op.operation_id).status, "done")
            self.assertEqual(len(restored.replay(op.operation_id)), 5)

    def test_cancel_and_unknown_event(self):
        journal = OperationJournal()
        op = journal.create(client_request_id="r")
        self.assertEqual(journal.cancel(op.operation_id).type, "cancel")
        with self.assertRaises(ValueError):
            journal.append(op.operation_id, "bogus")


if __name__ == "__main__":
    unittest.main()
