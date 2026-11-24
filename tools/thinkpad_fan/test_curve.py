import pytest

from tools.thinkpad_fan.curve import next_level, target_rpm

# Synthetic fixtures only, not L14 measurements or deployable settings.
POINTS = ((50, 2000), (70, 4000), (85, 5000))
LEVELS = ((3, 2000), (4, 3000), (5, 4000), (6, 5000))


@pytest.mark.parametrize("temp,expected", [(40, 2000), (50, 2000), (60, 3000),
                                           (70, 4000), (77.5, 4500), (85, 5000), (90, 5000)])
def test_continuous_target(temp, expected):
    assert target_rpm(POINTS, temp) == pytest.approx(expected)


@pytest.mark.parametrize("points", [(), ((50, 2000),), ((50, 2000), (50, 3000)),
                                    ((70, 2000), (50, 3000)), ((50, 3000), (70, 2000)),
                                    ((50, -1), (70, 3000)), ((50, 1000), (70, float("inf")))])
def test_reject_invalid_curve(points):
    with pytest.raises(ValueError):
        target_rpm(points, 60)


@pytest.mark.parametrize("temp", [float("nan"), float("inf"), -float("inf")])
def test_reject_nonfinite_temperature(temp):
    with pytest.raises(ValueError):
        target_rpm(POINTS, temp)


def test_step_up_and_down_only_one_level():
    assert next_level(LEVELS, 3, 5000, 100) == 4
    assert next_level(LEVELS, 6, 2000, 100) == 5


def test_hysteresis_prevents_chatter_around_boundary():
    assert next_level(LEVELS, 4, 3550, 100) == 4
    assert next_level(LEVELS, 4, 3601, 100) == 5
    assert next_level(LEVELS, 5, 3450, 100) == 5
    assert next_level(LEVELS, 5, 3399, 100) == 4


def test_endpoints_and_exact_threshold():
    assert next_level(LEVELS, 3, 0, 100) == 3
    assert next_level(LEVELS, 6, 6000, 100) == 6
    assert next_level(LEVELS, 4, 3600, 100) == 4
    assert next_level(LEVELS, 5, 3400, 100) == 5


@pytest.mark.parametrize("levels,current,target,band", [
    ((), 3, 3000, 100), (((3, 2000),), 3, 3000, 100),
    (((3, 2000), (5, 4000)), 3, 3000, 100),
    (((3, 2000), (4, 1900)), 3, 3000, 100),
    (((3, 2000), (4, 2000)), 3, 3000, 100),
    (((0, 0), (1, 2000)), 1, 3000, 100),
    (((6, 4000), (7, 5000), (8, 6000)), 7, 3000, 100),
    (LEVELS, 2, 3000, 100), (LEVELS, 3, float("nan"), 100),
    (LEVELS, 3, 3000, -1), (LEVELS, 3, 3000, 500),
    (LEVELS, 3, 3000, float("inf")), (LEVELS, 3.5, 3000, 100),
])
def test_reject_invalid_calibration(levels, current, target, band):
    with pytest.raises(ValueError):
        next_level(levels, current, target, band)
