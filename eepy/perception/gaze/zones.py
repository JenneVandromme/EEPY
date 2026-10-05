"""The work area: zones in gaze-angle space (yaw x pitch, degrees).

    screen      the screen -- from calibration: its centre and 4 corners
    notes       the notes on the desk -- from calibration: 1-3 points
    down_other  lower than the screen but NOT on the notes: the lap, a phone...
                Kept apart so the HMM can combine it with phone detection (A29).
    away        everything else: to the side, up
    no_face     no face found

`screen` and `notes` are ON WORK; the rest is off work.

A calibration holds two zone sets: one learned from iris+head gaze, one from
the head alone -- because when someone looks at a screen corner their EYES do
part of the turning, so the head-only angles are smaller. Each frame is
classified with the set that matches how its gaze was measured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .config import GazeConfig

WORK = ("screen", "notes")
ZONES = ("screen", "notes", "down_other", "away", "no_face")


@dataclass(frozen=True)
class Rect:
    yaw_min: float
    yaw_max: float
    pitch_min: float
    pitch_max: float

    def signed_distance(self, yaw: float, pitch: float) -> float:
        """Degrees outside the rectangle (negative: inside, as deep as the
        nearest edge)."""
        dx = max(self.yaw_min - yaw, 0.0, yaw - self.yaw_max)
        dy = max(self.pitch_min - pitch, 0.0, pitch - self.pitch_max)
        if dx > 0 or dy > 0:
            return math.hypot(dx, dy)
        return -min(yaw - self.yaw_min, self.yaw_max - yaw, pitch - self.pitch_min, self.pitch_max - pitch)

    def contains(self, yaw: float, pitch: float) -> bool:
        return self.signed_distance(yaw, pitch) <= 0

    @classmethod
    def around(cls, points: Iterable[Tuple[float, float]], margin: float) -> "Rect":
        points = list(points)
        yaws, pitches = [p[0] for p in points], [p[1] for p in points]
        return cls(min(yaws) - margin, max(yaws) + margin, min(pitches) - margin, max(pitches) + margin)

    def as_dict(self) -> Dict[str, float]:
        return {k: round(getattr(self, k), 2) for k in ("yaw_min", "yaw_max", "pitch_min", "pitch_max")}


@dataclass(frozen=True)
class ZoneSet:
    screen: Rect
    notes: Optional[Rect]

    def as_dict(self) -> dict:
        return {"screen": self.screen.as_dict(), "notes": self.notes.as_dict() if self.notes else None}

    @classmethod
    def from_dict(cls, raw: dict) -> "ZoneSet":
        return cls(Rect(**raw["screen"]), Rect(**raw["notes"]) if raw.get("notes") else None)


@dataclass(frozen=True)
class Classification:
    zone: str
    on_work: float       # 0-1, soft: 1 well inside screen/notes, fading near their edges
    edge_deg: float      # distance to the nearest work-zone edge (confidence)


class ZoneModel:
    """Classifies gaze angles into zones. `gaze` and `head` zone sets for the two
    modes; `calibrated` False means the defaults from config are in use."""

    def __init__(self, gaze: ZoneSet, head: ZoneSet, calibrated: bool,
                 softness: float = 3.0, label: str = "default") -> None:
        self.gaze, self.head = gaze, head
        self.calibrated = calibrated
        self.softness = softness
        self.label = label

    @classmethod
    def default(cls, config: Optional[GazeConfig] = None) -> "ZoneModel":
        cfg = config or GazeConfig()
        s = Rect(-cfg.get("yaw", "default_screen"), cfg.get("yaw", "default_screen"),
                 cfg.get("pitch_min", "default_screen"), cfg.get("pitch_max", "default_screen"))
        n = Rect(-cfg.get("yaw", "default_notes"), cfg.get("yaw", "default_notes"),
                 cfg.get("pitch_min", "default_notes"), cfg.get("pitch_max", "default_notes"))
        zones = ZoneSet(s, n)
        return cls(zones, zones, calibrated=False, softness=cfg.get("edge_softness_deg"))

    def classify(self, yaw: float, pitch: float, iris_mode: bool) -> Classification:
        zones = self.gaze if iris_mode else self.head
        candidates: List[Tuple[str, Rect]] = [("screen", zones.screen)]
        if zones.notes is not None:
            candidates.append(("notes", zones.notes))
        distances = {name: rect.signed_distance(yaw, pitch) for name, rect in candidates}
        nearest = min(distances, key=distances.get)
        d = distances[nearest]
        on_work = 1.0 / (1.0 + math.exp(d / self.softness))     # 0.5 exactly on the edge
        if d <= 0:
            zone = nearest
        elif pitch > zones.screen.pitch_max:
            zone = "down_other"                                  # below the screen, not the notes
        else:
            zone = "away"
        return Classification(zone, round(on_work, 4), abs(d))


def build_zone_set(screen_points: List[Tuple[float, float]], notes_points: List[Tuple[float, float]],
                   config: GazeConfig) -> ZoneSet:
    """Zones from calibration points (median angles per target)."""
    base = Rect.around(screen_points, 0.0)
    size = max(base.yaw_max - base.yaw_min, base.pitch_max - base.pitch_min)
    screen = Rect.around(screen_points, max(config.get("screen_margin_deg"), 0.15 * size))
    notes = Rect.around(notes_points, config.get("notes_margin_deg")) if notes_points else None
    return ZoneSet(screen, notes)
