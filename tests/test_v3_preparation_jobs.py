"""Synthetic stdlib tests; no app import, network, or production database."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from core.services.book_prepare_service import prepare_book, PreparationCancelled
from core.services.preparation_jobs_service import PreparationJobsService


def publisher(card):
    return {'saved': True, 'record': dict(card, revision=0)}


def model(prompt):
    if prompt.startswith('FULLBOOK_RICH_CHARACTER_V1'):
        data = json.loads(prompt.splitlines()[-1])
        return dict(name='Alice', desire='Find shelter', fear='Being lost', voice='Quiet',
                    background='A visitor', facts=[{'evidence_id': data['evidence'][0]['evidence_id'],
                                                   'value': 'Alice'}])
    if 'chapter=' not in prompt:
        return {key: {'rules': ['Seek shelter'], 'evidence': [
            {'chapter': 1, 'quote': 'Alice arrives in the village.', 'interpretation': 'Arrival'}]}
                for key in ('mind_model', 'decision_policy', 'voice_transfer', 'behavior_boundaries')}
    text = prompt.split('原文：\n', 1)[1]
    chapter = int(prompt.split('chapter=')[1].split('\n')[0])
    return {'anchor': {'chapter': chapter, 'title': 'Arrival', 'summary': 'A visitor arrives.',
                      'world': 'A village', 'ripple': 'Change', 'events': ['Arrival'],
                      'characters': ['Alice'], 'foreshadowing': ['Unknown'], 'quotes': [text]},
            'entities': [{'name': 'Alice', 'kind': 'character', 'excerpt': text}]}


class PreparationJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.book = self.root / 'book'
        (self.book / 'chapters').mkdir(parents=True)
        (self.book / 'chapters' / '0001.txt').write_text('Alice arrives in the village.', encoding='utf-8')
        (self.book / 'chapter_index.json').write_text(json.dumps({'book_id': 'test', 'chapters': [{'idx': 1}]}), encoding='utf-8')
        self.db = self.root / 'runtime' / 'jobs.sqlite'
        self.service = PreparationJobsService(self.db, publisher=publisher)
        self.addCleanup(self.service.close)

    def create(self, **kwargs):
        return self.service.create(self.book, idempotency_key='key', **kwargs)

    def test_window_ready_events_and_restart(self):
        job = self.create()
        self.assertIsNone(job['total_units'])
        done = self.service.start(job['job_id']).result(5)
        self.assertEqual(done['status'], 'READY')
        self.assertIsNotNone(done['result_id'])
        events = self.service.events(job['job_id'])
        self.assertEqual([e['sequence'] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(self.service.events(job['job_id'], after=events[-2]['sequence']), events[-1:])
        with PreparationJobsService(self.db) as other:
            self.assertEqual(other.get(job['job_id']), done)
        self.assertIsNone(done['character_counts']['verified_cards'])

    def test_fullbook_validated_and_gap_report(self):
        job = self.create(mode='fullbook', model_version='synthetic')
        done = self.service.start(job['job_id'], model=model).result(5)
        self.assertEqual(done['status'], 'READY', done)
        self.assertEqual(done['gap_report']['rich_character_extraction'], 'complete')
        self.assertEqual(done['character_counts']['verified_cards'], 1)
        self.assertTrue(done['published_characters'])
        self.assertIn('EXTRACTING_CHARACTERS', [e['stage'] for e in self.service.events(job['job_id'])])
        blocks = [e for e in self.service.events(job['job_id']) if e.get('verified')]
        self.assertEqual(blocks[-1]['completed_units'], 1)
        self.assertEqual(blocks[-1]['total_units'], 1)
        self.assertTrue(blocks[-1]['cache_key'])

    def test_concurrent_duplicates_across_services(self):
        with PreparationJobsService(self.db) as other:
            with ThreadPoolExecutor(8) as pool:
                jobs = list(pool.map(lambda i: (self.service if i % 2 else other).create(
                    self.book, idempotency_key=str(i)), range(16)))
        self.assertEqual(len({j['job_id'] for j in jobs}), 1)
        with self.assertRaises(ValueError):
            self.service.create(self.book, idempotency_key='0', target_chapter=2)
        changed = self.service.create(self.book, idempotency_key='different', schema_version=4)
        self.assertNotEqual(changed['config_hash'], jobs[0]['config_hash'])

    def test_cancel_inflight_keeps_cache_resume_without_model_call(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(prompt):
            entered.set()
            self.assertTrue(release.wait(5))
            return model(prompt)
        job = self.create(mode='fullbook')
        future = self.service.start(job['job_id'], model=blocked)
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.service.cancel(job['job_id'])['status'], 'CANCEL_REQUESTED')
        release.set()
        self.assertEqual(future.result(5)['status'], 'CANCELLED')
        self.assertEqual(len(list((self.book / 'preparation' / 'blocks').glob('*.json'))), 1)
        def no_call(prompt):
            self.assertNotIn('chapter=', prompt, 'validated block must be reused')
            return model(prompt)
        done = self.service.resume(job['job_id'], model=no_call).result(5)
        self.assertEqual(done['status'], 'READY')

    def test_failed_never_ready_and_secrets_not_persisted(self):
        job = self.create(mode='fullbook')
        def failed(prompt):
            raise RuntimeError('secret-credential-123')
        done = self.service.start(job['job_id'], model=failed).result(5)
        self.assertEqual(done['status'], 'FAILED')
        self.assertIsNone(done['result_id'])
        self.assertTrue(done['needs_credentials'])
        self.assertNotIn('secret-credential-123', self.db.read_bytes().decode('latin1'))
        self.assertNotIn('secret-credential-123', (self.book / 'opening_ready.json').read_text())
        self.assertEqual(self.service.resume(job['job_id'], model=model).result(5)['status'], 'READY')

    def test_verify_is_mandatory_and_old_ready_preserved(self):
        previous = prepare_book(self.book)
        job = self.create(mode='fullbook')
        with patch('core.services.preparation_jobs_service.verify_preparation', return_value={'ready': False}):
            done = self.service.start(job['job_id'], model=model).result(5)
        self.assertEqual(done['status'], 'FAILED')
        self.assertEqual(json.loads((self.book / 'opening_ready.json').read_text()), previous)

    def test_recovery_explicit_not_reads_or_default_init(self):
        job = self.create(mode='fullbook')
        with self.service._db() as db:
            self.service._update(db, job['job_id'], status='DISTILLING')
        with PreparationJobsService(self.db) as other:
            self.assertEqual(other.get(job['job_id'])['status'], 'DISTILLING')
        with PreparationJobsService(self.db, recover_interrupted=True, publisher=publisher) as recovered:
            done = recovered.get(job['job_id'])
            self.assertEqual(done['status'], 'INTERRUPTED')
            self.assertTrue(done['needs_credentials'])
            self.assertEqual(recovered.resume(job['job_id'], model=model).result(5)['status'], 'READY')

    def test_resume_rejects_changed_source(self):
        job = self.create()
        self.service.cancel(job['job_id'])
        (self.book / 'chapters' / '0001.txt').write_text('Changed source', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.service.resume(job['job_id'])

    def test_cancel_queued_and_callback_failure(self):
        job = self.create()
        self.assertEqual(self.service.cancel(job['job_id'])['status'], 'CANCELLED')
        self.assertEqual(self.service.cancel(job['job_id'])['status'], 'CANCELLED')
        self.assertEqual(self.service.resume(job['job_id']).result(5)['status'], 'READY')
        def fail(detail):
            raise RuntimeError('event store unavailable')
        with self.assertRaises(RuntimeError):
            prepare_book(self.book, progress=fail)
        with self.assertRaises(PreparationCancelled):
            prepare_book(self.book, cancelled=lambda: True)

    def test_rich_failure_does_not_publish(self):
        published = []
        self.service._publisher = lambda card: published.append(card)
        def missing(prompt):
            return {'error': 'insufficient_evidence'} if prompt.startswith('FULLBOOK_RICH') else model(prompt)
        job = self.create(mode='fullbook')
        done = self.service.start(job['job_id'], model=missing).result(5)
        self.assertEqual(done['status'], 'FAILED')
        self.assertEqual(published, [])
        self.assertIsNone(done['result_id'])

    def test_publisher_failure_resumes_cached_rich_without_model(self):
        self.service._publisher = lambda card: {'saved': False}
        job = self.create(mode='fullbook')
        self.assertEqual(self.service.start(job['job_id'], model=model).result(5)['status'], 'FAILED')
        records = []
        def save(card):
            records.append(card)
            return publisher(card)
        self.service._publisher = save
        def no_model(prompt):
            raise AssertionError('all validated extraction artifacts should be reused')
        done = self.service.resume(job['job_id'], model=no_model).result(5)
        self.assertEqual(done['status'], 'READY')
        card = records[0]
        self.assertEqual(card['schema_version'], 2)
        self.assertEqual(card['quality']['state'], 'ready')
        self.assertEqual(card['source']['book_id'], 'test')
        self.assertEqual(card['facts'][0]['knowledge_holder_id'], card['id'])
        self.assertIn(card['facts'][0]['value'], card['evidence'][0]['quote'])
        text = (self.book / 'chapters' / '0001.txt').read_text()
        for evidence in card['evidence']:
            self.assertEqual(text[evidence['start']:evidence['end']], evidence['quote'])

    def test_real_library_publishes_complete_v2_in_isolated_database(self):
        from core.engine import character_db as db
        from core.engine.opening_distill import publish_fullbook_characters
        old = db.DATABASE_PATH
        self.addCleanup(db.set_database_path, old)
        db.set_database_path(self.root / 'db' / 'characters.sqlite')
        db.ensure_database()
        book = self.root / 'books' / 'test'
        book.parent.mkdir()
        self.book.rename(book)
        self.book = book
        self.service._publisher = None
        job = self.create(mode='fullbook')
        done = self.service.start(job['job_id'], model=model).result(5)
        self.assertEqual(done['status'], 'READY', done)
        identity = done['published_characters'][0]
        record = db.get_character_record(identity['character_id'])
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual(record['facts'][0]['knowledge_holder_id'], record['id'])
        self.assertEqual(record['source']['source_hash'], record['evidence'][0]['source_hash'])
        from core.services.character_context_service import build_reader_context
        context = build_reader_context(book, record['id'], 1)
        self.assertIsNotNone(context)
        repeated = publish_fullbook_characters(book, lambda _: self.fail('cache should avoid calls'))
        self.assertEqual(repeated[0]['revision'], record['revision'])

    def test_internal_book_identity_distinguishes_identical_copied_books(self):
        import shutil
        job = self.create(model_version='original')
        copied = self.root / 'copied-book'
        shutil.copytree(self.book, copied)
        other = self.service.create(copied, idempotency_key='key', model_version='original')
        left = self.service.book_identity(job['job_id'])
        right = self.service.book_identity(other['job_id'])
        self.assertEqual(left['source_hash'], right['source_hash'])
        self.assertEqual(left['book_id'], right['book_id'])
        self.assertNotEqual(left['book_path'], right['book_path'])
        self.assertEqual(Path(left['book_path']), self.book.resolve())
        self.assertNotEqual(Path(left['book_path']), copied.resolve())
        self.assertEqual(set(left), {'book_id', 'book_path', 'source_hash'})
        # Identity remains the creation binding even if inventory is edited.
        (self.book / 'chapter_index.json').write_text('{"book_id":"changed"}', encoding='utf-8')
        self.assertEqual(self.service.book_identity(job['job_id']), left)
        snapshot = self.service.get(job['job_id'])
        self.assertEqual(snapshot['book_id'], 'test')
        self.assertEqual(snapshot['configuration'], dict(mode='window', target_chapter=1,
                         model_version='original', prompt_version='block-evidence-v1', schema_version=3))
        public = json.dumps([snapshot, self.service.events(job['job_id'])])
        self.assertNotIn('book_path', public)
        self.assertNotIn(str(self.root), public)
        with self.service._db() as db:
            self.service._update(db, job['job_id'], book_id=None)
        with self.assertRaises(ValueError):
            self.service.book_identity(job['job_id'])
        with self.assertRaises(KeyError):
            self.service.book_identity('missing')

    def test_resume_configuration_model_version_is_validated(self):
        job = self.create(model_version='original')
        self.service.cancel(job['job_id'])
        self.assertEqual(self.service.configuration(job['job_id'])['model_version'], 'original')
        with self.assertRaises(ValueError):
            self.service.resume(job['job_id'], model_version='changed')
        self.assertEqual(self.service.get(job['job_id'])['status'], 'CANCELLED')

    def test_many_identities_and_long_source_are_bounded_and_checkpointed(self):
        from core.engine.opening_distill import publish_fullbook_characters
        from core.services.book_prepare_service import _hash
        # Isolate aggregation from the independently tested preparation verifier.
        text = 'Alice knows the road. ' * 1500
        (self.book / 'chapters' / '0001.txt').write_text(text, encoding='utf-8')
        identities, entities = [], []
        for i in range(13):
            mid = 'mention-' + str(i)
            entities.append(dict(mention_id=mid, name='Alice', kind='character',
                                 chapter_no=1, block_id='large-block', start=0, end=5, excerpt='Alice'))
            identities.append(dict(identity_id='person-' + str(i), names=['Alice'],
                                   mention_ids=[mid], ready=True))
        prepared = dict(ready=True, book_id='test', source_hash=_hash(text),
                        preparation_id='synthetic-bounded', config={'model_version': 'fake', 'prompt_version': 'v1'},
                        entities=entities, identity_report={'identities': identities},
                        blocks=[dict(block_id='large-block', chapter_no=1, start=0, end=len(text))])
        calls, checkpoints = [], []
        def bounded(prompt):
            calls.append(len(prompt))
            self.assertLessEqual(len(prompt), 24000)
            if prompt.startswith('FULLBOOK_RICH'):
                data = json.loads(prompt.splitlines()[-1])
                self.assertLessEqual(len(data['chapters'][0]['text']), 3000)
                evidence = data['evidence'][0]
                return dict(name='Alice', desire='Travel safely', fear='Getting lost', voice='Clear',
                            background='A traveler', facts=[{'evidence_id': evidence['evidence_id'],
                                                             'value': evidence['quote'][:5]}])
            # Exact source string reused in every slice of this repeated fixture.
            return {key: {'rules': ['Follow the road'], 'evidence': [
                {'chapter': 1, 'quote': 'knows the road.', 'interpretation': 'Travel knowledge'}]}
                    for key in ('mind_model', 'decision_policy', 'voice_transfer', 'behavior_boundaries')}
        with patch('core.services.book_prepare_service.verify_preparation', return_value=prepared):
            stopped = threading.Event()
            def stop_after_slice(detail):
                if detail.get('completed_source_blocks') == 1:
                    stopped.set()
            with self.assertRaises(PreparationCancelled):
                publish_fullbook_characters(self.book, bounded, publisher=publisher,
                                            progress=stop_after_slice, cancelled=stopped.is_set)
            self.assertEqual(len(calls), 2)
            records = publish_fullbook_characters(self.book, bounded, publisher=publisher, progress=checkpoints.append)
            self.assertEqual(len(calls), 13 * ((len(text) + 2999) // 3000) * 2)
            self.assertEqual(len(records), 13)
            self.assertGreater(len(records[0]['facts']), 8)
            self.assertEqual(len(records[0]['semantic']['mind_model']), len(records[0]['facts']))
            self.assertTrue(any(c.get('completed_source_blocks', 0) > 8 for c in checkpoints))
            before = len(calls)
            publish_fullbook_characters(self.book, bounded, publisher=publisher)
            self.assertEqual(len(calls), before)

    def test_incomplete_publisher_record_cannot_ready(self):
        self.service._publisher = lambda card: {'saved': True, 'record': {'id': card['id'], 'revision': 0}}
        job = self.create(mode='fullbook')
        self.assertEqual(self.service.start(job['job_id'], model=model).result(5)['status'], 'FAILED')

    def test_transaction_rollback_no_event_loss(self):
        job = self.create()
        before = self.service.events(job['job_id'])
        with self.assertRaises(RuntimeError):
            with self.service._db() as db:
                self.service._update(db, job['job_id'], status='FAILED')
                raise RuntimeError('rollback')
        self.assertEqual(self.service.events(job['job_id']), before)
        self.assertEqual(self.service.get(job['job_id'])['status'], 'QUEUED')


if __name__ == '__main__':
    unittest.main()
