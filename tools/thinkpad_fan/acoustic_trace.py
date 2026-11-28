"""Uncalibrated, time-resolved acoustic diagnostics. No microphone access."""

import math
import statistics


def packet_clock(packets, rate):
    """Validate contiguous WASAPI packets and return first QPC timestamp in ns."""
    if not packets or rate <= 0:
        raise ValueError("missing packet clock")
    first_pos, _, first_qpc, _ = packets[0]
    expected = first_pos
    for pos, count, qpc, flags in packets:
        if (
            count <= 0
            or pos != expected
            or qpc <= 0
            or flags & 5
            or abs((qpc - first_qpc) * 100 - (pos - first_pos) * 1e9 / rate) > 5_000_000
        ):
            raise ValueError("invalid/discontinuous audio timing")
        expected += count
    return first_qpc * 100


def fft_power(x):
    n = len(x)
    a = [complex(v * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)))) for i, v in enumerate(x)]
    j = 0
    for i in range(1, n):
        b = n >> 1
        while j & b:
            j ^= b
            b >>= 1
        j ^= b
        if i < j:
            a[i], a[j] = a[j], a[i]
    step = 2
    while step <= n:
        w = complex(math.cos(-2 * math.pi / step), math.sin(-2 * math.pi / step))
        for i in range(0, n, step):
            q = 1
            for k in range(i, i + step // 2):
                u = a[k]
                v = q * a[k + step // 2]
                a[k] = u + v
                a[k + step // 2] = u - v
                q *= w
        step *= 2
    return [abs(v) ** 2 / (n * n) for v in a[: n // 2]]


def analyze(x, rate, first_ns):
    if not 8000 <= rate <= 192000 or len(x) > rate * 30 or not all(math.isfinite(v) for v in x):
        raise ValueError("invalid samples or rate")
    n = 2 ** int(math.log2(rate / 2))
    if len(x) < n:
        raise ValueError("insufficient samples")
    hop = n // 2
    spectra = []
    floor = 1e-18
    lo = max(1, math.ceil(25 * n / rate))
    hi = min(n // 2 - 1, math.floor(8000 * n / rate))
    for start in range(0, len(x) - n + 1, hop):
        p = fft_power(x[start : start + n])
        peaks = []
        for k in range(lo, hi):
            if p[k] <= p[k - 1] or p[k] <= p[k + 1] or p[k] < floor:
                continue
            local = statistics.median(p[max(1, k - 8) : min(len(p), k + 9)])
            prominence = 10 * math.log10((p[k] + floor) / (local + floor))
            if prominence >= 8:
                peaks.append((k * rate / n, p[k], prominence))
        peaks.sort(key=lambda v: v[1], reverse=True)
        spectra.append(
            {
                "center_ns": first_ns + round((start + n / 2) * 1e9 / rate),
                "peaks": [[round(f, 3), round(db, 2)] for f, _, db in peaks[:12]],
                "spectrum_dbfs": [round(10 * math.log10(max(floor, v)), 2) for v in p[lo:hi]],
            }
        )
    envelope = []
    block = max(1, round(rate * 0.02))
    for start in range(0, len(x), block):
        frame = x[start : start + block]
        envelope.append(
            {
                "start_ns": first_ns + round(start * 1e9 / rate),
                "peak": max(map(abs, frame)),
                "rms": math.sqrt(sum(v * v for v in frame) / len(frame)),
            }
        )
    return {
        "sample_rate": rate,
        "sample_count": len(x),
        "window_samples": n,
        "hop_samples": hop,
        "first_bin": lo,
        "bin_hz": rate / n,
        "spectra": spectra,
        "envelope": envelope,
    }
