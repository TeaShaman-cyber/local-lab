import importlib.util
from pathlib import Path

path = Path(__file__).with_name('push_deepinfra_usage.py')
spec = importlib.util.spec_from_file_location('push_deepinfra_usage', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

EMPTY_USAGE = {'months': []}


def test_negative_stripe_balance_is_published_raw_not_as_available_money():
    """The exact production shape: suspended account, negative stripe_balance."""
    lines = module.build_lines(
        {'stripe_balance': -5.0, 'suspended': True, 'recent': 5.0}, EMPTY_USAGE
    )
    assert 'metric=stripe_balance_usd value=-5.0000' in lines
    assert not any(line.startswith('metric=balance_usd') for line in lines)
    assert 'metric=suspended value=1' in lines


def test_positive_stripe_balance_is_owed():
    lines = module.build_lines({'stripe_balance': 3.5}, EMPTY_USAGE)
    assert 'metric=owed_usd value=3.5000' in lines
    assert 'metric=stripe_balance_usd value=3.5000' in lines


def test_missing_balance_emits_no_balance_metric():
    lines = module.build_lines({'suspended': False}, EMPTY_USAGE)
    assert not any('stripe_balance_usd' in line for line in lines)
    assert 'metric=suspended value=0' in lines


def test_usage_costs_are_published_per_pricing_type():
    usage = {'months': [{'items': [
        {'model': {'model_name': 'DeepSeek V4.1'}, 'pricing_type': 'input_tokens',
         'cost': 12.5, 'units': 1000},
    ]}]}
    lines = module.build_lines({}, usage)
    assert any('metric=cost_cents value=12.5000' in line and 'model=DeepSeek_V4.1' in line
               for line in lines)


def test_usage_period_interval_is_published_for_window_reconciliation():
    usage = {
        'months': [{'period': '2026.10', 'interval': {'fr': 1790838000000, 'to': 1793516399999},
                    'items': []}]
    }
    lines = module.build_lines({}, usage)
    assert 'metric=usage_period_start_unix value=1790838000.000 period=2026.10' in lines
    assert 'metric=usage_period_end_unix value=1793516399.999 period=2026.10' in lines


def test_missing_usage_interval_emits_no_period_metrics():
    lines = module.build_lines({}, {'months': [{'period': '2026.10', 'items': []}]})
    assert not any('usage_period_' in line for line in lines)
