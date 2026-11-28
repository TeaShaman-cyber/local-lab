"""Immutable local observation spool and additive DuckDB projection."""

import datetime as dt
import hashlib
import json
import re
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


def _unique_fields(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


_RECORD_KEYS = {
    "schema", "source", "begin_tick", "end_tick", "begin_ns", "end_ns",
    "status", "before", "after", "row_a", "row_b", "error",
}


def _validate_metadata(d):
    if not isinstance(d, dict) or set(d) != _RECORD_KEYS or type(d["schema"]) is not int or d["schema"] != 1:
        raise ValueError("invalid record schema")
    if not isinstance(d["source"], str) or not 1 <= len(d["source"]) <= 128:
        raise ValueError("invalid source")
    for k in ("begin_tick", "end_tick", "begin_ns", "end_ns"):
        if type(d[k]) is not int or not 0 <= d[k] < 2**63:
            raise ValueError("invalid time")
    if d["begin_tick"] > d["end_tick"] or d["begin_ns"] > d["end_ns"]:
        raise ValueError("reversed interval")
    if not isinstance(d["status"], str) or d["status"] not in STATUSES:
        raise ValueError("invalid status")
    if d["error"] is not None and (not isinstance(d["error"], str) or len(d["error"]) > 128):
        raise ValueError("invalid error")


def _decode_state(v):
    if v is None:
        return None
    if not isinstance(v, list) or len(v) != 4 or any(type(x) is not int or not 0 <= x <= 255 for x in v):
        raise ValueError("invalid state")
    return tuple(v)


def _decode_row(v):
    if v is None:
        return None
    if not isinstance(v, str) or len(v) != 20:
        raise ValueError("invalid row")
    b = bytes.fromhex(v)
    if len(b) != 10 or b.hex() != v:
        raise ValueError("noncanonical row")
    return b


def _validate_success(obs):
    if obs.status == "complete" and (
        obs.before is None
        or obs.before != obs.after
        or obs.before not in ((1, 0, 6, 96), (1, 1, 6, 96), (128, 1, 6, 96))
        or obs.row_a is None
        or obs.row_b is None
        or obs.error is not None
    ):
        raise ValueError("incomplete success record")


def decode(raw):
    d = json.loads(raw, object_pairs_hook=_unique_fields)
    _validate_metadata(d)
    obs = Observation(
        d["status"], _decode_state(d["before"]), _decode_state(d["after"]),
        _decode_row(d["row_a"]), _decode_row(d["row_b"]), d["error"],
    )
    _validate_success(obs)
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
    c.execute("create table if not exists r17_log_positions(sha varchar primary key,line_sha varchar not null)")


def import_rows(c, root):
    if not root.is_dir():
        return
    for p in sorted(root.glob("*.json")):
        with p.open("rb") as f:
            raw = f.read(MAX_RECORD + 1)
        sha = hashlib.sha256(raw).hexdigest()
        if len(raw) > MAX_RECORD or p.stem != sha:
            raise ValueError("invalid row evidence digest or size")
        insert_record(c,raw,sha)


def insert_record(c,raw,sha,wall_ok=True):
    d, obs = decode(raw)
    q = qualify(obs)
    # A long/clock-discontinuous acquisition is preserved but not qualified.
    if (
        not wall_ok or d["end_tick"] - d["begin_tick"] > 2000
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

# The producer emits UTC ns and boot-relative tick for the same interval.
# Never infer missing values from the last accepted sample.
TPFAN_ROW=re.compile(rb"\[([^\r\n]+)\] EcPair r17 v1 (\d+),(\d+),(\d+),(\d+),([0-5]),([0-9a-f]{8}|-),([0-9a-f]{8}|-),([0-9a-f]{20}|-),([0-9a-f]{20}|-),([01])\r?\n")
TPFAN_STATUS=("complete","guard_rejected","failed","restore_unverified","transport_uncertain","state_changed")


def import_tpfan_rows(c, raw, source):
    from hardware_warehouse import STARTUP
    if b" EcPair r17 v1 " not in raw:
        return 0
    offset=origin=0
    generation=b"legacy-prefix"
    seen=set()
    count=0
    lines=raw.splitlines(keepends=True)
    for i,line in enumerate(lines):
        if not line.endswith(b"\n"):
            if i!=len(lines)-1:
                raise ValueError("interior non-LF log record")
            break
        if STARTUP.fullmatch(line):
            generation=line
            if generation in seen:
                raise ValueError("ambiguous repeated R17 startup identity")
            seen.add(generation)
            origin=offset
        if b" EcPair r17 v1 " in line:
            m=TPFAN_ROW.fullmatch(line)
            if not m:
                raise ValueError("malformed integrated R17 observation")
            g=m.groups()
            begin,end,begin_ns,end_ns,status=map(int,g[1:6])
            before,after,a,b=(None if x==b"-" else bytes.fromhex(x.decode()) for x in g[6:10])
            inhibited=g[10]==b"1"
            name=TPFAN_STATUS[status]
            if inhibited and name=="complete":
                name="inhibited"
            obs=Observation(name,tuple(before) if before is not None else None,
                            tuple(after) if after is not None else None,a,b,
                            "inhibited" if inhibited else None)
            record=encode(obs,begin,end,begin_ns,end_ns,"TPFan-R17-row56-v1")
            sha=hashlib.sha256(b"tpfan-r17-v1|"+str(source).encode()+b"|"+generation+b"|"+str(offset-origin).encode()).hexdigest()
            line_sha=hashlib.sha256(line).hexdigest()
            prior=c.execute("select line_sha from r17_log_positions where sha=?",[sha]).fetchone()
            if prior and prior[0]!=line_sha:
                raise ValueError("conflicting integrated R17 evidence")
            try:
                stamp=dt.datetime.strptime(g[0].decode("ascii"),"%d.%m.%Y %H:%M:%S")
                t0,t1=stamp.timestamp(),stamp.replace(fold=1).timestamp()
                wall_ok=t0==t1 and abs(end_ns-int(t0)*10**9)<=2*10**9
            except (ValueError,OverflowError,OSError):
                wall_ok=False
            insert_record(c,record,sha,wall_ok=wall_ok)
            c.execute("insert into r17_log_positions values (?,?) on conflict do nothing",[sha,line_sha])
            count+=1
        offset+=len(line)
    return count
