"""The person's normal face: what thresholds are measured against (A12 hook).

People differ: one person's relaxed eye is another's squint. So thresholds are
relative to a Baseline -- the person's own open-eye EAR, closed-mouth MAR and
straight-ahead head angles -- not absolute numbers.

A9 only defines the interface and uses fixed defaults (from config/eepy.yaml).
Per-person calibration (A12) will provide its own BaselineProvider:

    class CalibratedBaseline:
        def current(self) -> Baseline:
            return Baseline(ear_open=0.31, ...)    # measured for this person

    pipeline = FacePipeline(camera, baseline=CalibratedBaseline())

The pipeline calls current() for every frame, so a calibration that finishes
(or updates) mid-session takes effect at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import PerceptionConfig


@dataclass(frozen=True)
class Baseline:
    ear_open: float = 0.28    # eye aspect ratio of this person's normally open eye
    ear_floor: float = 0.05   # ...and of their fully closed eye
    mar_closed: float = 0.0   # mouth aspect ratio with the mouth relaxed and closed
    yaw0: float = 0.0         # where "straight ahead" is for this person and desk
    pitch0: float = 0.0
    face_size0: Optional[float] = None   # usual face size (distance to the desk); None = unknown
    calibrated: bool = False  # False: these are defaults, not measured

    def eye_closure(self, ear: float) -> float:
        """0 = open as usual, 1 = fully shut."""
        span = self.ear_open - self.ear_floor
        if span <= 1e-6:
            return 0.0
        return min(1.0, max(0.0, (self.ear_open - ear) / span))


class BaselineProvider:
    """Anything with current() -> Baseline. This one returns the config's defaults."""

    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        config = config or PerceptionConfig()
        self._baseline = Baseline(ear_open=config.get("eyes", "ear_open"),
                                  ear_floor=config.get("eyes", "ear_floor"))

    def current(self) -> Baseline:
        return self._baseline
