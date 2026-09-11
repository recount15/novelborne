# -*- coding: utf-8 -*-
"""Versioned, resumable preparation; coverage is verified evidence, not file counts.

Full-book preparation never silently falls back to a window or extractive anchors.
The injected model receives each complete bounded block, including an explicit
entity inventory. This verifies structural/evidence coverage, not semantic truth.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from core.engine.book_index import build_book_index, checksum
from core.engine.anchor_distiller import ANCHOR_FIELDS, _parse_model_output, validate_anchor

VERSION = 3
MODES = frozenset({'window', 'fullbook'})


def normalize_mode(mode: str = 'window') -> str:
    value = str(mode or 'window').strip().lower()
    if value not in MODES:
        raise ValueError('mode must be window or fullbook')
    return value


def _hash(value: Any) -> str:
    return checksum(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _extractive(text: str, limit: int = 600) -> str:
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    parts = [p for p in text.replace('！', '。\n').replace('？', '。\n').split('。') if p.strip()]
    result = '。'.join(parts[:3]).strip()
    return (result + '。' if result else text[:limit])[:limit]


def _sources(root: Path, index: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
    """Resolve local offsets and verify a gap-free partition of every chapter."""
    sources, blocks = [], []
    chapters = index.get('chapters') or []
    if not chapters:
        raise ValueError('book has no chapters')
    seen = set()
    for chapter in chapters:
        number = int(chapter['chapter_no'])
        if number in seen:
            raise ValueError('duplicate chapter number')
        seen.add(number)
        source = chapter['source']
        path = root / 'chapters' / f'{number:04d}.txt'
        text = path.read_text(encoding='utf-8')
        if not text.strip():
            raise ValueError(f'chapter {number} is empty')
        if checksum(text) != source['checksum']:
            raise ValueError(f'chapter {number} source changed')
        sources.append({'chapter_no': number, 'checksum': checksum(text), 'chars': len(text)})
        offset = int(source['start'])
        cursor = 0
        for leaf in [x for x in index['leaves'] if x['chapter_no'] == number]:
            start, end = int(leaf['source']['start']) - offset, int(leaf['source']['end']) - offset
            if start != cursor or end <= start or end > len(text):
                raise ValueError(f'chapter {number} block partition is incomplete')
            excerpt = text[start:end]
            if checksum(excerpt) != leaf['source']['checksum']:
                raise ValueError('block checksum mismatch')
            blocks.append({'block_id': leaf['id'], 'chapter_no': number,
                           'start': start, 'end': end, 'source_hash': checksum(excerpt), 'text': excerpt})
            cursor = end
        if cursor != len(text):
            raise ValueError(f'chapter {number} has uncovered text')
    # An explicit chapter inventory is authoritative: missing files fail above;
    # unindexed chapter files fail here instead of being silently omitted.
    files = {int(p.stem) for p in (root / 'chapters').glob('*.txt') if p.stem.isdigit()}
    if files != seen:
        raise ValueError('chapter inventory does not match chapter files')
    return sources, blocks


class _BlockValidationError(ValueError):
    """结构化、不含上游文本的块校验失败（稳定 code + 字段路径）。"""

    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


_ERROR_LIMIT = 100


def _record_block_error(package: dict, block: Mapping[str, Any], code: str,
                        field: str | None, message: str) -> None:
    """只记录自产的安全信息；设上限防止死网关把包撑爆。"""
    if len(package['errors']) < _ERROR_LIMIT:
        package['errors'].append({'block_id': block['block_id'], 'chapter_no': block['chapter_no'],
                                  'code': code, 'field': field, 'message': message,
                                  'retryable': True})


def _validate_block(value: Any, block: Mapping[str, Any]) -> dict:
    try:
        data = _parse_model_output(value)
    except (ValueError, TypeError, KeyError) as exc:
        raise _BlockValidationError('MODEL_OUTPUT_INVALID', '块输出无法解析为JSON对象') from exc
    try:
        anchor = validate_anchor(data.get('anchor'), block['text'], block['chapter_no'])
    except (ValueError, TypeError, KeyError) as exc:
        raise _BlockValidationError('ANCHOR_INVALID', str(exc), 'anchor') from exc
    if not isinstance(data.get('entities'), list):
        raise _BlockValidationError('ENTITIES_NOT_LIST', '每个块都必须返回明确的实体清单', 'entities')
    entities = []
    for index, raw in enumerate(data['entities']):
        if not isinstance(raw, Mapping):
            raise _BlockValidationError('ENTITY_NOT_OBJECT', f'实体第{index + 1}项必须是对象',
                                        f'entities[{index}]')
        name, kind, excerpt = (str(raw.get(k) or '').strip() for k in ('name', 'kind', 'excerpt'))
        if not name:
            raise _BlockValidationError('ENTITY_FIELDS_INVALID', f'实体第{index + 1}项缺少名称',
                                        f'entities[{index}].name')
        if kind not in {'character', 'place', 'organization', 'object'}:
            raise _BlockValidationError('ENTITY_FIELDS_INVALID', f'实体第{index + 1}项类型不合法',
                                        f'entities[{index}].kind')
        if not excerpt or excerpt not in block['text'] or name not in excerpt:
            raise _BlockValidationError('ENTITY_EXCERPT_MISMATCH',
                                        f'实体第{index + 1}项摘录必须逐字来自本块且包含名称',
                                        f'entities[{index}].excerpt')
        local = block['text'].index(excerpt)
        mention_id = _hash({'block': block['block_id'], 'source_hash': block['source_hash'],
                            'name': name, 'kind': kind, 'offset': local})
        entities.append({'mention_id': mention_id, 'identity_status': 'unresolved',
                         'name': name, 'kind': kind, 'excerpt': excerpt,
                         'chapter_no': block['chapter_no'], 'block_id': block['block_id'],
                         'start': block['start'] + local, 'end': block['start'] + local + len(excerpt),
                         'source_hash': block['source_hash']})
    return {'anchor': anchor, 'entities': entities}


def _block_prompt(block: Mapping[str, Any]) -> str:
    return ('仅依据本块完整原文，返回JSON对象 {"anchor":九字段对象,"entities":实体数组}。'
            'anchor严格九字段为 ' + ','.join(ANCHOR_FIELDS) + '。'
            'title/summary/world/ripple非空，events/characters/foreshadowing/quotes非空数组。'
            'quotes必须逐字连续引用本块。entities完整列出本块明确出现的人物、地点、组织、物件；'
            '每项为{name,kind,excerpt}，kind为character/place/organization/object，'
            'name必须原文出现，excerpt必须逐字且包含name。没有明确实体则返回空数组，不得杜撰。'
            f'\nchapter={block["chapter_no"]}\nblock_id={block["block_id"]}\n原文：\n{block["text"]}')


class PreparationCancelled(Exception):
    """Cooperative cancellation at a durable artifact boundary."""


def _write_chapter_anchor(root: Path, number: int, values: list[dict[str, Any]]) -> None:
    """一章全部块验证后落盘该章锚点（合并规则与开局管线 fullbook 分支一致）。

    基字段取该章首个块，events/characters/foreshadowing/quotes 按块序拼接并
    截断到 12 条；开局管线之后会用相同来源重写同一文件，故此写入只让阅读器
    在蒸馏进行中提前读到已完成章节，不改变引擎消费语义。
    """
    merged = dict(values[0])
    for key in ('events', 'characters', 'foreshadowing', 'quotes'):
        merged[key] = [item for value in values for item in value[key]][:12]
    anchor_dir = root / 'anchors'
    anchor_dir.mkdir(parents=True, exist_ok=True)
    target = anchor_dir / ('%04d.json' % int(number))
    temp = target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(target)


def prepare_book(book_dir: str | Path, *, leaf_chars: int = 1200, arc_size: int = 10,
                 opening_chapters: int = 3, model: Any = None, resume: bool = True,
                 mode: str = 'window', model_version: str = 'unspecified',
                 prompt_version: str = 'block-evidence-v1', target_chapter: int = 1,
                 schema_version: int = VERSION,
                 progress: Callable[[dict], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    def checkpoint(stage: str, completed: int = 0, total: int | None = None,
                   unit_kind: str = 'stage', **detail: Any) -> None:
        # Persistence callback failures must stop work, not silently lose events.
        if progress is not None:
            progress(dict(stage=stage, completed_units=completed, total_units=total,
                          unit_kind=unit_kind, **detail))
        if cancelled is not None and cancelled():
            raise PreparationCancelled('preparation cancelled')

    mode = normalize_mode(mode)
    root = Path(book_dir)
    checkpoint('INDEXING')
    index = build_book_index(root, leaf_chars=leaf_chars, arc_size=arc_size, resume=resume)
    sources, blocks = _sources(root, index)
    config = {'version': VERSION, 'mode': mode, 'leaf_chars': int(leaf_chars),
              'opening_chapters': int(opening_chapters), 'target_chapter': int(target_chapter), 'model_version': str(model_version),
              'prompt_version': str(prompt_version), 'schema_version': int(schema_version),
              'arc_size': int(arc_size)}
    source_hash = _hash(sources)
    preparation_id = _hash({'source_hash': source_hash, 'config': config})
    positions = [i for i, c in enumerate(index['chapters']) if c['chapter_no'] == int(target_chapter)]
    if not positions:
        raise ValueError('target chapter does not exist')
    selected = index['chapters'][positions[0]:positions[0] + max(0, int(opening_chapters))]
    opening = [{'chapter_no': c['chapter_no'], 'title': c['title'],
                'summary': _extractive((root / 'chapters' / f'{c["chapter_no"]:04d}.txt').read_text(encoding='utf-8')),
                'chars': c['chars'], 'checksum': c['source']['checksum']} for c in selected]
    package = {'version': VERSION, 'book_id': index['book_id'], 'index_id': index['root_id'],
               'mode': mode, 'config': config, 'source_hash': source_hash,
               'preparation_id': preparation_id, 'chapters': opening, 'stats': index['stats'],
               'extractive': mode == 'window', 'entities': [], 'blocks': [], 'errors': [],
               'coverage': {'expected_blocks': len(blocks), 'verified_blocks': 0, 'complete': False},
               'coverage_ready': False, 'identity_ready': False,
               'ready': False, 'verification': 'exact-source-evidence; not semantic completeness proof'}
    if mode == 'window':
        checkpoint('VALIDATING', len(opening), len(selected), 'chapter')
        package['ready'] = bool(opening)
        _atomic_json(root / 'opening_ready.json', package)
        return package
    cache_dir = root / 'preparation' / 'blocks'
    # 章级进度与逐章锚点落盘：块按章分组，一章全部块验证完成即把该章锚点
    # 写入 anchors/NNNN.json（合并规则与开局管线一致），蒸馏进行中阅读器
    # 就能读到已完成章节的锚点与活跃人物；开局管线之后会用相同来源重写。
    blocks_per_chapter: dict[int, int] = {}
    for item in blocks:
        blocks_per_chapter[item['chapter_no']] = blocks_per_chapter.get(item['chapter_no'], 0) + 1
    chapters_total = len(blocks_per_chapter)
    chapter_results: dict[int, list[dict[str, Any]]] = {}
    chapters_done = 0
    # Save each block immediately, so interruption loses at most the active call.
    checkpoint('DISTILLING', 0, len(blocks), 'block',
               chapters_done=0, chapters_total=chapters_total)
    for attempted, block in enumerate(blocks, 1):
        if cancelled is not None and cancelled():
            raise PreparationCancelled('preparation cancelled')
        cache_key = _hash({'block_id': block['block_id'], 'source_hash': block['source_hash'], 'config': config})
        path = cache_dir / (cache_key + '.json')
        cached = _read_json(path) if resume else {}
        try:
            clean = _validate_block(cached.get('result'), block)
        except (ValueError, TypeError, KeyError):
            try:
                if not callable(model):
                    raise ValueError('fullbook preparation requires a block model')
                raw = model(_block_prompt(block))
            except PreparationCancelled:
                raise
            except Exception:
                # Upstream exceptions can contain credentials or request payloads;
                # only our own safe classification is recorded.
                _record_block_error(package, block, 'MODEL_FAILED', None,
                                    '块蒸馏调用失败（网络或模型异常），可重试')
                checkpoint('DISTILLING', len(package['blocks']), len(blocks), 'block',
                           attempted_units=attempted, block_id=block['block_id'], verified=False,
                           chapters_done=chapters_done, chapters_total=chapters_total,
                           current_chapter=block['chapter_no'])
                continue
            try:
                clean = _validate_block(raw, block)
                _atomic_json(path, {'cache_key': cache_key, 'result': clean})
            except PreparationCancelled:
                raise
            except _BlockValidationError as exc:
                _record_block_error(package, block, exc.code, exc.field,
                                    f'块校验未通过（{exc.code}）')
                checkpoint('DISTILLING', len(package['blocks']), len(blocks), 'block',
                           attempted_units=attempted, block_id=block['block_id'], verified=False,
                           chapters_done=chapters_done, chapters_total=chapters_total,
                           current_chapter=block['chapter_no'])
                continue
            except (ValueError, TypeError, KeyError, OSError):
                _record_block_error(package, block, 'CHECKPOINT_FAILED', None,
                                    '块结果校验或落盘失败，可重试')
                checkpoint('DISTILLING', len(package['blocks']), len(blocks), 'block',
                           attempted_units=attempted, block_id=block['block_id'], verified=False,
                           chapters_done=chapters_done, chapters_total=chapters_total,
                           current_chapter=block['chapter_no'])
                continue
        package['blocks'].append({k: v for k, v in block.items() if k != 'text'} | {'cache_key': cache_key, 'result_hash': _hash(clean)})
        package['entities'].extend(clean['entities'])
        number = block['chapter_no']
        chapter_results.setdefault(number, []).append(clean['anchor'])
        if len(chapter_results[number]) == blocks_per_chapter[number]:
            chapters_done += 1
            _write_chapter_anchor(root, number, chapter_results[number])
        checkpoint('DISTILLING', len(package['blocks']), len(blocks), 'block',
                   attempted_units=attempted, block_id=block['block_id'], verified=True,
                   cache_key=cache_key, chapters_done=chapters_done,
                   chapters_total=chapters_total, current_chapter=number)
    verified = len(package['blocks'])
    complete = verified == len(blocks)
    package['coverage'].update(verified_blocks=verified, complete=complete)
    from core.services.character_evidence_service import resolve_character_identities
    identity_path = root / 'preparation' / (preparation_id + '.identities.json')
    checkpoint('RESOLVING_IDENTITIES')
    identity = resolve_character_identities(package['entities'], model,
                                            cached=_read_json(identity_path) if resume else None)
    _atomic_json(identity_path, identity)
    checkpoint('VALIDATING')
    package.update(coverage_ready=complete, identity_ready=identity['identity_ready'],
                   identity_report=identity, identity_hash=_hash(identity))
    package['ready'] = complete and bool(package['entities']) and identity['identity_ready']
    if complete and not package['entities']:
        package['errors'].append({'block_id': None, 'chapter_no': None,
                                  'code': 'NO_ENTITY_EVIDENCE', 'field': None,
                                  'message': 'fullbook 需要有原文证据的实体清单', 'retryable': True})
    _atomic_json(root / 'opening_ready.json', package)
    return package


def verify_preparation(book_dir: str | Path, *, mode: str | None = None,
                       target_chapter: int | None = None) -> dict[str, Any]:
    """Revalidate saved preparation against current source and block artifacts.

    This must run server-side immediately before starting, not trust client flags.
    It does not invoke a model, rebuild indexes, or mutate the book.
    """
    root = Path(book_dir)
    package = _read_json(root / 'opening_ready.json')
    errors = []
    try:
        actual_mode = normalize_mode(package.get('mode', ''))
        if mode is not None and normalize_mode(mode) != actual_mode:
            raise ValueError('preparation mode mismatch')
        if package.get('version') != VERSION:
            raise ValueError('preparation version missing or obsolete')
        index = _read_json(root / 'book_index.json')
        sources, blocks = _sources(root, index)
        # Detect changed chapter inventory even when old book_index remains intact.
        inventory = _read_json(root / 'chapter_index.json').get('chapters')
        if inventory is not None and [int(c['idx']) for c in inventory] != [s['chapter_no'] for s in sources]:
            raise ValueError('chapter inventory changed')
        if _hash(sources) != package['source_hash']:
            raise ValueError('preparation source hash is stale')
        if _hash({'source_hash': package['source_hash'], 'config': package['config']}) != package['preparation_id']:
            raise ValueError('preparation version hash mismatch')
        if package['config']['mode'] != actual_mode:
            raise ValueError('preparation config mode mismatch')
        if actual_mode == 'window':
            target = int(package['config']['target_chapter'])
            if target_chapter is not None and int(target_chapter) != target:
                raise ValueError('window target chapter mismatch')
            positions = [i for i, c in enumerate(index['chapters']) if c['chapter_no'] == target]
            if not positions:
                raise ValueError('window target chapter missing')
            selected = index['chapters'][positions[0]:positions[0] + max(0, int(package['config']['opening_chapters']))]
            if not selected or [c['chapter_no'] for c in package['chapters']] != [c['chapter_no'] for c in selected]:
                raise ValueError('window scope mismatch')
            for saved, current in zip(package['chapters'], selected):
                if saved['checksum'] != current['source']['checksum']:
                    raise ValueError('window chapter hash mismatch')
        if target_chapter is not None and int(target_chapter) not in {c['chapter_no'] for c in sources}:
            raise ValueError('target chapter does not exist')
        if actual_mode == 'fullbook':
            expected = {b['block_id']: b for b in blocks}
            records = package.get('blocks') or []
            if len(records) != len(expected) or {r['block_id'] for r in records} != set(expected):
                raise ValueError('fullbook block coverage incomplete')
            entities = []
            for record in records:
                block = expected[record['block_id']]
                cache_key = _hash({'block_id': block['block_id'], 'source_hash': block['source_hash'], 'config': package['config']})
                if record['cache_key'] != cache_key:
                    raise ValueError('block version hash mismatch')
                clean = _validate_block(_read_json(root / 'preparation' / 'blocks' / (cache_key + '.json')).get('result'), block)
                if _hash(clean) != record['result_hash']:
                    raise ValueError('block result hash mismatch')
                entities.extend(clean['entities'])
            if not entities or entities != package.get('entities'):
                raise ValueError('entity inventory missing or changed')
            from core.services.character_evidence_service import validate_identity_report
            identity = validate_identity_report(entities, package.get('identity_report') or {})
            if _hash(identity) != package.get('identity_hash'):
                raise ValueError('identity report hash mismatch')
            package['coverage_ready'] = True
            package['identity_ready'] = identity['identity_ready']
            if not identity['identity_ready']:
                # 增量身份就绪放宽：若 caller 指定了 target_chapter，且目标章节自身
                # 及其之前的章节内部提及均已完成裁决且无冲突（chapter_readiness 为 True），
                # 则允许开局流程进入，无需等待后文 50+ 章长尾冲突完成。
                ch_readiness = identity.get('chapter_readiness') or {}
                target_ok = False
                if target_chapter is not None and ch_readiness:
                    needed = [ch for ch in range(1, int(target_chapter) + 1)]
                    if all(ch_readiness.get(str(ch), {}).get('ready') for ch in needed):
                        target_ok = True
                if not target_ok:
                    raise ValueError('character identities remain unresolved')
        if not package.get('ready'):
            # 同样地，如果是因为全书身份未就绪但目标章节已就绪，放行
            if actual_mode == 'fullbook' and target_chapter is not None:
                ch_readiness = (package.get('identity_report') or {}).get('chapter_readiness') or {}
                needed = [ch for ch in range(1, int(target_chapter) + 1)]
                if needed and ch_readiness and all(ch_readiness.get(str(ch), {}).get('ready') for ch in needed):
                    pass
                else:
                    raise ValueError('preparation is not ready')
            else:
                raise ValueError('preparation is not ready')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    return {**package, 'ready': not errors, 'errors': errors or package.get('errors', [])}


class BookPrepareService:
    def prepare(self, book_dir: str | Path, **kwargs: Any) -> dict[str, Any]:
        return prepare_book(book_dir, **kwargs)

    def verify(self, book_dir: str | Path, **kwargs: Any) -> dict[str, Any]:
        return verify_preparation(book_dir, **kwargs)


__all__ = ['prepare_book', 'verify_preparation', 'normalize_mode', 'BookPrepareService',
           'PreparationCancelled']
