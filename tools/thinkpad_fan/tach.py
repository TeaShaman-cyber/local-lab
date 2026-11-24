"""Read-only calibration of TPFanCtrl2 v2.7.0 raw shared-state tachometer."""
from __future__ import annotations

import argparse
import ctypes
import json
import struct
import sys
import time
from dataclasses import asdict, dataclass
from math import isfinite
from statistics import median

# FCSHARED prefix from pinned upstream 3acd20c; Windows LONG/int are 32 bits.
PREFIX = struct.Struct("<10i")


@dataclass(frozen=True)
class Sample:
    seq: int
    cmd: int
    ack: int
    mode: int
    level: int
    rpm: int


@dataclass(frozen=True)
class Plateau:
    rpm: float
    accepted: int
    rejected: int


def decode(data: bytes) -> Sample:
    seq, cmd, ack, mode, _, level, lo, hi, _, _ = PREFIX.unpack(data)
    if not 0 <= lo <= 255 or not 0 <= hi <= 255:
        raise ValueError("invalid tach byte")
    return Sample(seq, cmd, ack, mode, level, lo + 256 * hi)


def plateau(samples, level: int) -> Plateau:
    """Estimate a settled RPM, not a thermal safety decision.

    Criteria are provisional for levels 4/5 on this machine. Never replace
    invalid measurements with the preceding value. Require recent valid data.
    """
    samples = tuple(samples)
    if level not in (4, 5) or len(samples) < 10:
        raise ValueError("need ten samples at level 4 or 5")
    if any(not isfinite(t) for t, _ in samples):
        raise ValueError("invalid sample time")
    if samples[-1][0] - samples[0][0] < 8:
        raise ValueError("window too short")
    cmd = samples[0][1].cmd
    for t, s in samples:
        if s.mode != 3 or s.level != level or s.cmd != cmd or s.ack != cmd:
            raise ValueError("mode or command changed")
    for (t0, a), (t1, b) in zip(samples, samples[1:]):
        if not 0 < t1 - t0 <= 3 or b.seq <= a.seq:
            raise ValueError("stale or discontinuous sample")
    valid = [s.rpm for _, s in samples if 2500 <= s.rpm <= 6500]
    if len(valid) < 8 or 5 * len(valid) < 4 * len(samples):
        raise ValueError("insufficient valid RPM samples")
    if not 2500 <= samples[-1][1].rpm <= 6500:
        raise ValueError("latest RPM invalid")
    if max(valid) - min(valid) > 150:
        raise ValueError("RPM has not settled")
    return Plateau(median(valid), len(valid), len(samples) - len(valid))


def collect(level: int) -> Plateau:
    """Open the existing engine mapping read-only; never create a mapping."""
    if sys.platform != "win32":
        raise RuntimeError("Windows required")
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenFileMappingW.restype = wintypes.HANDLE
    k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, ctypes.c_size_t]
    k.MapViewOfFile.restype = ctypes.c_void_p
    k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k.OpenFileMappingW(4, False, r"Global\TPFanControl_State")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        view = k.MapViewOfFile(handle, 4, 0, 0, PREFIX.size)
        if not view:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            samples = []
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                a = ctypes.string_at(view, PREFIX.size)
                b = ctypes.string_at(view, PREFIX.size)
                if a == b:
                    s = decode(a)
                    if not samples or s.seq != samples[-1][1].seq:
                        samples.append((time.monotonic(), s))
                        print(json.dumps(asdict(s)), flush=True)
                if len(samples) >= 10 and samples[-1][0] - samples[0][0] >= 8:
                    return plateau(samples, level)
                time.sleep(0.5)
            raise RuntimeError("insufficient fresh samples within 20 seconds")
        finally:
            k.UnmapViewOfFile(view)
    finally:
        k.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", type=int, choices=(4, 5))
    args = parser.parse_args()
    print(json.dumps(asdict(collect(args.level))))


if __name__ == "__main__":
    main()
