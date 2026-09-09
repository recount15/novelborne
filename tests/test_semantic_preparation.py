import json
from unittest.mock import patch

import pytest

from tests.test_preparation_evidence import book, model
from core.services.book_prepare_service import prepare_book, verify_preparation
from core.services.character_evidence_service import validate_identity_report
from core.services.scene_locator_service import locate_scene, select_scene, project_scene_initial_state


def semantic(prompt):
    data = json.loads(prompt.split('\n', 2)[2])
    text = data['text']
    if 'room 2' not in text:
        return {'scenes': []}
    return {'scenes': [{'start': 0, 'end': len(text), 'during_offset': text.index(' Bob'),
                        'excerpt': text, 'reason': 'entering precedes leaving'}]}


def test_uncertain_identity_blocks_fullbook_ready(book):
    def uncertain(prompt):
        result = model(prompt)
        if prompt.startswith('IDENTITY_PAIR_V1'):
            result['relation'] = 'uncertain'
        return result
    result = prepare_book(book, mode='fullbook', model=uncertain)
    assert result['coverage_ready'] and not result['identity_ready'] and not result['ready']
    assert result['identity_report']['conflicts']
    checked = verify_preparation(book)
    assert checked['coverage_ready'] and not checked['ready']
    assert prepare_book(book, mode='fullbook', model=model)['ready']


def test_identity_same_name_separation_and_contradiction(book):
    package = prepare_book(book, mode='fullbook', model=model)
    report = package['identity_report']
    assert len(report['identities']) == 1
    decisions = report['decisions']
    separated = [{**d, 'relation': 'different'} for d in decisions]
    checked = validate_identity_report(package['entities'], {'decisions': separated})
    assert checked['identity_ready'] and len(checked['identities']) == 3
    bad = [dict(d) for d in decisions]
    bad[-1]['relation'] = 'different'
    assert not validate_identity_report(package['entities'], {'decisions': bad})['identity_ready']
    bad[0]['evidence'] = {k: 'made up' for k in bad[0]['evidence']}
    with pytest.raises(ValueError):
        validate_identity_report(package['entities'], {'decisions': bad})


def test_semantic_query_and_distinct_timepoints(book):
    prepare_book(book, mode='fullbook', model=model)
    candidate = locate_scene(book, 'arrival before departure', model=semantic)[0]
    assert candidate['match'] == 'semantic' and candidate['evidence_verified']
    assert not candidate['semantic_verified']
    before, during, after = [select_scene(book, candidate, t) for t in ('before', 'during', 'after')]
    offsets = [s['knowledge_cutoff']['offset'] for s in (before, during, after)]
    assert offsets[0] < offsets[1] < offsets[2]
    assert [f['chapter_no'] for f in before['initial_facts']] == [1]
    assert [f['chapter_no'] for f in during['initial_facts']] == [1, 2]
    with pytest.raises(ValueError):
        select_scene(book, {**candidate, 'during_offset': candidate['end'] - 1})


def test_projection_reuses_extractor_without_future_text(book):
    prepare_book(book, mode='fullbook', model=model)
    candidate = locate_scene(book, 'arrival', model=semantic)[0]
    selected = select_scene(book, candidate, 'during')
    seen = []
    def facts(prompt):
        data = json.loads(prompt.split('\n', 2)[2])
        seen.append(data['source'])
        return {'facts': [{'identity_id': data['identities'][0]['identity_id'], 'field': 'action',
                           'value': 'enters', 'excerpt': data['source'], 'start': 0, 'end': len(data['source'])}]}
    with patch('core.engine.opening_distill._characters_extract_job', return_value={'cards': [{'name': 'Alice'}]}) as reused:
        projected = project_scene_initial_state(book, selected, model=facts)
    assert reused.call_count == 2
    assert all('room 3' not in text for text in seen)
    assert 'Bob leaves room 2' not in seen[-1]
    assert projected['facts_ready'] and len(projected['characters']) == 1
    assert len(projected['characters'][0]['facts']) == 2
    assert projected['characters'][0]['facts'][-1]['end'] == selected['knowledge_cutoff']['offset']


def test_forged_semantic_quote_is_rejected(book):
    prepare_book(book)
    def forged(prompt):
        return {'scenes': [{'start': 0, 'end': 20, 'during_offset': 5, 'excerpt': 'not the source', 'reason': 'fake'}]}
    with pytest.raises(ValueError):
        locate_scene(book, 'event', model=forged)
