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
