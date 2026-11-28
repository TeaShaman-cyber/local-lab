"""Parse observed Smart trace events; never reconstruct an unobserved decision."""
import hashlib
import json
import re

KINDS = {
    "SmartDecision": ("reason", "level", "request", "set_ok"),
    "SmartInputs": ("valid", "cpu_valid", "epoch", "package", "hottest", "ec_max", "sensor0", "selected"),
    "SmartTiming": ("age_ms", "future", "gap_ms", "ack_before", "pending_after", "observed_after"),
    "SmartPoll": ("idle", "start", "ec", "msr", "queue", "handler"),
}
PREFIX = re.compile(rb"^\[[^\r\n]+\] (SmartDecision|SmartInputs|SmartTiming|SmartPoll)\b")
HEADER = re.compile(rb"^\[[^\r\n]+\] (\w+) tick=(\d+) (.+)\r?\n$")
FULL = re.compile(rb"^\[[^\r\n]+\] EcPair full (\d+),")
LINK_WINDOW_MS = 500


def payload(kind, value):
    if kind == "SmartPoll" and value == b"unavailable":
        return {"unavailable": True}
    fields = value.decode("ascii").split()
    result = {}
    for token in fields:
        key, val = token.split("=", 1)
        if key in result:
            raise ValueError("duplicate field")
        if key == "reason":
            if not re.fullmatch(r"[a-z_]+", val):
                raise ValueError("invalid reason")
            result[key] = val
        else:
            if not re.fullmatch(r"-?\d+", val):
                raise ValueError("invalid integer")
            result[key] = int(val)
    if tuple(result) != KINDS[kind]:
        raise ValueError("unexpected fields")
    return result


def event(line, offset, kind):
    tick = data = None
    status = "malformed"
    match = HEADER.fullmatch(line)
    if match:
        try:
            tick = int(match[2])
            if not 0 <= tick < 2**63:
                tick = None
                raise ValueError("invalid tick")
            data = json.dumps(payload(kind, match[3].rstrip(b"\r")), separators=(",", ":"))
            status = "ok"
        except (ValueError, UnicodeError):
            pass
    return [offset, tick, kind, None, data, status, hashlib.sha256(line).hexdigest()]


def link_group(group, poll):
    if not group or any(row[5] != "ok" for row in group):
        return
    kinds = [row[2] for row in group]
    if kinds not in (list(KINDS)[:3], list(KINDS)):
        return
    tick = group[0][1]
    if not all(row[1] == tick for row in group):
        return
    if not 0 <= tick - poll["now"] <= LINK_WINDOW_MS or poll["mode"] != 2:
        return
    d, i, t = (json.loads(row[4]) for row in group[:3])
    # The input snapshot, decision and full poll are written by different
    # code paths. A timestamp alone is not evidence of association.
    checks = (
        d["level"] == poll["level"],
        i["cpu_valid"] == poll["cpu_valid"],
        i["package"] == poll["package"],
        i["hottest"] == poll["hottest"],
        i["ec_max"] == poll["ec_max"],
        t["ack_before"] == poll["ack"],
        t["pending_after"] == poll["post_pending"],
        t["observed_after"] == poll["post_level"],
    )
    if all(checks):
        for row in group:
            row[3] = poll["seq"]


def parse_smart(raw, polls):
    """Return complete trace records with byte identity and optional poll link."""
    by_seq = {p["seq"]: p for p in polls}
    result, pending = [], []
    previous = None
    offset = 0
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        match = PREFIX.match(line)
        if match:
            row = event(line, offset, match[1].decode("ascii"))
            result.append(row)
            pending.append(row)
        else:
            match = FULL.match(line)
            if match:
                seq = int(match[1])
                if seq in by_seq and (seq == 1 if previous is None else seq == previous + 1):
                    link_group(pending, by_seq[seq])
                pending = []
                previous = seq
        offset += len(line)
    return [tuple(row) for row in result]


def init_smart(c):
    c.execute("""create table if not exists smart_events(
        run_id varchar, byte_offset bigint, tick bigint, kind varchar,
        seq bigint, payload varchar, parse_status varchar, line_sha256 varchar,
        primary key(run_id,byte_offset))""")


def import_smart(c, run, rows):
    # Existing raw identity is immutable. A trailing, previously unlinked
    # group may acquire a link when its full-poll line arrives later.
    existing = {r[0]: r for r in c.execute(
        """select byte_offset,tick,kind,seq,payload,parse_status,line_sha256
           from smart_events where run_id=?""", [run]).fetchall()}
    new, links = [], []
    for row in rows:
        old = existing.get(row[0])
        if old is None:
            new.append((run, *row))
        elif old[:3] != row[:3] or old[4:] != row[4:] or (
                old[3] is not None and row[3] is not None and old[3] != row[3]):
            raise ValueError("conflicting Smart event evidence")
        elif old[3] is None and row[3] is not None:
            links.append((row[3], run, row[0]))
    if new:
        c.executemany("insert into smart_events values (?,?,?,?,?,?,?,?)", new)
    if links:
        c.executemany("update smart_events set seq=? where run_id=? and byte_offset=?", links)
