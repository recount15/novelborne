"""Deterministic dynamic weights for historical events and generation factors."""
from __future__ import annotations
from math import exp
from typing import Any, Mapping

def event_weight(event: Mapping[str,Any], current_round:int, *, attention:float=0.0, causal:float=0.0) -> float:
    age=max(0,current_round-int(event.get('round') or current_round)); recency=exp(-age/12)
    unresolved=1.25 if str(event.get('status') or '') not in ('resolved','completed','cancelled') else .65
    evidence=min(1.0,len(event.get('evidence_refs') or event.get('evidence') or [])/3)
    return max(0.0,min(1.0,recency*unresolved*(.65+.25*causal+.1*evidence)+.2*max(0,min(1,attention))))

def factor_weight(factor: Mapping[str,Any], stage:str='turn') -> float:
    base=float(factor.get('base',factor.get('weight',.5)) or .5); hardness=str(factor.get('hardness') or 'soft')
    evidence=min(1.0,len(factor.get('evidence') or [])/3); conflict=max(0.0,min(1,float(factor.get('conflict_penalty',0) or 0)))
    stage_boost={'strategy':.1,'narrative':.05,'options':.08,'patch':.15,'polish':-.15}.get(stage,0)
    value=base*(.8+.2*evidence)*(1-conflict)+stage_boost
    if hardness=='hard': value=max(value,.7)
    return max(0.0,min(1.0,value))
