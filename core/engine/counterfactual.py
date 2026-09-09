"""Pure temporary-state simulation for option branches."""
from __future__ import annotations
from copy import deepcopy
from typing import Any, Mapping

def simulate(state:Mapping[str,Any], patch:Mapping[str,Any]|None=None)->dict[str,Any]:
    result=deepcopy(dict(state or {})); result.update(deepcopy(dict(patch or {})))
    result['round']=int(state.get('round') or 0)+1
    return {'state':result,'round_delta':1,'safe':result['round']==int(state.get('round') or 0)+1}
