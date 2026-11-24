"""Offline candidate fan curve and quantizer; no hardware access or defaults.

All RPM values, curve points and hysteresis must come from explicit inputs.
The quantizer proposes at most one adjacent calibrated step per evaluation.
It does not provide a time guarantee, thermal supervision or a live controller.
"""
from __future__ import annotations

from math import isfinite


def target_rpm(points, temperature):
    """Clamped, piecewise-linear, nondecreasing temperature-to-RPM curve."""
    points = tuple(points)
    if len(points) < 2 or not isfinite(temperature):
        raise ValueError("finite temperature and at least two points required")
    if any(not isfinite(t) or not isfinite(r) or r < 0 for t, r in points):
        raise ValueError("invalid curve point")
    for (t0, r0), (t1, r1) in zip(points, points[1:]):
        if t1 <= t0 or r1 < r0:
            raise ValueError("curve temperatures must rise and RPM must not fall")
    if temperature <= points[0][0]:
        return points[0][1]
    for (t0, r0), (t1, r1) in zip(points, points[1:]):
        if temperature <= t1:
            return r0 + (r1 - r0) * (temperature - t0) / (t1 - t0)
    return points[-1][1]


def next_level(calibration, current, target, hysteresis):
    """Propose an adjacent calibrated level using RPM midpoint hysteresis.

    Calibration must cover consecutive levels with distinct, increasing RPM.
    This deliberately rejects unknown gaps and nonmonotonic measurements.
    """
    calibration = tuple(calibration)
    if len(calibration) < 2 or not isfinite(target) or target < 0:
        raise ValueError("finite nonnegative target and two levels required")
    if not isfinite(hysteresis) or hysteresis < 0:
        raise ValueError("invalid hysteresis")
    if any(type(level) is not int or not 1 <= level <= 7 or not isfinite(rpm) or rpm <= 0
           for level, rpm in calibration):
        raise ValueError("invalid calibration point")
    for (l0, r0), (l1, r1) in zip(calibration, calibration[1:]):
        if l1 != l0 + 1 or r1 - r0 <= 2 * hysteresis:
            raise ValueError("calibration must be consecutive and strictly separated")
    if type(current) is not int or not calibration[0][0] <= current <= calibration[-1][0]:
        raise ValueError("current level is not calibrated")
    i = current - calibration[0][0]
    rpm = calibration[i][1]
    if i + 1 < len(calibration):
        upper = (rpm + calibration[i + 1][1]) / 2 + hysteresis
        if target > upper:
            return current + 1
    if i > 0:
        lower = (rpm + calibration[i - 1][1]) / 2 - hysteresis
        if target < lower:
            return current - 1
    return current
