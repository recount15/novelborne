"""Chapter intents and existing-source runtime contracts; no model/DB/network."""
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from core import server
from core.services import reader_start_service as reader, game_setup


@pytest.fixture
def book(tmp_path):
    root = tmp_path / 'books' / 'demo'
    (root / 'chapters').mkdir(parents=True)
    (root / 'chapter_index.json').write_text(json.dumps({'book_id': 'demo', 'chapters': [
        {'idx': 1, 'title': 'One', 'chars': 9}, {'idx': 2, 'title': 'Two', 'chars': 9}]}), encoding='utf-8')
    (root / 'chapters' / '0001.txt').write_text('past fact', encoding='utf-8')
    (root / 'chapters' / '0002.txt').write_text('next fact', encoding='utf-8')
    return root


def test_chapter_intent_is_readonly_and_rejects_stale_source(book, monkeypatch):
    monkeypatch.setattr(reader, 'verify_preparation', lambda *a, **k: {'ready': True, 'entities': [
        {'chapter_no': 1, 'start': 0, 'end': 4, 'name': 'past'},
        {'chapter_no': 2, 'start': 0, 'end': 4, 'name': 'future'}]})
    before = {str(p): p.read_bytes() for p in book.rglob('*') if p.is_file()}
    intent = reader.prepare_chapter_start(book, 'demo', 2)
    selected = intent['scene_selection']
    assert selected['knowledge_cutoff'] == {'chapter_no': 2, 'offset': 0}
    assert [f['name'] for f in selected['initial_facts']] == ['past']
    assert before == {str(p): p.read_bytes() for p in book.rglob('*') if p.is_file()}
    (book / 'chapters' / '0001.txt').write_text('edited', encoding='utf-8')
    with pytest.raises(ValueError, match='source changed'):
        reader.prepare_chapter_start(book, 'demo', 2, intent['source_hash'])


def test_existing_enhanced_book_reads_selected_chapter_without_resplit(book, monkeypatch):
    monkeypatch.setattr(game_setup.fe, 'WRITABLE_DIR', str(book.parent.parent))
    split = Mock(side_effect=AssertionError('must not split'))
    monkeypatch.setattr(game_setup, '_split_uploaded_book', split)
    index, excerpt, name, work = game_setup.resolve_work_source('强化模式', None, None, '', '',
                                                                book_dir=book, target_chapter=2)
    assert index['book_id'] == 'demo' and excerpt == 'next fact' and name == 'demo' and work is None
    split.assert_not_called()
    with pytest.raises(game_setup.WorkSourceError):
        game_setup.resolve_work_source('强化模式', None, None, '', '', book_dir=book, target_chapter=3)


def test_chapter_start_route_and_start_revalidate_client_facts(book, monkeypatch):
    monkeypatch.setattr(server, '_resolve_book_dir', lambda bid: book)
    monkeypatch.setattr(reader, 'verify_preparation', lambda *a, **k: {'ready': False})
    client = TestClient(server.app)
    response = client.post('/api/books/demo/chapter-start', json={'chapter_no': 2})
    assert response.status_code == 200
    intent = response.json()
    intent['scene_selection']['initial_facts'] = ['forged future']
    manager = Mock()
    manager.acquire.return_value = True
    monkeypatch.setattr(server, 'sessions', manager)
    monkeypatch.setattr(server, '_upload_or_404', lambda *a: None)
    monkeypatch.setattr(server, '_stream_response', lambda *a, **k: server.Response('ok'))
    on_start = Mock(return_value=iter(()))
    monkeypatch.setattr(server.gradio_app, 'on_start', on_start)
    response = client.post('/api/sessions/start', json={'book_id': 'demo', 'scene_selection': intent['scene_selection']})
    assert response.status_code == 200
    assert on_start.call_args.kwargs['scene_selection']['initial_facts'] == []
    assert on_start.call_args.kwargs['target_chapter'] == 2
    response = client.post('/api/sessions/start', json={'book_id': 'demo', 'chapter_selection': {
        'chapter_no': 2, 'source_hash': 'stale'}})
    assert response.status_code == 409


def test_anchors_report_unavailability_while_chapter_text_stays_readable(book, monkeypatch):
    """F25：锚点缺失/损坏时如实标注 unavailable，原文仍可读，不伪造锚点内容。"""
    monkeypatch.setattr(server, '_resolve_book_dir', lambda bid: book)
    client = TestClient(server.app)
    chapter = client.get('/api/books/demo/chapters/1')
    assert chapter.status_code == 200
    assert chapter.json()['chapter']['text'] == 'past fact'
    missing = client.get('/api/books/demo/chapters/1/anchors')
    assert missing.status_code == 200
    body = missing.json()
    assert body['status'] == 'unavailable'
    assert body['anchor'] is None
    assert body['characters'] == []
    (book / 'anchors').mkdir()
    (book / 'anchors' / '0001.json').write_text(
        json.dumps({'characters': ['甲'], 'plot': '旧桥夜话'}), encoding='utf-8')
    ready = client.get('/api/books/demo/chapters/1/anchors').json()
    assert ready['status'] == 'ready'
    assert ready['anchor']['plot'] == '旧桥夜话'
    assert ready['characters'] == [{'name': '甲'}]
    (book / 'anchors' / '0001.json').write_text('{broken', encoding='utf-8')
    assert client.get('/api/books/demo/chapters/1/anchors').json()['status'] == 'unavailable'

    # Window 准备模式下：如实返回透明诊断引导信息，告知锚点将在开局管线启动时生成
    (book / 'anchors' / '0001.json').unlink()
    (book / 'opening_ready.json').write_text(json.dumps({'mode': 'window'}), encoding='utf-8')
    window_resp = client.get('/api/books/demo/chapters/1/anchors').json()
    assert window_resp['status'] == 'unavailable'
    assert '窗口快速准备模式' in window_resp['detail']
    assert '开局管线' in window_resp['detail']
