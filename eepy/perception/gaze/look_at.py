"""Real "look at user": tell the panel where the student's face is.

The robot's "look_at: user" target is a fixed direction in _blocks.yaml. While
the vision process sees a face, this reports where it is instead, so the
panel's TrackingTargetProvider (eepy/behaviors/targets.py) can point the robot
at the actual student.

What is sent is an OFFSET in robot head angles (degrees): how far the face is
from the centre of the robot's view -- yaw + = to the robot's left, pitch + =
down, the robot's own convention. The panel adds it to where the head points
now. That works for both cameras, because both show what the robot looks at:
the robot's own camera moves with its head, and the laptop camera's blue box
follows the head pose.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

from ..observation import FaceObservation


def face_offset(obs: FaceObservation, fov_deg: float) -> Optional[Dict[str, float]]:
    """The face's offset from the centre of the view, as robot head angles; None
    without a face. Aims between the eyes (iris centres), not at the face box,
    which includes the chin."""
    if not obs.present or obs.width <= 0:
        return None
    if obs.landmarks is not None and len(obs.landmarks) >= 474:
        points = obs.points()
        u, v = (points[468] + points[473]) / 2
    elif obs.face_box is not None:
        u, v = obs.face_box.center
    else:
        return None
    focal = (obs.width / 2) / math.tan(math.radians(fov_deg) / 2)
    yaw = -math.degrees(math.atan((u - obs.width / 2) / focal))     # right in the picture = robot's right (-)
    pitch = math.degrees(math.atan((v - obs.height / 2) / focal))   # lower in the picture = chin down (+)
    return {"yaw": round(yaw, 2), "pitch": round(pitch, 2)}


class UserTargetPublisher:
    """Subscribe to a FacePipeline: sends face offsets to the panel over the camera
    relay, `rate` times a second at most. Silent and cheap when no panel runs."""

    RETRY_SECONDS = 2.0

    def __init__(self, port: int, robot_lens_fov: float, laptop_view_fov: float, rate: float = 5.0) -> None:
        from eepy.camera.relay import RelayClient

        self.client = RelayClient(port, timeout=0.3)
        self.robot_lens_fov, self.laptop_view_fov = robot_lens_fov, laptop_view_fov
        self.interval = 1.0 / rate
        self._next = 0.0
        self._retry_at = 0.0
        self._had_face = False
        self.sent = 0

    def __call__(self, result: Any) -> None:
        # perf_counter, not monotonic: on Windows (Python <= 3.12) monotonic moves in
        # 15.6 ms steps and would swallow reports (the same lesson as the AU worker).
        now = time.perf_counter()
        if now < self._next or now < self._retry_at:
            return
        self._next = now + self.interval
        obs = result.observation
        fov = self.robot_lens_fov if obs.source == "robot" else self.laptop_view_fov
        offset = face_offset(obs, fov)
        if offset is None and not self._had_face:
            return                                  # say "no face" once, not 5 times a second
        try:
            self.client.send_user(offset)
            self.sent += 1
        except Exception:  # noqa: BLE001 -- no panel running: try again later
            self._retry_at = now + self.RETRY_SECONDS
            return
        self._had_face = offset is not None

    def close(self) -> None:
        self.client.close()
