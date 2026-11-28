"""Bounded local diagnostic summaries; never persist audio or full spectrograms."""

import argparse
import json
import math
import os
import statistics
import tempfile
import time
from pathlib import Path

from acoustic_trace import analyze, stable_spectral_peaks

ROOT = Path("D:/theseus-hardware")
SECONDS = 15
CAP = 64 * 1024**2
MAX_RECORD = 64 * 1024


def atomic_json(path, value):
    raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_RECORD:
        raise ValueError("summary too large")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        f.write(raw)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def summarize(data, rate, first, clock):
    trace = analyze(data, rate, first)
    peaks = sorted(stable_spectral_peaks(trace), key=lambda p: p["prominence_db"], reverse=True)[:32]
    bands = (25, 300, 1000, 3500, 8000)
    groups = {}
    for frame in trace["spectra"]:
        second = (frame["center_ns"] - first) // 1_000_000_000
        if second < 2 or frame["center_ns"] > first + len(data) * 1e9 / rate - 0.5e9:
            continue
        powers = [0.0] * 4
        for k, db in enumerate(frame["spectrum_dbfs"], trace["first_bin"]):
            hz = k * trace["bin_hz"]
            for i, (lo, hi) in enumerate(zip(bands, bands[1:])):
                if lo <= hz < hi:
                    powers[i] += 10 ** (db / 10)
                    break
        groups.setdefault(second, []).append(powers)
    return {
        "schema": 1,
        "first_utc_ns": first,
        "sample_rate": rate,
        "sample_count": len(data),
        "clock": {k: clock[k] for k in ("before", "after", "first_qpc_ns")},
        "stable_peaks": peaks,
        "band_edges_hz": bands,
        "band_seconds": [
            {"second": sec, "median_dbfs": [10 * math.log10(max(1e-18, statistics.median(v))) for v in zip(*rows)]}
            for sec, rows in sorted(groups.items())
        ],
    }


def run_once(folder, expires, endpoint_id, capture, check_room, now=time.time):
    folder.mkdir(parents=True, exist_ok=True)

    def finish(status):
        atomic_json(folder / "status.json", {"status": status, "utc_ns": time.time_ns()})
        return status

    if now() >= expires:
        return finish("expired")
    if not endpoint_id:
        return finish("endpoint_unconfigured")
    try:
        if sum(p.stat().st_size for p in folder.iterdir() if p.is_file()) + MAX_RECORD * 2 > CAP:
            return finish("storage_paused")
        check_room()
    except (RuntimeError, OSError):
        return finish("storage_paused")
    try:
        data, rate, first, clock = capture(SECONDS, raw=True, expected_endpoint=endpoint_id)
    except RuntimeError as e:
        if str(e) in (
            "microphone already in use",
            "another capture session started; discarded",
            "capture contention; discarded",
        ):
            return finish("skipped_busy")
        return finish("capture_rejected")
    except (OSError, ValueError):
        return finish("capture_rejected")
    if now() >= expires:
        return finish("expired")
    try:
        result = summarize(data, rate, first, clock)
        path = folder / f"trace-{first}.json"
        if path.exists():
            return finish("duplicate")
        atomic_json(path, result)
    except (ValueError, OSError):
        return finish("analysis_rejected")
    return finish("captured")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expires", required=True, type=int)
    args = p.parse_args()
    from acoustic_windows import capture
    from hardware_warehouse import check_room

    folder = ROOT / "acoustic-periodic"
    endpoint_id = (folder / "endpoint.txt").read_text(encoding="utf-8").strip()
    run_once(folder / "records", args.expires, endpoint_id, capture, lambda: check_room(ROOT))


if __name__ == "__main__":
    main()
