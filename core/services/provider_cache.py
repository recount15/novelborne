"""Provider-neutral prompt cache metadata and stable prefix construction."""
from __future__ import annotations
import hashlib, json
from typing import Any, Mapping

def cache_key(provider:str, model:str, layers:Mapping[str,Any], *, version:str='1')->str:
    stable={k:layers.get(k,'') for k in ('system_rules','world_facts','character_models','output_contract')}
    raw=json.dumps({'provider':provider,'model':model,'version':version,'layers':stable},ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode()).hexdigest()

def build_prefix(layers:Mapping[str,Any])->str:
    return '\n\n'.join(str(layers.get(k) or '').strip() for k in ('system_rules','world_facts','character_models','output_contract') if str(layers.get(k) or '').strip())

def metadata(provider:str, model:str, key:str, hit:bool=False)->dict[str,Any]:
    return {'provider':provider,'model':model,'cache_key_hash':key,'cache_hit':bool(hit)}
