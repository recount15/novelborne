"""Exact-source scene candidates and conservative temporal evidence cutoffs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.engine.book_index import search_index
from core.services.book_prepare_service import _read_json, _sources, _hash, verify_preparation

TIMEPOINTS = ('before', 'during', 'after')

#: 证据展示上限：excerpt 本体保持原文精确子串（select_scene 据此校验），
#: 超长证据以 excerpt_display 显式截断并附 excerpt_total 总数，前端不得
#: 自行猜测截断规则。
EXCERPT_DISPLAY_LIMIT = 160


def _bounded_evidence(excerpt: str) -> dict[str, Any]:
    """Presentation projection for an evidence excerpt: explicit cap + total."""
    text = str(excerpt or '')
    overflow = len(text) > EXCERPT_DISPLAY_LIMIT
    return {'excerpt_display': text[:EXCERPT_DISPLAY_LIMIT] + ('…' if overflow else ''),
            'excerpt_total': len(text)}


def locate_scene(book_dir: str | Path, query: str, *, limit: int = 10,
                 model: Any = None) -> list[dict[str, Any]]:
    root = Path(book_dir)
    index = _read_json(root / 'book_index.json')
    sources, blocks = _sources(root, index)
    source_hash = _hash(sources)
    query = str(query or '').strip()
    if not query:
        return []
    if callable(model):
        return _semantic_candidates(root, blocks, source_hash, query, model, limit)
    ranked = {x['id']: x['score'] for x in search_index(index, query, limit=len(blocks))}
    candidates = []
    for block in blocks:
        text = block['text']
        exact = text.find(query)
        if exact < 0 and block['block_id'] not in ranked:
            continue
        # Candidates are evidence, not model-generated scene claims. An exact
        # query gets precise coordinates; keyword retrieval shows the full block.
        excerpt = query if exact >= 0 else text
        start = block['start'] + (exact if exact >= 0 else 0)
        end = start + len(excerpt)
        identity = {'chapter_no': block['chapter_no'], 'block_id': block['block_id'],
                    'start': start, 'end': end, 'excerpt': excerpt, 'source_hash': source_hash}
        candidates.append({**identity, 'id': _hash(identity), 'timepoints': list(TIMEPOINTS),
                           'score': 100000 + len(query) if exact >= 0 else ranked[block['block_id']],
                           'match': 'exact' if exact >= 0 else 'keywords',
                           **_bounded_evidence(excerpt)})
    exact_count = sum(c['match'] == 'exact' for c in candidates)
    for candidate in candidates:
        candidate['ambiguous'] = exact_count != 1 if candidate['match'] == 'exact' else True
        candidate['requires_confirmation'] = True
        candidate['semantic_verified'] = False
    return sorted(candidates, key=lambda c: (-c['score'], c['chapter_no'], c['start']))[:max(0, min(int(limit), 100))]


def select_scene(book_dir: str | Path, candidate: Mapping[str, Any], timepoint: str = 'during') -> dict[str, Any]:
    if timepoint not in TIMEPOINTS:
        raise ValueError('timepoint must be before, during or after')
    root = Path(book_dir)
    sources, blocks = _sources(root, _read_json(root / 'book_index.json'))
    source_hash = _hash(sources)
    required = ('chapter_no', 'block_id', 'start', 'end', 'excerpt', 'source_hash')
    missing = [key for key in required if candidate.get(key) in (None, '')]
    if missing:
        # 前端必须回传 locate 返回的完整候选对象；只回传坐标子集属于契约违规，
        # 显式指出缺失字段而不是误报证据过期。
        raise ValueError('候选证据身份不完整，缺少字段：' + '、'.join(missing))
    if candidate.get('source_hash') != source_hash:
        raise ValueError('scene evidence is stale')
    block = next((b for b in blocks if b['block_id'] == candidate.get('block_id')), None)
    if not block or candidate.get('chapter_no') != block['chapter_no']:
        raise ValueError('scene block/chapter mismatch')
    start, end = int(candidate['start']), int(candidate['end'])
    excerpt = candidate.get('excerpt')
    if not isinstance(excerpt, str) or not excerpt or not block['start'] <= start < end <= block['end']:
        raise ValueError('invalid scene span')
    if block['text'][start - block['start']:end - block['start']] != excerpt:
        raise ValueError('scene excerpt does not match source')
    identity = {k: candidate[k] for k in ('chapter_no', 'block_id', 'start', 'end', 'excerpt', 'source_hash')}
    during_offset = start
    semantic = candidate.get('match') == 'semantic'
    if semantic:
        stored = _read_json(root / 'preparation' / 'scenes' / (str(candidate.get('id', '')) + '.json'))
        if stored != dict(candidate):
            raise ValueError('semantic candidate is not a server-validated artifact')
        during_offset = int(candidate['during_offset'])
        if not start < during_offset < end:
            raise ValueError('semantic scene requires a distinct in-progress boundary')
        identity['during_offset'] = during_offset
    if candidate.get('id') != _hash(identity):
        raise ValueError('scene candidate identity mismatch')
    cutoff = {'chapter_no': block['chapter_no'], 'offset':
              end if timepoint == 'after' else during_offset if timepoint == 'during' else start}
    preparation = verify_preparation(root)
    facts = []
    if preparation.get('ready'):
        for fact in preparation.get('entities', []):
            if fact['chapter_no'] < cutoff['chapter_no'] or (fact['chapter_no'] == cutoff['chapter_no'] and fact['end'] <= cutoff['offset']):
                facts.append(dict(fact))
    return {'id': candidate['id'], 'timepoint': timepoint, 'evidence': identity,
            'candidate': dict(candidate), 'temporal_precision': 'event_boundary' if semantic else 'conservative',
            'knowledge_cutoff': cutoff, 'initial_facts': facts,
            'initialization_policy': 'evidence-before-cutoff-only; no inferred future state'}


def _semantic_candidates(root, blocks, source_hash, query, model, limit):
    from core.engine.anchor_distiller import _parse_model_output
    from core.services.book_prepare_service import _atomic_json
    candidates, failures = [], []
    for block in blocks:
        try:
            prompt = ('SEMANTIC_SCENE_V1\n'
                      '依据完整块原文理解用户要找的事件，不局限字面匹配。'
                      '返回{scenes:[{start,end,during_offset,excerpt,reason}]}。不匹配返回空数组。'
                      '坐标是块内字符偏移，excerpt必须等于text[start:end]。'
                      'during_offset必须在事件已经开始、结果尚未发生的完整子事件边界，严格start<during_offset<end。'
                      '无法找到可靠中间边界则不返回候选。reason解释语义匹配，不能编造事件。\n'
                      + json.dumps({'query': query, 'text': block['text']}, ensure_ascii=False))
            data = _parse_model_output(model(prompt))
            if not isinstance(data.get('scenes'), list):
                raise ValueError('missing semantic scenes array')
            for raw in data['scenes']:
                start, end, during = (raw.get(k) for k in ('start', 'end', 'during_offset'))
                if any(type(x) is not int for x in (start, end, during)) or not 0 <= start < during < end <= len(block['text']):
                    raise ValueError('invalid semantic event boundary')
                if block['text'][start:end] != raw.get('excerpt') or not str(raw.get('reason') or '').strip():
                    raise ValueError('semantic evidence mismatch')
                identity = {'chapter_no': block['chapter_no'], 'block_id': block['block_id'],
                            'start': block['start'] + start, 'end': block['start'] + end,
                            'during_offset': block['start'] + during,
                            'excerpt': raw['excerpt'], 'source_hash': source_hash}
                candidates.append({**identity, 'id': _hash(identity), 'match': 'semantic',
                    'reason': raw['reason'], 'score': 1, 'timepoints': list(TIMEPOINTS),
                    'semantic_verified': False, 'evidence_verified': True,
                    'requires_confirmation': True, 'verification': 'model judgment with exact evidence',
                    **_bounded_evidence(raw['excerpt'])})
        except Exception as exc:
            failures.append({'block_id': block['block_id'], 'error': str(exc)[:200]})
    unique = {c['id']: c for c in candidates}
    result = sorted(unique.values(), key=lambda c: (c['chapter_no'], c['start']))[:max(0, min(int(limit), 100))]
    for candidate in result:
        candidate.update(ambiguous=len(unique) != 1 or bool(failures), search_complete=not failures,
                         search_errors=failures)
        _atomic_json(root / 'preparation' / 'scenes' / (candidate['id'] + '.json'), candidate)
    if failures and not result:
        raise ValueError('semantic locator failed without validated candidates: ' + str(failures))
    return result


def project_scene_initial_state(book_dir: str | Path, selection: Mapping[str, Any], *, model: Any = None) -> dict:
    """Revalidate selection and derive characters solely from the selected prefix.

    Global identity aliases and future evidence never enter the extraction prompt.
    Facts carry exact chapter-local coordinates; they are observations, not an
    assertion that every historical condition remains currently true.
    """
    from core.services.character_evidence_service import extract_scoped_character_facts
    root = Path(book_dir)
    selected = select_scene(root, selection['candidate'], selection['timepoint'])
    preparation = verify_preparation(root)
    if not preparation.get('ready'):
        raise ValueError('verified preparation required for scoped initialization')
    cutoff = selected['knowledge_cutoff']
    available = selected['initial_facts']
    by_id = {e['mention_id']: e for e in available if e['kind'] == 'character'}
    identities = []
    conflicts = []
    for identity in (preparation.get('identity_report') or {}).get('identities', []):
        mentions = [by_id[m] for m in identity['mention_ids'] if m in by_id]
        if not mentions:
            continue
        if not identity['ready']:
            conflicts.append(identity['identity_id'])
            continue
        identities.append({'identity_id': identity['identity_id'],
                           'names': sorted({m['name'] for m in mentions}),
                           'mentions': mentions, 'facts': []})
    # Never send unbounded full-book text or post-cutoff text to extraction.
    index = _read_json(root / 'book_index.json')
    _, blocks = _sources(root, index)
    for block in blocks:
        if block['chapter_no'] > cutoff['chapter_no']:
            continue
        length = len(block['text']) if block['chapter_no'] < cutoff['chapter_no'] else max(0, min(len(block['text']), cutoff['offset'] - block['start']))
        source = block['text'][:length]
        relevant = []
        for identity in identities:
            mentions = [m for m in identity['mentions'] if m['block_id'] == block['block_id'] and m['end'] <= block['start'] + length]
            if mentions:
                relevant.append({'identity_id': identity['identity_id'], 'names': sorted({m['name'] for m in mentions})})
        for fact in extract_scoped_character_facts(model, source, relevant):
            fact.update(chapter_no=block['chapter_no'], block_id=block['block_id'], source_hash=block['source_hash'],
                        start=block['start'] + fact['start'], end=block['start'] + fact['end'])
            next(i for i in identities if i['identity_id'] == fact['identity_id'])['facts'].append(fact)
    return {'selection_id': selected['id'], 'timepoint': selected['timepoint'],
            'knowledge_cutoff': cutoff, 'characters': identities, 'conflicts': conflicts,
            'identity_ready': not conflicts, 'facts_ready': callable(model) and not conflicts,
            'temporal_precision': selected['temporal_precision'],
            'policy': 'cutoff-only evidence observations; unknown fields remain absent'}
