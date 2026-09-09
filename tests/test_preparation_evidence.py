import json
from pathlib import Path

import pytest

from core.services.book_prepare_service import prepare_book, verify_preparation
from core.services.scene_locator_service import locate_scene, select_scene
from core.services.book_library_service import list_playable_books, mark_book_played
from core.engine.opening_distill import run_opening_pipeline
from core.engine import opening_flow


@pytest.fixture
def book(tmp_path):
    root = tmp_path / 'book'
    (root / 'chapters').mkdir(parents=True)
    for i in range(1, 4):
        (root / 'chapters' / f'{i:04d}.txt').write_text(f'Alice enters room {i}. Bob leaves room {i}.', encoding='utf-8')
    (root / 'chapter_index.json').write_text(json.dumps({'book_id': 'test', 'chapters': [{'idx': i} for i in range(1, 4)]}), encoding='utf-8')
    return root


def model(prompt):
    if prompt.startswith('IDENTITY_PAIR_V1'):
        data = json.loads(prompt.split('\n', 2)[2])
        left, right = data['left'], data['right']
        return {'left': left['mention_id'], 'right': right['mention_id'], 'relation': 'same',
                'reason': 'fixture establishes a single Alice',
                'evidence': {left['mention_id']: left['excerpt'], right['mention_id']: right['excerpt']}}
    text = prompt.split('原文：\n', 1)[1]
    number = int(prompt.split('chapter=')[1].split('\n')[0])
    return {'anchor': {'chapter': number, 'title': 'Room', 'summary': text,
                       'events': [text], 'characters': ['Alice'], 'world': text,
                       'foreshadowing': [text], 'quotes': [text], 'ripple': text},
            'entities': [{'name': 'Alice', 'kind': 'character', 'excerpt': text.split('. ')[0] + '.'}]}


def test_fullbook_requires_all_blocks_not_first_anchor(book):
    result = prepare_book(book, mode='fullbook')
    assert not result['ready']
    assert result['coverage']['verified_blocks'] == 0
    (book / 'anchors').mkdir()
    (book / 'anchors' / '0001.json').write_text('{}')
    assert not verify_preparation(book, mode='fullbook')['ready']
    result = prepare_book(book, mode='fullbook', model=model)
    assert result['ready']
    assert result['coverage'] == {'expected_blocks': 3, 'verified_blocks': 3, 'complete': True}
    assert len(result['entities']) == 3
    assert len({e['mention_id'] for e in result['entities']}) == 3
    assert verify_preparation(book)['ready']


def test_fullbook_chapter_progress_and_incremental_anchor_writes(book):
    events = []
    prior_anchors = {}

    def checking(prompt):
        if not prompt.startswith('IDENTITY_PAIR_V1'):
            number = int(prompt.split('chapter=')[1].split('\n')[0])
            prior_anchors[number] = all(
                (book / 'anchors' / f'{i:04d}.json').is_file() for i in range(1, number))
        return model(prompt)

    prepare_book(book, mode='fullbook', model=checking,
                 progress=lambda detail: events.append(detail))
    distilling = [e for e in events if e['stage'] == 'DISTILLING']
    assert distilling[0]['chapters_total'] == 3 and distilling[0]['chapters_done'] == 0
    verified = [e for e in distilling if e.get('verified')]
    assert [e['current_chapter'] for e in verified] == [1, 2, 3]
    assert [e['chapters_done'] for e in verified] == [1, 2, 3]
    assert distilling[-1]['chapters_done'] == 3
    # 按章顺序即时落盘：进入第 N 章块蒸馏时，前 N-1 章锚点文件已存在。
    assert prior_anchors == {1: True, 2: True, 3: True}
    for i in range(1, 4):
        anchor = json.loads((book / 'anchors' / f'{i:04d}.json').read_text(encoding='utf-8'))
        assert anchor['title'] == 'Room'
        assert anchor['characters'] == ['Alice']
        assert anchor['events']


def test_resume_and_version_invalidation(book):
    calls = []
    def tracked(prompt):
        if not prompt.startswith('IDENTITY_PAIR_V1'):
            calls.append(prompt)
        return model(prompt)
    prepare_book(book, mode='fullbook', model=tracked, model_version='one')
    assert len(calls) == 3
    prepare_book(book, mode='fullbook', model=tracked, model_version='one')
    assert len(calls) == 3
    prepare_book(book, mode='fullbook', model=tracked, model_version='two')
    assert len(calls) == 6
    (book / 'chapters' / '0003.txt').write_text('Alice enters room 4. Bob leaves room 4.', encoding='utf-8')
    assert not verify_preparation(book)['ready']
    prepare_book(book, mode='fullbook', model=tracked, model_version='two')
    assert len(calls) == 7


