"""Test that all motions in robot/idle_and_nod.py comply strictly with safety_limits.py."""

from unittest.mock import MagicMock
import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R

from robot import idle_and_nod, safety_limits


class MockMini:
    """Mock ReachyMini that records every call to goto_target."""

    def __init__(self):
        self.calls = []

    def goto_target(self, **kwargs):
        self.calls.append(kwargs)


def test_idle_motions_respect_safety_limits():
    """Verify that every frame commanded during studying idle passes safety_limits."""
    mini = MockMini()
    yaw_rad = idle_and_nod.play_studying_idle(mini, cycles=2)

    assert len(mini.calls) > 0
    for call in mini.calls:
        head = call["head"]
        assert head.shape == (4, 4)
        roll, pitch, yaw = R.from_matrix(head[:3, :3]).as_euler("xyz", degrees=True)

        assert abs(pitch) <= safety_limits.HEAD_PITCH_ROLL_LIMIT_DEG
        assert abs(roll) <= safety_limits.HEAD_PITCH_ROLL_LIMIT_DEG
        assert abs(yaw) <= safety_limits.HEAD_YAW_LIMIT_DEG

        if "antennas" in call:
            r_rad, l_rad = call["antennas"]
            assert abs(r_rad) <= safety_limits.ANTENNA_LIMIT_RAD
            assert abs(l_rad) <= safety_limits.ANTENNA_LIMIT_RAD


def test_approving_nod_respects_safety_limits():
    """Verify that every frame commanded during approving nod passes safety_limits."""
    mini = MockMini()
    yaw_rad = idle_and_nod.play_approving_nod(mini)

    assert len(mini.calls) > 0
    for call in mini.calls:
        head = call["head"]
        assert head.shape == (4, 4)
        roll, pitch, yaw = R.from_matrix(head[:3, :3]).as_euler("xyz", degrees=True)

        assert abs(pitch) <= safety_limits.HEAD_PITCH_ROLL_LIMIT_DEG
        assert abs(roll) <= safety_limits.HEAD_PITCH_ROLL_LIMIT_DEG
        assert abs(yaw) <= safety_limits.HEAD_YAW_LIMIT_DEG

        if "antennas" in call:
            r_rad, l_rad = call["antennas"]
            assert abs(r_rad) <= safety_limits.ANTENNA_LIMIT_RAD
            assert abs(l_rad) <= safety_limits.ANTENNA_LIMIT_RAD


def test_return_to_neutral_respects_safety_limits():
    """Verify return_to_neutral respects safety limits."""
    mini = MockMini()
    idle_and_nod.return_to_neutral(mini)
    assert len(mini.calls) == 1
    call = mini.calls[0]
    head = call["head"]
    roll, pitch, yaw = R.from_matrix(head[:3, :3]).as_euler("xyz", degrees=True)
    assert roll == pytest.approx(0.0)
    assert pitch == pytest.approx(0.0)
    assert yaw == pytest.approx(0.0)
