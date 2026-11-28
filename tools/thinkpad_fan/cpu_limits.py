"""Passive Intel model 8E performance-limit telemetry and DuckDB import."""

import hashlib
import json
import time


def make_sample(start_ns, end_ns, start_tick, end_tick, package, cores):
    return dict(
        start_ns=start_ns, end_ns=end_ns, start_tick=start_tick, end_tick=end_tick, package=package, cores=cores
    )


def decode(s):
    p, cores = s["package"], s["cores"]
    if len(p) != 4 or len(cores) != 8 or any(len(c) != 6 for c in cores):
        raise ValueError("incomplete CPU sample")
    if [c[0] for c in cores] != list(range(8)):
        raise ValueError("incomplete logical CPU coverage")
    if any(type(v) is not int or not 0 <= v < 2**64 for v in p + [v for c in cores for v in c]):
        raise ValueError("invalid MSR")
    if not 0 < s["start_ns"] <= s["end_ns"] or not 0 <= s["start_tick"] <= s["end_tick"]:
        raise ValueError("invalid timestamp")
    if (
        s["end_ns"] - s["start_ns"] > 500_000_000
        or abs((s["end_ns"] - s["start_ns"]) - (s["end_tick"] - s["start_tick"]) * 1_000_000) > 32_000_000
    ):
        raise ValueError("invalid CPU sample interval")
    unit = 2 ** -(p[0] & 15)
    reason = 0
    for c in cores:
        reason |= c[1]
    return dict(
        reason_union=reason,
        pl1_active=bool(reason & (1 << 10)),
        pl2_active=bool(reason & (1 << 11)),
        thermal_active=bool(reason & 2),
        prochot_active=bool(reason & 1),
        pl1_w=(p[1] & 0x7FFF) * unit,
        pl2_w=((p[1] >> 32) & 0x7FFF) * unit,
        pl1_enabled=bool(p[1] & (1 << 15)),
        pl2_enabled=bool(p[1] & (1 << 47)),
    )


def decode_mmio(s):
    m = s.get("mmio")
    if "mmio" not in s:
        return None
    if not isinstance(m, dict):
        raise ValueError("invalid MMIO metadata")
    if m == {"status": "unavailable"}:
        return dict(
            status="unavailable",
            start_ns=None,
            end_ns=None,
            start_tick=None,
            end_tick=None,
            raw=None,
            unit=None,
            pl1_w=None,
            pl2_w=None,
            pl1_enabled=None,
            pl2_enabled=None,
            locked=None,
        )
    keys = ("start_ns", "end_ns", "start_tick", "end_tick", "raw", "unit")
    if m.get("status") != "ok" or any(type(m.get(k)) is not int for k in keys):
        raise ValueError("invalid MMIO metadata")
    a, b, t, u, raw, unit = (m[k] for k in keys)
    if not (
        s["end_ns"] <= a <= b <= s["start_ns"] + 500_000_000
        and s["end_tick"] <= t <= u
        and abs((b - s["start_ns"]) - (u - s["start_tick"]) * 1_000_000) <= 32_000_000
        and 0 <= raw < 2**64 - 1
        and 0 <= unit < 2**32
        and unit & 15 == s["package"][0] & 15
    ):
        raise ValueError("invalid MMIO value or acquisition interval")
    scale = 2 ** -(unit & 15)
    return dict(
        status="ok",
        start_ns=a,
        end_ns=b,
        start_tick=t,
        end_tick=u,
        raw=raw,
        unit=unit,
        pl1_w=(raw & 0x7FFF) * scale,
        pl2_w=((raw >> 32) & 0x7FFF) * scale,
        pl1_enabled=bool(raw & (1 << 15)),
        pl2_enabled=bool(raw & (1 << 47)),
        locked=bool(raw & (1 << 63)),
    )


