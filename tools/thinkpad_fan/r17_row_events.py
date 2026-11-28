"""Immutable local observation spool and additive DuckDB projection."""

import hashlib
import json
from pathlib import Path

from r17_row import Observation, qualify

MAX_RECORD = 8192
STATUSES = {
    "complete",
    "failed",
    "state_changed",
    "restore_unverified",
    "transport_uncertain",
    "guard_rejected",
    "lock_unavailable",
    "inhibited",
}
FIELDS = ("target_rpm", "processed_rpm", "output_pwm", "upper_pwm", "floor_pwm")
COLUMNS = (
    "sha,source,begin_tick,end_tick,begin_ns,end_ns,status,before_state,"
    "after_state,row_a,row_b,error,quality," + ",".join(FIELDS)
)


def encode(obs, begin_tick, end_tick, begin_ns, end_ns, source):
    return json.dumps(
        {
            "schema": 1,
            "source": source,
            "begin_tick": begin_tick,
            "end_tick": end_tick,
            "begin_ns": begin_ns,
            "end_ns": end_ns,
            "status": obs.status,
            "before": obs.before,
            "after": obs.after,
            "row_a": obs.row_a.hex() if obs.row_a is not None else None,
            "row_b": obs.row_b.hex() if obs.row_b is not None else None,
            "error": obs.error,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    d = json.loads(raw, object_pairs_hook=pairs)
    keys = {
        "schema",
        "source",
        "begin_tick",
        "end_tick",
        "begin_ns",
        "end_ns",
        "status",
        "before",
        "after",
        "row_a",
        "row_b",
        "error",
    }
    if not isinstance(d, dict) or set(d) != keys or type(d["schema"]) is not int or d["schema"] != 1:
        raise ValueError("invalid record schema")
    if not isinstance(d["source"], str) or not 1 <= len(d["source"]) <= 128:
        raise ValueError("invalid source")
    for k in ("begin_tick", "end_tick", "begin_ns", "end_ns"):
        if type(d[k]) is not int or not 0 <= d[k] < 2**63:
            raise ValueError("invalid time")
    if d["begin_tick"] > d["end_tick"] or d["begin_ns"] > d["end_ns"]:
        raise ValueError("reversed interval")
    if d["status"] not in STATUSES:
        raise ValueError("invalid status")
    if d["error"] is not None and (not isinstance(d["error"], str) or len(d["error"]) > 128):
        raise ValueError("invalid error")

    def state(key):
        v = d[key]
        if v is None:
            return None
        if not isinstance(v, list) or len(v) != 4 or any(type(x) is not int or not 0 <= x <= 255 for x in v):
            raise ValueError("invalid state")
        return tuple(v)

    def row(key):
        v = d[key]
        if v is None:
            return None
        if not isinstance(v, str) or len(v) != 20:
            raise ValueError("invalid row")
        b = bytes.fromhex(v)
        if len(b) != 10 or b.hex() != v:
            raise ValueError("noncanonical row")
        return b

    obs = Observation(d["status"], state("before"), state("after"), row("row_a"), row("row_b"), d["error"])
    if obs.status == "complete" and (
        obs.before is None
        or obs.before != obs.after
        or obs.before not in ((1, 0, 6, 96), (1, 1, 6, 96), (128, 1, 6, 96))
        or obs.row_a is None
        or obs.row_b is None
        or obs.error is not None
    ):
        raise ValueError("incomplete success record")
    return d, obs


def publish_record(root, obs, begin_tick, end_tick, begin_ns, end_ns, source):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    raw = encode(obs, begin_tick, end_tick, begin_ns, end_ns, source)
    if len(raw) > MAX_RECORD:
        raise ValueError("oversize observation")
    decode(raw)
    sha = hashlib.sha256(raw).hexdigest()
    p = root / (sha + ".json")
    if p.exists():
        if p.read_bytes() != raw:
            raise ValueError("conflicting observation")
        return p
    # Single producer only. Temp file is invisible to the warehouse reader.
    tmp = root / (sha + ".tmp")
    with tmp.open("xb") as f:
        f.write(raw)
    tmp.replace(p)
    return p


def init_rows(c):
    c.execute("""create table if not exists r17_rows(
        sha varchar primary key,source varchar,begin_tick bigint,end_tick bigint,
        begin_ns bigint,end_ns bigint,status varchar,before_state varchar,after_state varchar,
        row_a varchar,row_b varchar,error varchar,quality varchar,
        target_rpm integer,processed_rpm integer,output_pwm integer,upper_pwm integer,floor_pwm integer)""")


def import_rows(c, root):
    if not root.is_dir():
        return
    for p in sorted(root.glob("*.json")):
        with p.open("rb") as f:
            raw = f.read(MAX_RECORD + 1)
        sha = hashlib.sha256(raw).hexdigest()
        if len(raw) > MAX_RECORD or p.stem != sha:
            raise ValueError("invalid row evidence digest or size")
        d, obs = decode(raw)
        q = qualify(obs)
        # A long/clock-discontinuous acquisition is preserved but not qualified.
        if (
            d["end_tick"] - d["begin_tick"] > 2000
            or abs(d["end_ns"] - d["begin_ns"] - (d["end_tick"] - d["begin_tick"]) * 1000000) > 32000000
        ):
            q = {**dict.fromkeys(FIELDS), "quality": "stale"}
        c.execute(
            "insert into r17_rows values (" + ",".join("?" for _ in range(18)) + ") on conflict do nothing",
            [
                sha,
                d["source"],
                d["begin_tick"],
                d["end_tick"],
                d["begin_ns"],
                d["end_ns"],
                d["status"],
                json.dumps(d["before"]),
                json.dumps(d["after"]),
                d["row_a"],
                d["row_b"],
                d["error"],
                q["quality"],
                *(q[k] for k in FIELDS),
            ],
        )
