"""Evidence-constrained identity decisions and cutoff-only character extraction.

Model judgments are auditable hypotheses, not a proof of semantic truth. Missing
or contradictory pair decisions leave identities unresolved and block readiness.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from core.engine.anchor_distiller import _parse_model_output


def validate_identity_report(entities: list[dict], report: Mapping[str, Any]) -> dict:
    from core.services.book_prepare_service import _hash
    mentions = {e['mention_id']: e for e in entities if e['kind'] == 'character'}
    ids = sorted(mentions)
    required = {(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]}
    decisions = {}
    conflicts = []
    for raw in report.get('decisions', []):
        a, b = sorted([str(raw.get('left', '')), str(raw.get('right', ''))])
        if (a, b) not in required or (a, b) in decisions:
            raise ValueError('invalid or duplicate identity pair')
        relation = raw.get('relation')
        if relation not in {'same', 'different', 'uncertain'}:
            raise ValueError('invalid identity relation')
        evidence = raw.get('evidence')
        if not isinstance(evidence, dict) or set(evidence) != {a, b}:
            raise ValueError('identity decision requires evidence from both mentions')
        for key in (a, b):
            quote = evidence[key]
            if not isinstance(quote, str) or not quote or quote not in mentions[key]['excerpt']:
                raise ValueError('identity evidence is not exact source')
        if not str(raw.get('reason') or '').strip():
            raise ValueError('identity decision requires rationale')
        decisions[a, b] = {'left': a, 'right': b, 'relation': relation,
                            'evidence': evidence, 'reason': raw['reason']}
    parent = {key: key for key in ids}
    def find(key):
        while parent[key] != key:
            key = parent[key]
        return key
    for (a, b), decision in decisions.items():
        if decision['relation'] == 'same':
            parent[find(b)] = find(a)
    for a, b in sorted(required):
        decision = decisions.get((a, b))
        if decision is None or decision['relation'] == 'uncertain':
            conflicts.append({'left': a, 'right': b, 'reason': 'identity unresolved'})
        elif decision['relation'] == 'different' and find(a) == find(b):
            conflicts.append({'left': a, 'right': b, 'reason': 'contradictory identity cycle'})
    conflicted = {find(c[k]) for c in conflicts for k in ('left', 'right')}
    groups = {}
    for key in ids:
        groups.setdefault(find(key), []).append(key)
    identities = []
    for root, members in sorted(groups.items()):
        identities.append({'identity_id': 'person_' + _hash(members)[:24], 'mention_ids': members,
                           'names': sorted({mentions[m]['name'] for m in members}),
                           'ready': root not in conflicted})
    return {'version': 1, 'decisions': [decisions[k] for k in sorted(decisions)],
            'identities': identities, 'conflicts': conflicts,
            'identity_ready': not conflicts,
            'verification': 'model-judged identity with exact evidence, not semantic proof'}


def resolve_character_identities(entities: list[dict], model: Any = None, *, cached: Mapping | None = None) -> dict:
    """Compare all mention pairs, including aliases; never merge by name alone.

    Each request is bounded to two excerpts. Valid cached decisions are reused;
    unresolved decisions are retried, and failures become explicit conflicts.
    """
    mentions = sorted((e for e in entities if e['kind'] == 'character'), key=lambda e: e['mention_id'])
    known = {}
    if cached:
        try:
            checked = validate_identity_report(entities, cached)
            known = {(d['left'], d['right']): d for d in checked['decisions'] if d['relation'] != 'uncertain'}
        except (ValueError, TypeError, KeyError):
            pass
    decisions = []
    for i, left in enumerate(mentions):
        for right in mentions[i + 1:]:
            key = (left['mention_id'], right['mention_id'])
            decision = known.get(key)
            if decision is None and callable(model):
                try:
                    raw = _parse_model_output(model('IDENTITY_PAIR_V1\n判断两个原文人物提及是same/different/uncertain。'
                        '不能仅因同名判same；无充分证据必须uncertain，别名也需证据。'
                        '返回{left:提及ID,right:提及ID,relation,reason,evidence:{提及ID:逐字引文}}。\n'
                        + json.dumps({'left': left, 'right': right}, ensure_ascii=False)))
                    checked = validate_identity_report([left, right], {'decisions': [raw]})
                    decision = checked['decisions'][0]
                except Exception:
                    decision = None
            if decision:
                decisions.append(decision)
    return validate_identity_report(entities, {'decisions': decisions})


def extract_scoped_character_facts(model: Any, source: str, identities: list[dict]) -> list[dict]:
    """Reuse opening character extraction; accept only facts with exact evidence.

    Only caller-provided cutoff text enters either model call. Rich card output
    is advisory and never directly becomes an initial body/persona/state fact.
    """
    if not callable(model) or not source.strip() or not identities:
        return []
    from core.engine.opening_distill import _characters_extract_job
    cards = _characters_extract_job(model, 'cutoff-scoped opening', source)['cards']
    data = _parse_model_output(model('SCOPED_CHARACTER_FACTS_V1\n从原文提取当前人物初始事实，'
        '只允许提供的identity_id，不能推测未发生的事情。返回{facts:[{identity_id,field,value,excerpt,start,end}]}。'
        'value必须是excerpt中的逐字片段，start/end是原文字符偏移且原文[start:end]==excerpt。'
        'field只允许location/action/condition/appearance/relationship。无证据不填。\n'
        + json.dumps({'identities': identities, 'advisory_cards': cards, 'source': source}, ensure_ascii=False)))
    ids = {i['identity_id'] for i in identities}
    facts = []
    for fact in data.get('facts', []):
        if fact.get('identity_id') not in ids or fact.get('field') not in {'location', 'action', 'condition', 'appearance', 'relationship'}:
            raise ValueError('unknown character or initial fact field')
        start, end = fact.get('start'), fact.get('end')
        quote, value = fact.get('excerpt'), fact.get('value')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
            raise ValueError('invalid initial fact evidence offsets')
        if not isinstance(quote, str) or source[start:end] != quote or not isinstance(value, str) or not value or value not in quote:
            raise ValueError('initial fact is not exact evidence')
        facts.append({k: fact[k] for k in ('identity_id', 'field', 'value', 'excerpt', 'start', 'end')})
    return facts
