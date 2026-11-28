"""Uncalibrated, time-resolved acoustic diagnostics. No microphone access."""

import math
import statistics


def admit_packet(frames, flags, rate, accepted, elapsed):
    """Exclude flagged startup packets only, never a gap in accepted audio."""
    if not 0 < frames <= rate:
        raise ValueError("invalid audio frame count")
    if flags & 5:
        if accepted or elapsed > 1:
            raise ValueError("invalid audio packet after startup")
        return False
    return True


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


def order_contrast(frame, bin_hz, first_bin, rpm, order):
    """Local spectral contrast near a reference rotational order, not attribution."""
    if not all(math.isfinite(v) and v > 0 for v in (rpm, order, bin_hz)):
        return None
    values = frame["spectrum_dbfs"]
    i = round(rpm * order / (60 * bin_hz)) - first_bin
    if not 3 <= i < len(values) - 3:
        return None
    side = values[max(0, i - 8) : i - 2] + values[i + 3 : i + 9]
    if len(side) < 6:
        return None
    return max(values[i - 1 : i + 2]) - statistics.median(side)


def fit_harmonic_spacing(peaks, minimum_hz, maximum_hz, tolerance_hz=2.5):
    """Fit a harmonic comb within an explicit search band; never infer a unique RPM.

    One vote per distinct order, reject too few matches and competing aliases.
    The grid is only a candidate generator; refine using least squares through zero.
    """
    if not (
        0 < minimum_hz < maximum_hz <= 500 and maximum_hz - minimum_hz <= 200 and 0 < tolerance_hz < minimum_hz / 4
    ):
        raise ValueError("invalid search band")
    peaks = sorted(set(float(f) for f in peaks if math.isfinite(f) and 300 <= f < 3500))
    if len(peaks) > 512:
        raise ValueError("too many spectral candidates")

    def matches(spacing):
        chosen = {}
        for f in peaks:
            n = round(f / spacing)
            err = abs(f - n * spacing)
            if n > 0 and err <= tolerance_hz and (n not in chosen or err < chosen[n][0]):
                chosen[n] = (err, f)
        return [(n, f) for n, (_, f) in sorted(chosen.items())]

    fits = []
    for k in range(round((maximum_hz - minimum_hz) / 0.05) + 1):
        grid = min(maximum_hz, minimum_hz + k * 0.05)
        pairs = matches(grid)
        if len(pairs) < 5:
            continue
        spacing = sum(n * f for n, f in pairs) / sum(n * n for n, _ in pairs)
        if not minimum_hz <= spacing <= maximum_hz:
            continue
        pairs = matches(spacing)
        if len(pairs) < 5:
            continue
        mse = sum((f - n * spacing) ** 2 for n, f in pairs) / len(pairs)
        fits.append((len(pairs), mse, spacing, pairs))
    if not fits:
        return {"status": "INSUFFICIENT", "spacing_hz": None, "matched": 0}
    fits.sort(key=lambda v: (-v[0], v[1], -v[2]))
    count, mse, spacing, pairs = fits[0]
    rival = next((v for v in fits if abs(v[2] - spacing) > 1), None)
    # Distinct combs with equal support cannot be disambiguated by peak positions alone.
    if rival and rival[0] == count:
        return {"status": "AMBIGUOUS", "spacing_hz": None, "matched": count, "candidate_hz": [spacing, rival[2]]}
    return {
        "status": "CANDIDATE",
        "spacing_hz": spacing,
        "matched": count,
        "rms_error_hz": math.sqrt(mse),
        "pairs": pairs,
        "rival_matches": rival[0] if rival else 0,
    }


def stable_spectral_peaks(trace):
    """Median spectral lines, excluding two startup seconds and final half-second."""
    start = trace["envelope"][0]["start_ns"]
    end = start + trace["sample_count"] * 1e9 / trace["sample_rate"]
    spectra = [f["spectrum_dbfs"] for f in trace["spectra"] if start + 2e9 <= f["center_ns"] <= end - 0.5e9]
    if len(spectra) < 3:
        return []
    med = [statistics.median(v) for v in zip(*spectra, strict=True)]
    peaks = []
    for i in range(1, len(med) - 1):
        if med[i] <= med[i - 1] or med[i] <= med[i + 1]:
            continue
        prominence = med[i] - statistics.median(med[max(0, i - 8) : i + 9])
        if prominence < 8:
            continue
        delta = 0.5 * (med[i - 1] - med[i + 1]) / (med[i - 1] - 2 * med[i] + med[i + 1])
        peaks.append(
            {
                "frequency_hz": (trace["first_bin"] + i + delta) * trace["bin_hz"],
                "level_dbfs": med[i],
                "prominence_db": prominence,
            }
        )
    return peaks
