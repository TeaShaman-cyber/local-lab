import json

import duckdb
import pytest
from cpu_limits import decode, import_cpu_limits, make_sample


def fixture():
    cores = [[i, 0x400 if i == 0 else 0, 0x80000000, 0x1800, 100, 200] for i in range(8)]
    return make_sample(
        1000000000, 1001000000, 1000, 1001, [3, (120 | (1 << 15)) | ((352 | (1 << 15)) << 32), 123, 0x80000000], cores
    )


def test_decode_separates_live_and_sticky_flags_and_limits():
    s = fixture()
    d = decode(s)
    assert d["pl1_active"] and not d["pl2_active"]
    assert not d["thermal_active"]
    assert d["pl1_w"] == 15 and d["pl2_w"] == 44
    assert d["pl1_enabled"] and d["pl2_enabled"]
    s["cores"][0][1] = 1 << 26
    assert not decode(s)["pl1_active"]


def test_incomplete_thread_scan_is_not_a_valid_zero():
    s = fixture()
    s["cores"].pop()
    with pytest.raises(ValueError):
        decode(s)


def test_import_is_idempotent_and_preserves_raw(tmp_path):
    f = tmp_path / "segment-1"
    f.mkdir()
    (f / "cpu-limits.json").write_text(json.dumps({"schema": 1, "samples": [fixture()], "status": "ok"}))
    with duckdb.connect() as c:
        import_cpu_limits(c, f)
        import_cpu_limits(c, f)
        assert c.execute("select count(*),sum(pl1_w),bool_and(pl1_active) from cpu_limit_samples").fetchone() == (
            1,
            15,
            True,
        )
        assert c.execute("select count(*) from cpu_limit_batches").fetchone()[0] == 1
        assert c.execute("select json_array_length(cores) from cpu_limit_samples").fetchone()[0] == 8


def test_failed_batch_preserves_unknown_not_zero(tmp_path):
    f = tmp_path / "segment-2"
    f.mkdir()
    (f / "cpu-limits.json").write_text(json.dumps({"schema": 1, "samples": [], "status": "unavailable"}))
    with duckdb.connect() as c:
        import_cpu_limits(c, f)
        assert c.execute("select status,n from cpu_limit_batches").fetchone() == ("unavailable", 0)
        assert c.execute("select count(*) from cpu_limit_samples").fetchone()[0] == 0


def test_snapshot_contains_cpu_limit_tables(tmp_path):
    from hardware_snapshot import publish
    from hardware_warehouse import init

    with duckdb.connect() as c:
        init(c)
        f = tmp_path / "segment-3"
        f.mkdir()
        (f / "cpu-limits.json").write_text(json.dumps({"schema": 1, "samples": [fixture()], "status": "ok"}))
        import_cpu_limits(c, f)
        manifest = publish(c, tmp_path / "snapshot")
        assert manifest["tables"]["cpu_limit_samples"]["rows"] == 1
        assert manifest["tables"]["cpu_limit_batches"]["rows"] == 1


def test_bad_cpu_sidecar_does_not_block_other_batches(tmp_path):
    from cpu_limits import try_import_cpu_limits

    bad = tmp_path / "segment-4"
    bad.mkdir()
    (bad / "cpu-limits.json").write_text("{bad")
    good = tmp_path / "segment-5"
    good.mkdir()
    (good / "cpu-limits.json").write_text(json.dumps({"schema": 1, "samples": [fixture()], "status": "ok"}))
    with duckdb.connect() as c:
        assert try_import_cpu_limits(c, bad) is False
        assert try_import_cpu_limits(c, good) is True
        assert c.execute("select count(*) from cpu_limit_samples").fetchone()[0] == 1
        assert (bad / "cpu-limits.json").read_text() == "{bad"


def mmio_fixture(s):
    s["mmio"] = {
        "status": "ok",
        "start_ns": s["end_ns"],
        "end_ns": s["end_ns"] + 1000000,
        "start_tick": s["end_tick"],
        "end_tick": s["end_tick"] + 1,
        "raw": 0x42819800DD8078,
        "unit": 3,
    }
    return s


