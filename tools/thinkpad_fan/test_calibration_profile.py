import pytest

from tools.thinkpad_fan.calibration_profile import calibration_profile

BASE = (
    b"// keep \xfc comment\r\nActive=0\r\nManFanSpeed=0\r\n"
    b"ManModeExit=78 // old\r\nSingleFan=1\r\nPortBackend=pawnio\r\n"
    b"Cycle=1\r\nPowerSuspendMode=1\r\nLevel=50 0 0 0\r\n"
    b"Level=90 7 0 0\r\nLevel2=70 2 0 0\r\nLog2csv=1\r\n"
)


def test_profile_preserves_unrelated_bytes_and_never_starts_manual():
    result = calibration_profile(BASE)
    assert result == (
        b"// keep \xfc comment\r\nActive=1\r\nManFanSpeed=5\r\n"
        b"ManModeExit=85\r\nSingleFan=1\r\nPortBackend=pawnio\r\n"
        b"Cycle=1\r\nPowerSuspendMode=1\r\nLevel=1 128 0 0\r\n"
        b"Level2=1 128 0 0\r\nLog2csv=1\r\n"
    )


def test_absent_second_profile_is_explicit_auto():
    result = calibration_profile(BASE.replace(b"Level2=70 2 0 0\r\n", b""))
    assert result.count(b"Level2=1 128 0 0") == 1


@pytest.mark.parametrize("original,replacement", [
    (b"Active=0", b"Active=2"),
    (b"Active=0", b"Active=0\r\nactive=0"),
    (b"SingleFan=1", b"SingleFan=0"),
    (b"PortBackend=pawnio", b"PortBackend=other"),
    (b"Cycle=1", b"Cycle=5"),
    (b"PowerSuspendMode=1", b"PowerSuspendMode=3"),
    (b"Log2csv=1", b"Log2csv=0"),
    (b"ManModeExit=78 // old\r\n", b""),
])
def test_reject_unknown_or_unsafe_input(original, replacement):
    with pytest.raises(ValueError):
        calibration_profile(BASE.replace(original, replacement))


def test_lf_input():
    result = calibration_profile(BASE.replace(b"\r\n", b"\n"))
    assert b"\r" not in result
    assert b"Active=1\n" in result
