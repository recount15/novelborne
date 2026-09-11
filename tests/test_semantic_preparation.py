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


def test_stale_cached_identity_only_retries_its_own_pair(book):
    """块重蒸后个别缓存决策引文失配：只重裁该对，不作废整份缓存。

    真实模型上整份作废意味着重烧全部 O(n²) 配对调用（小时级）；
    resume 的经济性要求逐条复核。就绪门禁不变：仍须全覆盖通过校验。
    """
    first = prepare_book(book, mode='fullbook', model=model)
    assert first['ready'] and first['identity_report']['decisions']
    calls = []
    def counting(prompt):
        calls.append(prompt)
        return model(prompt)
    prepare_book(book, mode='fullbook', model=counting)
    assert [p for p in calls if p.startswith('IDENTITY_PAIR_V1')] == []
    report = json.loads(json.dumps(first['identity_report']))
    stale = report['decisions'][0]
    stale['evidence'][stale['left']] = '此引文已不在当前原文中'
    identity_files = list((book / 'preparation').glob('*.identities.json'))
    assert len(identity_files) == 1
    identity_files[0].write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    calls.clear()
    second = prepare_book(book, mode='fullbook', model=counting)
    identity_calls = [p for p in calls if p.startswith('IDENTITY_PAIR_V1')]
    assert len(identity_calls) == 1
    assert second['ready'] and second['identity_report']['identity_ready']


def test_identity_candidate_filtering_prunes_distant_different_names(book):
    """候选集过滤：不同名且无别名嫌疑/交叉提及的人物直接确定性分离，零模型调用。"""
    from core.services.character_evidence_service import resolve_character_identities
    entities = [
        {'mention_id': 'm_hero_1', 'name': '李青', 'kind': 'character', 'excerpt': '李青在北门按刀', 'chapter_no': 1},
        {'mention_id': 'm_hero_2', 'name': '李青', 'kind': 'character', 'excerpt': '李青进入客栈', 'chapter_no': 2},
        {'mention_id': 'm_waiter', 'name': '茶棚伙计', 'kind': 'character', 'excerpt': '茶棚伙计倒茶', 'chapter_no': 1},
        {'mention_id': 'm_stranger', 'name': '黑衣刺客', 'kind': 'character', 'excerpt': '黑衣刺客在屋檐伏击', 'chapter_no': 10},
    ]
    # 总对数: 4 * 3 // 2 = 6
    # 候选对: 李青1 vs 李青2 (同名); 其余 5 对皆为不同名且无交叉引文/子串
    calls = []
    def mock_model(prompt):
        calls.append(prompt)
        data = json.loads(prompt.split('\n', 2)[2])
        l, r = data['left'], data['right']
        return {'left': l['mention_id'], 'right': r['mention_id'], 'relation': 'same',
                'reason': '同一主角连贯行动',
                'evidence': {l['mention_id']: l['name'], r['mention_id']: r['name']}}

    report = resolve_character_identities(entities, model=mock_model)
    assert report['identity_ready']
    assert len(report['decisions']) == 6
    # 模型仅对同名候选对调用了一次
    assert len(calls) == 1
    # 其余 5 对全为确定性分离
    deterministic = [d for d in report['decisions'] if d.get('deterministic')]
    assert len(deterministic) == 5
    assert all(d['relation'] == 'different' for d in deterministic)
    assert all('确定性分离' in d['reason'] for d in deterministic)


