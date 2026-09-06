"""Immutable, hashable story snapshot for agent planning."""
from __future__ import annotations
import hashlib, json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

def _json(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"), default=str)
@dataclass(frozen=True)
class StorySnapshot:
    round:int; chapter:int; chapter_round:int; state_hash:str; facts:dict[str,Any]; recent_turns:tuple

def build_snapshot(state: Mapping[str,Any]) -> StorySnapshot:
    payload=deepcopy(dict(state or {})); payload.pop('history',None); payload.pop('request_kwargs',None)
    digest=hashlib.sha256(_json(payload).encode()).hexdigest()
    ledger=state.get('story_ledger') if isinstance(state.get('story_ledger'),list) else []
    facts={k:deepcopy(state.get(k)) for k in ('state_memory','ledger','anchor_timeline','quest','break_anchor','active_members','options')}
    return StorySnapshot(int(state.get('round') or 0),int(state.get('current_chapter') or 1),int(state.get('chapter_round') or 0),digest,facts,tuple(deepcopy(ledger[-8:])))
