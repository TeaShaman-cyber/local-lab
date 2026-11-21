"""Read-only per-process disk I/O counters on Windows (no admin, no ETW session).

Windows exposes IO_COUNTERS through GetProcessIoCounters: read/write/other
operation counts and byte totals, per process, cumulative since process start.
Sampling twice and subtracting gives rates; OpenProcess with
PROCESS_QUERY_LIMITED_INFORMATION is enough on Windows 8+. Protected processes
are skipped. This is a coarse attribution tool: it cannot name files or stacks,
and it counts I/O on all volumes, not only C:.

Use for "which process is generating many small operations" when an elevated
WPR/ETW capture is not available. No code here writes to any process.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = -1


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _api():
    if os.name != "nt":
        raise RuntimeError("per-process I/O counters require Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessIoCounters.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(IO_COUNTERS),
    ]
    kernel.GetProcessIoCounters.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def read_process_counters():
    """Return {pid: {'name', 'read_ops', 'write_ops', 'other_ops',
    'read_bytes', 'write_bytes', 'other_bytes'}} for processes we can open."""
    kernel = _api()
    result = {}
    snap = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel.Process32FirstW(snap, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            pid = entry.th32ProcessID
            handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:
                    counters = IO_COUNTERS()
                    if kernel.GetProcessIoCounters(handle, ctypes.byref(counters)):
                        result[pid] = {
                            "name": entry.szExeFile,
                            "read_ops": counters.read_operation_count,
                            "write_ops": counters.write_operation_count,
                            "other_ops": counters.other_operation_count,
                            "read_bytes": counters.read_transfer_count,
                            "write_bytes": counters.write_transfer_count,
                            "other_bytes": counters.other_transfer_count,
                        }
                finally:
                    kernel.CloseHandle(handle)
            if not kernel.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel.CloseHandle(snap)
    return result


def _delta(before, after, seconds):
    """Pure function: subtract two counter maps. Testable without Windows."""
    rows = []
    for pid, new in after.items():
        old = before.get(pid)
        if old is None or old.get("name") != new.get("name"):
            continue
        fields = (
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
        if any(new.get(f, 0) < old.get(f, 0) for f in fields):
            continue  # counter reset or PID reuse; never report negative rates
        row = {f: new[f] - old[f] for f in fields}
        row.update(
            pid=pid,
            name=new["name"],
            total_ops=row["read_ops"] + row["write_ops"] + row["other_ops"],
            total_bytes=row["read_bytes"] + row["write_bytes"] + row["other_bytes"],
            seconds=seconds,
        )
        row["ops_per_s"] = row["total_ops"] / seconds if seconds else 0.0
        row["bytes_per_s"] = row["total_bytes"] / seconds if seconds else 0.0
        row["avg_bytes_per_op"] = (
            row["total_bytes"] / row["total_ops"] if row["total_ops"] else 0.0
        )
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--sort", choices=["ops", "bytes", "avg"], default="ops")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.seconds <= 0:
        parser.error("--seconds must be positive")

    before = read_process_counters()
    started = time.monotonic()
    time.sleep(args.seconds)
    after = read_process_counters()
    seconds = time.monotonic() - started
    rows = _delta(before, after, seconds)
    key = {
        "ops": lambda r: r["ops_per_s"],
        "bytes": lambda r: r["bytes_per_s"],
        "avg": lambda r: (r["avg_bytes_per_op"], r["ops_per_s"]),
    }[args.sort]
    rows.sort(key=key, reverse=True)

    if args.json:
        print(json.dumps({"seconds": seconds, "processes": rows[: args.top]}, indent=2))
        return 0

    print(f"window={seconds:.2f}s processes={len(rows)} sort={args.sort}")
    print(f"{'PID':>8} {'OPS/S':>10} {'KB/S':>10} {'B/OP':>8}  NAME")
    for row in rows[: args.top]:
        print(
            f"{row['pid']:>8} {row['ops_per_s']:>10.1f} "
            f"{row['bytes_per_s'] / 1024:>10.1f} {row['avg_bytes_per_op']:>8.0f}  "
            f"{row['name']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