def test_identity_candidate_includes_alias_and_cross_reference():
    """候选集过滤准则必须涵盖：子串别名与交叉提及。"""
    from core.services.character_evidence_service import resolve_character_identities
    entities = [
        {'mention_id': 'm_short', 'name': '白芷', 'kind': 'character', 'excerpt': '白芷递茶'},
        {'mention_id': 'm_full', 'name': '掌柜白芷', 'kind': 'character', 'excerpt': '掌柜白芷算账'},
        {'mention_id': 'm_cross', 'name': '伙计', 'kind': 'character', 'excerpt': '伙计听从白芷的吩咐'},
        {'mention_id': 'm_other', 'name': '路人甲', 'kind': 'character', 'excerpt': '路人甲在街头走过'},
    ]
    # 4 个实体 = 6 对:
    # 1. 白芷 vs 掌柜白芷: 子串关系 -> 候选
    # 2. 白芷 vs 伙计: 伙计的 excerpt 包含'白芷' (交叉提及) -> 候选
    # 3. 掌柜白芷 vs 伙计: 掌柜白芷与伙计无交叉，但同店
    # 4. 路人甲 vs 其它三者: 完全无关 -> 全部确定性分离
    calls = []
    def mock_model(prompt):
        calls.append(prompt)
        data = json.loads(prompt.split('\n', 2)[2])
        l, r = data['left'], data['right']
        return {'left': l['mention_id'], 'right': r['mention_id'], 'relation': 'different',
                'reason': '虽有名称关联但身份不同',
                'evidence': {l['mention_id']: l['name'], r['mention_id']: r['name']}}

    report = resolve_character_identities(entities, model=mock_model)
    assert report['identity_ready']
    assert len(report['decisions']) == 6
    # 验证路人甲相关的 3 对全部为确定性分离，未浪费模型调用
    luren_pairs = [d for d in report['decisions'] if 'm_other' in (d['left'], d['right'])]
    assert len(luren_pairs) == 3
    assert all(d.get('deterministic') for d in luren_pairs)
    assert all(d['relation'] == 'different' for d in luren_pairs)


def test_identity_model_retry_tolerates_transient_failures():
    """模型瞬态解析失败（例如返回非 JSON）时触发重试，重试成功后正常入账。"""
    from core.services.character_evidence_service import resolve_character_identities
    entities = [
        {'mention_id': 'm1', 'name': '李青', 'kind': 'character', 'excerpt': '李青在北门'},
        {'mention_id': 'm2', 'name': '李青', 'kind': 'character', 'excerpt': '李青进茶棚'},
    ]
    call_attempts = [0]
    def flaky_model(prompt):
        call_attempts[0] += 1
        if call_attempts[0] == 1:
            return "<html>502 Bad Gateway</html>"
        data = json.loads(prompt.split('\n', 2)[2])
        l, r = data['left'], data['right']
        return {'left': l['mention_id'], 'right': r['mention_id'], 'relation': 'same',
                'reason': '同一人物',
                'evidence': {l['mention_id']: l['name'], r['mention_id']: r['name']}}

    report = resolve_character_identities(entities, model=flaky_model)
    assert report['identity_ready']
    # 第一次失败，第二次重试成功
    assert call_attempts[0] == 2
    assert len(report['decisions']) == 1
    assert report['decisions'][0]['relation'] == 'same'


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


def test_incremental_identity_allows_early_chapter_verify(book):
    """增量身份就绪：当全书因后文未决冲突未整体就绪时，若目标开局章节已就绪，verify 放行。"""
    # 构造：全书满蒸馏，但身份阶段故意让后文发生 uncertain 冲突
    def partial_uncertain(prompt):
        result = model(prompt)
        if prompt.startswith('IDENTITY_PAIR_V1'):
            # 如果是第 2 或第 3 章的对，返回 uncertain
            data = json.loads(prompt.split('\n', 2)[2])
            l, r = data['left'], data['right']
            if l.get('chapter_no', 0) > 1 or r.get('chapter_no', 0) > 1:
                result['relation'] = 'uncertain'
                result['reason'] = '后文章节故意冲突'
        return result

    package = prepare_book(book, mode='fullbook', model=partial_uncertain)
    # 全书整体未就绪
    assert not package['ready']
    assert not package['identity_ready']
    # 不指定目标章节或者指定未就绪章节时，verify_preparation 拒绝
    assert not verify_preparation(book, mode='fullbook')['ready']
    assert not verify_preparation(book, mode='fullbook', target_chapter=2)['ready']
    # 但指定目标章节为第 1 章时，由于第 1 章内部已无冲突，verify_preparation 放行
    verified = verify_preparation(book, mode='fullbook', target_chapter=1)
    assert verified['ready']

