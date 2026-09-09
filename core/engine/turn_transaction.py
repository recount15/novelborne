"""Detached turn candidates with SQLite as the only commit authority."""
from __future__ import annotations
from copy import deepcopy


class TurnTransaction:
    STATES = ('OPEN', 'PLANNED', 'GENERATED', 'VALIDATED', 'COMMITTED', 'FAILED', 'ROLLED_BACK')

    def __init__(self, state):
        self.before = deepcopy(dict(state or {}))
        self.candidate = deepcopy(self.before)
        self.status = 'OPEN'

    def advance(self, status):
        allowed = {'OPEN': ('PLANNED', 'FAILED'), 'PLANNED': ('GENERATED', 'FAILED'),
                   'GENERATED': ('VALIDATED', 'FAILED'), 'VALIDATED': ('FAILED',),
                   'FAILED': ('ROLLED_BACK',)}
        if status not in allowed.get(self.status, ()):
            raise ValueError(f'invalid transition {self.status}->{status}; use commit for durable writes')
        self.status = status

    def apply(self, patch):
        if self.status not in ('OPEN', 'PLANNED', 'GENERATED'):
            raise ValueError('transaction not mutable')
        self.candidate.update(deepcopy(dict(patch or {})))

    def commit(self, *, root, session_id, request_id, save_id='latest', target=None):
        from core.engine.persistence import save_state
        if self.status != 'VALIDATED':
            raise ValueError('only validated candidates can commit')
        if not session_id or not request_id:
            raise ValueError('session and request IDs required')
        try:
            path = save_state(self.candidate, root=root, session_id=session_id,
                              save_id=save_id, request_id=request_id,
                              expected_revision=int(self.before.get('revision') or 0))
        except Exception:
            self.status = 'FAILED'
            raise
        self.status = 'COMMITTED'
        if target is not None:
            target.clear()
            target.update(deepcopy(self.candidate))
        return path

    def rollback(self):
        if self.status == 'COMMITTED':
            raise ValueError('durable commits cannot be rolled back in memory')
        self.candidate = deepcopy(self.before)
        self.status = 'ROLLED_BACK'
