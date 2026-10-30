import importlib.util
from pathlib import Path

path = Path(__file__).with_name('push_session_usage.py')
spec = importlib.util.spec_from_file_location('push_session_usage', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SESSION = {
    'id': 'local-a347ac1f', 'title': 'memory rehab 2', 'model': 'deepseek/deepseek-v4.1-flash-1017',
    'input_tokens': 15019441, 'output_tokens': 1397890, 'cache_read_tokens': 1044996704,
    'api_call_count': 2388, 'last_activity_at': 1793133953,
}


def test_emits_all_counters_with_title():
    lines = module.build_lines([SESSION])
    assert any('metric=session_input_tokens value=15019441' in line for line in lines)
    assert all('title="memory rehab 2"' in line for line in lines)
    assert any('metric=session_api_calls value=2388' in line for line in lines)


def test_zero_counters_are_skipped():
    zero = dict(SESSION, output_tokens=0, cache_read_tokens=0)
    lines = module.build_lines([zero])
    assert not any('session_output_tokens' in line for line in lines)
    assert any('session_input_tokens' in line for line in lines)


def test_title_quotes_and_newlines_are_flattened():
    dirty = dict(SESSION, title='bad "name"\nwith break')
    lines = module.build_lines([dirty])
    assert 'title="bad \'name\' with break"' in lines[0]


def test_missing_db_returns_no_sessions(tmp_path):
    assert module.read_sessions(tmp_path / 'absent.db', 1000.0) == []


def test_read_sessions_filters_old_and_untitled(tmp_path):
    import sqlite3
    db = tmp_path / 'state.db'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE sessions (id TEXT, title TEXT, model TEXT, input_tokens INT,'
                ' output_tokens INT, cache_read_tokens INT, api_call_count INT, last_activity_at REAL)')
    now = 10_000_000.0            # 30 days is 2_592_000 seconds
    fresh_ts, stale_ts = now - 60, now - 40 * 86400
    con.executemany('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)', [
        ('local-fresh', 'fresh chat', 'm', 10, 1, 5, 2, fresh_ts),
        ('local-old', 'old chat', 'm', 10, 1, 5, 2, stale_ts),
        ('local-untitled', '', 'm', 10, 1, 5, 2, fresh_ts),
    ])
    con.commit()
    con.close()
    rows = module.read_sessions(db, now)
    assert [r['id'] for r in rows] == ['local-fresh']