def init_cpu_limits(c):
    c.execute("""create table if not exists cpu_limit_samples(
        batch_sha varchar, sample_index integer, start_ns bigint, end_ns bigint,
        start_tick bigint, end_tick bigint, package json, cores json, reason_union ubigint,
        pl1_active boolean, pl2_active boolean, thermal_active boolean, prochot_active boolean,
        pl1_w double, pl2_w double, pl1_enabled boolean, pl2_enabled boolean,
        primary key(batch_sha,sample_index))""")
    c.execute("""create table if not exists cpu_mmio_limits(
        batch_sha varchar, sample_index integer, status varchar,
        start_ns bigint, end_ns bigint, start_tick bigint, end_tick bigint,
        raw ubigint, unit ubigint, pl1_w double, pl2_w double,
        pl1_enabled boolean, pl2_enabled boolean, locked boolean,
        primary key(batch_sha,sample_index))""")
    c.execute("""create table if not exists cpu_limit_batches(
        sha varchar primary key, segment varchar unique, status varchar, n integer)""")


def import_cpu_limits(c, folder):
    path = folder / "cpu-limits.json"
    if not path.is_file():
        return
    if path.stat().st_size > 1024**2:
        raise ValueError("CPU telemetry exceeds bound")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    init_cpu_limits(c)
    prior = c.execute("select sha from cpu_limit_batches where segment=?", [folder.name]).fetchone()
    if prior:
        if prior[0] != sha:
            raise ValueError("conflicting CPU batch")
        return
    batch = json.loads(raw)
    samples = batch["samples"]
    status = batch["status"]
    if batch["schema"] != 1 or len(samples) > 121 or status not in ("ok", "unavailable", "partial"):
        raise ValueError("invalid CPU batch")
    if (
        (status == "ok" and not samples)
        or (status == "unavailable" and samples)
        or (status == "partial" and not samples)
    ):
        raise ValueError("inconsistent CPU batch")
    rows = []
    mmio_rows = []
    prev = -1
    for i, s in enumerate(samples):
        d = decode(s)
        m = decode_mmio(s)
        if m is not None:
            mmio_rows.append((sha, i, *m.values()))
        if s["start_tick"] <= prev:
            raise ValueError("nonmonotonic CPU samples")
        prev = s["end_tick"]
        rows.append(
            (
                sha,
                i,
                s["start_ns"],
                s["end_ns"],
                s["start_tick"],
                s["end_tick"],
                json.dumps(s["package"]),
                json.dumps(s["cores"]),
                *d.values(),
            )
        )
    c.execute("begin")
    try:
        if rows:
            c.executemany("insert into cpu_limit_samples values (" + ",".join(["?"] * 17) + ")", rows)
        if mmio_rows:
            c.executemany("insert into cpu_mmio_limits values (" + ",".join(["?"] * 14) + ")", mmio_rows)
        c.execute("insert into cpu_limit_batches values (?,?,?,?)", [sha, folder.name, status, len(rows)])
        c.execute("commit")
    except Exception:
        c.execute("rollback")
        raise


def try_import_cpu_limits(c, folder):
    try:
        import_cpu_limits(c, folder)
        return True
    except (ValueError, KeyError, TypeError, OSError) as e:
        print("CPU_LIMIT_IMPORT_REJECTED", folder.name, str(e)[:200], flush=True)
        return False


def collect_interval(seconds, reader_factory, sleep=time.sleep, monotonic=time.monotonic):
    """Keep the ETL window bounded; never substitute zero for a failed read."""
    end = monotonic() + seconds
    samples = []
    status = "ok"
    try:
        with reader_factory() as reader:
            while monotonic() < end and len(samples) < 120:
                sample = reader.sample()
                decode(sample)
                decode_mmio(sample)
                samples.append(sample)
                sleep(max(0, min(2, end - monotonic())))
    except (OSError, ValueError, RuntimeError) as e:
        status = "partial" if samples else "unavailable"
        print("CPU_LIMIT", status, str(e)[:200], flush=True)
    sleep(max(0, end - monotonic()))
    return {"schema": 1, "samples": samples, "status": status if samples else "unavailable"}
