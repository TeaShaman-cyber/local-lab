import json

import duckdb
from hardware_warehouse import FIELDS, init, parse_polls
from smart_events import import_smart, parse_smart


def full(seq, now, mode=2):
    data=dict.fromkeys(FIELDS,0)
    data.update(seq=seq, now=now, mode=mode, level=1, ack=1, ec_max=66,
                cpu_valid=1, package=68, hottest=67, post_level=128)
    return b"[date] EcPair full "+",".join(str(data[k]) for k in FIELDS).encode()+b"\n"


def records(tick):
    return (
        f"[date] SmartDecision tick={tick} reason=invalid_input level=1 request=128 set_ok=1\n"
        f"[date] SmartInputs tick={tick} valid=0 cpu_valid=1 epoch=1 package=68 hottest=67 ec_max=66 sensor0=66 selected=0\n"
        f"[date] SmartTiming tick={tick} age_ms=501 future=0 gap_ms=1000 ack_before=1 pending_after=0 observed_after=128\n"
        f"[date] SmartPoll tick={tick} unavailable\n"
    ).encode()


def parse(raw):
    return parse_smart(raw,[dict(zip(FIELDS,r)) for r in parse_polls(raw)])


def test_link_complete_group_and_retain_unavailable():
    raw=full(1,1000)+records(2001)+full(2,2000)
    rows=parse(raw)
    assert [r[1] for r in rows]==[2001]*4
    assert [r[3] for r in rows]==[2]*4
    assert json.loads(rows[-1][4])=={"unavailable":True}
    assert all(r[5]=="ok" for r in rows)
    assert raw[rows[0][0]:].startswith(b"[date] SmartDecision")


def test_never_link_across_gap_wrong_mode_or_partial_group():
    for raw in (
        full(1,1000)+records(2001)+full(3,2000),
        full(1,1000)+records(2001)+full(2,2000,3),
        full(1,1000)+records(2001).splitlines(keepends=True)[0]+full(2,2000),
        full(1,1000)+records(2001)+full(2,1500),
        full(1,1000)+records(2001).replace(b"level=1",b"level=4")+full(2,2000),
        full(1,1000)+records(2001)+records(2001)+full(2,2000),
    ):
        assert all(r[3] is None for r in parse(raw))


def test_malformed_and_unfinished_records_are_not_invented():
    raw=full(1,1000)+b"[date] SmartDecision tick=2001 reason=invalid_input\n"+full(2,2000)+b"[date] SmartTiming tick=3000"
    rows=parse(raw)
    assert len(rows)==1
    assert rows[0][3] is None and rows[0][4] is None and rows[0][5]=="malformed"


def test_import_is_idempotent_and_conflicting_bytes_rejected():
    import pytest
    raw=full(1,1000)+records(2001)+full(2,2000)
    with duckdb.connect() as c:
        init(c)
        import_smart(c,"run",parse(raw))
        import_smart(c,"run",parse(raw))
        assert c.execute("select count(*) from smart_events").fetchone()==(4,)
        changed=raw.replace(b"invalid_input",b"urgent_temp__")
        with pytest.raises(ValueError):
            import_smart(c,"run",parse(changed))
        assert c.execute("select count(*) from smart_events").fetchone()==(4,)


def test_trailing_group_link_is_enriched_on_append_and_not_erased():
    prefix=full(1,1000)+records(2001)
    with duckdb.connect() as c:
        init(c)
        import_smart(c,"run",parse(prefix))
        assert c.execute("select count(seq) from smart_events").fetchone()==(0,)
        import_smart(c,"run",parse(prefix+full(2,2000)))
        assert c.execute("select count(seq) from smart_events").fetchone()==(4,)
        import_smart(c,"run",parse(prefix))
        assert c.execute("select count(seq) from smart_events").fetchone()==(4,)


def test_portable_smart_events_are_optional_for_legacy_db(tmp_path):
    from hardware_snapshot import publish
    raw=full(1,1000)+records(2001)+full(2,2000)
    with duckdb.connect() as c:
        init(c)
        import_smart(c,"run",parse(raw))
        manifest=publish(c,tmp_path)
        assert manifest["tables"]["smart_events"]["rows"]==4
        path=tmp_path/manifest["generation"]/"smart_events.parquet"
        assert c.execute("select count(seq) from read_parquet(?)",[str(path)]).fetchone()==(4,)
        c.execute("drop table smart_events")
        legacy=publish(c,tmp_path)
        assert "smart_events" not in legacy["tables"]