def test_long_chapter_all_blocks_and_late_entity_are_covered(book):
    text = ''.join(f'Alice enters room {i}. Bob leaves room {i}.' for i in range(15))
    (book / 'chapters' / '0003.txt').write_text(text, encoding='utf-8')
    calls = []
    def bounded(prompt):
        if prompt.startswith('IDENTITY_PAIR_V1'):
            return model(prompt)
        calls.append(prompt)
        source = prompt.split('原文：\n', 1)[1]
        number = int(prompt.split('chapter=')[1].split('\n')[0])
        return {'anchor': {'chapter': number, 'title': 'Room', 'summary': source,
                           'events': [source], 'characters': ['Alice'], 'world': source,
                           'foreshadowing': [source], 'quotes': [source], 'ripple': source},
                'entities': [{'name': 'Alice', 'kind': 'character', 'excerpt': 'Alice'}] if 'Alice' in source else []}
    result = prepare_book(book, mode='fullbook', leaf_chars=100, model=bounded)
    assert result['ready']
    assert len(calls) == result['coverage']['expected_blocks'] > 3
    assert max(b['end'] for b in result['blocks'] if b['chapter_no'] == 3) == len(text)
    assert verify_preparation(book)['ready']


def test_partial_failure_is_resumable(book):
    def partial(prompt):
        if 'chapter=2\n' in prompt:
            raise RuntimeError('interrupted block')
        return model(prompt)
    assert not prepare_book(book, mode='fullbook', model=partial)['ready']
    calls = []
    def remaining(prompt):
        if not prompt.startswith('IDENTITY_PAIR_V1'):
            calls.append(prompt)
        return model(prompt)
    assert prepare_book(book, mode='fullbook', model=remaining)['ready']
    assert len(calls) == 1


def test_missing_chapter_and_tampered_evidence_fail_closed(book):
    prepare_book(book, mode='fullbook', model=model)
    cache = next((book / 'preparation' / 'blocks').glob('*.json'))
    data = json.loads(cache.read_text(encoding='utf-8'))
    data['result']['entities'][0]['excerpt'] = 'Alice never appeared here'
    cache.write_text(json.dumps(data), encoding='utf-8')
    assert not verify_preparation(book)['ready']
    (book / 'chapters' / '0003.txt').unlink()
    assert not verify_preparation(book)['ready']
    with pytest.raises(FileNotFoundError):
        prepare_book(book, mode='fullbook', model=model)


def test_window_scope_and_unknown_mode(book):
    package = prepare_book(book, target_chapter=2, opening_chapters=1)
    assert [x['chapter_no'] for x in package['chapters']] == [2]
    assert verify_preparation(book, mode='window', target_chapter=2)['ready']
    assert not verify_preparation(book, target_chapter=1)['ready']
    with pytest.raises(ValueError):
        prepare_book(book, mode='fast')


def test_locator_exact_evidence_timepoint_cutoff_and_stale(book):
    prepare_book(book, mode='fullbook', model=model)
    candidates = locate_scene(book, 'Alice enters room 2.')
    scene = candidates[0]
    assert scene['chapter_no'] == 2 and scene['match'] == 'exact'
    before = select_scene(book, scene, 'before')
    during = select_scene(book, scene, 'during')
    after = select_scene(book, scene, 'after')
    assert [e['chapter_no'] for e in before['initial_facts']] == [1]
    assert during['initial_facts'] == before['initial_facts']
    assert [e['chapter_no'] for e in after['initial_facts']] == [1, 2]
    with pytest.raises(ValueError):
        select_scene(book, {**scene, 'excerpt': 'forged'}, 'before')
    (book / 'chapters' / '0003.txt').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError):
        select_scene(book, scene)


def test_library_only_played_ready_and_keeps_originals(book):
    prepare_book(book)
    assert list_playable_books(book.parent) == []
    mark_book_played(book, 'committed-session')
    assert len(list_playable_books(book.parent)) == 1
    (book / 'chapters' / '0003.txt').write_text('changed', encoding='utf-8')
    assert list_playable_books(book.parent) == []
    assert (book / 'chapters' / '0001.txt').exists()


def test_fullbook_pipeline_consumes_all_entities_without_sampling(book):
    prepare_book(book, mode='fullbook', model=model)
    def forbidden(prompt):
        raise AssertionError('prepared fullbook must not resample characters')
    report = run_opening_pipeline(book, 'Test', forbidden, mode='fullbook')
    assert report['ok'] and len(report['entities']) == 3
    assert len(report['anchors']) == 3


def test_opening_flow_fullbook_gate_is_not_first_anchor():
    state = opening_flow.initial_state(txt_uploaded=True, plot_ready=True,
                gf_confirmed=True, opening_confirmed=True, preparation_mode='fullbook')
    assert not opening_flow.start_game(state)['ok']
    state['preparation_mode'] = 'window'
    assert opening_flow.start_game(state)['ok']
