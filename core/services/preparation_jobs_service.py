"""Durable standalone preparation jobs. No workers or database access on import.

Host API: PreparationJobsService(runtime_db_path), create(book_dir,
idempotency_key=..., ...), get(job_id), events(job_id, after=0),
start(job_id, model=callable), cancel(job_id), resume(job_id, model=callable),
close(). create only commits QUEUED; start explicitly schedules work and returns
its Future. Instantiate once in application lifespan, close at shutdown. Only an
exclusive startup owner may pass recover_interrupted=True. Never do that per
request or while another process is executing jobs. Model closures (including
credentials) live only in scheduled work; resumed fullbook jobs need a fresh one.

SQLite tables are standalone pending integration with the database foundation.
Fullbook READY requires verified evidence AND complete rich-card publication.
Optional publisher(payload)->{saved:True,record:complete_record} is a trusted
committing adapter; default lazily uses character_library.save_card. Window jobs
remain evidence-only and do not publish cards.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable

from core.services.book_prepare_service import (
    VERSION, PreparationCancelled, _atomic_json, _read_json,
    normalize_mode, prepare_book, verify_preparation,
)

ACTIVE = ('QUEUED', 'INDEXING', 'DISTILLING', 'RESOLVING_IDENTITIES',
          'EXTRACTING_CHARACTERS', 'VALIDATING', 'CANCEL_REQUESTED')
RUNNING = ACTIVE[1:]
TERMINAL = ('READY', 'CANCELLED', 'FAILED', 'INTERRUPTED')


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source(root: Path) -> str:
    paths = sorted((root / 'chapters').glob('*.txt'))
    if not paths:
        raise ValueError('book has no chapters')
    inventory = root / 'chapter_index.json'
    if inventory.exists():
        paths.append(inventory)
    return _hash([(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])


def _error(code: str, message: str, job_id: str, retryable: bool = True) -> dict:
    return dict(code=code, message=message, retryable=retryable,
                field=None, issues=[], request_id=job_id)


class PreparationJobsService:
    def __init__(self, db_path: str | Path, *, max_workers: int = 1,
                 recover_interrupted: bool = False,
                 publisher: Callable[[dict], dict] | None = None):
        if str(db_path) == ':memory:':
            raise ValueError('a durable explicit database path is required')
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._closed = False
        self._publisher = publisher
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix='preparation')
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS preparation_jobs (
                job_id TEXT PRIMARY KEY, book_path TEXT NOT NULL,
                config_hash TEXT NOT NULL, config_json TEXT NOT NULL,
                source_hash TEXT NOT NULL, status TEXT NOT NULL,
                snapshot TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS preparation_job_keys (
                key_hash TEXT PRIMARY KEY, config_hash TEXT NOT NULL,
                job_id TEXT NOT NULL REFERENCES preparation_jobs(job_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS preparation_job_events (
                job_id TEXT NOT NULL REFERENCES preparation_jobs(job_id),
                sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(job_id, sequence))''')
            db.execute('CREATE INDEX IF NOT EXISTS preparation_jobs_active ON preparation_jobs(book_path, status)')
            if recover_interrupted:
                for row in db.execute('SELECT * FROM preparation_jobs').fetchall():
                    if row['status'] in ACTIVE:
                        self._update(db, row['job_id'], status='INTERRUPTED',
                                     needs_credentials=json.loads(row['config_json'])['mode'] == 'fullbook',
                                     retryable=True, error=_error('INTERRUPTED', 'Worker interrupted; explicit resume required.', row['job_id']))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.db_path), timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _row(self, db, job_id):
        row = db.execute('SELECT * FROM preparation_jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError('preparation job not found')
        return row

    def _update(self, db, job_id, **changes):
        state = json.loads(self._row(db, job_id)['snapshot'])
        state.update(changes)
        state['sequence'] += 1
        state['updated_at'] = _now()
        db.execute('UPDATE preparation_jobs SET status=?, snapshot=? WHERE job_id=?',
                   (state['status'], _json(state), job_id))
        db.execute('INSERT INTO preparation_job_events VALUES (?,?,?)',
                   (job_id, state['sequence'], _json(state)))
        return state

    def create(self, book_dir: str | Path, *, idempotency_key: str,
               mode: str = 'window', target_chapter: int = 1,
               model_version: str = 'unspecified', prompt_version: str = 'block-evidence-v1',
               schema_version: int = VERSION, leaf_chars: int = 1200,
               arc_size: int = 10, opening_chapters: int = 3) -> dict:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError('idempotency_key is required')
        if any(type(v) is not int or v < 1 for v in
               (target_chapter, schema_version, leaf_chars, arc_size, opening_chapters)):
            raise ValueError('preparation sizes and versions must be positive integers')
        if not isinstance(model_version, str) or not isinstance(prompt_version, str):
            raise ValueError('model and prompt versions must be strings, not configuration objects')
        root = Path(book_dir).resolve()
        source = _source(root)
        book_id = str(_read_json(root / 'chapter_index.json').get('book_id') or root.name)
        config = dict(mode=normalize_mode(mode), target_chapter=target_chapter,
                      model_version=model_version, prompt_version=prompt_version,
                      schema_version=schema_version, leaf_chars=leaf_chars,
                      arc_size=arc_size, opening_chapters=opening_chapters)
        digest = _hash(dict(book=str(root), source=source, config=config, prepare_version=VERSION, job_version='rich-publication-v2-blocks'))
        key = _hash([str(root), idempotency_key])
        with self._db() as db:
            existing = db.execute('SELECT * FROM preparation_job_keys WHERE key_hash=?', (key,)).fetchone()
            if existing:
                if existing['config_hash'] != digest:
                    raise ValueError('idempotency key conflicts with source or configuration')
                return json.loads(self._row(db, existing['job_id'])['snapshot'])
            row = db.execute('SELECT * FROM preparation_jobs WHERE config_hash=? AND status IN (%s)' %
                             ','.join('?' for _ in ACTIVE), (digest, *ACTIVE)).fetchone()
            if row:
                job_id = row['job_id']
                state = json.loads(row['snapshot'])
            else:
                job_id = uuid.uuid4().hex
                state = dict(job_id=job_id, book_id=book_id,
                             configuration={k: config[k] for k in ('mode', 'target_chapter', 'model_version',
                                                                  'prompt_version', 'schema_version')},
                             status='QUEUED', stage='QUEUED',
                             completed_units=0, total_units=None, unit_kind='stage',
                             coverage=None, character_counts={'verified_cards': None},
                             sequence=0, result_id=None, error=None, retryable=False,
                             needs_credentials=config['mode'] == 'fullbook',
                             config_hash=digest, source_hash=source,
                             created_at=_now(), event_domain='preparation',
                             gap_report={'rich_character_extraction': 'pending' if config['mode'] == 'fullbook' else 'not_applicable',
                                         'card_publication': 'pending' if config['mode'] == 'fullbook' else 'not_applicable',
                                         'readiness_scope': 'published_rich_cards' if config['mode'] == 'fullbook' else 'window_evidence'})
                db.execute('INSERT INTO preparation_jobs VALUES (?,?,?,?,?,?,?)',
                           (job_id, str(root), digest, _json(config), source, 'QUEUED', _json(state)))
                state = self._update(db, job_id)
            db.execute('INSERT INTO preparation_job_keys VALUES (?,?,?)', (key, digest, job_id))
            return state

    def get(self, job_id: str) -> dict:
        with self._db() as db:
            return json.loads(self._row(db, job_id)['snapshot'])

    def book_identity(self, job_id: str) -> dict:
        """Internal server-only ownership binding; NEVER serialize to clients.

        Compare Path(book_path) to the authorized book root resolved by the
        server. Source hashes alone cannot distinguish copied books. Legacy
        rows without a persisted book_id fail closed; do not infer identity
        from potentially changed files on disk. source_hash is the task's
        original source fingerprint, not a live readiness assertion.
        """
        with self._db() as db:
            row = self._row(db, job_id)
            snapshot = json.loads(row['snapshot'])
            if not snapshot.get('book_id'):
                raise ValueError('legacy preparation lacks durable book identity; create a new job')
            return dict(book_id=snapshot['book_id'], book_path=row['book_path'],
                        source_hash=row['source_hash'])

    def events(self, job_id: str, *, after: int = 0) -> list[dict]:
        if type(after) is not int or after < 0:
            raise ValueError('after must be a nonnegative sequence')
        with self._db() as db:
            self._row(db, job_id)
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM preparation_job_events WHERE job_id=? AND sequence>? ORDER BY sequence',
                (job_id, after))]

    def cancel(self, job_id: str) -> dict:
        with self._db() as db:
            row = self._row(db, job_id)
            if row['status'] in TERMINAL or row['status'] == 'CANCEL_REQUESTED':
                return json.loads(row['snapshot'])
            status = 'CANCELLED' if row['status'] == 'QUEUED' else 'CANCEL_REQUESTED'
            return self._update(db, job_id, status=status, retryable=True)

    def start(self, job_id: str, *, model: Callable | None = None) -> Future:
        with self._lock:
            if self._closed:
                raise RuntimeError('preparation service is closed')
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                return current
            with self._db() as db:
                row = self._row(db, job_id)
                if row['status'] != 'QUEUED':
                    raise ValueError('only queued jobs can start; use resume for terminal jobs')
                if json.loads(row['config_json'])['mode'] == 'fullbook' and not callable(model):
                    raise ValueError('fullbook requires an in-memory model callable')
            future = self._executor.submit(self._run, job_id, model)
            self._futures[job_id] = future
            return future

    def configuration(self, job_id: str) -> dict:
        """Credential-free original configuration for rebuilding model adapters."""
        with self._db() as db:
            return json.loads(self._row(db, job_id)['config_json'])

    def resume(self, job_id: str, *, model: Callable | None = None,
               model_version: str | None = None) -> Future:
        with self._lock:
            if self._closed:
                raise RuntimeError('preparation service is closed')
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                return current
            with self._db() as db:
                row = self._row(db, job_id)
                if row['status'] not in ('FAILED', 'CANCELLED', 'INTERRUPTED'):
                    raise ValueError('job is not resumable')
                if _source(Path(row['book_path'])) != row['source_hash']:
                    raise ValueError('source changed; create a new preparation job')
                config = json.loads(row['config_json'])
                if model_version is not None and model_version != config['model_version']:
                    raise ValueError('resume model version conflicts with original configuration')
                expected = _hash(dict(book=row['book_path'], source=row['source_hash'],
                                      config=config, prepare_version=VERSION, job_version='rich-publication-v2-blocks'))
                if expected != row['config_hash']:
                    raise ValueError('preparation version changed; create a new job')
                if config['mode'] == 'fullbook' and not callable(model):
                    raise ValueError('resume requires an in-memory model callable')
                # Shared opening_ready.json means only one config may write per book.
                conflict = db.execute('SELECT job_id FROM preparation_jobs WHERE book_path=? AND job_id<>? AND status IN (%s)' %
                                      ','.join('?' for _ in ACTIVE), (row['book_path'], job_id, *ACTIVE)).fetchone()
                if conflict:
                    raise ValueError('another preparation for this book is active')
                self._update(db, job_id, status='QUEUED', stage='QUEUED', error=None,
                             result_id=None, retryable=False, needs_credentials=False)
            return self.start(job_id, model=model)

    def _run(self, job_id, model):
        claimed = False
        previous = None
        root = None
        try:
            # Database claim serializes all artifact writers for the same book,
            # including different service instances sharing this database.
            while not claimed:
                with self._db() as db:
                    row = self._row(db, job_id)
                    if row['status'] != 'QUEUED':
                        return json.loads(row['snapshot'])
                    other = db.execute('SELECT job_id FROM preparation_jobs WHERE book_path=? AND job_id<>? AND status IN (%s)' %
                                       ','.join('?' for _ in RUNNING), (row['book_path'], job_id, *RUNNING)).fetchone()
                    if not other:
                        self._update(db, job_id, status='INDEXING', stage='INDEXING', needs_credentials=False)
                        claimed = True
                if not claimed:
                    time.sleep(0.05)
            root = Path(row['book_path'])
            config = json.loads(row['config_json'])
            previous = _read_json(root / 'opening_ready.json')
            if _source(root) != row['source_hash']:
                raise ValueError('source changed')

            def cancelled():
                return self.get(job_id)['status'] == 'CANCEL_REQUESTED'

            def progress(detail):
                with self._db() as db:
                    state = json.loads(self._row(db, job_id)['snapshot'])
                    status = 'CANCEL_REQUESTED' if state['status'] == 'CANCEL_REQUESTED' else detail['stage']
                    updates = dict(detail)
                    if detail['unit_kind'] == 'block':
                        updates['coverage'] = dict(verified_blocks=detail['completed_units'],
                                                   expected_blocks=detail['total_units'],
                                                   complete=detail['completed_units'] == detail['total_units'])
                    self._update(db, job_id, status=status, **updates)

            prepared = prepare_book(root, model=model, resume=True, progress=progress,
                                    cancelled=cancelled, **config)
            if cancelled():
                raise PreparationCancelled()
            verified = verify_preparation(root, mode=config['mode'], target_chapter=config['target_chapter'])
            if (_source(root) != row['source_hash'] or not prepared.get('ready')
                    or not verified.get('ready') or verified.get('config') != prepared.get('config')):
                raise ValueError('preparation validation failed')
            cards = None
            if config['mode'] == 'fullbook':
                from core.engine.opening_distill import publish_fullbook_characters
                cards = publish_fullbook_characters(root, model, publisher=self._publisher,
                                                    progress=progress, cancelled=cancelled)
                if not cards:
                    raise ValueError('no rich characters published')
                verified = verify_preparation(root, mode=config['mode'], target_chapter=config['target_chapter'])
                if not verified.get('ready') or _source(root) != row['source_hash']:
                    raise ValueError('source changed before publication completion')
            with self._db() as db:
                if self._row(db, job_id)['status'] == 'CANCEL_REQUESTED':
                    raise PreparationCancelled()
                return self._update(db, job_id, status='READY', stage='READY',
                                    completed_units=1, total_units=1, unit_kind='preparation',
                                    coverage=verified.get('coverage'),
                                    result_id=verified['preparation_id'], error=None, retryable=False,
                                    published_characters=[{'character_id': c['id'], 'revision': c['revision']} for c in (cards or [])],
                                    gap_report={'rich_character_extraction': 'complete' if cards else 'not_applicable',
                                                'card_publication': 'complete' if cards else 'not_applicable',
                                                'readiness_scope': 'published_rich_cards' if cards else 'window_evidence'},
                                    character_counts={'entity_mentions': len(verified.get('entities', [])),
                                                      'verified_cards': len(cards) if cards is not None else None})
        except Exception as exc:
            if not claimed:
                raise
            # Failed/cancelled attempts never replace an older ready package.
            if previous and previous.get('ready') and root is not None:
                _atomic_json(root / 'opening_ready.json', previous)
            with self._db() as db:
                status = 'CANCELLED' if isinstance(exc, PreparationCancelled) else 'FAILED'
                return self._update(db, job_id, status=status, retryable=True,
                                    needs_credentials=json.loads(self._row(db, job_id)['config_json'])['mode'] == 'fullbook',
                                    error=None if status == 'CANCELLED' else _error(
                                        'PREPARATION_FAILED', 'Preparation failed; validated checkpoints can be resumed.', job_id))

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
            for job_id, future in self._futures.items():
                if not future.done():
                    self.cancel(job_id)
        self._executor.shutdown(wait=wait)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


__all__ = ['PreparationJobsService', 'ACTIVE', 'RUNNING', 'TERMINAL']