def test_independent_mmio_limit_is_not_msr_limit(tmp_path):
    from cpu_limits import decode_mmio

    s = mmio_fixture(fixture())
    s["package"][1] = 0x42819800DD8198
    assert decode(s)["pl1_w"] == 51
    assert decode_mmio(s)["pl1_w"] == 15
    f = tmp_path / "segment-1"
    f.mkdir()
    (f / "cpu-limits.json").write_text(json.dumps({"schema": 1, "samples": [s], "status": "ok"}))
    with duckdb.connect() as c:
        import_cpu_limits(c, f)
        import_cpu_limits(c, f)
        assert c.execute(
            "select m.pl1_w,s.pl1_w,m.pl2_w,m.status from cpu_mmio_limits m join cpu_limit_samples s using(batch_sha,sample_index)"
        ).fetchall() == [(15, 51, 51, "ok")]


def test_legacy_and_unavailable_are_not_zero(tmp_path):
    from cpu_limits import decode_mmio

    a = fixture()
    assert decode_mmio(a) is None
    b = fixture()
    b["start_ns"] += 2000000
    b["end_ns"] += 2000000
    b["start_tick"] += 2
    b["end_tick"] += 2
    b["mmio"] = {"status": "unavailable"}
    f = tmp_path / "segment-1"
    f.mkdir()
    (f / "cpu-limits.json").write_text(json.dumps({"schema": 1, "samples": [a, b], "status": "ok"}))
    with duckdb.connect() as c:
        import_cpu_limits(c, f)
        assert c.execute("select count(*) from cpu_limit_samples").fetchone() == (2,)
        assert c.execute("select sample_index,status,pl1_w,raw from cpu_mmio_limits").fetchall() == [
            (1, "unavailable", None, None)
        ]


@pytest.mark.parametrize("change", [{"unit": 4}, {"raw": 2**64 - 1}, {"start_ns": 999999999}, {"end_tick": 2000}])
def test_invalid_mmio_does_not_become_valid_measurement(change):
    from cpu_limits import decode_mmio

    s = mmio_fixture(fixture())
    s["mmio"].update(change)
    with pytest.raises(ValueError):
        decode_mmio(s)


def test_platform_reader_keeps_msr_when_mmio_unavailable():
    from cpu_limits_windows import PlatformReader

    class Good:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sample(self):
            return fixture()

    class Bad(Good):
        def __enter__(self):
            raise OSError("unavailable")

    with PlatformReader(Good, Bad) as r:
        s = r.sample()
        assert decode(s)["pl1_w"] == 15
        assert s["mmio"] == {"status": "unavailable"}


def test_mmio_snapshot_is_separate_and_preserves_msr(tmp_path):
    from hardware_snapshot import publish
    from hardware_warehouse import init

    f = tmp_path / "segment-1"
    f.mkdir()
    s = mmio_fixture(fixture())
    s["package"][1] = 0x42819800DD8198
    (f / "cpu-limits.json").write_text(json.dumps({"schema": 1, "samples": [s], "status": "ok"}))
    with duckdb.connect() as c:
        init(c)
        import_cpu_limits(c, f)
        manifest = publish(c, tmp_path / "snapshot")
        assert manifest["tables"]["cpu_mmio_limits"]["rows"] == 1
        assert manifest["tables"]["cpu_limit_samples"]["rows"] == 1


def test_mmio_read_error_keeps_msr_and_disables_repeat_reads():
    from cpu_limits_windows import PlatformReader

    class Good:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sample(self):
            return fixture()

    class Broken(Good):
        calls = 0

        def read_limits(self):
            self.calls += 1
            raise OSError("unavailable")

    broken = Broken()
    with PlatformReader(Good, lambda: broken) as r:
        for _ in range(2):
            assert decode(r.sample())["pl1_w"] == 15
        assert broken.calls == 1
