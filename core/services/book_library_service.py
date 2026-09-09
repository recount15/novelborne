"""Playable library projection; originals and incomplete archives remain untouched."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.services.book_prepare_service import _atomic_json, _read_json, verify_preparation


def mark_book_played(book_dir: str | Path, session_id: str) -> dict[str, Any]:
    """Call only after the authoritative start transaction has committed."""
    if not str(session_id or '').strip():
        raise ValueError('committed session id required')
    preparation = verify_preparation(book_dir)
    if not preparation['ready']:
        raise ValueError('book preparation is not ready')
    marker = {'version': 1, 'played': True, 'session_id': str(session_id),
              'book_id': preparation['book_id'], 'source_hash': preparation['source_hash']}
    _atomic_json(Path(book_dir) / 'played_ready.json', marker)
    return marker


def list_playable_books(books_root: str | Path) -> list[dict[str, Any]]:
    root = Path(books_root)
    if not root.is_dir():
        return []
    result = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        marker = _read_json(directory / 'played_ready.json')
        if marker.get('played') is not True or not marker.get('session_id'):
            continue
        preparation = verify_preparation(directory)
        if not preparation['ready'] or marker.get('source_hash') != preparation.get('source_hash'):
            continue
        index = _read_json(directory / 'chapter_index.json')
        result.append({'book_id': preparation['book_id'], 'title': index.get('title') or index.get('name') or directory.name,
                       'book_dir': str(directory.resolve()), 'mode': preparation['mode'],
                       'preparation_id': preparation['preparation_id'], 'source_hash': preparation['source_hash'],
                       'played': True, 'ready': True, 'coverage': preparation['coverage']})
    return result
