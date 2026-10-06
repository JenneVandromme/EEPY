"""The person's normal face: what thresholds and normalised features are measured against.

People differ: one person's relaxed eye is another's squint; heads rest at
different angles; cameras sit at different heights. So thresholds are relative
to a Baseline -- the person's own open-eye EAR, resting mouth, neutral head
pose, usual distance and resting action units -- not absolute numbers.

Without a calibration the defaults below stand in (calibrated=False, and every
normalised feature is flagged uncalibrated). A calibration (A12,
eepy/perception/calibration/) measures them over ~60 s of normal work and is
stored per person and camera setup; StoredBaselineProvider loads it.

The pipeline asks its provider for current() on EVERY frame, so a calibration
that finishes mid-session takes effect at once.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

from .config import PerceptionConfig


@dataclass(frozen=True)
class Baseline:
    ear_open: float = 0.28    # eye aspect ratio of this person's normally open eye (mean of both)
    ear_floor: float = 0.05   # ...and of their fully closed eye (the bottom of a blink)
    mar_closed: float = 0.0   # mouth aspect ratio at rest
    yaw0: float = 0.0         # neutral head pose for this person, desk and camera
    pitch0: float = 0.0
    roll0: float = 0.0
    face_size0: Optional[float] = None   # usual face size (distance); None = unknown
    blink_rate: Optional[float] = None   # usual blinks per minute
    blink_ms: Optional[float] = None     # usual blink duration
    ear_open_left: Optional[float] = None
    ear_open_right: Optional[float] = None
    au_rest: Dict[str, float] = field(default_factory=dict)   # AU -> resting probability
    calibrated: bool = False  # False: these are defaults, not measured
    label: str = ""           # whose and which camera, e.g. "P03/laptop"
    taken_at: str = ""        # when the calibration was made (local time)
    late_evening: bool = False

    def eye_closure(self, ear: float) -> float:
        """0 = open as usual, 1 = fully shut."""
        span = self.ear_open - self.ear_floor
        if span <= 1e-6:
            return 0.0
        return min(1.0, max(0.0, (self.ear_open - ear) / span))

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: Dict[str, Any], **overrides: Any) -> "Baseline":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in values.items() if k in known}
        data.update(overrides)
        return cls(**data)


class BaselineProvider:
    """Anything with current() -> Baseline. This one returns the config's defaults."""

    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        config = config or PerceptionConfig()
        self._baseline = Baseline(ear_open=config.get("eyes", "ear_open"),
                                  ear_floor=config.get("eyes", "ear_floor"))

    def current(self) -> Baseline:
        return self._baseline


class StoredBaselineProvider(BaselineProvider):
    """The person's calibrated baseline from data/calibration/<user>/<setup>.json
    ("baseline" section), else the defaults. use() switches person or camera;
    set() applies a fresh calibration straight away (no reload needed)."""

    def __init__(self, config: Optional[PerceptionConfig] = None, store: Any = None,
                 user: str = "default", source: str = "laptop", setup: Optional[str] = None) -> None:
        super().__init__(config)
        self._default = self._baseline
        if store is None:
            from .gaze.calibration import CalibrationStore

            store = CalibrationStore()
        self.store, self.setup_label = store, setup
        self._loaded_for: Optional[tuple] = None
        self.use(user, source)

    def use(self, user: str, source: str) -> None:
        from .gaze.calibration import setup_name

        setup = setup_name(source, self.setup_label)
        if self._loaded_for == (user, setup):
            return
        self._loaded_for = (user, setup)
        section = self.store.section(user, setup, "baseline")
        if section and isinstance(section.get("values"), dict):
            self._baseline = Baseline.from_dict(
                section["values"], calibrated=True, label=f"{user}/{setup}",
                taken_at=section.get("taken_at", ""), late_evening=bool(section.get("late_evening")))
        else:
            self._baseline = self._default

    def set(self, baseline: Baseline) -> None:
        self._baseline = baseline

    def forget(self) -> None:
        """Re-read the file on the next use() (after a new calibration was saved)."""
        self._loaded_for = None
