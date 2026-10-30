import importlib.util
from pathlib import Path

path = Path(__file__).with_name('push_timeweb_usage.py')
spec = importlib.util.spec_from_file_location('push_timeweb_usage', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_full_payload_emits_rubles():
    payload = {'finances': {'balance': 898.33, 'total_balance': 898.33, 'monthly_cost': 80,
                            'hourly_cost': 0.11, 'hours_left': 8219, 'total_paid': 3800}}
    lines = module.build_lines(payload, 100)
    assert 'metric=read_ok value=1' in lines
    assert 'metric=balance_rub value=898.3300' in lines
    assert 'metric=monthly_cost_rub value=80.0000' in lines


def test_missing_field_is_unknown_not_zero():
    lines = module.build_lines({'finances': {'balance': 5.0}}, 100)
    assert 'metric=balance_rub value=5.0000' in lines
    assert 'metric=monthly_cost_rub_known value=0' in lines
    assert not any(line.startswith('metric=monthly_cost_rub ') for line in lines)


def test_broken_payload_is_not_success():
    lines = module.build_lines({'unexpected': True}, 100)
    assert lines == ['metric=sample_timestamp_seconds value=100', 'metric=read_ok value=0']


def test_nan_is_rejected():
    lines = module.build_lines({'finances': {'balance': float('nan')}}, 100)
    assert 'metric=balance_rub_known value=0' in lines


def test_cost_payload_splits_ai_from_infrastructure():
    payload = {'services_costs': [
        {'type': 's3', 'total_cost': 79, 'info': {'name': 'backups'}},
        {'type': 's3_outgoing_traffic', 'total_cost': 1, 'info': {'name': ''}},
        {'type': 'ai_proxy_key', 'total_cost': 2.5, 'info': {'name': 'hermes'},
         'services': [{'type': 'token_output', 'cost': 1.7, 'amount': 25671, 'model_id': 259}]},
    ]}
    lines = module.build_cost_lines(payload, 100)
    assert 'metric=cost_read_ok value=1' in lines
    assert any(line.startswith('metric=finops_ai_cost_rub value=2.5') for line in lines)
    assert any(line.startswith('metric=finops_infra_cost_rub value=80.0') for line in lines)
    assert any('kind=token_output model_id=259' in line for line in lines)


def test_cost_payload_broken_is_not_success():
    lines = module.build_cost_lines({'services_costs': 'nope'}, 100)
    assert lines == ['metric=cost_sample_timestamp_seconds value=100', 'metric=cost_read_ok value=0']


def test_cost_total_missing_stays_zero_but_read_is_ok():
    lines = module.build_cost_lines({'services_costs': [{'type': 'ai_proxy_key', 'info': {'name': 'x'}}]}, 100)
    assert 'metric=cost_read_ok value=1' in lines
    assert any(line.startswith('metric=ai_cost_rub value=0.0') for line in lines)
