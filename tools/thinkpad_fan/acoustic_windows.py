"""User-initiated, local-only fan acoustic capture. No continuous recording."""

import argparse
import ctypes as C
import hashlib
import json
import struct
import time
import uuid
import wave
from ctypes import wintypes as W
from pathlib import Path

from acoustic_trace import analyze, packet_clock

ROOT = Path("D:/theseus-hardware")


class GUID(C.Structure):
    _fields_ = [("raw", C.c_ubyte * 16)]


def guid(s):
    return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


def call(p, i, types, *args):
    v = C.cast(p, C.POINTER(C.POINTER(C.c_void_p))).contents
    fn = C.WINFUNCTYPE(C.c_long, C.c_void_p, *types)(v[i])
    hr = fn(p, *args)
    if hr < 0:
        raise OSError(hex(hr & 0xFFFFFFFF))


def release(p):
    if p:
        v = C.cast(p, C.POINTER(C.POINTER(C.c_void_p))).contents
        C.WINFUNCTYPE(W.ULONG, C.c_void_p)(v[2])(p)


def activate(dev, iid):
    p = C.c_void_p()
    call(
        dev, 3, [C.POINTER(GUID), W.DWORD, C.c_void_p, C.POINTER(C.c_void_p)], C.byref(guid(iid)), 23, None, C.byref(p)
    )
    return p


