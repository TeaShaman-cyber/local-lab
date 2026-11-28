import json
import math
import xml.etree.ElementTree as ET

from acoustic_periodic import run_once, summarize
from acoustic_task import NS, task_xml


def test_expiry_prevents_capture(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("microphone opened")

    assert run_once(tmp_path, 100, "endpoint", forbidden, lambda: None, now=lambda: 100) == "expired"
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "expired"


def test_busy_capture_records_failure_without_trace(tmp_path):
    def busy(*args, **kwargs):
        raise RuntimeError("microphone already in use")

    assert run_once(tmp_path, 100, "endpoint", busy, lambda: None, now=lambda: 10) == "skipped_busy"
    assert not list(tmp_path.glob("trace-*.json"))


def test_summary_retains_diagnostic_features_not_waveform():
    rate = 8192
    x = [0.05 * math.sin(2 * math.pi * 76 * i / rate) for i in range(rate * 4)]
    s = summarize(x, rate, 1_000_000_000, {"before": {}, "after": {}, "first_qpc_ns": 1, "packets": [(1, 2, 3, 0)]})
    assert s["sample_count"] == len(x)
    assert any(abs(p["frequency_hz"] - 76) < 2 for p in s["stable_peaks"])
    assert "spectra" not in s and "envelope" not in s
    assert "packets" not in s["clock"]
    assert s["band_seconds"]


def test_task_is_interactive_bounded_and_does_not_save_wav():
    xml = ET.fromstring(task_xml("S-1-5-21-1", "2026-11-28T12:00:00", 1800000000))

    def q(tag):
        return xml.findtext(".//{" + NS + "}" + tag)

    assert q("Interval") == "PT5M"
    assert q("LogonType") == "InteractiveToken"
    assert q("RunLevel") == "LeastPrivilege"
    assert q("WakeToRun") == "false"
    assert q("MultipleInstancesPolicy") == "IgnoreNew"
    assert q("ExecutionTimeLimit") == "PT1M"
    assert "--save-wav" not in q("Arguments")


def test_success_publishes_only_compact_local_summary(tmp_path):
    rate = 8192

    def capture(seconds, raw, expected_endpoint):
        assert seconds == 15 and raw and expected_endpoint == "pinned"
        return [0.0] * (4 * rate), rate, 123, {"before": {}, "after": {}, "first_qpc_ns": 1}

    assert run_once(tmp_path, 100, "pinned", capture, lambda: None, now=lambda: 1) == "captured"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["status.json", "trace-123.json"]
    assert json.loads((tmp_path / "trace-123.json").read_text())["first_utc_ns"] == 123


def test_storage_gate_precedes_microphone(tmp_path):
    def denied():
        raise RuntimeError("budget")

    def forbidden(*args, **kwargs):
        raise AssertionError("microphone opened")

    assert run_once(tmp_path, 100, "pinned", forbidden, denied, now=lambda: 1) == "storage_paused"


def test_report_does_not_count_invalid_identity(tmp_path):
    from acoustic_report import report

    (tmp_path / "trace-123.json").write_text(
        json.dumps({"schema": 1, "first_utc_ns": 123, "sample_count": 48000, "sample_rate": 48000})
    )
    (tmp_path / "trace-456.json").write_text("{}")
    assert report(tmp_path) == {"captures": 1, "rejected": 1, "seconds": 1.0, "first_utc_ns": 123, "last_utc_ns": 123}


def test_pinned_capture_uses_explicit_device_not_current_default(monkeypatch):
    import acoustic_windows as w

    class Ole:
        def CoCreateInstance(self, *args):
            args[-1]._obj.value = 10

    calls = []

    def call(p, i, types, *args):
        calls.append((p.value, i))
        if p.value == 10 and i == 5:
            assert args[0] == "pinned"
            args[-1]._obj.value = 20
        elif p.value == 20 and i == 6:
            args[-1]._obj.value = 1
        elif p.value == 20 and i == 0:
            args[-1]._obj.value = 30
        elif p.value == 30 and i == 3:
            args[-1]._obj.value = 1
        else:
            raise AssertionError((p.value, i))

    monkeypatch.setattr(w, "call", call)
    monkeypatch.setattr(w, "release", lambda p: None)
    assert w.endpoint(Ole(), "pinned").value == 20
    assert (10, 4) not in calls
