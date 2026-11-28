import math

import pytest
from acoustic_trace import analyze, packet_clock


def tone(hz, rate, seconds):
    return [0.1 * math.sin(2 * math.pi * hz * i / rate) for i in range(int(rate * seconds))]


def test_tracks_frequency_change_instead_of_averaging_it_away():
    rate = 8192
    x = tone(84, rate, 1) + tone(112, rate, 1)
    result = analyze(x, rate, 10_000_000_000)
    a, b = result["spectra"][0], result["spectra"][-1]
    assert abs(a["peaks"][0][0] - 84) <= 2
    assert abs(b["peaks"][0][0] - 112) <= 2
    assert a["center_ns"] < 11_000_000_000 < b["center_ns"]


def test_impulse_visible_in_short_time_envelope():
    rate = 8192
    x = [0.0] * rate
    x[4096] = 0.7
    r = analyze(x, rate, 0)
    peak = max(r["envelope"], key=lambda f: f["peak"])
    assert 480_000_000 <= peak["start_ns"] <= 520_000_000
    assert peak["peak"] == 0.7


def test_invalid_and_short_audio_rejected():
    for x in ([0.0] * 10, [float("nan")] * 8192):
        with pytest.raises(ValueError):
            analyze(x, 8192, 0)


def test_packet_clock_rejects_discontinuity_and_bad_timestamp():
    # (device frame, frame count, QPC in 100ns, WASAPI flags).
    good = [(10, 480, 10000000, 0), (490, 480, 10100000, 0)]
    assert packet_clock(good, 48000) == 1_000_000_000
    for bad in (
        [(10, 480, 10000000, 4)],
        [(10, 480, 10000000, 0), (491, 480, 10100000, 0)],
        [(10, 480, 10000000, 0), (490, 480, 10500000, 0)],
        [(10, 480, 10000000, 0), (490, 480, 10100000, 1)],
    ):
        with pytest.raises(ValueError):
            packet_clock(bad, 48000)
