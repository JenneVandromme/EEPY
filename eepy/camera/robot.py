"""RobotCamera: the Reachy Mini's own camera, through the camera relay.

The relay runs in the robot's virtualenv -- inside the Wizard panel, or as
`python -m eepy.camera.relay` -- and hands over JPEGs on 127.0.0.1. This side
decodes them. The robot's camera IS the robot's view, so the whole picture is
passed on: there is no blue box here.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional, Tuple

from eepy.logs.clock import monotonic

from .config import CameraConfig
from .errors import CameraError, robot_no_pictures
from .relay import RelayClient
from .source import CameraSource


class RobotCamera(CameraSource):
    name = "robot"

    def __init__(self, config: Optional[CameraConfig] = None, events: Any = None,
                 clock: Callable[[], float] = monotonic) -> None:
        super().__init__(config, events, clock=clock)
        self._client: Optional[RelayClient] = None
        self._last_index = 0
        self._cv2: Any = None

    def describe(self) -> str:
        return "robot camera"

    def _open(self) -> None:
        if self._cv2 is None:
            try:
                import cv2
            except ImportError:
                raise CameraError("OpenCV is not installed here. The camera runs in .venv-vision:\n"
                                  "    pip install -r requirements-vision.txt") from None
            self._cv2 = cv2
        client = RelayClient(self.config.relay_port)
        status = client.status()          # raises RelayUnavailable (a CameraError) if nobody answers
        if not status.get("camera"):
            client.close()
            raise CameraError(robot_no_pictures())
        self._client, self._last_index = client, 0

    def _read(self) -> Optional[Tuple[Any, float]]:
        assert self._client is not None
        header, jpeg = self._client.frame()
        index = int(header.get("index", 0))
        if not jpeg or index == self._last_index:
            time.sleep(min(0.02, 0.5 / self.config.fps))   # nothing new yet
            return None
        self._last_index = index
        image = self._cv2.imdecode(_buffer(jpeg), self._cv2.IMREAD_COLOR)
        if image is None:
            return None
        # The relay stamped it when it took it from the SDK, on the same clock.
        return image, float(header.get("t", self._clock()))

    def _close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _buffer(data: bytes) -> Any:
    import numpy as np
    return np.frombuffer(data, dtype=np.uint8)
