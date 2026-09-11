"""Evidence-constrained identity decisions and cutoff-only character extraction.

Model judgments are auditable hypotheses, not a proof of semantic truth. Missing
or contradictory pair decisions leave identities unresolved and block readiness.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from core.engine.anchor_distiller import _parse_model_output


def validate_identity_report(entities: list[dict], report: Mapping[str, Any]) -> dict:
    from core.services.book_prepare_service import _hash
    mentions = {e['mention_id']: e for e in entities if e['kind'] == 'character'}
    ids = sorted(mentions)
    required = {(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]}
    decisions = {}
    conflicts = []
    for raw in report.get('decisions', []):
        a, b = sorted([str(raw.get('left', '')), str(raw.get('right', ''))])
        if (a, b) not in required or (a, b) in decisions:
            raise ValueError('invalid or duplicate identity pair')
        relation = raw.get('relation')
        if relation not in {'same', 'different', 'uncertain'}:
            raise ValueError('invalid identity relation')
        evidence = raw.get('evidence')
        if not isinstance(evidence, dict) or set(evidence) != {a, b}:
            raise ValueError('identity decision requires evidence from both mentions')
        for key in (a, b):
            quote = evidence[key]
            if not isinstance(quote, str) or not quote or quote not in mentions[key]['excerpt']:
                raise ValueError('identity evidence is not exact source')
        if not str(raw.get('reason') or '').strip():
            raise ValueError('identity decision requires rationale')
        dec_entry = {'left': a, 'right': b, 'relation': relation,
                     'evidence': evidence, 'reason': raw['reason']}
        if raw.get('deterministic'):
            dec_entry['deterministic'] = True
        decisions[a, b] = dec_entry
    parent = {key: key for key in ids}
    def find(key):
        while parent[key] != key:
            key = parent[key]
        return key
    for (a, b), decision in decisions.items():
        if decision['relation'] == 'same':
            parent[find(b)] = find(a)
    for a, b in sorted(required):
        decision = decisions.get((a, b))
        if decision is None or decision['relation'] == 'uncertain':
            conflicts.append({'left': a, 'right': b, 'reason': 'identity unresolved'})
        elif decision['relation'] == 'different' and find(a) == find(b):
            conflicts.append({'left': a, 'right': b, 'reason': 'contradictory identity cycle'})
    conflicted = {find(c[k]) for c in conflicts for k in ('left', 'right')}
    groups = {}
    for key in ids:
        groups.setdefault(find(key), []).append(key)
    identities = []
    for root, members in sorted(groups.items()):
        identities.append({'identity_id': 'person_' + _hash(members)[:24], 'mention_ids': members,
                           'names': sorted({mentions[m]['name'] for m in members}),
                           'ready': root not in conflicted})
    # 章节级就绪评估：供开局定位按目标章节局部放宽门禁（避免全书 50+ 章全量等待）
    # 冲突归属准则：跨章提及冲突归属于较晚章节（max(ch_a, ch_b)），
    # 只要前 K 章涉及的所有提及均已相互裁决且无冲突，前 K 章即可安全开局。
    chapter_mentions: dict[int, list[str]] = {}
    mention_chapter: dict[str, int] = {}
    for m in entities:
        if m.get('kind') == 'character' and 'chapter_no' in m:
            ch_no = int(m['chapter_no'])
            mid = m['mention_id']
            chapter_mentions.setdefault(ch_no, []).append(mid)
            mention_chapter[mid] = ch_no

    chapter_conflicts: dict[int, list[dict]] = {ch: [] for ch in chapter_mentions}
    for c in conflicts:
        ch_l = mention_chapter.get(c['left'])
        ch_r = mention_chapter.get(c['right'])
        if ch_l is not None and ch_r is not None:
            affect_ch = max(ch_l, ch_r)
        elif ch_l is not None:
            affect_ch = ch_l
        elif ch_r is not None:
            affect_ch = ch_r
        else:
            affect_ch = None
        if affect_ch is not None and affect_ch in chapter_conflicts:
            chapter_conflicts[affect_ch].append(c)
        elif affect_ch is None:
            for ch_list in chapter_conflicts.values():
                ch_list.append(c)

    chapter_readiness: dict[str, dict[str, Any]] = {}
    for ch_no, ch_mids in sorted(chapter_mentions.items()):
        ch_conf = chapter_conflicts.get(ch_no, [])
        chapter_readiness[str(ch_no)] = {
            'chapter_no': ch_no,
            'ready': len(ch_conf) == 0,
            'conflicts_count': len(ch_conf),
            'mentions_count': len(ch_mids),
        }
    return {'version': 1, 'decisions': [decisions[k] for k in sorted(decisions)],
            'identities': identities, 'conflicts': conflicts,
            'identity_ready': not conflicts,
            'chapter_readiness': chapter_readiness,
            'verification': 'model-judged identity with exact evidence, not semantic proof'}


def _is_identity_candidate(left: dict, right: dict) -> bool:
    """判定两处人物提及是否存在同人/别名嫌疑（需模型裁决）。

    候选准则（满足其一即进入模型复核）：
    1. 同名（如“李青”与“李青”）；
    2. 名称互为子串（如“白芷”与“掌柜白芷”）；
    3. 编辑距离相似度 >= 0.5（简繁、别字、笔误）；
    4. 交叉提及：一方的名称出现在另一方的原文摘录中。

    不同名、非别名嫌疑且未在对方上下文出现的两个人物，在此阶段无需
    挥霍模型调用，直接由确定性规则判定为 different（宁分勿合，且有
    各自身份名称作为逐字引文证据）。
    """
    from difflib import SequenceMatcher
    na = str(left.get('name') or '').strip()
    nb = str(right.get('name') or '').strip()
    if not na or not nb:
        return True
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    if SequenceMatcher(None, na, nb).ratio() >= 0.5:
        return True
    ea = str(left.get('excerpt') or '')
    eb = str(right.get('excerpt') or '')
    if nb in ea or na in eb:
        return True
    return False


def _call_identity_pair_model_with_retry(model: Any, left: dict, right: dict, max_attempts: int = 3) -> dict | None:
    """调用模型裁决身份对，针对瞬态非 JSON 体/网络抖动进行有界重试。"""
    prompt = ('IDENTITY_PAIR_V1\n判断两个原文人物提及是same/different/uncertain。'
              '同名不是same的充分证据，但同名且摘录内容一致连贯（同一动作、场景或行为特征）可判same；'
              '姓名不同且摘录中没有同指或别名证据应判different（宁可分开，不得合并不同人物）；'
              '仅当证据直接冲突（同名但场景证据指向两个并存个体）且无法取舍时才判uncertain。'
              '返回{left:提及ID,right:提及ID,relation,reason,evidence:{提及ID:逐字引文}}。\n'
              + json.dumps({'left': left, 'right': right}, ensure_ascii=False))
    for attempt in range(1, max_attempts + 1):
        try:
            raw = _parse_model_output(model(prompt))
            checked = validate_identity_report([left, right], {'decisions': [raw]})
            return checked['decisions'][0]
        except Exception:
            if attempt >= max_attempts:
                return None
    return None


def resolve_character_identities(entities: list[dict], model: Any = None, *, cached: Mapping | None = None) -> dict:
    """Compare all mention pairs, including aliases; never merge by name alone.

    Each request is bounded to two excerpts. Valid cached decisions are reused;
    unresolved decisions are retried, and failures become explicit conflicts.
    """
    mentions = sorted((e for e in entities if e['kind'] == 'character'), key=lambda e: e['mention_id'])
    known = {}
    if cached:
        try:
            checked = validate_identity_report(entities, cached)
            # uncertain 不进缓存复用：它是“证据不足”的可修复裁决，换更强的
            # 模型或更完整的摘录后必须重裁；缓存复用会让弱模型造成的
            # uncertain 永久化（就绪门禁视 uncertain 为未解决冲突）。
            known = {(d['left'], d['right']): d for d in checked['decisions'] if d['relation'] != 'uncertain'}
        except (ValueError, TypeError, KeyError):
            # A re-distilled block changes mention excerpts; discarding the
            # whole cache over one stale decision re-burns every pair call on
            # real models (hours of work). Re-check decisions individually and
            # keep exactly those whose evidence still matches the current
            # excerpts; stale ones are re-adjudicated below. No gate change:
            # readiness still requires full pair coverage through
            # validate_identity_report.
            by_id = {e['mention_id']: e for e in mentions}
            seen = set()
            for raw in (cached.get('decisions') or []):
                if not isinstance(raw, dict):
                    continue
                pair = tuple(sorted([str(raw.get('left') or ''), str(raw.get('right') or '')]))
                if pair[0] == pair[1] or pair in seen or pair[0] not in by_id or pair[1] not in by_id:
                    continue
                try:
                    decision = validate_identity_report(
                        [by_id[pair[0]], by_id[pair[1]]], {'decisions': [raw]})['decisions'][0]
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
                seen.add(pair)
                if decision['relation'] != 'uncertain':
                    known[pair] = decision
    decisions = []
    for i, left in enumerate(mentions):
        for right in mentions[i + 1:]:
            key = (left['mention_id'], right['mention_id'])
            decision = known.get(key)
            if decision is None:
                if not _is_identity_candidate(left, right):
                    qa = left['name'] if left['name'] in left['excerpt'] else left['excerpt']
                    qb = right['name'] if right['name'] in right['excerpt'] else right['excerpt']
                    decision = {
                        'left': left['mention_id'],
                        'right': right['mention_id'],
                        'relation': 'different',
                        'evidence': {left['mention_id']: qa, right['mention_id']: qb},
                        'reason': f'不同名（"{left["name"]}"与"{right["name"]}"）且无交叉提及或别名嫌疑，确定性分离',
                        'deterministic': True
                    }
                elif callable(model):
                    decision = _call_identity_pair_model_with_retry(model, left, right, max_attempts=3)
            if decision:
                decisions.append(decision)
    return validate_identity_report(entities, {'decisions': decisions})


def extract_scoped_character_facts(model: Any, source: str, identities: list[dict]) -> list[dict]:
    """Reuse opening character extraction; accept only facts with exact evidence.

    Only caller-provided cutoff text enters either model call. Rich card output
    is advisory and never directly becomes an initial body/persona/state fact.
    """
    if not callable(model) or not source.strip() or not identities:
        return []
    from core.engine.opening_distill import _characters_extract_job
    cards = _characters_extract_job(model, 'cutoff-scoped opening', source)['cards']
    data = _parse_model_output(model('SCOPED_CHARACTER_FACTS_V1\n从原文提取当前人物初始事实，'
        '只允许提供的identity_id，不能推测未发生的事情。返回{facts:[{identity_id,field,value,excerpt,start,end}]}。'
        'value必须是excerpt中的逐字片段，start/end是原文字符偏移且原文[start:end]==excerpt。'
        'field只允许location/action/condition/appearance/relationship。无证据不填。\n'
        + json.dumps({'identities': identities, 'advisory_cards': cards, 'source': source}, ensure_ascii=False)))
    ids = {i['identity_id'] for i in identities}
    facts = []
    for fact in data.get('facts', []):
        if fact.get('identity_id') not in ids or fact.get('field') not in {'location', 'action', 'condition', 'appearance', 'relationship'}:
            raise ValueError('unknown character or initial fact field')
        start, end = fact.get('start'), fact.get('end')
        quote, value = fact.get('excerpt'), fact.get('value')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
            raise ValueError('invalid initial fact evidence offsets')
        if not isinstance(quote, str) or source[start:end] != quote or not isinstance(value, str) or not value or value not in quote:
            raise ValueError('initial fact is not exact evidence')
        facts.append({k: fact[k] for k in ('identity_id', 'field', 'value', 'excerpt', 'start', 'end')})
    return facts
