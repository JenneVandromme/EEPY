"""Tests for the reachability check.

The validator's strongest guarantee rests on this module, so it is checked
against the SDK's own solver whenever the full SDK happens to be installed,
and against fixed known-good/known-bad poses when it is not.
"""

from __future__ import annotations

import pytest

from eepy.behaviors import reach

pytestmark = pytest.mark.skipif(
    not reach.available(), reason="reachy_mini_rust_kinematics is not installed"
)


def test_neutral_is_reachable():
    assert reach.is_reachable()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pitch": 20},
        {"pitch": -20},
        {"roll": 20},
        {"yaw": 40},
        {"z_mm": -20},
        {"z_mm": 10},
        {"pitch": 24, "z_mm": -20},
    ],
)
def test_sensible_poses_are_reachable(kwargs):
    assert reach.is_reachable(**kwargs), kwargs


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pitch": 80},
        {"roll": 60},
        {"z_mm": -100},
        {"z_mm": 60},
        {"pitch": 35, "z_mm": 18},   # the coupled case: both legal alone
    ],
)
def test_impossible_poses_are_rejected(kwargs):
    assert not reach.is_reachable(**kwargs), kwargs


def test_coupling_is_real_not_a_range_check():
    """pitch 35 alone: fine. z 18 alone: fine. Together: impossible."""
    assert reach.is_reachable(pitch=35)
    assert reach.is_reachable(z_mm=18)
    assert not reach.is_reachable(pitch=35, z_mm=18)


def test_body_yaw_changes_the_answer():
    """The legs only have to achieve the head pose relative to the body."""
    # The head alone spans about +-102 deg, so 130 is out of reach with the
    # body facing forward -- but trivial once the body has turned 90.
    assert not reach.is_reachable(yaw=130, body_yaw=0)
    assert reach.is_reachable(yaw=130, body_yaw=90)


def test_matches_the_sdk_solver_when_the_sdk_is_installed():
    """Our standalone solver must agree with reachy_mini's own.

    Skipped where the full SDK is absent (CI installs only the small wheel).
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("reachy_mini")
    from reachy_mini.kinematics.analytical_kinematics import AnalyticalKinematics
    from reachy_mini.utils import create_head_pose

    sdk = AnalyticalKinematics(automatic_body_yaw=False)
    limits = reach.LEG_LIMITS_RAD

    def sdk_ok(yaw, pitch, roll, z, body_yaw):
        try:
            joints = sdk.ik(
                create_head_pose(yaw=yaw, pitch=pitch, roll=roll, z=z, mm=True),
                body_yaw=numpy.deg2rad(body_yaw),
            )
        except Exception:
            return False
        if joints is None or not numpy.all(numpy.isfinite(joints)):
            return False
        legs = numpy.asarray(joints[1:7])
        return bool(
            numpy.all(legs >= limits[:, 0]) and numpy.all(legs <= limits[:, 1])
        )

    rng = numpy.random.default_rng(0)
    for _ in range(400):
        pose = dict(
            yaw=rng.uniform(-110, 110),
            pitch=rng.uniform(-80, 60),
            roll=rng.uniform(-55, 55),
            z=rng.uniform(-60, 35),
            body_yaw=rng.uniform(-90, 90),
        )
        mine = reach.is_reachable(
            yaw=pose["yaw"], pitch=pose["pitch"], roll=pose["roll"],
            z_mm=pose["z"], body_yaw=pose["body_yaw"],
        )
        assert mine == sdk_ok(**pose), pose
