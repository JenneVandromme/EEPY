"""Where the eyes point: head direction (A9) plus the iris within the eye.

Angles in degrees, in A9's convention for the PERSON: yaw + = towards their
own left, pitch + = down. Relative to the camera; calibration absorbs where the
camera, screen and notes are.

The iris position is measured inside each eye opening (an idea from BURTRAM's
gaze_down_ratio, extended from vertical-only to both directions):

    h  -1 .. +1  along the eye's corner-to-corner axis, + = towards the
                 person's left (same direction as yaw +)
    v  -1 .. +1  between upper and lower eyelid, + = down

Eye-in-head angle = gain x offset; gaze = head + eye. When the iris cannot be
trusted, gaze = head alone, and the mode says so.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

import numpy as np

from .. import landmarks as lm
from ..observation import FaceObservation
from .config import GazeConfig

# (image-left corner, image-right corner, upper lid, lower lid, iris centre).
# Image-left -> image-right is the person's right -> left.
RIGHT = (33, 133, 159, 145, lm.RIGHT_IRIS)
LEFT = (362, 263, 386, 374, lm.LEFT_IRIS)

MODE_IRIS, MODE_HEAD = 1, 0


@dataclass(frozen=True)
class GazeEstimate:
    yaw: float
    pitch: float
    mode: int                    # MODE_IRIS (iris + head) or MODE_HEAD (head only)
    head_yaw: float
    head_pitch: float
    eye_yaw: Optional[float]     # the iris part, when measured
    eye_pitch: Optional[float]
    why_head_only: str = ""      # empty in iris mode


def iris_offsets(points: np.ndarray, eye: Tuple[int, int, int, int, int]) -> Tuple[float, float, float]:
    """(h, v, eye width in px) for one eye, from pixel landmarks."""
    left, right, top, bottom, iris = (points[i] for i in eye)
    axis = right - left
    width = float(np.hypot(*axis))
    if width < 1e-6:
        return 0.0, 0.0, 0.0
    unit = axis / width
    t = float(np.dot(iris - left, unit)) / width               # 0 = image-left corner
    lid = bottom - top
    height = float(np.hypot(*lid))
    s = float(np.dot(iris - top, lid / height)) / height if height > 1e-6 else 0.5
    return 2 * t - 1, 2 * s - 1, width


class GazeEstimator:
    """Per frame: GazeEstimate. Keeps a short history to spot a jittery iris."""

    def __init__(self, config: Optional[GazeConfig] = None) -> None:
        self.config = config or GazeConfig()
        self._recent: Deque[Tuple[float, float]] = deque()    # (t, eye_yaw)

    def reset(self) -> None:
        self._recent.clear()

    def estimate(self, obs: FaceObservation, eye_closure: Optional[float] = None) -> Optional[GazeEstimate]:
        if not obs.present or obs.head is None or obs.landmarks is None or len(obs.landmarks) < 478:
            self.reset()
            return None
        head_yaw, head_pitch = obs.head.yaw, obs.head.pitch
        points = obs.points()
        h_r, v_r, w_r = iris_offsets(points, RIGHT)
        h_l, v_l, w_l = iris_offsets(points, LEFT)
        h, v = (h_r + h_l) / 2, (v_r + v_l) / 2
        eye_yaw = h * self.config.get("eye_gain_yaw")
        eye_pitch = v * self.config.get("eye_gain_pitch")
        self._remember(obs.t, eye_yaw)
        why = self._unreliable(head_yaw, eye_closure, (h_r, v_r, w_r), (h_l, v_l, w_l))
        if why:
            return GazeEstimate(head_yaw, head_pitch, MODE_HEAD, head_yaw, head_pitch,
                                eye_yaw, eye_pitch, why)
        return GazeEstimate(head_yaw + eye_yaw, head_pitch + eye_pitch, MODE_IRIS,
                            head_yaw, head_pitch, eye_yaw, eye_pitch)

    def _remember(self, t: float, eye_yaw: float) -> None:
        if self._recent and t - self._recent[-1][0] > 0.5:
            self._recent.clear()
        self._recent.append((t, eye_yaw))
        while self._recent and t - self._recent[0][0] > 0.5:
            self._recent.popleft()

    def _unreliable(self, head_yaw: float, eye_closure: Optional[float],
                    right: Tuple[float, float, float], left: Tuple[float, float, float]) -> str:
        cfg = self.config
        if eye_closure is not None and eye_closure >= 0.5:
            return "eyes half closed"
        if min(right[2], left[2]) < cfg.get("min_eye_width_px"):
            return "eyes too small in the picture"
        if abs(head_yaw) > cfg.get("max_head_yaw_for_iris"):
            return "head turned too far"
        if any(abs(x) > 1.4 for x in (right[0], right[1], left[0], left[1])):
            return "iris outside the eye (landmarks unreliable)"
        if (abs(right[0] - left[0]) > cfg.get("max_eye_disagreement")
                or abs(right[1] - left[1]) > cfg.get("max_eye_disagreement")):
            return "the two eyes disagree"
        if len(self._recent) >= 4:
            values = [y for _, y in self._recent]
            mean = sum(values) / len(values)
            jitter = math.sqrt(sum((y - mean) ** 2 for y in values) / len(values))
            if jitter > cfg.get("max_iris_jitter_deg"):
                return "iris jittery (glasses? low light?)"
        return ""


def blendshape_gaze(blendshapes: dict) -> Optional[Tuple[float, float]]:
    """MediaPipe's own eyeLook* scores as a (horizontal, vertical) offset in the
    same convention as iris_offsets (+ = person's left, + = down), for the
    cross-check. None when the blendshapes are missing."""
    names = ("eyeLookOutLeft", "eyeLookInLeft", "eyeLookOutRight", "eyeLookInRight",
             "eyeLookUpLeft", "eyeLookUpRight", "eyeLookDownLeft", "eyeLookDownRight")
    if not all(n in blendshapes for n in names):
        return None
    b = blendshapes
    # Looking to the person's LEFT: left eye looks OUT, right eye looks IN.
    h = ((b["eyeLookOutLeft"] + b["eyeLookInRight"]) - (b["eyeLookInLeft"] + b["eyeLookOutRight"])) / 2
    v = ((b["eyeLookDownLeft"] + b["eyeLookDownRight"]) - (b["eyeLookUpLeft"] + b["eyeLookUpRight"])) / 2
    return h, v
