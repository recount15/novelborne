"""Read-only chapter-start intents. No DB, indexing, model, or session side effects."""
from __future__ import annotations

import json
from pathlib import Path

from core.engine.book_index import checksum
from core.services.book_prepare_service import _hash, verify_preparation


def read_book_source(book_dir, chapter_no):
    root = Path(book_dir).resolve(strict=True)
    inventory_path = (root / 'chapter_index.json').resolve(strict=True)
    if not inventory_path.is_relative_to(root):
        raise ValueError('chapter inventory escapes book')
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    rows = inventory.get('chapters', [])
    numbers = [row.get('idx') for row in rows]
    if (not numbers or any(type(n) is not int or n < 1 for n in numbers)
            or numbers != sorted(set(numbers)) or type(chapter_no) is not int or chapter_no not in numbers):
        raise ValueError('invalid chapter inventory or selection')
    files = {int(p.stem) for p in (root / 'chapters').glob('*.txt') if p.stem.isdigit()}
    if files != set(numbers):
        raise ValueError('chapter inventory mismatch')
    texts, sources = {}, []
    for number in numbers:
        path = (root / 'chapters' / f'{number:04d}.txt').resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError('chapter escapes book')
        text = path.read_text(encoding='utf-8')
        texts[number] = text
        sources.append({'chapter_no': number, 'checksum': checksum(text), 'chars': len(text)})
    return {'book_id': root.name, 'source_hash': _hash(sources), 'texts': texts, 'inventory': inventory}


def prepare_chapter_start(book_dir, book_id, chapter_no, expected_source_hash=None):
    source = read_book_source(book_dir, chapter_no)
    if book_id != source['book_id']:
        raise ValueError('book identity mismatch')
    if expected_source_hash is not None and expected_source_hash != source['source_hash']:
        raise ValueError('source changed; select chapter again')
    facts = []
    prepared = verify_preparation(book_dir, target_chapter=chapter_no)
    if prepared.get('ready'):
        for fact in prepared.get('entities', []):
            number, start, end = fact.get('chapter_no'), fact.get('start'), fact.get('end')
            if (type(number) is int and number < chapter_no and number in source['texts']
                    and type(start) is int and type(end) is int
                    and 0 <= start < end <= len(source['texts'][number])):
                facts.append(dict(fact))
    evidence = {'book_id': book_id, 'chapter_no': chapter_no, 'source_hash': source['source_hash']}
    selection = {'kind': 'chapter_start', 'id': _hash(evidence), 'timepoint': 'before',
                 'evidence': evidence, 'knowledge_cutoff': {'chapter_no': chapter_no, 'offset': 0},
                 'initial_facts': facts, 'temporal_precision': 'chapter_boundary',
                 'initialization_policy': 'evidence-before-cutoff-only; no inferred future state'}
    return {**evidence, 'scene_selection': selection}
