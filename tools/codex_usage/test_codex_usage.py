"""Слайс 1: снимок лимитов превращается в строки метрик.

Фикстура — реальный ответ `account/rateLimits/read` (probe 2026-11-01, codex-cli 0.144.1),
а не придуманная структура.

Запуск: uv run --no-project --with pytest python -m pytest -q tools/codex_usage
"""

import importlib.util
from pathlib import Path

path = Path(__file__).with_name("codex_usage.py")
spec = importlib.util.spec_from_file_location("codex_usage", path)
codex_usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codex_usage)

PROBE = {
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 27, "windowDurationMins": 300, "resetsAt": 1793555675},
        "secondary": {"usedPercent": 88, "windowDurationMins": 10080, "resetsAt": 1793792511},
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "planType": "plus",
        "rateLimitReachedType": None,
    },
    "rateLimitResetCredits": {"availableCount": 1, "credits": []},
}


def test_publishes_both_windows_from_probe_snapshot():
    lines = codex_usage.build_lines(PROBE)
    assert "metric=codex_session_used_percent value=27.0000" in lines
    assert "metric=codex_weekly_used_percent value=88.0000" in lines


def test_windows_are_matched_by_duration_not_by_position():
    """Если провайдер поменяет окна местами, смысл метрик не должен поехать."""
    swapped = {
        "rateLimits": {
            "primary": {"usedPercent": 88, "windowDurationMins": 10080, "resetsAt": 1793792511},
            "secondary": {"usedPercent": 27, "windowDurationMins": 300, "resetsAt": 1793555675},
        }
    }
    lines = codex_usage.build_lines(swapped)
    assert "metric=codex_session_used_percent value=27.0000" in lines
    assert "metric=codex_weekly_used_percent value=88.0000" in lines


def test_string_balance_becomes_a_number():
    """API присылает balance строкой; строка в unwrap читается как «нет данных»."""
    lines = codex_usage.build_lines(PROBE)
    assert "metric=codex_credits_balance value=0.0000" in lines
    assert "metric=codex_credits_has value=0" in lines


def test_reset_time_and_window_length_are_published():
    lines = codex_usage.build_lines(PROBE)
    assert "metric=codex_session_reset_unix value=1793555675" in lines
    assert "metric=codex_weekly_reset_unix value=1793792511" in lines
    assert "metric=codex_session_window_minutes value=300" in lines
    assert "metric=codex_weekly_window_minutes value=10080" in lines


def test_plan_limit_and_reset_credits_are_published():
    lines = codex_usage.build_lines(PROBE)
    # Тариф — label, а не значение: unwrap требует число.
    assert "metric=codex_plan value=1 plan=plus" in lines
    assert "metric=codex_limit_reached value=0" in lines
    assert "metric=codex_reset_credits_available value=1" in lines


def test_limit_reached_is_one_when_provider_says_so():
    reached = {
        "rateLimits": {
            "primary": {"usedPercent": 100, "windowDurationMins": 300},
            "rateLimitReachedType": "primary",
        }
    }
    assert "metric=codex_limit_reached value=1" in codex_usage.build_lines(reached)


def test_unknown_window_duration_is_not_published():
    """Окно с незнакомой длительностью — UNKNOWN, а не 0% и не догадка."""
    odd = {"rateLimits": {"primary": {"usedPercent": 50, "windowDurationMins": 60}}}
    lines = codex_usage.build_lines(odd)
    assert not any("used_percent" in line for line in lines)
    assert not any("window_minutes" in line for line in lines)


def test_missing_window_emits_nothing_for_that_window():
    only_session = {"rateLimits": {"primary": {"usedPercent": 40, "windowDurationMins": 300}}}
    lines = codex_usage.build_lines(only_session)
    assert "metric=codex_session_used_percent value=40.0000" in lines
    assert not any("weekly" in line for line in lines)


def test_missing_credentials_exit_2_and_publish_nothing():
    """Нет доступа — код 2 и пустая публикация. Ноль вместо этого читался бы как «всё свободно»."""
    published = []
    code = codex_usage.main(collect=lambda: None, publish=published.append)
    assert code == 2
    assert published == []
