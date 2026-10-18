"""Windowed features: summaries over the last 10 s and 60 s, about once a second.

These are what the HMM will consume later. Everything is weighted by TIME, not
by frame count: each frame stands for the time until the next one (at most
MAX_WEIGHT seconds -- a long gap is unknown time, not more of the same), so a
dip in frame rate does not shift the ratios.

Per window (suffix _10s / _60s in the event log):
    perclos            share of the JUDGED eye time with the eyes >= 80 % shut (P80)
    blink_rate         blinks per minute of judged eye time
    eyes_measured_s    seconds the eyes were judged (looking roughly forward; see
                       EyeExtractor.eyes_judged). Eye figures over little time are noise
    blink_mean_ms      mean blink duration
    blink_max_ms       the longest eye closure (a microsleep is > 1500 ms)
    long_blinks        blinks longer than blink.long_ms
    yawns, nods        counts
    look_away_ratio    share of face-present time with the head turned > look_away_deg
    pitch_mean/var     head pitch (chin down +), degrees / degrees squared
    face_absent_ratio  share of the time with no face
    fps                frames per second actually processed
    blink_confidence   "ok", or "low" below ~min_blink_fps (blink figures unreliable)
    blink_rate_rel     blink rate / the person's usual rate (A12 baseline; else None)
    blink_mean_ms_rel  mean blink duration / their usual duration
    coverage_s         seconds of data actually in the window (less at start-up)

Once per summary (no suffix):
    eyes_closed_now_s    how long the eyes have been closed right now (0 when open);
                         a blink event only exists once the eyes reopen
    baseline_calibrated  true when the values are relative to this person (A12)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

from .baseline import Baseline
from .config import PerceptionConfig
from .extractors import FaceEvent

MAX_WEIGHT = 0.5   # seconds one frame can stand for


@dataclass
class _Sample:
    t: float
    present: bool
    judged: bool            # eyes judged (looking roughly forward)
    closed: bool
    away: bool
    pitch: Optional[float]
    weight: float = 0.0     # filled in when the next frame arrives


class WindowAggregator:
    def __init__(self, config: Optional[PerceptionConfig] = None) -> None:
        self.config = config or PerceptionConfig()
        self.windows = tuple(sorted(set(self.config.windows)))
        self._samples: Deque[_Sample] = deque()
        self._events: Deque[FaceEvent] = deque()
        self._next_emit: Optional[float] = None

    def add(self, t: float, values: Dict[str, Optional[float]], events: List[FaceEvent],
            baseline: Baseline) -> Optional[Dict[str, Any]]:
        """Feed one frame. Returns the window summary when one is due, else None."""
        present = bool(values.get("face_present"))
        closure = values.get("eye_closure")
        yaw, pitch = values.get("head_yaw"), values.get("head_pitch")
        sample = _Sample(
            t=t, present=present, judged=present and closure is not None,
            closed=present and closure is not None and closure >= self.config.get("eyes", "perclos_closure"),
            away=present and yaw is not None and abs(yaw - baseline.yaw0) > self.config.get("", "look_away_deg"),
            pitch=pitch if present else None)
        self._baseline = baseline
        if self._samples:
            last = self._samples[-1]
            last.weight = max(0.0, min(t - last.t, MAX_WEIGHT))
        self._samples.append(sample)
        self._events.extend(events)
        horizon = t - self.windows[-1]
        while self._samples and self._samples[0].t < horizon:
            self._samples.popleft()
        while self._events and self._events[0].t < horizon:
            self._events.popleft()
        if self._next_emit is None:
            self._next_emit = t + self.config.get("", "window_every_seconds")
        if t < self._next_emit:
            return None
        self._next_emit += self.config.get("", "window_every_seconds")
        if self._next_emit <= t:           # after a long pause: do not emit a burst
            self._next_emit = t + self.config.get("", "window_every_seconds")
        return self.summary(t)

    def summary(self, now: float) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for seconds in self.windows:
            suffix = f"_{int(seconds)}s"
            for key, value in self._window(now, seconds).items():
                out[key + suffix] = value
        out["eyes_closed_now_s"] = self._closed_now(now)
        base: Optional[Baseline] = getattr(self, "_baseline", None)
        out["baseline_calibrated"] = bool(base is not None and base.calibrated)
        return out

    def _closed_now(self, now: float) -> float:
        """Seconds the eyes have been closed (P80) without a break, up to `now`."""
        start = None
        for sample in reversed(self._samples):
            if not sample.closed:
                break
            start = sample.t
        return round(now - start, 2) if start is not None else 0.0

    def _window(self, now: float, seconds: float) -> Dict[str, Any]:
        start = now - seconds
        samples = [s for s in self._samples if s.t >= start]
        events = [e for e in self._events if e.t >= start]
        total = sum(s.weight for s in samples)
        present = [s for s in samples if s.present]
        present_time = sum(s.weight for s in present)
        span = samples[-1].t - samples[0].t if len(samples) > 1 else 0.0
        fps = (len(samples) - 1) / span if span > 0 else 0.0

        def share(items: List[_Sample], of: float) -> Optional[float]:
            return round(sum(s.weight for s in items) / of, 4) if of > 0 else None

        blinks = [e for e in events if e.type == "blink"]
        pitches = [(s.pitch, s.weight) for s in present if s.pitch is not None and s.weight > 0]
        weight = sum(w for _, w in pitches)
        mean = sum(p * w for p, w in pitches) / weight if weight > 0 else None
        var = (sum(w * (p - mean) ** 2 for p, w in pitches) / weight) if mean is not None else None
        judged = [s for s in present if s.judged]
        judged_time = sum(s.weight for s in judged)
        enough_face = judged_time >= 0.1 * seconds
        return {
            "perclos": share([s for s in judged if s.closed], judged_time),
            "eyes_measured_s": round(judged_time, 1),
            "blink_rate": round(len(blinks) / (judged_time / 60), 1) if enough_face else None,
            "blink_mean_ms": round(sum(e.fields["duration_ms"] for e in blinks) / len(blinks), 1) if blinks else None,
            "blink_max_ms": round(max(e.fields["duration_ms"] for e in blinks), 1) if blinks else None,
            "long_blinks": sum(1 for e in blinks if e.fields.get("long")),
            "yawns": sum(1 for e in events if e.type == "yawn"),
            "nods": sum(1 for e in events if e.type == "nod"),
            "look_away_ratio": share([s for s in present if s.away], present_time),
            "pitch_mean": None if mean is None else round(mean, 2),
            "pitch_var": None if var is None else round(var, 2),
            "face_absent_ratio": share([s for s in samples if not s.present], total),
            "fps": round(fps, 1),
            # 10 % slack: a camera set to 15 fps measures 14.8-15.1.
            "blink_confidence": "ok" if fps >= 0.9 * self.config.get("", "min_blink_fps") else "low",
            "coverage_s": round(total, 1),
            **self._relative(blinks, judged_time, enough_face),
        }

    def _relative(self, blinks: List[FaceEvent], present_time: float, enough_face: bool) -> Dict[str, Any]:
        """Blink rate and duration relative to the person's own (A12): 1.5 = 50 % more
        than usual. None without a calibrated baseline."""
        base: Optional[Baseline] = getattr(self, "_baseline", None)
        out: Dict[str, Any] = {"blink_rate_rel": None, "blink_mean_ms_rel": None}
        if base is None or not base.calibrated:
            return out
        if base.blink_rate and enough_face:
            out["blink_rate_rel"] = round((len(blinks) / (present_time / 60)) / base.blink_rate, 3)
        if base.blink_ms and blinks:
            mean = sum(e.fields["duration_ms"] for e in blinks) / len(blinks)
            out["blink_mean_ms_rel"] = round(mean / base.blink_ms, 3)
        return out
