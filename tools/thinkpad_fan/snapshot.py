"""Read-only TPFan file snapshots, never hold a live writer's file open.

Each immutable batch contains complete lines and records the byte interval.
Existing batch names are the checkpoint; no separate checkpoint write can
lose a successfully published batch. Keep batches until externally verified.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
from pathlib import Path

LIMIT = 512 * 1024


def shared_open(path):
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    # GENERIC_READ, FILE_SHARE_READ|WRITE|DELETE, OPEN_EXISTING.
    h = k.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if h == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
    except OSError:
        k.CloseHandle(h)
        raise
    return os.fdopen(fd, "rb")


def fingerprint(f, end):
    f.seek(max(0, end - 128))
    return hashlib.sha256(f.read(min(end, 128))).hexdigest()[:16]


def export_file(path, spool, kind):
    spool.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[a-z]+", kind):
        raise ValueError("invalid stream kind")
    with shared_open(path) as f:
        st = os.fstat(f.fileno())
        identity = hashlib.sha256(f"{st.st_dev}:{st.st_ino}".encode()).hexdigest()[:20]
        # A generation is distinguished by first bytes plus prior checkpoint.
        # Longest valid prefix wins; old batches remain immutable.
        candidates = []
        for p in spool.glob(f"{kind}-{identity}-*.jsonl"):
            m = re.fullmatch(r"([a-z]+)-([0-9a-f]+)-(\d+)-(\d+)-(\d+)-([0-9a-f]+)\.jsonl", p.name)
            if m:
                gen, start, end = map(int, m.group(3, 4, 5))
                candidates.append((gen, end, m.group(6)))
        generation, offset = 0, 0
        if candidates:
            gen, end, anchor = max(candidates)
            if end <= st.st_size and fingerprint(f, end) == anchor:
                generation, offset = gen, end
            else:
                generation = gen + 1
        f.seek(offset)
        data = f.read(LIMIT)
        n = data.rfind(b"\n") + 1
        if not n:
            return 0
        end = offset + n
        anchor = hashlib.sha256(data[max(0, n-128):n]).hexdigest()[:16] if n >= 128 else fingerprint(f, end)
    # Handle closed before JSON generation and disk writes.
    stamp = datetime.datetime.now().astimezone().isoformat()
    batch = spool / f"{kind}-{identity}-{generation:08d}-{offset:016d}-{end:016d}-{anchor}.jsonl"
    temp = batch.with_suffix(".tmp")
    rows = []
    pos = offset
    for line in data[:n].splitlines(keepends=True):
        raw = line.rstrip(b"\r\n").decode("cp1252", errors="replace")
        # Only bounded, task-specific diagnostics; no config or arbitrary logs.
        allowed = (re.match(r"^\d{2}:\d{2}:\d{2};", raw) if kind == "csv" else
                   re.match(r"^\[[0-9.]+ [0-9:]+\] (CPU MSR:|Fan:|Manual:|Smart:|EcPair|EC |Lid |Suspend|Resume|Error)", raw)
                   if kind == "events" else True)
        if allowed:
            rows.append(json.dumps({"observed_at":stamp,"source":kind,
                                    "file_id":identity,"generation":generation,
                                    "offset":pos,"raw":raw},ensure_ascii=True))
        pos += len(line)
    # A checkpoint-only batch has no log lines but must persist the interval.
    with temp.open("w",encoding="utf-8",newline="\n") as f:
        if rows:
            f.write("\n".join(rows)+"\n")
        f.flush()
        os.fsync(f.fileno())
    if batch.exists():
        temp.unlink()
    else:
        temp.replace(batch)
    return len(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--spool",type=Path,required=True)
    a = p.parse_args()
    for name,kind in (("TPFanControl_csv.txt","csv"),
                      ("TPFanControl_last_csv.txt","csv"),
                      ("TPFanControl.log","events")):
        path = a.root/name
        if path.is_file():
            print(kind,export_file(path,a.spool,kind),flush=True)


if __name__ == "__main__":
    main()
