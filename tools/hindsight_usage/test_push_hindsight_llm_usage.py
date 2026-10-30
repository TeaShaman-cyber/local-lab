import importlib.util
from pathlib import Path

path = Path(__file__).with_name('push_hindsight_llm_usage.py')
spec = importlib.util.spec_from_file_location('push_hindsight_llm_usage', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PAID = {
    'id': 'a1', 'provider': 'openai', 'model': 'mistral-small-3.2-24b-instruct',
    'operation': 'reflect', 'status': 'success', 'started_at': '2026-10-27T18:49:07+00:00',
    'input_tokens': 14564, 'output_tokens': 236, 'cached_tokens': None,
}
LOCAL = {
    'id': 'a2', 'provider': 'local', 'model': 'sentence-transformers/x',
    'operation': 'consolidation', 'status': 'success',
    'started_at': '2026-10-27T18:49:08+00:00', 'input_tokens': None,
}


def test_paid_call_emits_tokens_and_one_call_marker():
    lines = module.build_lines([PAID])
    assert any('metric=llm_input_tokens value=14564' in line for line in lines)
    assert any('metric=llm_output_tokens value=236' in line for line in lines)
    assert sum(1 for line in lines if line.startswith('metric=llm_calls')) == 1
    assert not any('llm_cached_tokens' in line for line in lines)


def test_local_provider_is_skipped():
    assert module.build_lines([LOCAL]) == []


def test_failed_call_without_tokens_emits_nothing():
    failed = dict(PAID, status='error', input_tokens=None, output_tokens=None)
    assert module.build_lines([failed]) == []


def test_cursor_excludes_old_rows_and_orders_newest_last():
    items = [
        dict(PAID, id='old', started_at='2026-10-27T18:00:00+00:00'),
        dict(PAID, id='mid', started_at='2026-10-27T18:49:07+00:00'),
        dict(PAID, id='new', started_at='2026-10-27T19:00:00+00:00'),
    ]
    fresh = module.select_new(items, '2026-10-27T18:49:07+00:00')
    assert [i['id'] for i in fresh] == ['new']


def test_cursor_none_returns_everything_ordered():
    items = [dict(PAID, id='b', started_at='2026-10-27T19:00:00+00:00'),
             dict(PAID, id='a', started_at='2026-10-27T18:00:00+00:00')]
    fresh = module.select_new(items, '')
    assert [i['id'] for i in fresh] == ['a', 'b']
