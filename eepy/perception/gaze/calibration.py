"""Gaze calibration: learning one person's work area, and keeping it.

THE STORE. One JSON file per person and camera setup:

    data/calibration/<user>/<setup>.json      (data/ is never committed)

with one section per kind of calibration: "gaze" (this module) and, later,
"baseline" (A12: the person's normal eye and mouth openness). Saving one
section keeps the others. Only angles and numbers -- never a picture.

    user   --user, else the participant of the session the panel is running,
           else "default"
    setup  the camera ("laptop", "robot"), plus an optional label for a
           particular desk ("laptop-lab"): the work area depends on where the
           camera sits.

THE FLOW. GazeCalibration is a step-by-step state machine, not a loop in a
script, so the command-line tool drives it now and the Wizard panel can drive
it later:

    cal = GazeCalibration(config)
    while not cal.done:
        step = cal.step                 # instruction, target, seconds
        ... show the instruction, then for each frame while recording:
        cal.add(estimate)               # a GazeEstimate, or None (no face)
        cal.next()                      # median of the step's samples; on to the next
    result = cal.finish()               # zones + a quality report
"""

from __future__ import annotations

import json
import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import GazeConfig
from .estimate import MODE_IRIS, GazeEstimate
from .zones import ZoneModel, ZoneSet, build_zone_set

REPO_ROOT = Path(__file__).resolve().parents[3]
CALIBRATION_DIR = REPO_ROOT / "data" / "calibration"
VERSION = 1


