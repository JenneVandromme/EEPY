"""FakeCamera: pictures without a camera, for tests, CI and trying things out.

By default it draws a moving test pattern. Pass `clip=` a video file to loop it
instead (needs OpenCV). Keep clips of real people in .cache/ -- it is
gitignored -- and never commit them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import numpy as np

from eepy.logs.clock import monotonic

from .config import CameraConfig
from .errors import CameraError
from .source import CameraSource


class FakeCamera(CameraSource):
    """A camera that makes up its pictures.

    native_fps   how fast the 'hardware' produces pictures (0 = as fast as asked).
    fail_after   raise CameraError ("unplugged") after this many pictures, then
                 work again on the next open() -- for reconnect tests.
    fail_opens   the first N open() calls fail -- for "camera not there yet".
    """

    name = "fake"

    def __init__(self, config: Optional[CameraConfig] = None, events: Any = None,
                 resolution: Tuple[int, int] = (320, 180), native_fps: float = 60.0,
                 clip: Optional[Path] = None, fail_after: Optional[int] = None,
                 fail_opens: int = 0, clock: Callable[[], float] = monotonic) -> None:
        super().__init__(config, events, clock=clock)
        self.resolution = resolution
        self.native_fps = native_fps
        self.clip = Path(clip) if clip is not None else None
        self.fail_after = fail_after
        self.fail_opens = fail_opens
        self.opens = 0
        self.produced = 0
        self._next = 0.0
        self._video: Any = None
        self._failed_once = False

    def describe(self) -> str:
        return f"fake camera ({self.clip.name if self.clip else 'test pattern'})"

    def _open(self) -> None:
        self.opens += 1
        if self.opens <= self.fail_opens:
            raise CameraError("fake camera: not plugged in yet")
        if self.clip is not None:
            import cv2

            self._video = cv2.VideoCapture(str(self.clip))
            if not self._video.isOpened():
                raise CameraError(f"cannot open the test clip {self.clip}")
        self._next = self._clock()

    def _read(self) -> Optional[Tuple[Any, float]]:
        if self.fail_after is not None and self.produced >= self.fail_after and not self._failed_once:
            self._failed_once = True
            raise CameraError("fake camera: unplugged")
        if self.native_fps > 0:
            wait = self._next - self._clock()
            if wait > 0:
                time.sleep(min(wait, 0.05))
                if self._next - self._clock() > 0:
                    return None
            self._next = max(self._next + 1.0 / self.native_fps, self._clock() - 0.1)
        image = self._picture()
        self.produced += 1
        return image, self._clock()

    def _picture(self) -> np.ndarray:
        if self._video is not None:
            ok, image = self._video.read()
            if not ok:
                self._video.set(1, 0)   # cv2.CAP_PROP_POS_FRAMES: loop the clip
                ok, image = self._video.read()
            if ok:
                return image
        width, height = self.resolution
        image = np.zeros((height, width, 3), dtype=np.uint8)
        image[:, :, 0] = np.linspace(40, 200, width, dtype=np.uint8)[None, :]   # blue ramp
        image[:, :, 1] = np.linspace(40, 200, height, dtype=np.uint8)[:, None]  # green ramp
        x = (self.produced * 4) % width                                         # moving bar
        image[:, x:x + 6, 2] = 255
        return image

    def _close(self) -> None:
        if self._video is not None:
            self._video.release()
            self._video = None
