"""Real database integration, synthetic native reader boundary; no device access."""
import json
import sys
from types import SimpleNamespace

import duckdb
import hardware_warehouse as h
import pytest


def trace_fixture(tmp_path, monkeypatch, *, drift=0, events=None):
    path = tmp_path / "hardware.etl"
    path.write_bytes(b"synthetic ETL identity")
    (tmp_path / "anchors.json").write_text(json.dumps([
        {"tick_before": 100, "tick_after": 102, "unix_ns": 1000000000},
        {"tick_before": 200, "tick_after": 202, "unix_ns": 1100000000 + drift},
    ]))
    if events is None:
        event = dict(timestamp="1970-01-01T00:00:01.123456789Z",
                     provider_id="test", provider_name="MSNT_SystemTrace",
                     event_id=1, version=0, task=0, opcode=0, level=0,
                     properties={"EventsLost": 3, "BuffersLost": 2, "raw": b"\x00\xff"})
        events = [event]
    calls = []
    def reader(name):
        calls.append(name)
        return SimpleNamespace(read_all=lambda: [
            SimpleNamespace(to_dict=lambda v=v: v) for v in events])
    monkeypatch.setitem(sys.modules, "pyetwkit._core", SimpleNamespace(EtlReader=reader))
    return calls


@pytest.mark.parametrize("drift,expected", [(0, 1), (32000000, 1), (32000001, 0)])
def test_trace_retains_loss_and_only_qualified_anchor(tmp_path, monkeypatch, drift, expected):
    calls = trace_fixture(tmp_path, monkeypatch, drift=drift)
    with duckdb.connect() as c:
        h.init(c)
        h.import_trace(c, tmp_path)
        h.import_trace(c, tmp_path)
        assert len(calls) == 1
        assert c.execute("select events_lost,buffers_lost from capture_batches").fetchall() == [(3, 2)]
        ns, raw = c.execute("select timestamp_ns,event from events").fetchone()
        assert ns == 1123456789
        assert json.loads(raw)["properties"]["raw"] == {"encoding": "base64", "data": "AP8="}
        assert c.execute("select count(*) from trace_anchors").fetchone() == (expected,)
        assert c.execute("select count(*) from imports").fetchone() == (1,)


def test_empty_trace_is_retained_without_import(tmp_path, monkeypatch):
    trace_fixture(tmp_path, monkeypatch, events=[])
    with duckdb.connect() as c:
        h.init(c)
        with pytest.raises(RuntimeError, match="trace empty"):
            h.import_trace(c, tmp_path)
        assert c.execute("select count(*) from imports").fetchone() == (0,)
        assert (tmp_path / "hardware.etl").exists()


def test_trace_rolls_back_all_rows_on_late_failure(tmp_path, monkeypatch):
    trace_fixture(tmp_path, monkeypatch)
    with duckdb.connect() as c:
        h.init(c)
        c.execute("drop table trace_anchors")
        with pytest.raises(duckdb.Error):
            h.import_trace(c, tmp_path)
        for table in ("imports", "events", "capture_batches"):
            assert c.execute("select count(*) from " + table).fetchone() == (0,)


def test_oversized_trace_never_reaches_reader(tmp_path, monkeypatch):
    calls = trace_fixture(tmp_path, monkeypatch)
    with (tmp_path / "hardware.etl").open("wb") as f:
        f.truncate(32 * 1024**2 + 1)
    with duckdb.connect() as c:
        h.init(c)
        with pytest.raises(ValueError, match="input bound"):
            h.import_trace(c, tmp_path)
        assert calls == []


def event_xml(channel):
    return (f'<Events><Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
            f'<System><Provider Name="Netwtw06"/><EventID>6000</EventID>'
            f'<TimeCreated SystemTime="1970-01-01T00:00:01.123456789Z"/>'
            f'<EventRecordID>1</EventRecordID><Channel>{channel}</Channel></System>'
            f'</Event></Events>').encode("utf-16")


def test_eventlog_cap_does_not_advance_watermark_and_retry_deduplicates(monkeypatch):
    monkeypatch.setattr(h, "CHANNELS", {"System": ("Netwtw06",)})
    monkeypatch.setattr(h, "LIMIT", 1)
    now = 10**18
    monkeypatch.setattr(h.time, "time_ns", lambda: now)
    queries = []
    def checked(args, timeout):
        queries.append(args)
        assert timeout == 45
        return event_xml("System")
    monkeypatch.setattr(h, "checked", checked)
    with duckdb.connect() as c:
        h.init(c)
        h.import_windows(c)
        now += 10**9
        monkeypatch.setattr(h, "LIMIT", 2)
        h.import_windows(c)
        now += 10**9
        h.import_windows(c)
        assert c.execute("select lookback_ms,n,capped from windows_batches order by query_ns").fetchall() == [
            (604800000, 1, True), (604800000, 1, False), (3601000, 1, False)]
        assert c.execute("select count(*) from windows_events").fetchone() == (1,)
        assert 'Provider[@Name="Netwtw06"]' in queries[0][3]


def test_eventlog_transaction_rolls_back_if_batch_cannot_commit(monkeypatch):
    monkeypatch.setattr(h, "CHANNELS", {"System": ()})
    monkeypatch.setattr(h, "checked", lambda *args: event_xml("System"))
    with duckdb.connect() as c:
        h.init(c)
        c.execute("drop table windows_batches")
        # Fail the batch INSERT, not the earlier SELECT, with a constraint.
        c.execute("create table windows_batches(channel varchar, query_ns bigint, "
                  "lookback_ms bigint,n bigint check(n=0),capped boolean)")
        with pytest.raises(duckdb.ConstraintException):
            h.import_windows(c)
        assert c.execute("select count(*) from windows_events").fetchone() == (0,)


@pytest.mark.parametrize("data", [b"", event_xml("Other"), b"x"*(32*1024**2+1)])
def test_eventlog_empty_or_invalid_payload(monkeypatch, data):
    monkeypatch.setattr(h, "CHANNELS", {"System": ()})
    monkeypatch.setattr(h, "checked", lambda *args: data)
    with duckdb.connect() as c:
        h.init(c)
        if data:
            with pytest.raises((ValueError, RuntimeError)):
                h.import_windows(c)
            assert c.execute("select count(*) from windows_batches").fetchone() == (0,)
        else:
            h.import_windows(c)
            assert c.execute("select n,capped from windows_batches").fetchall() == [(0, False)]
        assert c.execute("select count(*) from windows_events").fetchone() == (0,)


@pytest.mark.parametrize("status", [[], {}, True, None, 1])
def test_r17_malformed_status_is_a_validation_error(status):
    from r17_row import Observation
    from r17_row_events import decode, encode
    record = json.loads(encode(Observation("failed"), 1, 2, 1000000, 2000000, "fixture"))
    record["status"] = status
    with pytest.raises(ValueError, match="invalid status"):
        decode(json.dumps(record).encode())