def sessions(dev):
    mgr = activate(dev, "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
    en = C.c_void_p()
    try:
        call(mgr, 5, [C.POINTER(C.c_void_p)], C.byref(en))
        n = C.c_int()
        call(en, 3, [C.POINTER(C.c_int)], C.byref(n))
        states = []
        for i in range(n.value):
            p = C.c_void_p()
            call(en, 4, [C.c_int, C.POINTER(C.c_void_p)], i, C.byref(p))
            try:
                st = C.c_int()
                call(p, 3, [C.POINTER(C.c_int)], C.byref(st))
                states.append(st.value)
            finally:
                release(p)
        return states
    finally:
        release(en)
        release(mgr)


def endpoint(ole):
    e = C.c_void_p()
    d = C.c_void_p()
    ole.CoCreateInstance(
        C.byref(guid("BCDE0395-E52F-467C-8E3D-C4579291692E")),
        None,
        23,
        C.byref(guid("A95664D2-9614-4F35-A746-DE8DB63617E6")),
        C.byref(e),
    )
    try:
        call(e, 4, [C.c_int, C.c_int, C.POINTER(C.c_void_p)], 1, 0, C.byref(d))
        return d
    finally:
        release(e)


def clock_anchor():
    k = C.WinDLL("kernel32", use_last_error=True)
    k.GetTickCount64.restype = C.c_ulonglong
    k.QueryPerformanceCounter.argtypes = [C.POINTER(C.c_longlong)]
    k.QueryPerformanceFrequency.argtypes = [C.POINTER(C.c_longlong)]
    f = C.c_longlong()
    a = C.c_longlong()
    b = C.c_longlong()
    if not k.QueryPerformanceFrequency(C.byref(f)) or not k.QueryPerformanceCounter(C.byref(a)):
        raise OSError("QPC unavailable")
    tick = k.GetTickCount64()
    utc = time.time_ns()
    if not k.QueryPerformanceCounter(C.byref(b)) or f.value <= 0:
        raise OSError("QPC unavailable")
    span = (b.value - a.value) * 1e9 / f.value
    if span > 2_000_000 or span < 0:
        raise ValueError("clock anchor too wide")
    return {
        "qpc_ns": ((a.value + b.value) * 1_000_000_000) // (2 * f.value),
        "utc_ns": utc,
        "tick_ms": tick,
        "bracket_ns": round(span),
    }


def capture(seconds):
    ole = C.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
    ole.CoCreateInstance.argtypes = [C.POINTER(GUID), C.c_void_p, W.DWORD, C.POINTER(GUID), C.POINTER(C.c_void_p)]
    ole.CoInitializeEx(None, 0)
    dev = client = cap = fmt = None
    started = False
    data = []
    packets = []
    try:
        dev = endpoint(ole)
        if 1 in sessions(dev):
            raise RuntimeError("microphone already in use")
        client = activate(dev, "1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
        fmt = C.c_void_p()
        call(client, 8, [C.POINTER(C.c_void_p)], C.byref(fmt))
        tag, ch, rate, _, align, bits, extra = struct.unpack("<HHIIHHH", C.string_at(fmt, 18))
        if tag == 65534 and extra >= 22:
            tag = struct.unpack("<I", C.string_at(fmt.value + 24, 4))[0]
        if (
            (tag, bits) not in ((3, 32), (1, 16))
            or not 1 <= ch <= 8
            or not 8000 <= rate <= 192000
            or align != ch * bits // 8
        ):
            raise ValueError("unsupported format")
        call(
            client,
            3,
            [C.c_int, W.DWORD, C.c_longlong, C.c_longlong, C.c_void_p, C.c_void_p],
            0,
            0,
            1000000,
            0,
            fmt,
            None,
        )
        cap = C.c_void_p()
        call(
            client,
            14,
            [C.POINTER(GUID), C.POINTER(C.c_void_p)],
            C.byref(guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")),
            C.byref(cap),
        )
        if 1 in sessions(dev):
            raise RuntimeError("microphone already in use")
        a = clock_anchor()
        call(client, 10, [])
        started = True
        end = time.monotonic() + seconds
        check = 0
        limit = int(rate * seconds)
        print("Recording fan diagnostics locally; please remain silent.", flush=True)
        while time.monotonic() < end and len(data) < limit:
            if time.monotonic() >= check:
                if sessions(dev).count(1) > 1:
                    raise RuntimeError("another capture session started; discarded")
                check = time.monotonic() + 0.25
            n = C.c_uint32()
            call(cap, 5, [C.POINTER(C.c_uint32)], C.byref(n))
            while n.value and time.monotonic() < end and len(data) < limit:
                ptr = C.c_void_p()
                frames = C.c_uint32()
                flags = W.DWORD()
                pos = C.c_uint64()
                qpc = C.c_uint64()
                call(
                    cap,
                    3,
                    [
                        C.POINTER(C.c_void_p),
                        C.POINTER(C.c_uint32),
                        C.POINTER(W.DWORD),
                        C.POINTER(C.c_uint64),
                        C.POINTER(C.c_uint64),
                    ],
                    C.byref(ptr),
                    C.byref(frames),
                    C.byref(flags),
                    C.byref(pos),
                    C.byref(qpc),
                )
                try:
                    if not 0 < frames.value <= rate or flags.value & 5:
                        raise ValueError("invalid audio packet")
                    take = min(frames.value, limit - len(data))
                    packets.append((pos.value, take, qpc.value, flags.value))
                    if flags.value & 2:
                        data.extend([0.0] * take)
                    else:
                        v = struct.unpack(
                            "<" + ("f" if tag == 3 else "h") * (frames.value * ch),
                            C.string_at(ptr, frames.value * align),
                        )
                        data.extend((x if tag == 3 else x / 32768) for x in v[: take * ch : ch])
                finally:
                    call(cap, 4, [C.c_uint32], frames.value)
                call(cap, 5, [C.POINTER(C.c_uint32)], C.byref(n))
            time.sleep(0.015)
        call(client, 11, [])
        started = False
        b = clock_anchor()
        if sessions(dev).count(1) > 0:
            raise RuntimeError("capture contention; discarded")
    finally:
        try:
            if started:
                call(client, 11, [])
        finally:
            if fmt:
                ole.CoTaskMemFree(fmt)
            release(cap)
            release(client)
            release(dev)
            ole.CoUninitialize()
    first = packet_clock(packets, rate)
    if len(data) < rate * (seconds - 1):
        raise ValueError("too little contiguous audio")
    # UTC and GetTickCount64 alignment are both checked against QPC drift.
    if (
        abs((b["utc_ns"] - a["utc_ns"]) - (b["qpc_ns"] - a["qpc_ns"])) > 20_000_000
        or abs((b["tick_ms"] - a["tick_ms"]) * 1_000_000 - (b["qpc_ns"] - a["qpc_ns"])) > 32_000_000
    ):
        raise ValueError("clock discontinuity")
    first_utc = a["utc_ns"] + first - a["qpc_ns"]
    if not a["qpc_ns"] - 200_000_000 <= first <= b["qpc_ns"]:
        raise ValueError("packet outside capture")
    return (
        data,
        rate,
        first_utc,
        {
            "before": a,
            "after": b,
            "first_qpc_ns": first,
            "packets": packets,
            "format": [tag, ch, rate, bits],
            "channel": 0,
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", action="store_true")
    p.add_argument("--seconds", type=int, default=15)
    p.add_argument("--save-wav", action="store_true", help="Explicitly retain audio including possible room speech")
    args = p.parse_args()
    if not args.capture or not 4 <= args.seconds <= 30:
        p.error("explicit --capture with 4..30 seconds required")
    from hardware_warehouse import check_room

    check_room(ROOT)
    data, rate, first, clock = capture(args.seconds)
    print("Microphone closed. Computing acoustic trace.", flush=True)
    result = analyze(data, rate, first)
    result["clock"] = clock
    folder = ROOT / "acoustic" / str(first)
    folder.mkdir(parents=True)
    if args.save_wav:
        path = folder / "fan.wav"
        with wave.open(str(path), "wb") as f:
            f.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            f.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, round(x * 32768)))) for x in data))
        result["wav_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    raw = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
    (folder / "trace.json").write_bytes(raw)
    print("TRACE", str(folder), len(data), len(result["spectra"]), hashlib.sha256(raw).hexdigest(), flush=True)


if __name__ == "__main__":
    main()
