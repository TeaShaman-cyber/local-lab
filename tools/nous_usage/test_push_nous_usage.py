import importlib.util
from types import SimpleNamespace
from pathlib import Path

path = Path(__file__).with_name('push_nous_usage.py')
spec = importlib.util.spec_from_file_location('push_nous_usage', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_success_emits_known_credits():
    access = SimpleNamespace(
        purchased_credits_remaining=1.25,
        subscription_credits_remaining=2.5,
        total_usable_credits=3.75,
    )
    account = SimpleNamespace(source='account_api', fresh=True, error=None, paid_service_access_info=access)
    lines = module.build_lines(account, 123)
    assert 'metric=read_ok value=1' in lines
    assert 'metric=total_usable_credits_usd value=3.750000' in lines


def test_missing_credit_is_unknown_not_zero():
    account = SimpleNamespace(
        source='account_api', fresh=True, error=None,
        paid_service_access_info=SimpleNamespace(total_usable_credits=None),
    )
    lines = module.build_lines(account, 123)
    assert 'metric=total_usable_credits_known value=0' in lines
    assert not any(line.endswith('total_usable_credits_usd value=0.000000') for line in lines)


def test_stale_read_is_not_success():
    account = SimpleNamespace(source='account_api', fresh=False, error=None)
    lines = module.build_lines(account, 123)
    assert lines == ['metric=read_ok value=0', 'metric=sample_timestamp_seconds value=123']
