"""One camera frame, as perception receives it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np

    from .region import Box


@dataclass(frozen=True)
class Frame:
    """A frame and where it came from.

    image   BGR uint8 array, height x width x 3 (OpenCV's layout). For the
            laptop camera this is ONLY what is inside the blue box -- what the
            robot would see. Treat it as read-only: every subscriber gets the
            same array.
    t       when it was captured, on eepy.logs.clock.monotonic() -- the clock
            every logged event uses, so frames line up with robot events.
    index   counts up from 1 for each frame this source delivered. A gap means
            frames were dropped to stay real-time.
    source  "laptop", "robot" or "fake".
    box     laptop camera only: where `image` sits in the full webcam picture
            (it moves with the robot's head). None for the robot's camera.
    full    the whole webcam picture, ONLY when the camera was opened with
            keep_full=True (the preview does this to draw the blue box).
            Perception must use `image`, never `full`.
    """

    image: "np.ndarray"
    t: float
    index: int
    source: str
    box: Optional["Box"] = None
    full: Optional["np.ndarray"] = None

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def resolution(self) -> tuple:
        return self.width, self.height

    def __repr__(self) -> str:  # never dump pixels into a log or a traceback
        return (f"Frame(#{self.index} {self.source} {self.width}x{self.height} "
                f"t={self.t:.3f})")