def _safe(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", (name or "").strip()) or "default"
    return name[:60]


def setup_name(source: str, label: Optional[str] = None) -> str:
    return _safe(f"{source}-{label}" if label else source)


class CalibrationStore:
    """Read and write data/calibration/<user>/<setup>.json, one section at a time."""

    def __init__(self, root: Path = CALIBRATION_DIR) -> None:
        self.root = Path(root)

    def path(self, user: str, setup: str) -> Path:
        return self.root / _safe(user) / f"{_safe(setup)}.json"

    def load(self, user: str, setup: str) -> Dict[str, Any]:
        path = self.path(user, setup)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def section(self, user: str, setup: str, name: str) -> Optional[Dict[str, Any]]:
        value = self.load(user, setup).get(name)
        return value if isinstance(value, dict) else None

    def save_section(self, user: str, setup: str, name: str, value: Dict[str, Any]) -> Path:
        """Replace one section, keep the others; written atomically."""
        path = self.path(user, setup)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.load(user, setup)
        data.update({"version": VERSION, "user": user, "setup": setup, name: value})
        partial = path.with_suffix(".json.part")
        partial.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(partial, path)
        return path


def zone_model_from(section: Optional[Dict[str, Any]], config: GazeConfig,
                    label: str = "") -> ZoneModel:
    """The calibrated ZoneModel from a stored "gaze" section, or the defaults."""
    if not section or "zones" not in section:
        return ZoneModel.default(config)
    zones = section["zones"]
    return ZoneModel(ZoneSet.from_dict(zones["gaze"]), ZoneSet.from_dict(zones["head"]),
                     calibrated=True, softness=config.get("edge_softness_deg"), label=label)


# --- the flow -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    key: str            # screen_center, screen_top_left, ..., notes_center, away
    group: str          # screen, notes, away
    instruction: str
    target: Optional[Tuple[float, float]]   # where to draw a dot on a full screen (0-1), or None
    seconds: float = 1.5


STEPS: Tuple[Step, ...] = (
    Step("screen_center", "screen", "Look at the dot in the CENTRE of the screen.", (0.5, 0.5)),
    Step("screen_top_left", "screen", "Look at the dot in the TOP-LEFT corner.", (0.04, 0.06)),
    Step("screen_top_right", "screen", "Look at the dot in the TOP-RIGHT corner.", (0.96, 0.06)),
    Step("screen_bottom_right", "screen", "Look at the dot in the BOTTOM-RIGHT corner.", (0.96, 0.94)),
    Step("screen_bottom_left", "screen", "Look at the dot in the BOTTOM-LEFT corner.", (0.04, 0.94)),
    Step("notes_center", "notes", "Look at the MIDDLE of your notes (where you write).", None, 2.0),
    Step("notes_far", "notes", "Look at the FAR edge of your notes (towards the screen).", None, 2.0),
    Step("away", "away", "Look AWAY, to the side (a window, a door...).", None, 2.0),
)


@dataclass
class _Samples:
    gaze: List[Tuple[float, float]] = field(default_factory=list)
    head: List[Tuple[float, float]] = field(default_factory=list)
    iris: int = 0
    frames: int = 0


def _median(points: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    if not points:
        return None
    return statistics.median(p[0] for p in points), statistics.median(p[1] for p in points)


class GazeCalibration:
    def __init__(self, config: Optional[GazeConfig] = None, steps: Tuple[Step, ...] = STEPS) -> None:
        self.config = config or GazeConfig()
        self.steps = steps
        self.index = 0
        self.samples: Dict[str, _Samples] = {s.key: _Samples() for s in steps}

    @property
    def done(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def step(self) -> Step:
        return self.steps[self.index]

    def add(self, estimate: Optional[GazeEstimate]) -> None:
        """One frame recorded during the current step (None: no face)."""
        bucket = self.samples[self.step.key]
        bucket.frames += 1
        if estimate is None:
            return
        bucket.gaze.append((estimate.yaw, estimate.pitch))
        bucket.head.append((estimate.head_yaw, estimate.head_pitch))
        bucket.iris += estimate.mode == MODE_IRIS

    def step_quality(self) -> Tuple[bool, str]:
        """Enough face in the current step? (False: repeat it.)"""
        bucket = self.samples[self.step.key]
        if bucket.frames == 0 or len(bucket.gaze) < 0.6 * bucket.frames or len(bucket.gaze) < 5:
            return False, "no face for most of this step"
        return True, ""

    def redo(self) -> None:
        self.samples[self.step.key] = _Samples()

    def next(self) -> None:
        self.index += 1

    def finish(self) -> Dict[str, Any]:
        """The "gaze" section to store: points, zones and a quality report."""
        points: Dict[str, Any] = {}
        for step in self.steps:
            bucket = self.samples[step.key]
            points[step.key] = {"gaze": _median(bucket.gaze), "head": _median(bucket.head),
                                "iris_share": round(bucket.iris / len(bucket.gaze), 2) if bucket.gaze else 0.0,
                                "frames": bucket.frames}
        zones: Dict[str, Any] = {}
        for kind in ("gaze", "head"):
            screen = [points[s.key][kind] for s in self.steps if s.group == "screen" and points[s.key][kind]]
            notes = [points[s.key][kind] for s in self.steps if s.group == "notes" and points[s.key][kind]]
            if len(screen) < 3:
                raise ValueError("Too few screen points were recorded (at least 3 needed). Try again.")
            zones[kind] = build_zone_set(screen, notes, self.config).as_dict()
        section = {"zones": zones, "points": points, "warnings": self._warnings(points, zones)}
        return section

    def _warnings(self, points: Dict[str, Any], zones: Dict[str, Any]) -> List[str]:
        warnings = []
        model = ZoneModel(ZoneSet.from_dict(zones["gaze"]), ZoneSet.from_dict(zones["head"]), calibrated=True)
        away = points.get("away", {}).get("gaze")
        if away is not None:
            hit = model.classify(away[0], away[1], iris_mode=True)
            if hit.zone in ("screen", "notes"):
                warnings.append("Looking away landed inside the work area: turn further away, "
                                "or the zones are too large.")
        if zones["gaze"]["notes"] and zones["gaze"]["screen"]:
            s, n = zones["gaze"]["screen"], zones["gaze"]["notes"]
            if n["pitch_min"] < s["pitch_max"] and n["yaw_min"] < s["yaw_max"] and s["yaw_min"] < n["yaw_max"]:
                warnings.append("The screen and notes zones overlap: fine for 'on work', but the two "
                                "cannot be told apart.")
        iris = [p["iris_share"] for p in points.values() if p["gaze"] is not None]
        if iris and sum(iris) / len(iris) < 0.5:
            warnings.append("The iris was usable in under half of the frames (glasses? light? too far?): "
                            "gaze will mostly follow the head.")
        return warnings
