"""Transport contracts only: injected services, no live database/model/network."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from core import server

@pytest.fixture
def routes(monkeypatch, tmp_path):
    book = tmp_path / 'books' / 'demo'
    book.mkdir(parents=True)
    monkeypatch.setattr(server, '_resolve_book_dir', Mock(return_value=book))
    jobs = Mock()
    jobs.create.return_value = {'job_id': 'job1', 'status': 'QUEUED'}
    jobs.get.return_value = {'job_id': 'job1', 'status': 'READY', 'needs_credentials': False}
    jobs.book_identity.return_value = {'book_id': 'demo', 'book_path': str(book), 'source_hash': 'current'}
    from core.services import preparation_jobs_service
    monkeypatch.setattr(preparation_jobs_service, '_source', lambda root: 'current')
    jobs.configuration.return_value = {'mode': 'window', 'model_version': 'unspecified'}
    jobs.events.return_value = [{'sequence': 2}]
    jobs.cancel.return_value = {'job_id': 'job1', 'status': 'CANCELLED'}
    monkeypatch.setattr(server, '_get_preparation_jobs', lambda: jobs)
    chat = Mock()
    chat.get_thread.return_value = {'thread_id': 'reader1', 'scope': 'reader', 'context': {'book_id': 'demo'}}
    chat.list_roster.return_value = {'status': 'preparation_required', 'characters': []}
    chat.create_thread.return_value = chat.get_thread.return_value
    chat.send_message.return_value = {'saved': True, 'reply': 'scoped'}
    monkeypatch.setattr(server, '_get_reader_chat', lambda: chat)
    model = Mock(return_value='model result')
    model_factory = Mock(return_value=model)
    monkeypatch.setattr(server, '_request_model_callable', model_factory)
    return SimpleNamespace(client=TestClient(server.app), book=book, jobs=jobs, chat=chat,
                           model=model, model_factory=model_factory)


def test_preparation_create_uses_body_credentials_without_persisting_them(routes, monkeypatch):
    mark = Mock()
    monkeypatch.setattr(server, 'mark_book_played', mark)
    response = routes.client.post('/api/books/demo/preparation-jobs', json={
        'mode': 'fullbook', 'api_key': ' secret ', 'model': 'chosen',
        'target_chapter': 5, 'idempotency_key': 'retry-1'})
    assert response.status_code == 202
    kwargs = routes.jobs.create.call_args.kwargs
    assert kwargs['target_chapter'] == 5 and kwargs['model_version'] == 'chosen'
    assert 'api_key' not in kwargs and 'secret' not in repr(kwargs)
    routes.jobs.start.assert_called_once_with('job1', model=routes.model)
    mark.assert_not_called()


def test_credentials_in_query_do_not_authorize_preparation(routes):
    response = routes.client.post('/api/books/demo/preparation-jobs?api_key=secret',
                                  json={'mode': 'fullbook', 'idempotency_key': 'one'})
    assert response.status_code == 400
    routes.jobs.create.assert_not_called()


def test_job_poll_events_cancel_resume_and_not_found(routes):
    assert routes.client.get('/api/preparation-jobs/job1').status_code == 200
    assert routes.client.get('/api/preparation-jobs/job1/events?after=1').json() == {'events': [{'sequence': 2}]}
    routes.jobs.events.assert_called_once_with('job1', after=1)
    assert routes.client.get('/api/preparation-jobs/job1/events?after=-1').status_code == 422
    assert routes.client.post('/api/preparation-jobs/job1/cancel').status_code == 200
    assert routes.client.post('/api/preparation-jobs/job1/resume', json={}).status_code == 202
    routes.jobs.resume.assert_called_once_with('job1', model=None, model_version='unspecified')
    routes.jobs.get.side_effect = KeyError('private path')
    assert routes.client.get('/api/preparation-jobs/missing').status_code == 404


def test_sync_prepare_forwards_model_version_and_target(routes, monkeypatch):
    prepare = Mock(return_value={'ready': True})
    monkeypatch.setattr(server, 'prepare_book', prepare)
    response = routes.client.post('/api/books/demo/prepare', json={
        'mode': 'fullbook', 'api_key': 'secret', 'model': 'chosen', 'target_chapter': 7})
    assert response.status_code == 200
    assert prepare.call_args.kwargs['model_version'] == 'chosen'
    assert prepare.call_args.kwargs['target_chapter'] == 7


def test_reader_chat_contracts_and_redacted_errors(routes):
    assert routes.client.get('/api/books/demo/reader-chat/roster?chapter_no=3').status_code == 200
    # C07：路由显式传递 view/session_state（original 默认，不触碰会话）。
    routes.chat.list_roster.assert_called_once_with(routes.book, 3, view='original', session_state=None)
    response = routes.client.post('/api/books/demo/reader-chat/threads', json={
        'character_id': 'char1', 'chapter_no': 3, 'card_revision': 2, 'source_hash': 'hash'})
    assert response.status_code == 200
    routes.chat.create_thread.assert_called_once_with(routes.book, 'char1', 3,
        card_revision=2, source_hash='hash', view='original', session_state=None)
    assert routes.client.get('/api/reader-chat/threads/reader1').status_code == 200
    response = routes.client.post('/api/reader-chat/threads/reader1/messages', json={
        'message': 'hello', 'request_id': 'one', 'api_key': 'secret'})
    assert response.status_code == 200
    routes.chat.send_message.assert_called_once_with('reader1', 'hello', request_id='one', model_fn=routes.model)
    routes.chat.send_message.side_effect = RuntimeError('secret model configuration')
    response = routes.client.post('/api/reader-chat/threads/reader1/messages', json={
        'message': 'hello', 'request_id': 'two', 'api_key': 'secret'})
    assert response.status_code == 409 and 'secret' not in response.text


def test_start_reuses_book_and_rebuilds_scene_facts(routes, monkeypatch):
    routes.jobs.configuration.return_value = {'mode': 'fullbook'}
    routes.jobs.get.return_value = {'status': 'READY', 'character_counts': {'verified_cards': 1},
        'gap_report': {'card_publication': 'complete', 'rich_character_extraction': 'complete'},
        'published_characters': [{'character_id': 'char1'}]}
    canonical = {'knowledge_cutoff': {'chapter_no': 3}, 'initial_facts': ['server fact']}
    select = Mock(return_value=canonical)
    monkeypatch.setattr(server, 'select_scene', select)
    session_manager = Mock()
    session_manager.acquire.return_value = True
    monkeypatch.setattr(server, 'sessions', session_manager)
    monkeypatch.setattr(server, '_upload_or_404', lambda *args: None)
    monkeypatch.setattr(server, '_stream_response', lambda *args, **kwargs: server.Response('ok'))
    on_start = Mock(return_value=iter(()))
    monkeypatch.setattr(server.gradio_app, 'on_start', on_start)
    split = Mock(side_effect=AssertionError('must not split existing source'))
    monkeypatch.setattr(server.engine.chapter_tools, 'split_file', split)
    response = routes.client.post('/api/sessions/start', json={
        'book_id': 'demo', 'mode': '强化模式', 'preparation_job_id': 'job1',
        'scene_selection': {'candidate': {'id': 'candidate'}, 'timepoint': 'before',
                            'initial_facts': ['forged future']}})
    assert response.status_code == 200
    params = on_start.call_args.kwargs
    assert params['book_dir'] == str(routes.book)
    assert params['novel_file'] is None and params['work'] is None
    assert params['scene_selection'] == canonical and params['target_chapter'] == 3
    split.assert_not_called()


def test_fullbook_start_requires_matching_published_ready_job(routes, monkeypatch):
    manager = Mock()
    monkeypatch.setattr(server, 'sessions', manager)
    assert routes.client.post('/api/sessions/start', json={
        'book_id': 'demo', 'mode': '强化模式'}).status_code == 409
    routes.jobs.book_identity.return_value = {'book_id': 'other', 'book_path': str(routes.book.parent / 'other'), 'source_hash': 'current'}
    assert routes.client.post('/api/sessions/start', json={
        'book_id': 'demo', 'mode': '强化模式', 'preparation_job_id': 'other-book'}).status_code == 409
    manager.create.assert_not_called()
    routes.jobs.book_identity.assert_called_once_with('other-book')


def test_resume_uses_stored_model_and_rejects_mismatch(routes):
    routes.jobs.configuration.return_value = {'mode': 'fullbook', 'model_version': 'original'}
    assert routes.client.post('/api/preparation-jobs/job1/resume', json={
        'api_key': 'fresh', 'model': 'different'}).status_code == 409
    routes.jobs.resume.assert_not_called()
    assert routes.client.post('/api/preparation-jobs/job1/resume', json={
        'api_key': 'fresh'}).status_code == 202
    routes.model_factory.assert_called_once_with('deepseek', None, 'fresh', 'original')
    routes.jobs.resume.assert_called_once_with('job1', model=routes.model, model_version='original')


def test_start_does_not_choose_first_work(routes, monkeypatch):
    manager = Mock()
    manager.acquire.return_value = True
    monkeypatch.setattr(server, 'sessions', manager)
    monkeypatch.setattr(server, '_upload_or_404', lambda *args: None)
    monkeypatch.setattr(server, '_stream_response', lambda *args, **kwargs: server.Response('ok'))
    first_work = Mock(side_effect=AssertionError('implicit work lookup forbidden'))
    monkeypatch.setattr(server.fe, 'list_works', first_work)
    on_start = Mock(return_value=iter(()))
    monkeypatch.setattr(server.gradio_app, 'on_start', on_start)
    assert routes.client.post('/api/sessions/start', json={}).status_code == 200
    assert on_start.call_args.kwargs['work'] is None
    first_work.assert_not_called()


def test_start_rejects_scene_without_explicit_book(routes):
    response = routes.client.post('/api/sessions/start', json={'scene_selection': {'initial_facts': ['forged']}})
    assert response.status_code == 422


def test_fullbook_resume_requires_fresh_body_credentials(routes):
    routes.jobs.get.return_value = {'job_id': 'job1', 'needs_credentials': True}
    routes.jobs.configuration.return_value = {'mode': 'fullbook', 'model_version': 'chosen'}
    response = routes.client.post('/api/preparation-jobs/job1/resume?api_key=secret', json={})
    assert response.status_code == 400
    routes.jobs.resume.assert_not_called()


def test_job_replay_does_not_restart_ready_job(routes):
    routes.jobs.create.return_value = {'job_id': 'job1', 'status': 'READY'}
    response = routes.client.post('/api/books/demo/preparation-jobs', json={'idempotency_key': 'replay'})
    assert response.status_code == 202
    routes.jobs.start.assert_not_called()


def test_lifespan_does_not_initialize_unused_job_store(monkeypatch):
    monkeypatch.setattr(server, '_preparation_jobs', None)
    from core.services import preparation_jobs_service
    constructor = Mock(side_effect=AssertionError('must remain lazy'))
    monkeypatch.setattr(preparation_jobs_service, 'PreparationJobsService', constructor)
    with TestClient(server.app):
        pass
    constructor.assert_not_called()


def test_txt_upload_returns_authoritative_book_id(routes, monkeypatch):
    manager = Mock()
    manager.create.return_value = SimpleNamespace(session_id='session1')
    manager.put_upload.return_value = {'upload_id': 'upload1', 'filename': 'novel.txt'}
    manager.upload_path.return_value = routes.book / 'upload1_novel.txt'
    monkeypatch.setattr(server, 'sessions', manager)
    split = Mock(return_value={'book_id': 'upload1_novel', 'chapters': [{'index': 1}]})
    monkeypatch.setattr(server.engine.chapter_tools, 'split_file', split)
    response = routes.client.post('/api/uploads', files={'file': ('novel.txt', b'chapter one', 'text/plain')},
                                  data={'kind': 'novel'})
    assert response.status_code == 200
    assert response.json()['upload']['book_id'] == 'upload1_novel'
    assert split.call_args.kwargs['book_id'] == 'upload1_novel'


@pytest.mark.parametrize('book_id', ['linked', 'link'])
def test_book_resolver_rejects_directory_escape(monkeypatch, tmp_path, book_id):
    books = tmp_path / 'books'
    linked = books / 'linked'
    linked.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.setattr(server.fe, 'WRITABLE_DIR', str(tmp_path))
    original_resolve = Path.resolve
    # Emulate a junction without requiring Windows symlink privileges.
    monkeypatch.setattr(Path, 'resolve', lambda self, *a, **kw:
                        outside if self == linked else original_resolve(self, *a, **kw))
    with pytest.raises(server.HTTPException) as error:
        server._resolve_book_dir(book_id)
    assert error.value.status_code == 400


def test_shutdown_closes_only_initialized_service(monkeypatch):
    service = Mock()
    monkeypatch.setattr(server, '_preparation_jobs', service)
    server._close_preparation_jobs()
    service.close.assert_called_once_with()
    assert server._preparation_jobs is None
