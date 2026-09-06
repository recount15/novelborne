"""Small deterministic turn transaction state machine."""
from __future__ import annotations
from copy import deepcopy
class TurnTransaction:
    STATES=('OPEN','PLANNED','GENERATED','VALIDATED','COMMITTED','FAILED','ROLLED_BACK')
    def __init__(self,state): self.before=deepcopy(dict(state or {})); self.candidate=deepcopy(self.before); self.status='OPEN'
    def advance(self,status):
        allowed={'OPEN':('PLANNED','FAILED'),'PLANNED':('GENERATED','FAILED'),'GENERATED':('VALIDATED','FAILED'),'VALIDATED':('COMMITTED','FAILED'),'FAILED':('ROLLED_BACK',)}
        if status not in allowed.get(self.status,()): raise ValueError(f'invalid transition {self.status}->{status}')
        self.status=status
    def apply(self,patch):
        if self.status not in ('OPEN','PLANNED','GENERATED'): raise ValueError('transaction not mutable')
        self.candidate.update(deepcopy(dict(patch or {})))
    def rollback(self): self.candidate=deepcopy(self.before); self.status='ROLLED_BACK'
