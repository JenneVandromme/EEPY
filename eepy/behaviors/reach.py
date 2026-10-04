"""Is a head pose physically reachable?

Range-checking each axis on its own is *not* enough on Reachy Mini. The head
sits on a Stewart platform: six legs, and every axis competes for the same
leg travel. Lifting the head and tilting it at the same time can be
impossible even though each value on its own is well inside its limit.
Measured against the SDK's own IK, 51% of poses drawn from the documented
safety limits are unreachable, and 8 of the 16 corners of a much tighter box
still fail.

So this module asks the real inverse kinematics whether a pose has a
solution, and whether that solution keeps all six legs inside their URDF
limits. It needs no robot, no daemon and no simulator -- it is pure
computation on a 7.4 KB geometry file.

Two dependencies, both small:
  - reachy_mini_rust_kinematics: a zero-dependency wheel (the Rust solver).
  - data/kinematics_data.json: vendored from reachy_mini/assets/ so CI does
    not have to install the whole SDK (GStreamer, MuJoCo and all).

If the solver is not installed, `available()` returns False and the
validator says so loudly rather than quietly skipping the check.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

_DATA = Path(__file__).parent / "data" / "kinematics_data.json"

# Leg limits come from the URDF (descriptions/reachy_mini/urdf/robot.urdf,
# joints stewart_1..6), NOT from kinematics_data.json -- that file ships
# +-180 deg placeholders in its "limits" field, which would accept anything.
# Order matches the IK's output order (stewart_1..stewart_6).
LEG_LIMITS_RAD = np.array(
    [
        (-0.837758, 1.396260),  # stewart_1  -48 .. +80 deg
        (-1.396260, 1.221730),  # stewart_2  -80 .. +70 deg
        (-0.837758, 1.396260),  # stewart_3  -48 .. +80 deg
        (-1.396260, 0.837758),  # stewart_4  -80 .. +48 deg
        (-1.221730, 1.396260),  # stewart_5  -70 .. +80 deg
        (-1.396260, 0.837758),  # stewart_6  -80 .. +48 deg
    ]
)


def available() -> bool:
    """True if the Rust IK solver can be imported."""
    try:
        import reachy_mini_rust_kinematics  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _solver():
    """Build the IK solver.

    Mirrors reachy_mini.kinematics.analytical_kinematics.AnalyticalKinematics
    .__init__ exactly, so results match the SDK's own solver. Kept in sync by
    tests/test_reach.py, which compares the two side by side when the full SDK
    happens to be installed.
    """
    from reachy_mini_rust_kinematics import ReachyMiniRustKinematics

    data = json.loads(_DATA.read_text())
    kin = ReachyMiniRustKinematics(data["motor_arm_length"], data["rod_length"])
    for motor in data["motors"]:
        kin.add_branch(
            motor["branch_position"],
            np.linalg.inv(motor["T_motor_world"]),
            1 if motor["solution"] else -1,
        )
    return kin, data["head_z_offset"]


def head_pose_matrix(
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    z_mm: float = 0.0,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
) -> np.ndarray:
    """A 4x4 head pose from designer units (degrees, millimetres).

    Rotation order and sign convention match reachy_mini.utils.create_head_pose
    (scipy 'xyz' euler, degrees), so a pose built here is the same pose the
    behaviour player will send to goto_target().
    """
    from scipy.spatial.transform import Rotation as R

    pose = np.eye(4)
    pose[:3, :3] = R.from_euler("xyz", [roll, pitch, yaw], degrees=True).as_matrix()
    pose[:3, 3] = [x_mm / 1000.0, y_mm / 1000.0, z_mm / 1000.0]
    return pose


def is_reachable(
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    z_mm: float = 0.0,
    body_yaw: float = 0.0,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
) -> bool:
    """Can the head actually hold this pose, given where the body is?

    body_yaw matters: the six legs sit on the rotating body, so what they have
    to achieve is the head pose *relative* to the body. A head yaw of 40 deg
    is a 40 deg job for the legs when the body faces forward, and no job at
    all when the body has already turned 40 deg.

    Returns False if the IK has no solution, returns something non-finite, or
    needs a leg outside its URDF range.
    """
    kin, head_z_offset = _solver()
    # x and y are never set by a behaviour file; they only appear while the
    # player blends out of the SDK's sleep pose, which uses a sideways shift.
    pose = head_pose_matrix(yaw, pitch, roll, z_mm, x_mm, y_mm)
    pose[2, 3] += head_z_offset
    try:
        legs = kin.inverse_kinematics(pose.tolist(), np.deg2rad(body_yaw))
    except Exception:  # the solver raises when a pose has no solution at all
        return False
    legs = np.asarray(legs, dtype=float)
    if legs.size != 6 or not np.all(np.isfinite(legs)):
        return False
    return bool(
        np.all(legs >= LEG_LIMITS_RAD[:, 0]) and np.all(legs <= LEG_LIMITS_RAD[:, 1])
    )
