"""C03 回归：fullbook 蒸馏块错误必须结构化（code/block/field/retryable）、
可断点续蒸、且绝不携带上游异常文本（凭据安全）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.services.book_prepare_service import (
    _BlockValidationError, _validate_block, prepare_book,
)
from core.services.preparation_jobs_service import PreparationJobsService

SECRET = 'secret-credential-c03'


def _anchor(chapter: int, text: str) -> dict:
    return {'chapter': chapter, 'title': 'Arrival', 'summary': 'A visitor arrives.',
            'world': 'A village', 'ripple': 'Change', 'events': ['Arrival'],
            'characters': ['Alice'], 'foreshadowing': ['Unknown'], 'quotes': [text]}


def _block(text: str = 'Alice arrives in the village.', chapter_no: int = 1) -> dict:
    return {'block_id': 'b1', 'chapter_no': chapter_no, 'text': text,
            'source_hash': 'hash', 'start': 0, 'end': len(text)}


def _good_output(prompt: str) -> dict:
    chapter = int(prompt.split('chapter=')[1].split('\n')[0])
    text = prompt.split('原文：\n', 1)[1]
    return {'anchor': _anchor(chapter, text),
            'entities': [{'name': 'Alice', 'kind': 'character', 'excerpt': text}]}


def _model(prompt: str) -> dict:
    if prompt.startswith('FULLBOOK_RICH_CHARACTER_V1'):
        data = json.loads(prompt.splitlines()[-1])
        return dict(name='Alice', desire='Find shelter', fear='Being lost', voice='Quiet',
                    background='A visitor', facts=[{'evidence_id': data['evidence'][0]['evidence_id'],
                                                    'value': 'Alice'}])
    if 'chapter=' not in prompt:
        return {key: {'rules': ['Seek shelter'], 'evidence': [
            {'chapter': 1, 'quote': 'Alice arrives in the village.', 'interpretation': 'Arrival'}]}
                for key in ('mind_model', 'decision_policy', 'voice_transfer', 'behavior_boundaries')}
    return _good_output(prompt)


def publisher(card: dict) -> dict:
    return {'saved': True, 'record': dict(card, revision=0)}


class ValidateBlockErrorShape(unittest.TestCase):
    def assert_structured(self, callable_, code: str, field: str):
        with self.assertRaises(_BlockValidationError) as caught:
            callable_()
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.field, field)

    def test_entities_not_list(self):
        self.assert_structured(
            lambda: _validate_block({'anchor': _anchor(1, 'Alice arrives in the village.'),
                                     'entities': 'nope'}, _block()),
            'ENTITIES_NOT_LIST', 'entities')

    def test_entity_not_object_reports_index(self):
        bad = {'anchor': _anchor(1, 'Alice arrives in the village.'),
               'entities': [{'name': 'Alice', 'kind': 'character',
                             'excerpt': 'Alice arrives in the village.'}, 'Alice']}
        self.assert_structured(lambda: _validate_block(bad, _block()),
                               'ENTITY_NOT_OBJECT', 'entities[1]')

    def test_entity_missing_name_reports_field(self):
        bad = {'anchor': _anchor(1, 'Alice arrives in the village.'),
               'entities': [{'kind': 'character', 'excerpt': 'Alice arrives'}]}
        self.assert_structured(lambda: _validate_block(bad, _block()),
                               'ENTITY_FIELDS_INVALID', 'entities[0].name')

    def test_entity_bad_kind_reports_field(self):
        bad = {'anchor': _anchor(1, 'Alice arrives in the village.'),
               'entities': [{'name': 'Alice', 'kind': 'monster', 'excerpt': 'Alice arrives'}]}
        self.assert_structured(lambda: _validate_block(bad, _block()),
                               'ENTITY_FIELDS_INVALID', 'entities[0].kind')

    def test_excerpt_mismatch_reports_field(self):
        bad = {'anchor': _anchor(1, 'Alice arrives in the village.'),
               'entities': [{'name': 'Alice', 'kind': 'character', 'excerpt': 'Bob leaves town'}]}
        self.assert_structured(lambda: _validate_block(bad, _block()),
                               'ENTITY_EXCERPT_MISMATCH', 'entities[0].excerpt')

    def test_anchor_failure_reports_field(self):
        short = {k: v for k, v in _anchor(1, 'x').items() if k != 'ripple'}
        bad = {'anchor': short, 'entities': []}
        self.assert_structured(lambda: _validate_block(bad, _block()),
                               'ANCHOR_INVALID', 'anchor')

    def test_unparsable_output_reports_code(self):
        self.assert_structured(lambda: _validate_block('not-json{{', _block()),
                               'MODEL_OUTPUT_INVALID', None)


class PrepareBookErrorStructure(unittest.TestCase):
    def _book(self, root: Path) -> Path:
        book = root / 'book'
        (book / 'chapters').mkdir(parents=True)
        for i, text in enumerate(('Alice arrives in the village.',
                                  'Alice leaves the village.'), 1):
            (book / 'chapters' / f'{i:04d}.txt').write_text(text, encoding='utf-8')
        (book / 'chapter_index.json').write_text(
            json.dumps({'book_id': 't', 'chapters': [{'idx': 1}, {'idx': 2}]}), encoding='utf-8')
        return book

    def test_model_failure_records_structured_safe_errors(self):
        with tempfile.TemporaryDirectory() as d:
            book = self._book(Path(d))

            def dead_model(prompt):
                raise RuntimeError(f'upstream 500 api_key={SECRET} payload={{...}}')

            package = prepare_book(book, mode='fullbook', model=dead_model, resume=True)
            self.assertFalse(package['ready'])
            self.assertEqual(package['coverage']['verified_blocks'], 0)
            self.assertEqual(len(package['errors']), 2)
            for entry in package['errors']:
                self.assertEqual(entry['code'], 'MODEL_FAILED')
                self.assertIsNone(entry['field'])
                self.assertTrue(entry['retryable'])
                self.assertIn(entry['chapter_no'], (1, 2))
                self.assertTrue(entry['block_id'])
            self.assertNotIn(SECRET, json.dumps(package, ensure_ascii=False, default=str))
            self.assertNotIn('block preparation failed',
                             json.dumps(package, ensure_ascii=False, default=str))

    def test_invalid_output_records_code_and_field(self):
        with tempfile.TemporaryDirectory() as d:
            book = self._book(Path(d))

            def lying_model(prompt):
                chapter = int(prompt.split('chapter=')[1].split('\n')[0])
                text = prompt.split('原文：\n', 1)[1]
                return {'anchor': _anchor(chapter, text), 'entities': '凭空捏造'}

            package = prepare_book(book, mode='fullbook', model=lying_model, resume=True)
            self.assertEqual([e['code'] for e in package['errors']],
                             ['ENTITIES_NOT_LIST', 'ENTITIES_NOT_LIST'])
            self.assertTrue(all(e['field'] == 'entities' for e in package['errors']))

    def test_verified_block_resumes_from_cache_without_recall(self):
        with tempfile.TemporaryDirectory() as d:
            book = self._book(Path(d))
            calls: list[int] = []

            def flaky_model(prompt):
                chapter = int(prompt.split('chapter=')[1].split('\n')[0])
                calls.append(chapter)
                if chapter == 2:
                    raise RuntimeError(f'gateway down api_key={SECRET}')
                return _good_output(prompt)

            first = prepare_book(book, mode='fullbook', model=flaky_model, resume=True)
            self.assertEqual(first['coverage']['verified_blocks'], 1)
            self.assertEqual([e['code'] for e in first['errors']], ['MODEL_FAILED'])
            self.assertTrue(next((book / 'preparation' / 'blocks').glob('*.json')))

            recalled: list[int] = []

            def counting_model(prompt):
                if 'chapter=' in prompt:
                    recalled.append(int(prompt.split('chapter=')[1].split('\n')[0]))
                return _model(prompt)

            second = prepare_book(book, mode='fullbook', model=counting_model, resume=True)
            self.assertNotIn(1, recalled, '已验证块必须复用缓存，不得重新调用模型')
            self.assertEqual(second['coverage']['verified_blocks'], 2)
            self.assertEqual(second['errors'], [])


class FailedJobAggregatesIssues(unittest.TestCase):
    def test_failed_job_issues_structured_without_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            book = root / 'book'
            (book / 'chapters').mkdir(parents=True)
            (book / 'chapters' / '0001.txt').write_text('Alice arrives in the village.',
                                                        encoding='utf-8')
            (book / 'chapter_index.json').write_text(
                json.dumps({'book_id': 't', 'chapters': [{'idx': 1}]}), encoding='utf-8')
            service = PreparationJobsService(root / 'runtime' / 'jobs.sqlite', publisher=publisher)
            self.addCleanup(service.close)
            job = service.create(book, idempotency_key='k', mode='fullbook')

            def dead_model(prompt):
                raise RuntimeError(f'upstream 500 api_key={SECRET}')

            done = service.start(job['job_id'], model=dead_model).result(5)
            self.assertEqual(done['status'], 'FAILED')
            issues = done['error']['issues']
            self.assertEqual([i['code'] for i in issues], ['MODEL_FAILED'])
            self.assertEqual(issues[0]['chapter_no'], 1)
            self.assertTrue(issues[0]['block_id'])
            self.assertTrue(issues[0]['retryable'])
            self.assertNotIn(SECRET, json.dumps(done, ensure_ascii=False, default=str))


if __name__ == '__main__':
    unittest.main()
