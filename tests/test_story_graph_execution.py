from core.engine.story_graph import check_events, transition, build_graph
import pytest


def test_causal_order():
    a = {'event_id': 'a'}
    b = {'event_id': 'b', 'prerequisites': ['a']}
    assert check_events([a, b])['ok']
    assert not check_events([b, a])['ok']


def test_cycles_self_and_unresolved():
    assert not check_events([{'event_id': 'a', 'prerequisites': ['a']}])['ok']
    assert not check_events([{'event_id': 'a', 'prerequisites': ['b']},
                             {'event_id': 'b', 'prerequisites': ['a']}])['ok']
    assert not check_events([{'event_id': 'a', 'status': 'planned'},
                             {'event_id': 'b', 'prerequisites': ['a']}])['ok']


def test_duplicate_and_round_bounds():
    assert not check_events([{'event_id': 'a'}, {'event_id': 'a'}])['ok']
    assert not check_events([{'event_id': 'a', 'round': 4, 'latest_round': 3}])['ok']
    assert not check_events([{'event_id': 'a', 'earliest_round': 3}])['ok']


def test_lifecycle_and_copy():
    event = {'event_id': 'a', 'status': 'planned'}
    assert transition(event, 'active')['status'] == 'active'
    assert event['status'] == 'planned'
    with pytest.raises(ValueError):
        transition(event, 'resolved')
    state = {'story_graph': {'events': [event]}}
    graph = build_graph(state)
    graph['events'][0]['status'] = 'cancelled'
    assert event['status'] == 'planned'
