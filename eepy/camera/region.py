"""The blue box: which part of the laptop camera's picture the robot "sees".

With the laptop camera standing in for the robot's, the robot's narrower view
is a box inside the webcam picture, as in BURTRAM's eye_gaze_follow.py --sim:

  * SIZE. Width = picture width x robot_fov / laptop_fov -- BURTRAM's ratio
    (50 / 78 = 64 % by default). Height follows from the ROBOT camera's shape
    (16:9 for its 1280x720 stream), so the crop has the robot's proportions.
  * POSITION. Centred where the robot's head is looking. The webcam is treated
    as a fixed pinhole camera looking straight out from the robot, so a head
    turned 10 degrees to its left puts the box's centre where a 10-degree ray
    to the left lands in the picture.

Only numpy, no OpenCV, and Python 3.9: this runs in both virtualenvs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class Box:
    """A rectangle in picture pixels. May extend past the picture's edges."""

    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> Tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def inside(self, width: int, height: int) -> bool:
        """Does the whole box lie within a width x height picture?"""
        return self.x >= 0 and self.y >= 0 and self.x + self.w <= width and self.y + self.h <= height


@dataclass(frozen=True)
class HeadPose:
    """Where the head points, in degrees, in the robot's own convention:
    yaw + turns LEFT, pitch + is chin DOWN (docs/BEHAVIORS_GUIDE.md). Yaw is
    measured in the world, so a body turn is already included."""

    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0


def focal_px(width: int, fov_deg: float) -> float:
    """Pinhole focal length, in pixels, for a horizontal field of view."""
    return (width / 2) / math.tan(math.radians(fov_deg) / 2)


def box_size(width: int, laptop_fov: float, robot_fov: float, robot_aspect: float) -> Tuple[int, int]:
    """(w, h) of the box in a picture `width` pixels wide."""
    w = max(2, int(round(width * robot_fov / laptop_fov)))
    h = max(2, int(round(w / robot_aspect)))
    return w, h


def gaze_point(pose: HeadPose, width: int, height: int, laptop_fov: float) -> Optional[Tuple[float, float]]:
    """Where the head's line of sight lands in the picture (u right, v down),
    or None when the head points sideways or backwards (90 degrees or more).

    The gaze direction is the head's forward axis, R_z(yaw) R_y(pitch) R_x(roll)
    applied to (1, 0, 0) -- the same rotation as Pose.head_matrix(). Roll spins
    around that axis, so it does not move the point.
    """
    yaw, pitch = math.radians(pose.yaw), math.radians(pose.pitch)
    if math.cos(yaw) <= 1e-6 or math.cos(pitch) <= 1e-6:
        return None
    f = focal_px(width, laptop_fov)
    u = width / 2 - f * math.tan(yaw)                     # left turn -> picture left
    v = height / 2 + f * math.tan(pitch) / math.cos(yaw)   # chin down -> picture lower
    return u, v


def robot_box(width: int, height: int, laptop_fov: float, robot_fov: float,
              robot_aspect: float, pose: Optional[HeadPose] = None) -> Box:
    """The box for a width x height picture. No pose: centred."""
    w, h = box_size(width, laptop_fov, robot_fov, robot_aspect)
    point = gaze_point(pose, width, height, laptop_fov) if pose is not None else None
    if point is None and pose is not None:
        # Looking 90 degrees or more away: entirely off the picture, to that side.
        cx = -w if pose.yaw > 0 else width + w
        return Box(int(round(cx - w / 2)), (height - h) // 2, w, h)
    cx, cy = point if point is not None else (width / 2, height / 2)
    return Box(int(round(cx - w / 2)), int(round(cy - h / 2)), w, h)


def crop(image: np.ndarray, box: Box) -> np.ndarray:
    """What is inside `box`, always box.w x box.h. Parts of the box beyond the
    picture's edges -- where the laptop camera cannot see -- are black."""
    height, width = image.shape[:2]
    if box.inside(width, height):
        return np.ascontiguousarray(image[box.y:box.y + box.h, box.x:box.x + box.w])
    out = np.zeros((box.h, box.w) + image.shape[2:], dtype=image.dtype)
    x0, y0 = max(box.x, 0), max(box.y, 0)
    x1, y1 = min(box.x + box.w, width), min(box.y + box.h, height)
    if x0 < x1 and y0 < y1:
        out[y0 - box.y:y1 - box.y, x0 - box.x:x1 - box.x] = image[y0:y1, x0:x1]
    return out
