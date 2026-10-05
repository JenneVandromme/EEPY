"""GazeExtractor: is the person looking at their work? A FacePipeline plug-in.

Per frame (features.csv):
    gaze_yaw, gaze_pitch   smoothed gaze direction, degrees (yaw + = the
                           person's left, pitch + = down), relative to the camera
    gaze_zone              screen | notes | down_other | away | no_face
    gaze_on_work           0-1: 1 well inside screen/notes, fading near their edges
    gaze_confidence        0-1: lower near a zone edge, with head-only gaze, or
                           without calibration
    gaze_mode              1 = iris + head, 0 = head only (iris not trusted)
    gaze_calibrated        1 when this person's calibration is in use
    away_s                 how long they have been off work right now (0 on work)

Events: looked_away (t = when it began), back_on_work (away_seconds).
About once a second: gaze_window (10 s / 60 s summaries).

HYSTERESIS. A quick glance does not count: off work must last
gaze.min_away_seconds before "looked away", and on work gaze.min_back_seconds
before "back on work" -- both measured in time, never in frames. The events
carry the moment the change really began, not when it was confirmed.

CALIBRATION. The zones come from data/calibration/<user>/<setup>.json (see
calibration.py), else the defaults ("head roughly facing the screen"),
flagged uncalibrated. Call use_calibration() to switch person mid-run.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from .. import landmarks as lm
from ..baseline import Baseline
from ..config import PerceptionConfig
from ..extractors import Extractor, FaceEvent, Output
from ..observation import FaceObservation
from .calibration import CalibrationStore, setup_name, zone_model_from
from .config import GazeConfig
from .estimate import MODE_IRIS, GazeEstimator, blendshape_gaze
from .zones import WORK, ZONES, ZoneModel

MAX_GAP_SECONDS = 0.5     # a longer gap between frames restarts the smoothing
MAX_WEIGHT = 0.5          # seconds one frame can stand for in the windows (as in A9)


class GazeExtractor(Extractor):
    name = "gaze"
    columns = ("gaze_yaw", "gaze_pitch", "gaze_zone", "gaze_on_work", "gaze_confidence",
               "gaze_mode", "gaze_calibrated", "away_s")

    def __init__(self, config: Optional[GazeConfig] = None, perception: Optional[PerceptionConfig] = None,
                 zones: Optional[ZoneModel] = None, store: Optional[CalibrationStore] = None,
                 user: str = "default", setup: Optional[str] = None) -> None:
        super().__init__(perception)
        self.gaze = config or GazeConfig()
        self.estimator = GazeEstimator(self.gaze)
        self.store = store or CalibrationStore()
        self.user, self.setup_label = user, setup
        self.zones = zones or ZoneModel.default(self.gaze)
        self._fixed_zones = zones is not None          # tests: never replaced by a file
        self._loaded_for: Optional[Tuple[str, str]] = None
        self._records: List[Tuple[str, Dict[str, Any]]] = []
        self._history: Deque[Tuple[float, bool, str, float]] = deque()   # (t, off, zone, weight)
        self._episodes: Deque[Tuple[float, float]] = deque()             # (start, end) of finished aways
        self._agree: Deque[float] = deque(maxlen=300)                    # iris vs blendshape agreement
        self.reset()

    # --- calibration -----------------------------------------------------------------------

    def use_calibration(self, user: str, source: str) -> None:
        """Load (or fall back from) the calibration for this person and camera."""
        setup = setup_name(source, self.setup_label)
        if self._fixed_zones or self._loaded_for == (user, setup):
            return
        self._loaded_for, self.user = (user, setup), user
        section = self.store.section(user, setup, "gaze")
        self.zones = zone_model_from(section, self.gaze, label=f"{user}/{setup}")
        status: Dict[str, Any] = {"calibrated": self.zones.calibrated, "user": user, "setup": setup}
        if not self.zones.calibrated:
            status["note"] = ("no calibration for this person and camera: default work area in use. "
                              "Run: python -m eepy.perception.calibrate_gaze")
        self._records.append(("gaze_status", status))

    # --- the plug-in interface ---------------------------------------------------------------

    def reset(self) -> None:
        self.estimator.reset()
        self._smooth: Optional[Tuple[float, float]] = None
        self._last_t: Optional[float] = None
        self._mode = MODE_IRIS
        self._state: Optional[str] = None           # "on" / "off" (confirmed)
        self._candidate: Optional[Tuple[str, float]] = None
        self._away_since: Optional[float] = None
        self._away_zones: Counter = Counter()

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        if obs.frame is not None:
            self.use_calibration(self.user, obs.source)
        values: Dict[str, Any] = {c: None for c in self.columns}
        values["gaze_calibrated"] = 1 if self.zones.calibrated else 0
        estimate = None
        if obs.present and obs.landmarks is not None:
            points = obs.points()
            ear = (lm.eye_aspect_ratio(points, lm.RIGHT_EYE) + lm.eye_aspect_ratio(points, lm.LEFT_EYE)) / 2
            estimate = self.estimator.estimate(obs, baseline.eye_closure(ear))
        if estimate is None:
            self._smooth = None
            zone, on_work, confidence = "no_face", 0.0, 1.0 if not obs.present else 0.3
        else:
            yaw, pitch = self._smoothed(obs.t, estimate.yaw, estimate.pitch, estimate.mode)
            hit = self.zones.classify(yaw, pitch, iris_mode=estimate.mode == MODE_IRIS)
            zone, on_work = hit.zone, hit.on_work
            confidence = min(1.0, 0.3 + hit.edge_deg / (3 * self.zones.softness))
            confidence *= 1.0 if estimate.mode == MODE_IRIS else 0.75
            confidence *= 1.0 if self.zones.calibrated else 0.7
            values.update(gaze_yaw=round(yaw, 2), gaze_pitch=round(pitch, 2), gaze_mode=estimate.mode)
            self._cross_check(obs, estimate)
        values.update(gaze_zone=zone, gaze_on_work=round(on_work, 3), gaze_confidence=round(confidence, 3))
        events = self._track(obs.t, zone)
        values["away_s"] = round(obs.t - self._away_since, 2) if self._state == "off" and self._away_since else 0.0
        return Output(values, events)

    def drain(self) -> List[Tuple[str, Dict[str, Any]]]:
        records, self._records = self._records, []
        return records

    def summary(self, now: float) -> List[Tuple[str, Dict[str, Any]]]:
        fields: Dict[str, Any] = {"calibrated": self.zones.calibrated,
                                  "away_current_s": round(now - self._away_since, 2)
                                  if self._state == "off" and self._away_since else 0.0}
        for seconds in sorted(set(self.config.windows)):
            suffix = f"_{int(seconds)}s"
            fields.update({k + suffix: v for k, v in self._window(now, seconds).items()})
        if self._agree:
            fields["iris_blendshape_agree"] = round(sum(self._agree) / len(self._agree), 3)
        return [("gaze_window", fields)]

    # --- internals -------------------------------------------------------------------------

    def _smoothed(self, t: float, yaw: float, pitch: float, mode: int) -> Tuple[float, float]:
        tau = self.gaze.get("smoothing_seconds")
        gap = t - self._last_t if self._last_t is not None else None
        if self._smooth is None or gap is None or gap > MAX_GAP_SECONDS or mode != self._mode or tau <= 0:
            self._smooth = (yaw, pitch)              # (a mode switch jumps: the two scales differ)
        else:
            alpha = 1.0 - math.exp(-max(gap, 0.0) / tau)
            sy, sp = self._smooth
            self._smooth = (sy + alpha * (yaw - sy), sp + alpha * (pitch - sp))
        self._last_t, self._mode = t, mode
        return self._smooth

    def _cross_check(self, obs: FaceObservation, estimate: Any) -> None:
        """Do MediaPipe's eyeLook* blendshapes point the same way as the iris geometry?"""
        shapes = blendshape_gaze(obs.blendshapes)
        if shapes is None or estimate.eye_yaw is None:
            return
        h_bs = shapes[0]
        h_iris = estimate.eye_yaw / self.gaze.get("eye_gain_yaw")
        if abs(h_bs) < 0.15 and abs(h_iris) < 0.15:
            return                                     # both near centre: nothing to compare
        self._agree.append(1.0 if (h_bs > 0) == (h_iris > 0) else 0.0)

    def _track(self, t: float, zone: str) -> List[FaceEvent]:
        """Hysteresis on top of the per-frame zone: returns looked_away / back_on_work."""
        off = zone not in WORK
        weight = min(t - self._history[-1][0], MAX_WEIGHT) if self._history else 0.0
        self._history.append((t, off, zone, max(weight, 0.0)))
        horizon = t - max(self.config.windows)
        while self._history and self._history[0][0] < horizon:
            self._history.popleft()
        while self._episodes and self._episodes[0][1] < horizon:
            self._episodes.popleft()
        if off and self._state == "off":
            self._away_zones[zone] += 1
        wanted = "off" if off else "on"
        if self._state is None:                        # first frames: settle without an event
            self._state = wanted
            if off:
                self._away_since = t
            return []
        if wanted == self._state:
            self._candidate = None
            return []
        if self._candidate is None or self._candidate[0] != wanted:
            self._candidate = (wanted, t)
        since = self._candidate[1]
        needed = self.gaze.get("min_away_seconds" if wanted == "off" else "min_back_seconds")
        if t - since < needed:
            return []
        self._state, self._candidate = wanted, None
        if wanted == "off":
            self._away_since = since
            self._away_zones = Counter({zone: 1})
            return [FaceEvent("looked_away", since, {"zone": zone})]
        start = self._away_since if self._away_since is not None else since
        away = round(since - start, 2)
        self._episodes.append((start, since))
        was = self._away_zones.most_common(1)[0][0] if self._away_zones else "away"
        self._away_since = None
        return [FaceEvent("back_on_work", since, {"away_seconds": away, "zone_was": was})]

    def _window(self, now: float, seconds: float) -> Dict[str, Any]:
        start = now - seconds
        rows = [r for r in self._history if r[0] >= start]
        total = sum(w for _, _, _, w in rows)
        out: Dict[str, Any] = {}
        for name in ZONES:
            out[f"{name}_ratio"] = round(sum(w for _, _, z, w in rows if z == name) / total, 3) if total else None
        out["off_work_ratio"] = round(sum(w for _, o, _, w in rows if o) / total, 3) if total else None
        episodes = [(s, e) for s, e in self._episodes if e >= start]
        ongoing = now - self._away_since if self._state == "off" and self._away_since else None
        durations = [e - s for s, e in episodes] + ([ongoing] if ongoing is not None else [])
        out["away_episodes"] = len([s for s, _ in episodes if s >= start]) + \
            (1 if ongoing is not None and self._away_since >= start else 0)
        out["away_mean_s"] = round(sum(durations) / len(durations), 2) if durations else None
        out["away_longest_s"] = round(max(durations), 2) if durations else None
        return out
