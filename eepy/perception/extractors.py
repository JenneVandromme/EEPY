"""Feature extractors: plug-ins that turn FaceObservations into numbers and events.

    class MyExtractor(Extractor):
        name = "my"
        columns = ("my_value",)                       # per-frame numbers it adds
        def update(self, obs, baseline) -> Output:
            return Output({"my_value": 1.0}, [])      # values, events

A9 provides PresenceExtractor, EyeExtractor, MouthExtractor and HeadExtractor.
Py-Feat (A10) and gaze (A11) plug in the same way and receive the same
FaceObservation, so the face is found only once per frame.

TIME. Every duration is computed from frame timestamps (obs.t), never from
frame counts: the robot's camera rate is low and varies. Where a threshold is
crossed between two frames, the crossing time is interpolated, so a blink
measured at 10 fps and at 30 fps comes out (nearly) the same length.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import landmarks as lm
from .baseline import Baseline
from .config import PerceptionConfig
from .observation import FaceObservation

# A gap between frames longer than this breaks any blink/yawn/nod in progress:
# nobody can tell what happened in between.
MAX_GAP_SECONDS = 0.5


@dataclass(frozen=True)
class FaceEvent:
    """Something that happened over time. `t` is when it started; `fields`
    are its details (all numbers or short text, ready for the event log)."""

    type: str               # blink, yawn, nod, face_lost, face_back
    t: float
    fields: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Output:
    values: Dict[str, Optional[float]]
    events: List[FaceEvent] = field(default_factory=list)


class Extractor:
    """Base class for feature plug-ins. See the module docstring."""

    name = "extractor"
    columns: Tuple[str, ...] = ()

    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        self.config = config or PerceptionConfig()

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        raise NotImplementedError

    def reset(self) -> None:
        """Forget everything in progress (e.g. after the camera reconnected)."""

    def _empty(self) -> Dict[str, Optional[float]]:
        return {c: None for c in self.columns}


def _cross(t0: float, v0: float, t1: float, v1: float, level: float) -> float:
    """When a signal going from v0 (at t0) to v1 (at t1) passed `level`, assuming
    it changed linearly in between."""
    if v1 == v0:
        return t1
    share = (level - v0) / (v1 - v0)
    return t0 + min(1.0, max(0.0, share)) * (t1 - t0)


# --- presence ------------------------------------------------------------------------------


class PresenceExtractor(Extractor):
    """face_present (1/0), face_size (face height / picture height: bigger =
    closer to the camera). Events face_lost / face_back."""

    name = "presence"
    columns = ("face_present", "face_size")

    def reset(self) -> None:
        self._state = "unknown"       # unknown, present, lost
        self._last_seen: Optional[float] = None
        self._first_t: Optional[float] = None
        self._back_since: Optional[float] = None
        self._lost_since: Optional[float] = None

    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        super().__init__(config)
        self.reset()

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        lost_after = self.config.get("face", "lost_seconds")
        back_after = self.config.get("face", "back_seconds")
        events: List[FaceEvent] = []
        if self._first_t is None:
            self._first_t = obs.t
        if obs.present:
            if self._state == "lost":
                self._back_since = self._back_since if self._back_since is not None else obs.t
                if obs.t - self._back_since >= back_after:
                    away = self._back_since - (self._lost_since or self._back_since)
                    events.append(FaceEvent("face_back", self._back_since,
                                            {"away_seconds": round(away, 2)}))
                    self._state, self._back_since = "present", None
            else:
                self._state = "present"
            self._last_seen = obs.t if self._state == "present" else self._last_seen
            assert obs.face_box is not None
            size = obs.face_box.h / obs.height if obs.height else None
            return Output({"face_present": 1.0, "face_size": None if size is None else round(size, 4)},
                          events)
        self._back_since = None
        since = self._last_seen if self._last_seen is not None else self._first_t
        if self._state != "lost" and obs.t - since >= lost_after:
            events.append(FaceEvent("face_lost", since, {}))
            self._state, self._lost_since = "lost", since
        return Output({"face_present": 0.0, "face_size": None}, events)


# --- eyes ------------------------------------------------------------------------------------


class EyeExtractor(Extractor):
    """Eye openness and blinks.

    ear_left/right/ear   Eye Aspect Ratio: eyelid opening / eye width (~0.28
                         open, ~0.05 shut). Per eye and the mean of both.
    eye_closure          0 = open as usual for this person, 1 = shut
                         (relative to Baseline.ear_open / ear_floor).
    blink_left/right     MediaPipe's own eyeBlink blendshapes (0-1): a
                         cross-check, computed by a separate model.
    Event `blink`        eye_closure rose past blink.close and fell back below
                         blink.open; duration_ms is measured at the blink.close
                         level, interpolated between frames; `long` when longer
                         than blink.long_ms.
    """

    name = "eyes"
    columns = ("ear_left", "ear_right", "ear", "eye_closure", "blink_left", "blink_right")

    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        super().__init__(config)
        self.reset()

    def reset(self) -> None:
        self._prev: Optional[Tuple[float, float]] = None   # (t, closure)
        self._in_blink = False
        self._start = 0.0
        self._last_down: Optional[float] = None

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        if not obs.present:
            self.reset()
            return Output(self._empty())
        points = obs.points()
        right = lm.eye_aspect_ratio(points, lm.RIGHT_EYE)
        left = lm.eye_aspect_ratio(points, lm.LEFT_EYE)
        ear = (left + right) / 2
        closure = baseline.eye_closure(ear)
        values = {"ear_left": round(left, 4), "ear_right": round(right, 4), "ear": round(ear, 4),
                  "eye_closure": round(closure, 4),
                  "blink_left": _shape(obs, "eyeBlinkLeft"), "blink_right": _shape(obs, "eyeBlinkRight")}
        return Output(values, self.track(obs.t, closure))

    def track(self, t: float, closure: float) -> List[FaceEvent]:
        """The blink state machine on its own: also used by the tests."""
        close = self.config.get("blink", "close")
        reopen = self.config.get("blink", "open")
        events: List[FaceEvent] = []
        prev = self._prev
        if prev is not None and t - prev[0] > MAX_GAP_SECONDS:
            self.reset()
            prev = None
        if prev is not None:
            t0, c0 = prev
            if not self._in_blink and c0 < close <= closure:
                self._in_blink, self._start, self._last_down = True, _cross(t0, c0, t, closure, close), None
            elif self._in_blink:
                if c0 >= close > closure:
                    self._last_down = _cross(t0, c0, t, closure, close)
                elif closure >= close:
                    self._last_down = None    # closed again before reopening: same blink
                if closure < reopen:
                    end = self._last_down if self._last_down is not None else t
                    duration_ms = (end - self._start) * 1000
                    self._in_blink = False
                    if duration_ms >= self.config.get("blink", "min_ms"):
                        events.append(FaceEvent("blink", self._start, {
                            "duration_ms": round(duration_ms, 1),
                            "long": duration_ms >= self.config.get("blink", "long_ms")}))
        # (On the very first frame there is no previous one: a blink already
        # under way then has an unknown start, so it is not counted.)
        self._prev = (t, closure)
        return events


def _shape(obs: FaceObservation, name: str) -> Optional[float]:
    value = obs.blendshapes.get(name)
    return None if value is None else round(value, 4)


# --- mouth -------------------------------------------------------------------------------------


class MouthExtractor(Extractor):
    """Mouth opening and yawns.

    mar         Mouth Aspect Ratio: inner-lip opening / mouth width (~0 closed,
                0.1-0.4 talking, 0.6+ yawning).
    jaw_open    MediaPipe's jawOpen blendshape (0-1).
    Event `yawn`  mouth wide open (jaw_open >= yawn.jaw_open AND mar >=
                  baseline + yawn.mar) for at least yawn.min_seconds; dips
                  shorter than yawn.gap_seconds do not end it. Talking opens
                  the mouth for well under a second at a time, so it does not
                  count.
    """

    name = "mouth"
    columns = ("mar", "jaw_open")

    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        super().__init__(config)
        self.reset()

    def reset(self) -> None:
        self._open_since: Optional[float] = None
        self._last_open: Optional[float] = None
        self._last_t: Optional[float] = None

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        if not obs.present:
            events = self._finish()
            self.reset()
            return Output(self._empty(), events)
        mar = lm.mouth_aspect_ratio(obs.points())
        jaw = obs.blendshapes.get("jawOpen")
        values = {"mar": round(mar, 4), "jaw_open": None if jaw is None else round(jaw, 4)}
        return Output(values, self.track(obs.t, mar, jaw, baseline))

    def track(self, t: float, mar: float, jaw: Optional[float], baseline: Baseline) -> List[FaceEvent]:
        events: List[FaceEvent] = []
        if self._last_t is not None and t - self._last_t > MAX_GAP_SECONDS:
            events += self._finish()
            self.reset()
        self._last_t = t
        wide = mar >= baseline.mar_closed + self.config.get("yawn", "mar") and (
            jaw is None or jaw >= self.config.get("yawn", "jaw_open"))
        if wide:
            if self._open_since is None:
                self._open_since = t
            self._last_open = t
        elif self._last_open is not None and t - self._last_open > self.config.get("yawn", "gap_seconds"):
            events += self._finish()
        return events

    def _finish(self) -> List[FaceEvent]:
        events = []
        if self._open_since is not None and self._last_open is not None:
            duration = self._last_open - self._open_since
            if duration >= self.config.get("yawn", "min_seconds"):
                events.append(FaceEvent("yawn", self._open_since, {"duration_s": round(duration, 2)}))
        self._open_since = self._last_open = None
        return events


# --- head --------------------------------------------------------------------------------------


class HeadExtractor(Extractor):
    """Head angles and nods.

    head_yaw/pitch/roll   degrees, for the PERSON: yaw + = turned to their
                          left, pitch + = chin down, roll + = tipped to their left ear.
    Event `nod`  the "nodding off" pattern: the head drops (chin down) at least
                 nod.drop_deg below its usual position (median of the last
                 nod.median_seconds) within nod.drop_seconds, then comes back up
                 at least nod.recover_fraction of the way within
                 nod.recover_seconds. depth_deg = how far it dropped.
    """

    name = "head"
    columns = ("head_yaw", "head_pitch", "head_roll")

    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        super().__init__(config)
        self.reset()

    def reset(self) -> None:
        self._history: Deque[Tuple[float, float]] = deque()
        self._state = "idle"           # idle, down
        self._ref = 0.0
        self._start = 0.0
        self._peak = 0.0
        self._peak_t = 0.0
        self._last_t: Optional[float] = None

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        if not obs.present or obs.head is None:
            self.reset()
            return Output(self._empty())
        h = obs.head
        values = {"head_yaw": round(h.yaw, 2), "head_pitch": round(h.pitch, 2), "head_roll": round(h.roll, 2)}
        return Output(values, self.track(obs.t, h.pitch))

    def track(self, t: float, pitch: float) -> List[FaceEvent]:
        cfg = self.config
        drop, drop_s = cfg.get("nod", "drop_deg"), cfg.get("nod", "drop_seconds")
        events: List[FaceEvent] = []
        if self._last_t is not None and t - self._last_t > MAX_GAP_SECONDS:
            self.reset()
        self._last_t = t
        history = self._history
        if self._state == "idle" and len(history) >= 2:
            span = t - history[0][0]
            ref = float(np.median([p for _, p in history]))
            recent = [(ht, p) for ht, p in history if t - ht <= drop_s]
            if span >= min(1.0, cfg.get("nod", "median_seconds")) and recent:
                low = min(p for _, p in recent)
                if pitch - ref >= drop and pitch - low >= drop:
                    # The drop began at the LAST moment the head was still at its low point.
                    start = max(ht for ht, p in recent if p <= low + 1.0)
                    self._state, self._ref, self._start = "down", ref, start
                    self._peak, self._peak_t = pitch, t
        elif self._state == "down":
            if pitch > self._peak:
                self._peak, self._peak_t = pitch, t
            depth = self._peak - self._ref
            if self._peak - pitch >= cfg.get("nod", "recover_fraction") * depth:
                events.append(FaceEvent("nod", self._start, {
                    "depth_deg": round(depth, 1), "duration_s": round(t - self._start, 2)}))
                self._state = "idle"
                history.clear()        # the dip must not drag the next reference down
            elif t - self._peak_t > cfg.get("nod", "recover_seconds"):
                self._state = "idle"   # stayed down: a lowered head, not a nod
                history.clear()
        if self._state == "idle":
            history.append((t, pitch))
            while history and t - history[0][0] > cfg.get("nod", "median_seconds"):
                history.popleft()
        return events


def default_extractors(config: Optional[PerceptionConfig] = None) -> List[Extractor]:
    return [PresenceExtractor(config), EyeExtractor(config), MouthExtractor(config),
            HeadExtractor(config)]


def all_columns(extractors: Sequence[Extractor]) -> List[str]:
    columns: List[str] = []
    for extractor in extractors:
        columns += [c for c in extractor.columns if c not in columns]
    return columns
