"""The face check before calibrating: is the person in a good position?

Passes once the face has been found continuously for `steady` seconds AND:
  * it is fully inside the view -- for the laptop camera, inside the blue box
    (with a small margin, so a face cut off at the edge does not pass);
  * the eyes are big enough in the picture (not too far from the camera);
  * the face is lit well enough (not too dark, not washed out).

Each problem comes with a message for the person and a line ID to speak.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

import numpy as np

MARGIN = 0.03              # share of the view a face must keep from the edges
MIN_EYE_PX = 18
DARK, BRIGHT = 50, 215     # mean brightness (0-255) of the face


class FaceCheck:
    def __init__(self, steady: float = 3.0) -> None:
        self.steady = steady
        self._good_since: Optional[float] = None
        self.problem: Optional[str] = "calibration_face_not_found"
        self.message = "Looking for your face..."

    def add(self, result: Any) -> bool:
        """True once the face has been good for `steady` seconds."""
        problem, message = self.judge(result.observation)
        self.problem, self.message = problem, message
        t = result.observation.t
        if problem is not None:
            self._good_since = None
            return False
        self._good_since = self._good_since if self._good_since is not None else t
        return t - self._good_since >= self.steady

    @staticmethod
    def judge(obs: Any) -> Tuple[Optional[str], str]:
        if not obs.present or obs.face_box is None:
            return ("calibration_face_not_found",
                    "No face found. Sit in front of the camera, facing the screen.")
        box, w, h = obs.face_box, obs.width, obs.height
        mx, my = MARGIN * w, MARGIN * h
        if box.x < mx or box.y < my or box.x + box.w > w - mx or box.y + box.h > h - my:
            return ("calibration_face_outside_box",
                    "Part of your face is outside the view. Move so your whole face is inside "
                    "the blue rectangle.")
        if obs.landmarks is not None:
            points = obs.points()
            eye = min(float(np.hypot(*(points[33] - points[133]))),
                      float(np.hypot(*(points[362] - points[263]))))
            if eye < MIN_EYE_PX:
                return ("calibration_too_far",
                        "You are a bit far from the camera. Move a little closer.")
        frame = obs.frame
        if frame is not None and getattr(frame, "image", None) is not None:
            x0, y0 = max(0, box.x), max(0, box.y)
            face = frame.image[y0:y0 + max(1, box.h), x0:x0 + max(1, box.w)]
            if face.size:
                brightness = float(face.mean())
                if brightness < DARK:
                    return ("calibration_too_dark",
                            "Your face is too dark for the camera. Turn on a light or face a window.")
                if brightness > BRIGHT:
                    return ("calibration_too_bright",
                            "Your face is too brightly lit (washed out). Move away from direct light.")
        return None, "Good -- hold still a moment."

