"""Unified objective records for wishes, quests, anchors and character goals."""
from __future__ import annotations
from typing import Any, Mapping

def normalize(row: Mapping[str,Any], kind: str='objective') -> dict[str,Any]:
    return {'objective_id':str(row.get('objective_id') or row.get('id') or ''),'kind':str(row.get('kind') or kind),'fact':str(row.get('fact') or row.get('goal') or row.get('title') or ''),'status':str(row.get('status') or 'active'),'activated_round':int(row.get('activated_round') or 0),'required_evidence':list(row.get('required_evidence') or row.get('keywords') or []),'links':list(row.get('links') or [])}

def active(state: Mapping[str,Any]) -> list[dict[str,Any]]:
    rows=[]
    for item in (state.get('objectives') or []):
        if isinstance(item,Mapping) and normalize(item).get('status') not in ('resolved','cancelled','completed'): rows.append(normalize(item))
    quest=state.get('quest')
    if isinstance(quest,Mapping) and quest.get('status') in ('active','pending'): rows.append(normalize(quest,'quest'))
    return rows
