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


def test_handshake_sends_initialize_then_initialized_then_read():
    """Без второго шага app-server молчит — проверено probe."""
    msgs = codex_usage.handshake_messages()
    assert msgs[0]["method"] == "initialize"
    assert msgs[1]["method"] == "initialized"
    assert msgs[2]["method"] == "account/rateLimits/read"
    # initialized — нотификация: id быть не должно, иначе сервер ждёт ответа на неё.
    assert "id" not in msgs[1]


def test_collect_returns_the_read_result_and_ignores_notifications():
    stream = [
        '{"id": 1, "result": {"userAgent": "probe"}}',
        '{"method": "remoteControl/status/changed", "params": {"status": "disabled"}}',
        '{"id": 2, "result": {"rateLimits": {"planType": "plus"}, '
        '"rateLimitResetCredits": {"availableCount": 1}}}',
    ]
    snapshot = codex_usage.collect_from_app_server(runner=lambda messages: iter(stream))
    assert snapshot["rateLimits"]["planType"] == "plus"
    assert snapshot["rateLimitResetCredits"]["availableCount"] == 1


def test_collect_returns_none_when_the_source_fails():
    def boom(messages):
        raise OSError("codex.cmd not found")

    assert codex_usage.collect_from_app_server(runner=boom) is None


def test_publish_builds_loki_payload_with_stream_labels():
    sent = {}

    def sender(url, auth, payload):
        sent["url"] = url
        sent["auth"] = auth
        sent["payload"] = payload
        return 204

    code = codex_usage.publish_lines(
        ["metric=codex_session_used_percent value=31.0000"],
        sender=sender,
        url="http://loki.example/push",
        auth=("user", "pass"),
    )
    assert code == 0
    assert sent["url"] == "http://loki.example/push"
    assert sent["auth"] == ("user", "pass")

    stream = sent["payload"]["streams"][0]
    assert stream["stream"]["service_name"] == "openai-codex"
    assert stream["stream"]["log_type"] == "subscription"
    assert stream["stream"]["job"] == "codex-usage"
    assert stream["values"][0][1] == "metric=codex_session_used_percent value=31.0000"


def test_publish_failure_returns_4():
    def broken(url, auth, payload):
        raise OSError("connection refused")

    code = codex_usage.publish_lines(
        ["metric=a value=1"], sender=broken, url="http://x", auth=("u", "p")
    )
    assert code == 4


def test_main_propagates_publish_failure():
    """Если Loki не принял строки, CLI не должен рапортовать успех."""
    code = codex_usage.main(collect=lambda: PROBE, publish=lambda lines: 4)
    assert code == 4


def test_main_reports_success_after_publishing():
    published = []
    code = codex_usage.main(collect=lambda: PROBE, publish=lambda lines: published.append(lines) or 0)
    assert code == 0
    assert published and len(published[0]) > 0


def test_app_server_is_started_without_a_console_window(monkeypatch):
    """Каждые 5 минут всплывало окно cmd: `codex` — это npm-шим .cmd, и Popen
    создавал для него консоль. Флаг обязан быть, иначе фикс задачи на pythonw
    закрывает только половину причины."""
    import io
    import sys as _sys

    calls = []

    class FakeProc:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"")
            self.pid = 4242

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProc()

    monkeypatch.setattr(codex_usage.subprocess, "Popen", fake_popen)
    codex_usage._run_app_server(codex_usage.handshake_messages())

    # Функция запускает два процесса: сам app-server и taskkill для уборки.
    # Окно поднимает любой из них, поэтому проверяются все, а не последний.
    assert calls, "ни один процесс не запускался"
    for cmd, kwargs in calls:
        flags = kwargs.get("creationflags", 0)
        name = Path(cmd[0]).name if cmd else "?"
        if _sys.platform == "win32":
            assert flags & codex_usage._NO_WINDOW,                 f"{name} запущен без CREATE_NO_WINDOW — поднимет окно"
        else:
            assert flags == 0, f"{name}: на других платформах флаг нейтральный"


def test_error_message_does_not_crash_without_a_console(monkeypatch):
    """Под pythonw sys.stderr и sys.stdout равны None: print(..., file=None)
    уходит в sys.stdout и падает на None.write, то есть отказ источника
    превращался бы в AttributeError вместо честного «недоступен»."""
    monkeypatch.setattr(codex_usage.sys, "stderr", None)
    monkeypatch.setattr(codex_usage.sys, "stdout", None)

    def boom(messages):
        raise OSError("codex.cmd not found")

    assert codex_usage.collect_from_app_server(runner=boom) is None


def test_publish_failure_does_not_crash_without_a_console(monkeypatch):
    monkeypatch.setattr(codex_usage.sys, "stderr", None)
    monkeypatch.setattr(codex_usage, "_post_loki",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("loki down")))
    assert codex_usage.publish_lines(["line"]) != 0
