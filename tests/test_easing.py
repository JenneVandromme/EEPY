"""The easing curves must be the SDK's, exactly.

eepy/behaviors/easing.py copies the SDK's four curves so the player and the
validator can run without the SDK installed (CI installs only the small IK
wheel). If Pollen ever change a curve upstream, the copy would silently drift
-- this test is what catches that.
"""

from __future__ import annotations

import pytest

from eepy.behaviors import easing


def test_curves_start_at_0_and_end_at_1():
    for name in easing.CURVES:
        assert easing.ease(name, 0.0) == pytest.approx(0.0, abs=1e-12), name
        assert easing.ease(name, 1.0) == pytest.approx(1.0, abs=1e-12), name


def test_only_cartoon_overshoots():
    for name, (low, high) in easing.RANGE.items():
        if name == "cartoon":
            assert low < 0 and high > 1, "cartoon winds up and overshoots"
        else:
            assert (low, high) == pytest.approx((0.0, 1.0)), name


def test_known_peak_speeds():
    assert easing.PEAK_SPEED["linear"] == pytest.approx(1.0, rel=1e-3)
    assert easing.PEAK_SPEED["minjerk"] == pytest.approx(1.875, rel=1e-3)
    assert easing.PEAK_SPEED["ease_in_out"] == pytest.approx(2.0, rel=1e-3)
    assert easing.PEAK_SPEED["cartoon"] == pytest.approx(5.59, rel=1e-2)


def test_identical_to_the_sdk_when_it_is_installed():
    interpolation = pytest.importorskip("reachy_mini.utils.interpolation")
    for method in interpolation.InterpolationTechnique:
        for i in range(1001):
            t = i / 1000
            assert easing.ease(method.value, t) == pytest.approx(
                interpolation.time_trajectory(t, method), abs=1e-12
            ), (method.value, t)
