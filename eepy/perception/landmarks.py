"""Geometry on MediaPipe's 478-point face mesh: eye and mouth openness, head
angles, face box. Pure numpy, no MediaPipe import, so it is testable anywhere.

"Left" and "right" are the PERSON's own (as in BURTRAM): their right eye is on
the left of an unmirrored camera picture.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np

from eepy.camera.region import Box

# Eye Aspect Ratio points, Soukupova & Cech (2016): p1..p6 = corner, top, top,
# corner, bottom, bottom. EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|).
RIGHT_EYE = (33, 160, 158, 133, 153, 144)
LEFT_EYE = (362, 385, 387, 263, 373, 380)
# Mouth: three vertical inner-lip gaps over the inner-corner width.
MOUTH_VERTICAL = ((82, 87), (13, 14), (312, 317))
MOUTH_CORNERS = (78, 308)
# Irises (478-point model only), for gaze later (A11).
RIGHT_IRIS, LEFT_IRIS = 468, 473
# Outline, for drawing.
FACE_OVAL = (10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379,
             378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127,
             162, 21, 54, 103, 67, 109)


def to_pixels(landmarks: np.ndarray, width: int, height: int) -> np.ndarray:
    """(N, 3) normalised landmarks -> (N, 2) pixels. Distances must be measured
    in pixels: normalised x and y have different scales on a non-square picture."""
    return landmarks[:, :2] * np.array([width, height], dtype=float)


def _dist(points: np.ndarray, a: int, b: int) -> float:
    return float(np.hypot(*(points[a] - points[b])))


def eye_aspect_ratio(points: np.ndarray, eye: Sequence[int]) -> float:
    p1, p2, p3, p4, p5, p6 = eye
    width = _dist(points, p1, p4)
    if width <= 1e-6:
        return 0.0
    return (_dist(points, p2, p6) + _dist(points, p3, p5)) / (2.0 * width)


def mouth_aspect_ratio(points: np.ndarray) -> float:
    width = _dist(points, *MOUTH_CORNERS)
    if width <= 1e-6:
        return 0.0
    return sum(_dist(points, a, b) for a, b in MOUTH_VERTICAL) / (len(MOUTH_VERTICAL) * width)


def face_box(points: np.ndarray) -> Box:
    x0, y0 = np.floor(points.min(axis=0)).astype(int)
    x1, y1 = np.ceil(points.max(axis=0)).astype(int)
    return Box(int(x0), int(y0), int(x1 - x0), int(y1 - y0))


def square_box(box: Box, expand: float = 1.2) -> Box:
    """A square around the face, `expand` times its longer side -- the crop
    shape face models such as Py-Feat's expect (A10)."""
    side = int(round(max(box.w, box.h) * expand))
    cx, cy = box.center
    return Box(int(round(cx - side / 2)), int(round(cy - side / 2)), side, side)


def head_angles(matrix: np.ndarray) -> Tuple[float, float, float]:
    """(yaw, pitch, roll) in degrees from MediaPipe's facial transformation matrix.

    The matrix maps the canonical face into camera space: x right, y up, z
    towards the camera; a face looking straight into the camera has R = I. It
    is decomposed as R = Ry(yaw) Rx(pitch) Rz(-roll), giving the robot's
    convention (docs/BEHAVIORS_GUIDE.md), for the PERSON's head:
        yaw   + = the person turns to THEIR left
        pitch + = chin down
        roll  + = head tips toward their LEFT ear
    """
    r = np.asarray(matrix, dtype=float)[:3, :3]
    # Remove any scale the matrix carries.
    r = r / np.linalg.norm(r, axis=0, keepdims=True)
    forward = r[:, 2]
    yaw = math.atan2(forward[0], forward[2])
    pitch = math.atan2(-forward[1], math.hypot(forward[0], forward[2]))
    rest = _rot_x(pitch).T @ _rot_y(yaw).T @ r          # = Rz(-roll)
    roll = -math.atan2(rest[1, 0], rest[0, 0])
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def _rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def matrix_from_angles(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """The inverse of head_angles(): for tests and synthetic data."""
    m = np.eye(4)
    m[:3, :3] = _rot_y(math.radians(yaw)) @ _rot_x(math.radians(pitch)) @ _rot_z(-math.radians(roll))
    return m
