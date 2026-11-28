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


def test_startup_packet_exclusion_never_hides_later_discontinuity():
    from acoustic_trace import admit_packet

    assert not admit_packet(480, 1, 48000, 0, 0.01)
    assert not admit_packet(480, 4, 48000, 0, 0.03)
    assert admit_packet(480, 0, 48000, 0, 0.04)
    for frames, flags, accepted, elapsed in (
        (480, 1, 480, 0.1),
        (480, 4, 480, 0.1),
        (480, 1, 0, 1.01),
        (0, 0, 0, 0.01),
    ):
        with pytest.raises(ValueError):
            admit_packet(frames, flags, 48000, accepted, elapsed)


def test_raw_audio_properties_are_16_byte_session_local_request():
    import ctypes

    from acoustic_windows import raw_properties

    p = raw_properties()
    assert ctypes.sizeof(p) == 16
    assert (p.cbSize, p.bIsOffload, p.eCategory, p.Options) == (16, 0, 0, 1)


def test_order_contrast_separates_moving_harmonic_from_fixed_frequency():
    from acoustic_trace import order_contrast

    rate = 8192
    for rpm in (2400, 3360):
        x = [a + b for a, b in zip(tone(4 * rpm / 60, rate, 1), tone(300, rate, 1))]
        result = analyze(x, rate, 0)
        frame = result["spectra"][0]
        assert order_contrast(frame, result["bin_hz"], result["first_bin"], rpm, 4) > 8
        other = 3360 if rpm == 2400 else 2400
        # Wrong expected rotation should not explain the fixed or moving peak.
        assert order_contrast(frame, result["bin_hz"], result["first_bin"], other, 4) < 8
    assert order_contrast(frame, result["bin_hz"], result["first_bin"], 0, 4) is None


def test_order_contrast_handles_fundamental_near_lower_spectral_edge():
    from acoustic_trace import order_contrast

    frame = {"spectrum_dbfs": [-100.0] * 30}
    frame["spectrum_dbfs"][5] = -80
    assert order_contrast(frame, 2.9296875, 9, 2518, 1) == 20


def test_comb_finds_multiple_orders_not_fixed_single_tone():
    from acoustic_trace import fit_harmonic_spacing

    peaks = [44.0, 50.0, 386.6, 463.5, 541.1, 695.7, 927.4, 1004.8, 1313.8, 1391.1, 1468.3, 2241.4, 3323.3]
    result = fit_harmonic_spacing(peaks, 72, 82)
    assert result["status"] == "CANDIDATE"
    assert abs(result["spacing_hz"] - 77.28) < 0.05
    assert result["matched"] == 11
    assert fit_harmonic_spacing([44.0, 509.0], 38, 47)["status"] == "INSUFFICIENT"
    assert fit_harmonic_spacing([386.6, 386.7, 386.8, 386.9, 387.0], 72, 82)["status"] == "INSUFFICIENT"


def test_comb_alias_or_competing_spacings_are_not_forced_to_one_rpm():
    from acoustic_trace import fit_harmonic_spacing

    result = fit_harmonic_spacing([77.3 * n for n in (5, 6, 7, 9, 12, 13)], 35, 90)
    assert result["status"] == "AMBIGUOUS"
    assert result["spacing_hz"] is None
    result = fit_harmonic_spacing([77.3 * n for n in (5, 6, 7, 9, 12, 13)], 72, 82)
    assert result["status"] == "CANDIDATE"


def test_stable_peaks_exclude_startup_and_interpolate_bins():
    from acoustic_trace import stable_spectral_peaks

    trace = {
        "bin_hz": 2.0,
        "first_bin": 10,
        "sample_count": 40000,
        "sample_rate": 8000,
        "envelope": [{"start_ns": 1000000000}],
        "spectra": [],
    }
    # Startup tone is discarded; repeated parabolic peak at index 10.25.
    base = [-100.0] * 30
    for i in (9, 10, 11):
        base[i] = -60 - 8 * (i - 10.25) ** 2
    trace["spectra"] = [
        {"center_ns": 1000000000 + i * 500000000, "spectrum_dbfs": base if i >= 4 else [-100.0] * 30} for i in range(10)
    ]
    peaks = stable_spectral_peaks(trace)
    assert len(peaks) == 1
    assert abs(peaks[0]["frequency_hz"] - 40.5) < 1e-6


def test_late_packet_error_retains_metadata_without_payload():
    from acoustic_windows import admit_capture_packet

    with pytest.raises(ValueError, match=r"flags=0x01.*pos=960.*frames=480.*accepted=480.*buffer_frames=48000"):
        admit_capture_packet(480, 1, 48000, 480, 0.2, 960, 123456, 48000, 110)
    assert not admit_capture_packet(480, 1, 48000, 0, 0.02, 0, 1, 48000, 17)


def test_capture_buffer_is_one_second():
    from acoustic_windows import CAPTURE_BUFFER_HNS

    assert CAPTURE_BUFFER_HNS == 10_000_000
