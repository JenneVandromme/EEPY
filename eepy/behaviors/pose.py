"""One full robot pose, in designer units.

This is the pose the player streams and the backends send. It has two fields
the YAML can never set: x and y, the head's sideways shift. Behaviours never
use them, but the robot's own sleep pose does (x = -21 mm). After the robot
wakes from sleep, the player reads that shift back and blends it to 0 instead
of snapping it there on the first frame.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np

from . import limits

# (name, unit kind) for every field. The unit kind picks the speed limit.
AXES: tuple[tuple[str, str], ...] = (
    ("yaw", "head"),
    ("pitch", "head"),
    ("roll", "head"),
    ("x", "translation"),
    ("y", "translation"),
    ("z", "translation"),
    ("antenna_right", "antenna"),
    ("antenna_left", "antenna"),
    ("body_yaw", "body"),
)


@dataclass(frozen=True)
class Pose:
    """Angles in degrees, translations in millimetres."""

    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    antenna_right: float = limits.NEUTRAL_ANTENNA_RIGHT_DEG
    antenna_left: float = limits.NEUTRAL_ANTENNA_LEFT_DEG
    body_yaw: float = 0.0

    def lerp(self, other: "Pose", progress: float) -> "Pose":
        """The pose `progress` of the way from self to other.

        progress may leave [0, 1] -- cartoon easing overshoots on purpose.
        """
        return Pose(
            **{
                name: getattr(self, name)
                + (getattr(other, name) - getattr(self, name)) * progress
                for name, _ in AXES
            }
        )

    def delta(self, other: "Pose") -> dict[str, float]:
        """Largest absolute change per unit kind (head, translation, ...)."""
        out: dict[str, float] = {}
        for name, kind in AXES:
            change = abs(getattr(other, name) - getattr(self, name))
            out[kind] = max(out.get(kind, 0.0), change)
        return out

    def is_close(self, other: "Pose", tolerance: float = 0.5) -> bool:
        return all(
            abs(getattr(self, f.name) - getattr(other, f.name)) <= tolerance
            for f in fields(self)
        )

    # --- conversion to and from what the SDK speaks -------------------------

    def head_matrix(self) -> np.ndarray:
        """4x4 head pose, matching reachy_mini.utils.create_head_pose exactly."""
        from scipy.spatial.transform import Rotation as R

        matrix = np.eye(4)
        matrix[:3, :3] = R.from_euler(
            "xyz", [self.roll, self.pitch, self.yaw], degrees=True
        ).as_matrix()
        matrix[:3, 3] = [self.x / 1000.0, self.y / 1000.0, self.z / 1000.0]
        return matrix

    @classmethod
    def from_robot(
        cls, head: np.ndarray, antennas_rad: list[float], body_yaw_rad: float
    ) -> "Pose":
        """Build a Pose from what the SDK reports."""
        from scipy.spatial.transform import Rotation as R

        roll, pitch, yaw = R.from_matrix(np.asarray(head)[:3, :3]).as_euler(
            "xyz", degrees=True
        )
        x, y, z = (np.asarray(head)[:3, 3] * 1000.0).tolist()
        return cls(
            yaw=float(yaw),
            pitch=float(pitch),
            roll=float(roll),
            x=x,
            y=y,
            z=z,
            antenna_right=float(np.rad2deg(antennas_rad[0])),
            antenna_left=float(np.rad2deg(antennas_rad[1])),
            body_yaw=float(np.rad2deg(body_yaw_rad)),
        )

    def __str__(self) -> str:
        return (
            f"head y/p/r {self.yaw:6.1f} {self.pitch:6.1f} {self.roll:6.1f}  "
            f"z {self.z:5.1f}  ant {self.antenna_right:6.1f} {self.antenna_left:6.1f}  "
            f"body {self.body_yaw:5.1f}"
        )


NEUTRAL = Pose()
