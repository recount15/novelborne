"""Hard deterministic narrative invariants."""
from __future__ import annotations
from typing import Any, Mapping

def validate_transition(before:Mapping[str,Any], after:Mapping[str,Any])->dict[str,Any]:
    errors=[]
    br=int(before.get('round') or 0); ar=int(after.get('round') or 0)
    if ar not in (br,br+1): errors.append({'code':'round_jump','before':br,'after':ar})
    if ar==br+1 and str(after.get('save_stage') or '') not in ('committed','streaming'): errors.append({'code':'stage_invalid'})
    return {'ok':not errors,'errors':errors}

def validate_options(options)->dict[str,Any]:
    keys=[str(x.get('key')) for x in options or () if isinstance(x,Mapping)]
    return {'ok':keys==list('ABCDEF'),'errors':[] if keys==list('ABCDEF') else [{'code':'options_shape'}]}

def validate_narrative(narrative:str, *, required_terms=(), forbidden_terms=()):
    text=str(narrative or '')
    missing=[str(x) for x in required_terms if str(x) and str(x) not in text]
    forbidden=[str(x) for x in forbidden_terms if str(x) and str(x) in text]
    return {'ok':not missing and not forbidden,'errors':([{'code':'missing_evidence','term':x} for x in missing]+[{'code':'forbidden_event','term':x} for x in forbidden])}

__all__=['validate_transition','validate_options','validate_narrative']
