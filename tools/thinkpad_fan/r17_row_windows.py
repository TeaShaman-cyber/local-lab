"""One-shot opt-in diagnostic for the previously verified L14 R17 channel.

Never called by the recurring warehouse task. No driver installation,
fan command, firmware change, layout fallback or automatic retry.
"""

import ctypes as c
import hashlib
import struct
import time
from ctypes import wintypes as w
from pathlib import Path

from r17_row import Observer
from r17_row_events import publish_record
from r17_row_transport import StrictTransport

MODULE = Path("E:/tpfan-qa/f4212a7-full-poll/LpcThinkpadEC.bin")
MODULE_SHA = "54bbf7f10d3511abfff167597485926ddf1587045f6ae849671cfaa85f97a1db"
SOURCE_ID = "R17ET45W-row56-PawnIO-strict-v1"


def preflight():
    import winreg

    from hardware_warehouse import ROOT, SOURCE, check_room
    from snapshot import shared_open

    check_room(ROOT)
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as k:
        model = winreg.QueryValueEx(k, "SystemProductName")[0]
        bios = winreg.QueryValueEx(k, "BIOSVersion")[0]
    if not model.startswith("20U2") or not bios.startswith("R17ET45W"):
        raise RuntimeError("unsupported platform")
    module = MODULE.read_bytes()
    if hashlib.sha256(module).hexdigest() != MODULE_SHA:
        raise RuntimeError("unexpected PawnIO module")
    with shared_open(SOURCE) as f:
        raw = f.read(128 * 1024**2 + 1)
    if len(raw) > 128 * 1024**2:
        raise RuntimeError("source too large")
    tick = c.windll.kernel32.GetTickCount64
    tick.restype = c.c_ulonglong
    live_level_one(raw, tick())
    return ROOT, module


def live_level_one(raw, now):
    from hardware_warehouse import FIELDS, POLL_LINE, STARTUP, parse_polls, split_poll_runs

    # A pending startup without a fresh completed poll must not borrow the
    # preceding service's last good samples. Use the same restart validation
    # as the canonical importer, not a naive sequence reset.
    lines=raw.splitlines(keepends=True)
    last_poll=max((i for i,line in enumerate(lines) if POLL_LINE.fullmatch(line)),default=-1)
    if last_poll<0 or any(STARTUP.fullmatch(line) for line in lines[last_poll+1:]):
        raise RuntimeError("no current service polls")
    runs=list(split_poll_runs(raw))
    rows=parse_polls(runs[-1])
    if len(rows)<3:
        raise RuntimeError("insufficient live evidence")
    last=[dict(zip(FIELDS,row)) for row in rows[-3:]]
    for row in last:
        if (
            row["mode"] not in (2, 3)
            or row["level"] != 1
            or row["post_level"] != 1
            or row["ack"] != 1
            or row["post_pending"] != 0
            or row["ok"] != 1
            or row["cpu_valid"] != 1
            or not 0 <= now - row["now"] <= 5000
            or not 0 < max(row["package"], row["hottest"]) < 78
        ):
            raise RuntimeError("not in stable, cool, acknowledged level 1")
    return last


class PawnPort:
    def __init__(self, module):
        self.module = module
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.m = self.h = None
        self.owned = False

        def bind(name, args, ret):
            f = getattr(self.k, name)
            f.argtypes = args
            f.restype = ret
            return f

        self.openfile = bind(
            "CreateFileW", [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE
        )
        self.openmutex = bind("OpenMutexW", [w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE)
        self.wait = bind("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)
        self.unlock = bind("ReleaseMutex", [w.HANDLE], w.BOOL)
        self.close = bind("CloseHandle", [w.HANDLE], w.BOOL)
        self.dio = bind(
            "DeviceIoControl",
            [w.HANDLE, w.DWORD, c.c_void_p, w.DWORD, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p],
            w.BOOL,
        )
        self.tick = bind("GetTickCount64", [], c.c_ulonglong)

    def now(self):
        return self.tick()

    def sleep(self):
        time.sleep(0.001)

    def acquire(self):
        self.m = self.openmutex(0x00100001, False, r"Global\Access_EC")
        if not self.m:
            raise c.WinError(c.get_last_error())
        result = self.wait(self.m, 0)
        if result != 0:
            if result == 0x80:
                self.owned = True
                self.release()
                raise RuntimeError("abandoned EC ownership")
            self.release()
            if result == 0x102:
                return False
            raise RuntimeError("EC mutex wait failed")
        self.owned = True
        try:
            self.h = self.openfile(r"\\?\GLOBALROOT\Device\PawnIO", 3, 3, None, 3, 0x80, None)
            if self.h in (None, 0, c.c_void_p(-1).value):
                self.h = None
                raise c.WinError(c.get_last_error())
            self.io((41394 << 16) | (0x821 << 2), self.module)
        except Exception:
            self.release()
            raise
        return True

    def release(self):
        errors = []
        if self.h:
            if not self.close(self.h):
                errors.append("driver handle")
            self.h = None
        if self.owned:
            if not self.unlock(self.m):
                errors.append("mutex release")
            self.owned = False
        if self.m:
            if not self.close(self.m):
                errors.append("mutex handle")
            self.m = None
        if errors:
            raise RuntimeError(", ".join(errors))

    def io(self, code, raw, n=0):
        data = c.create_string_buffer(raw)
        output = c.create_string_buffer(n) if n else None
        got = w.DWORD()
        if not self.dio(self.h, code, data, len(raw), output, n, c.byref(got), None):
            raise c.WinError(c.get_last_error())
        if got.value != n:
            raise RuntimeError("unexpected driver response size")
        return output.raw if n else b""

    def execute(self, name, *args, n=0):
        return self.io(
            (41394 << 16) | (0x841 << 2),
            name.encode().ljust(32, b"\0") + b"".join(struct.pack("<q", v) for v in args),
            n,
        )

    def status(self):
        return struct.unpack("<q", self.execute("ioctl_pio_read", 0x1604, n=8))[0] & 255

    def command_write(self, value):
        if value not in (0x80, 0x81):
            raise ValueError("unsupported command")
        self.execute("ioctl_pio_write", 0x1604, value)

    def data_write(self, value):
        self.execute("ioctl_pio_write", 0x1600, value)

    def data_read(self):
        return struct.unpack("<q", self.execute("ioctl_pio_read", 0x1600, n=8))[0] & 255


def main():
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", action="store_true")
    args = p.parse_args()
    if not args.sample:
        p.error("one explicit sample required; no automatic sampling")
    root, module = preflight()
    marker = root / "r17-row-attempt.lock"
    # Crash or uncertain hardware state is a permanent stop, not a retry signal.
    with marker.open("x", encoding="ascii") as f:
        f.write(str(time.time_ns()))
    port = PawnPort(module)
    observer = Observer()
    begin_tick = port.now()
    begin_ns = time.time_ns()
    obs = observer.sample(StrictTransport(port))
    end_tick = port.now()
    end_ns = time.time_ns()
    record = publish_record(root / "r17-row-spool", obs, begin_tick, end_tick, begin_ns, end_ns, SOURCE_ID)
    print("R17", obs.status, record.name, "duration_ms", end_tick - begin_tick, flush=True)
    if observer.inhibited or obs.status in ("restore_unverified", "transport_uncertain"):
        raise RuntimeError("diagnostic inhibited; inspect state, no retry")
    marker.unlink()


if __name__ == "__main__":
    main()
