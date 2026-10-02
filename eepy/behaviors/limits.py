"""The robot's limits, in the units designers write.

There is exactly one source of truth for the rotation limits in this repo:
``robot/safety_limits.py``. This module imports that file rather than copying
its numbers, so the two can never drift apart. If someone corrects a limit
there, the YAML validator picks it up with no edit here.

``robot/safety_limits.py`` is not an installable package (the scripts in
``robot/`` are run directly, and rely on the script directory being on
sys.path), so it is loaded by file path. That keeps ``robot/`` exactly as it
is -- no __init__.py, no restructuring.

Two kinds of limit live here, and the difference matters:

  * HARD limits -- break these and the robot physically cannot do it, or
    hurts itself trying. Out of range is an ERROR. Never clamped silently.
  * ADVISORY limits -- speed. Legal but probably not what you meant. These
    are WARNINGS, promoted to errors with ``--strict``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAFETY_LIMITS_PATH = _REPO_ROOT / "robot" / "safety_limits.py"


def _load_safety_limits():
    """Import robot/safety_limits.py by path, as a module named _eepy_safety."""
    if not _SAFETY_LIMITS_PATH.is_file():
        raise RuntimeError(
            f"Cannot find the robot's safety limits at {_SAFETY_LIMITS_PATH}.\n"
            "The behaviour validator reads its joint limits from that file so "
            "there is only one copy of them in the repo. If you moved or "
            "renamed robot/safety_limits.py, update eepy/behaviors/limits.py "
            "to match."
        )
    spec = importlib.util.spec_from_file_location(
        "_eepy_safety_limits", _SAFETY_LIMITS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_eepy_safety_limits"] = module
    spec.loader.exec_module(module)
    return module


_safety = _load_safety_limits()

# --- Hard limits, straight from robot/safety_limits.py --------------------
HEAD_PITCH_LIMIT_DEG: float = _safety.HEAD_PITCH_ROLL_LIMIT_DEG   # +-40
HEAD_ROLL_LIMIT_DEG: float = _safety.HEAD_PITCH_ROLL_LIMIT_DEG    # +-40
HEAD_YAW_LIMIT_DEG: float = _safety.HEAD_YAW_LIMIT_DEG            # +-180
BODY_YAW_LIMIT_DEG: float = _safety.BODY_YAW_LIMIT_DEG            # +-160
HEAD_BODY_YAW_DELTA_LIMIT_DEG: float = _safety.HEAD_BODY_YAW_DELTA_LIMIT_DEG  # 65
ANTENNA_LIMIT_DEG: float = float(np.rad2deg(_safety.ANTENNA_LIMIT_RAD))  # +-180

# --- Head height ----------------------------------------------------------
# Not in robot/safety_limits.py, because that file only covers rotations.
# Measured by sweeping the SDK's own inverse kinematics (see eepy/behaviors/
# reach.py): the head reaches -50 mm down but only +23 mm up. Trimmed to a
# round, slightly conservative pair. The real constraint on height is
# coupling with pitch, which the reachability check catches properly -- this
# range alone only catches obvious nonsense like z: 500.
HEAD_Z_MIN_MM: float = -45.0
HEAD_Z_MAX_MM: float = 20.0

# --- Advisory speed limits ------------------------------------------------
# The hardware will go far faster than this: every actuated joint is rated
# 8 rad/s (458 deg/s) in the URDF. But the SDK's own built-in moves run at
# roughly 100-125 deg/s, and anything much past that looks like a twitch
# rather than a gesture. These are warnings, not errors.
ADVISORY_HEAD_SPEED_DEG_PER_S: float = 150.0
ADVISORY_BODY_SPEED_DEG_PER_S: float = 150.0
ADVISORY_ANTENNA_SPEED_DEG_PER_S: float = 300.0

# --- Hard speed limits ----------------------------------------------------
# Unlike the advisory ones above, these are ERRORS in the validator and are
# REFUSED by the player at runtime. They apply to the PEAK speed of a move,
# not its average: easing curves peak well above average (minjerk 1.9x,
# cartoon 5.6x -- see easing.py), and the peak is what the motors feel.
# All sit under the 458 deg/s (8 rad/s) URDF rating of every actuated joint.
# The translation limit has no URDF rating to anchor it; 100 mm/s is
# generous for a head that only travels about 70 mm end to end, and exists
# so that blending out of the sleep pose (x = -21, z = -44 mm) is never a jolt.
HARD_HEAD_SPEED_DEG_PER_S: float = 300.0
HARD_BODY_SPEED_DEG_PER_S: float = 200.0
HARD_ANTENNA_SPEED_DEG_PER_S: float = 400.0
HARD_TRANSLATION_SPEED_MM_PER_S: float = 100.0

# Hard limit per unit kind, as used by pose.Pose.delta().
HARD_SPEED: dict[str, float] = {
    "head": HARD_HEAD_SPEED_DEG_PER_S,
    "body": HARD_BODY_SPEED_DEG_PER_S,
    "antenna": HARD_ANTENNA_SPEED_DEG_PER_S,
    "translation": HARD_TRANSLATION_SPEED_MM_PER_S,
}

# When the player has to blend from wherever the robot is into a new
# behaviour, it aims for this fraction of the hard limit, so a preemption is
# never only just legal. See player.py, "blend-in".
BLEND_SPEED_FRACTION: float = 0.6

# --- Reference poses, decoded from the SDK -------------------------------
# reachy_mini/reachy_mini.py: INIT_ANTENNAS_JOINT_POSITIONS = [-0.1745, 0.1745]
# Mirrored, with a deliberate ~10 deg offset "to reduce shaking at vertical".
NEUTRAL_ANTENNA_RIGHT_DEG: float = -10.0
NEUTRAL_ANTENNA_LEFT_DEG: float = 10.0

# The four interpolation methods the SDK actually implements
# (reachy_mini/utils/interpolation.py, InterpolationTechnique).
EASINGS: tuple[str, ...] = ("linear", "minjerk", "ease_in_out", "cartoon")

# Built-in robot actions a keyframe may hand over to.
ACTIONS: tuple[str, ...] = ("goto_sleep", "wake_up")

# Placeholders a spoken line may contain.
ALLOWED_PLACEHOLDERS: tuple[str, ...] = ("minutes", "evidence")
