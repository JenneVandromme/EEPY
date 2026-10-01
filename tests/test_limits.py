"""The validator's limits must come from robot/safety_limits.py, not a copy."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from eepy.behaviors import limits

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_limits_come_from_the_robots_own_file():
    """If these drift apart, the validator is checking the wrong numbers."""
    import importlib.util

    path = REPO_ROOT / "robot" / "safety_limits.py"
    spec = importlib.util.spec_from_file_location("_check_safety", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert limits.HEAD_PITCH_LIMIT_DEG == module.HEAD_PITCH_ROLL_LIMIT_DEG
    assert limits.HEAD_ROLL_LIMIT_DEG == module.HEAD_PITCH_ROLL_LIMIT_DEG
    assert limits.HEAD_YAW_LIMIT_DEG == module.HEAD_YAW_LIMIT_DEG
    assert limits.BODY_YAW_LIMIT_DEG == module.BODY_YAW_LIMIT_DEG
    assert (
        limits.HEAD_BODY_YAW_DELTA_LIMIT_DEG
        == module.HEAD_BODY_YAW_DELTA_LIMIT_DEG
    )
    assert limits.ANTENNA_LIMIT_DEG == np.rad2deg(module.ANTENNA_LIMIT_RAD)


def test_easings_match_the_sdks_four_methods():
    assert limits.EASINGS == ("linear", "minjerk", "ease_in_out", "cartoon")


def test_neutral_antennas_are_mirrored():
    assert limits.NEUTRAL_ANTENNA_RIGHT_DEG == -limits.NEUTRAL_ANTENNA_LEFT_DEG


def test_head_sinks_further_than_it_rises():
    """Measured from the IK: -50 mm down but only +23 mm up."""
    assert abs(limits.HEAD_Z_MIN_MM) > abs(limits.HEAD_Z_MAX_MM)
