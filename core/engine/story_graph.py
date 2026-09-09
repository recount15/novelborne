"""Validate proposed events in causal order without changing committed facts."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence


STATUSES = frozenset({'planned', 'active', 'resolved', 'blocked', 'cancelled'})
TRANSITIONS = {
    'planned': {'active', 'blocked', 'cancelled'},
    'active': {'resolved', 'blocked', 'cancelled'},
    'blocked': {'active', 'cancelled'},
    'resolved': set(),
    'cancelled': set(),
}


def check_events(events: Sequence[Mapping[str, Any]], completed: Sequence[str] = ()) -> dict[str, Any]:
    done = set(completed)
    errors = []
    seen = set()
    rows = list(events)
    ids = {str(row.get('event_id') or row.get('id') or '')
           for row in rows if isinstance(row, Mapping)}
    for row in rows:
        if not isinstance(row, Mapping):
            errors.append({'error': 'invalid_event'})
            continue
        eid = str(row.get('event_id') or row.get('id') or '')
        if not eid or eid in seen or eid in done:
            errors.append({'event_id': eid, 'error': 'missing_or_duplicate_id'})
        seen.add(eid)
        prerequisites = row.get('prerequisites', [])
        if not isinstance(prerequisites, (list, tuple)):
            errors.append({'event_id': eid, 'error': 'invalid_prerequisites'})
            prerequisites = []
        for req in prerequisites:
            req = str(req)
            if req not in done:
                code = 'self_dependency' if req == eid else (
                    'future_prerequisite' if req in ids else 'missing_prerequisite')
                errors.append({'event_id': eid, 'error': code, 'prerequisite': req})
        try:
            round_no = int(row['round']) if 'round' in row else None
            for field, code, comparison in (
                ('earliest_round', 'too_early', lambda a, b: a < b),
                ('latest_round', 'too_late', lambda a, b: a > b),
            ):
                if row.get(field) is not None:
                    if round_no is None:
                        errors.append({'event_id': eid, 'error': 'missing_round'})
                    elif comparison(round_no, int(row[field])):
                        errors.append({'event_id': eid, 'error': code})
        except (TypeError, ValueError):
            errors.append({'event_id': eid, 'error': 'invalid_round'})
        status = row.get('status', 'resolved')
        if status not in STATUSES:
            errors.append({'event_id': eid, 'error': 'invalid_status'})
        if status == 'resolved' and not any(e.get('event_id') == eid for e in errors):
            done.add(eid)
    return {'ok': not errors, 'ok_for_play': True, 'errors': errors, 'warnings': errors, 'repair_plan': repair_plan(rows, errors), 'completed': sorted(done)}


def repair_plan(events: Sequence[Mapping[str, Any]], errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert graph violations into non-blocking repair actions."""
    plan = []
    for error in errors or ():
        code = error.get('error')
        if code in {'future_prerequisite', 'missing_prerequisite'}:
            plan.append({'action': 'defer_event', 'event_id': error.get('event_id'), 'reason': code, 'prerequisite': error.get('prerequisite')})
        elif code in {'too_early', 'too_late'}:
            plan.append({'action': 'adjust_event_window', 'event_id': error.get('event_id'), 'reason': code})
        elif code in {'duplicate_id', 'missing_or_duplicate_id'}:
            plan.append({'action': 'mark_uncertain', 'event_id': error.get('event_id'), 'reason': code})
        else:
            plan.append({'action': 'record_warning', 'event_id': error.get('event_id'), 'reason': code})
    return plan


def transition(event: Mapping[str, Any], status: str) -> dict[str, Any]:
    before = event.get('status', 'planned')
    if status != before and status not in TRANSITIONS.get(before, set()):
        raise ValueError(f'Illegal event transition: {before} -> {status}')
    return {**deepcopy(dict(event)), 'status': status}


def build_graph(state: Mapping[str, Any]) -> dict[str, Any]:
    graph = state.get('story_graph')
    if not isinstance(graph, Mapping):
        graph = {}
    return deepcopy({'events': list(graph.get('events') or []),
                     'completed': list(graph.get('completed') or [])})
