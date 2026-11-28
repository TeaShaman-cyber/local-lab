import struct

import pytest
from r17_row import Observer, qualify


class FakeTransport:
    def __init__(self):
        self.selector = 96
        self.locked = False
        self.synchronized = True
        self.steps = 0
        self.fail_at = None
        self.ambiguous = False
        self.write_log = []
        self.rows = [struct.pack("<5H", 2550, 2524, 149, 500, 25)] * 2
        self.level = 1
        self.atmx = 1
        self.read_count = 0

    def acquire(self):
        self.locked = True
        return True

    def release(self):
        assert self.locked
        self.locked = False

    def begin_restore(self):
        pass

    def synchronized_idle(self):
        assert self.locked
        return self.synchronized

    def step(self):
        assert self.locked and self.synchronized
        self.steps += 1
        if self.steps == self.fail_at:
            self.synchronized = not self.ambiguous
            raise OSError("injected failure")

    def read(self, offset):
        self.step()
        if offset == 0x81:
            return self.selector
        if offset == 0x2F:
            return self.level
        if offset == 0xC8:
            return self.atmx
        if offset == 0xCB:
            return 6
        assert self.selector == 0x56
        assert 0xA0 <= offset <= 0xA9
        self.read_count += 1
        return self.rows[(self.read_count - 1) // 10][offset - 0xA0]

    def write_selector(self, value):
        # Simulate a write whose effect may precede the reported failure.
        self.selector = value
        self.write_log.append(value)
        self.step()


def test_restores_and_returns_two_independent_rows():
    t = FakeTransport()
    result = Observer().sample(t)
    assert result.status == "complete"
    assert result.before == result.after == (1, 1, 6, 96)
    assert result.row_a == result.row_b == t.rows[0]
    assert t.selector == 96 and not t.locked
    assert t.write_log == [0x56, 96]
    assert qualify(result)["floor_pwm"] == 25


def test_each_known_idle_failure_restores_or_latches():
    baseline = FakeTransport()
    Observer().sample(baseline)
    for step in range(1, baseline.steps + 1):
        t = FakeTransport()
        t.fail_at = step
        obs = Observer()
        result = obs.sample(t)
        assert result.status != "complete"
        assert not t.locked
        if t.selector != 96 or result.status == "restore_unverified":
            assert obs.inhibited
            writes = t.write_log.copy()
            assert obs.sample(t).status == "inhibited"
            assert t.write_log == writes
        else:
            assert t.selector == 96


def test_ambiguous_failure_never_issues_further_transactions():
    baseline = FakeTransport()
    Observer().sample(baseline)
    for step in range(1, baseline.steps + 1):
        t = FakeTransport()
        t.fail_at = step
        t.ambiguous = True
        obs = Observer()
        result = obs.sample(t)
        assert result.status != "complete"
        assert not t.locked and obs.inhibited
        assert t.steps == step
        assert obs.sample(t).status == "inhibited"


def test_unsupported_state_does_not_switch_page():
    t = FakeTransport()
    t.level = 3
    result = Observer().sample(t)
    assert result.status == "guard_rejected"
    assert t.write_log == [] and not t.locked


def test_floor_can_agree_even_when_tach_changes():
    t = FakeTransport()
    t.rows[1] = struct.pack("<5H", 2550, 2550, 150, 500, 25)
    result = Observer().sample(t)
    q = qualify(result)
    assert q["floor_pwm"] == 25 and q["upper_pwm"] == 500
    assert q["processed_rpm"] is None and q["output_pwm"] is None
    assert q["quality"] == "partial_agreement"


def test_different_bounds_never_certify_floor():
    t = FakeTransport()
    t.rows[1] = struct.pack("<5H", 2550, 2524, 149, 500, 26)
    assert qualify(Observer().sample(t))["floor_pwm"] is None


def test_zero_is_not_missing_and_sentinel_is_not_zero():
    t = FakeTransport()
    t.rows = [struct.pack("<5H", 2550, 0, 0, 500, 358)] * 2
    q = qualify(Observer().sample(t))
    assert q["processed_rpm"] == 0 and q["output_pwm"] == 0 and q["floor_pwm"] == 358
    t = FakeTransport()
    t.rows = [b"\xff" * 10] * 2
    q = qualify(Observer().sample(t))
    assert q["processed_rpm"] is None and q["floor_pwm"] is None


def test_unavailable_lock_produces_no_io():
    t = FakeTransport()
    t.acquire = lambda: False
    assert Observer().sample(t).status == "lock_unavailable"
    assert not t.locked and t.steps == 0


def test_third_party_selector_change_is_not_overwritten():
    t = FakeTransport()
    read = t.read

    def changed(offset):
        if t.read_count == 20 and offset == 0x81:
            t.selector = 0x60 + 1
        return read(offset)

    t.read = changed
    obs = Observer()
    result = obs.sample(t)
    assert result.status == "restore_unverified" and obs.inhibited
    assert t.selector == 0x61 and t.write_log == [0x56]


@pytest.mark.parametrize("atmx", [0, 1])
def test_only_previously_observed_manual1_or_bios_config(atmx):
    t = FakeTransport()
    t.atmx = atmx
    assert Observer().sample(t).status == "complete"


def test_foreign_page_at_entry_is_not_changed():
    t = FakeTransport()
    t.selector = 0x62
    assert Observer().sample(t).status == "guard_rejected"
    assert t.selector == 0x62 and not t.write_log


def test_bounds_out_of_range_never_certify_pwm():
    t = FakeTransport()
    t.rows = [struct.pack("<5H", 2550, 2524, 149, 25, 358)] * 2
    q = qualify(Observer().sample(t))
    assert q["floor_pwm"] is None and q["upper_pwm"] is None and q["output_pwm"] is None
    assert q["processed_rpm"] == 2524


def test_known_idle_read_failure_restores_page_and_discards_partial_data():
    t = FakeTransport()
    t.fail_at = 10
    obs = Observer()
    result = obs.sample(t)
    assert result.status == "failed"
    assert t.selector == 96 and not t.locked and not obs.inhibited
    assert qualify(result)["quality"] == "unavailable"


def test_already_unsynchronized_transport_is_never_used():
    t = FakeTransport()
    t.synchronized = False
    obs = Observer()
    assert obs.sample(t).status == "transport_uncertain"
    assert obs.inhibited and not t.locked and t.steps == 0


def test_observation_store_is_idempotent_and_does_not_invent_missing_fields(tmp_path):
    import duckdb
    from hardware_warehouse import init
    from r17_row_events import import_rows, publish_record

    t = FakeTransport()
    t.rows[1] = struct.pack("<5H", 2550, 2550, 150, 500, 25)
    obs = Observer().sample(t)
    p = publish_record(tmp_path, obs, 100, 200, 100000000, 200000000, "fixture")
    assert p.is_file()
    with duckdb.connect() as c:
        init(c)
        import_rows(c, tmp_path)
        import_rows(c, tmp_path)
        assert c.execute("select count(*),min(floor_pwm),min(processed_rpm),min(quality) from r17_rows").fetchone() == (
            1,
            25,
            None,
            "partial_agreement",
        )
        assert c.execute("select row_a,row_b from r17_rows").fetchone() == (obs.row_a.hex(), obs.row_b.hex())
        assert c.execute("select begin_tick,end_tick,begin_ns,end_ns from r17_rows").fetchone() == (
            100,
            200,
            100000000,
            200000000,
        )
        from hardware_snapshot import publish

        manifest = publish(c, tmp_path / "portable")
        assert manifest["tables"]["r17_rows"]["rows"] == 1


def test_bad_record_never_reaches_database(tmp_path):
    import duckdb
    from hardware_warehouse import init
    from r17_row_events import import_rows, publish_record

    t = FakeTransport()
    obs = Observer().sample(t)
    p = publish_record(tmp_path, obs, 100, 200, 100000000, 200000000, "fixture")
    p.write_bytes(p.read_bytes().replace(b'"end_tick":200', b'"end_tick":201'))
    with duckdb.connect() as c:
        init(c)
        with pytest.raises(ValueError):
            import_rows(c, tmp_path)
        assert c.execute("select count(*) from r17_rows").fetchone() == (0,)


def test_live_preflight_uses_current_generation_after_reboot():
    from hardware_warehouse import FIELDS
    from r17_row_windows import live_level_one

    def poll(seq,tick):
        r=dict.fromkeys(FIELDS,0)
        r.update(seq=seq,now=tick,begin_ms=tick-100,end_ms=tick-10,ok=1,mode=3,level=1,post_level=1,ack=1,cpu_valid=1,package=65,hottest=67)
        return b"[28.11.2026 15:14:00] EcPair full "+",".join(str(r[k]) for k in FIELDS).encode()+b"\n"

    raw=poll(1,900000)+poll(2,901000)+b"[28.11.2026 15:14:00] Port backend: PawnIO test\n"+poll(1,1000)+poll(2,2000)+poll(3,3000)
    live_level_one(raw,3500)
    with pytest.raises(RuntimeError):
        live_level_one(raw,900000)
    with pytest.raises(RuntimeError):
        live_level_one(raw+b"[28.11.2026 15:14:01] Port backend: PawnIO test\n",3500)


def integrated_line(tick=100, status=0, a="f609ca099300f4011900", b="f609ca099300f4011900",
                    before="01000660", after="01000660", inhibited=0, shift=0):
    import datetime as dt
    stamp=dt.datetime(2026,11,28,17,35,57)
    ns=int(stamp.timestamp())*10**9+shift
    return f"[28.11.2026 17:35:57] EcPair r17 v1 {tick},{tick+100},{ns},{ns+100000000},{status},{before},{after},{a},{b},{inhibited}\n".encode()


def test_integrated_tpfan_rows_import_from_log_idempotently():
    import duckdb
    from hardware_warehouse import init
    from r17_row_events import import_tpfan_rows
    good=integrated_line()
    uncertain=integrated_line(60100,3,b="-",after="-",inhibited=1)
    with duckdb.connect() as c:
        init(c)
        import_tpfan_rows(c,good+uncertain,"run-a")
        import_tpfan_rows(c,good+uncertain,"run-a")
        assert c.execute("select status,floor_pwm,quality from r17_rows order by begin_tick").fetchall()==[
            ("complete",25,"repeatable"),("restore_unverified",None,"unavailable")]
        import_tpfan_rows(c,good[:-1],"run-a")
        assert c.execute("select count(*) from r17_rows").fetchone()==(2,)
        with pytest.raises(ValueError,match="conflicting"):
            import_tpfan_rows(c,integrated_line(a="f609ca099300f4011a00")+uncertain,"run-a")


def test_integrated_tpfan_row_does_not_qualify_unrestored_or_torn_data():
    import duckdb
    from hardware_warehouse import init
    from r17_row_events import import_tpfan_rows
    with duckdb.connect() as c:
        init(c)
        import_tpfan_rows(c,integrated_line(b="f609ca099300f4011a00"),"run-a")
        assert c.execute("select floor_pwm,quality from r17_rows").fetchone()==(None,"partial_agreement")
        import_tpfan_rows(c,integrated_line(shift=3600*10**9),"run-b")
        assert c.execute("select count(*) from r17_rows where quality='stale' and floor_pwm is null").fetchone()==(1,)
        with pytest.raises(ValueError,match="interior"):
            import_tpfan_rows(c,b"broken\r"+integrated_line(),"run-c")


def test_pending_startup_evidence_survives_before_any_poll(tmp_path):
    import duckdb
    from hardware_warehouse import init,import_polls,FIELDS
    path=tmp_path/"log"
    start=b"[28.11.2026 17:35:56] Port backend: PawnIO test\n"
    row=integrated_line(100,3,b="-",after="-",inhibited=1)
    with duckdb.connect() as c:
        init(c)
        path.write_bytes(start+row)
        import_polls(c,path)
        assert c.execute("select status from r17_rows").fetchall()==[("restore_unverified",)]
        vals=dict.fromkeys(FIELDS,0)
        vals.update(seq=1,now=300)
        path.write_bytes(start+row+b"[28.11.2026 17:35:57] EcPair full "+",".join(str(vals[k]) for k in FIELDS).encode()+b"\n")
        import_polls(c,path)
        assert c.execute("select count(*) from r17_rows").fetchone()==(1,)
