"""BaselineCollector: ~60 s of normal work -> this person's baseline.

Feed it every PerceptionResult while the person works as usual; it keeps the
per-frame numbers and the events, then computes ROBUST statistics:

  1. drop frames without a face, in or near a blink (+-150 ms), during a yawn,
     and while looking away (gaze off work, or head > 25 deg from its median);
  2. drop outliers beyond median +- 3 MAD (median absolute deviation);
  3. medians (and P10/P90 as the spread; P25 for the resting mouth, so talking
     while working does not raise it).

Quality checks decide whether to accept, extend (up to `max_extra` seconds)
or give up -- each with a message for the person, in plain language.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import PerceptionConfig

BLINK_MARGIN = 0.15          # seconds around a blink that are not "open eye"
LOOK_AWAY_DEG = 25.0         # head further than this from its median = looking away
MIN_BLINKS_FOR_FLOOR = 3
LATE_FROM, LATE_UNTIL = 21, 6  # a baseline taken 21:00-06:00 may already be tired
# What counts as "not working" during the baseline minute. NOT down_other: typing means
# looking at the keyboard, which lies below the screen and outside the notes (found
# live: with it, most of a normal working minute was thrown away).
OFF_WORK = ("away", "no_face")


@dataclass
class Frame:
    t: float
    present: bool
    values: Dict[str, Any]


@dataclass
class Verdict:
    """What to do now: "collect" (keep going), "extend", "accept" or "fail"."""

    action: str
    message: str = ""
    line_id: Optional[str] = None
    checks: List[Dict[str, Any]] = field(default_factory=list)


def _median(xs: List[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _percentile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    pos = (len(xs) - 1) * q
    low, high = int(pos), min(int(pos) + 1, len(xs) - 1)
    return xs[low] + (xs[high] - xs[low]) * (pos - low)


def robust(xs: List[float]) -> List[float]:
    """Drop outliers beyond median +- 3 MAD (kept as is when there is no spread)."""
    if len(xs) < 5:
        return list(xs)
    mid = statistics.median(xs)
    mad = statistics.median(abs(x - mid) for x in xs)
    if mad <= 1e-9:
        return list(xs)
    return [x for x in xs if abs(x - mid) <= 3 * 1.4826 * mad]


class BaselineCollector:
    def __init__(self, config: Optional[PerceptionConfig] = None, seconds: float = 60.0,
                 max_extra: float = 30.0) -> None:
        self.config = config or PerceptionConfig()
        self.seconds, self.max_extra = seconds, max_extra
        self.frames: List[Frame] = []
        self.blinks: List[Tuple[float, float]] = []      # (start, end)
        self.yawns: List[Tuple[float, float]] = []
        self.started: Optional[float] = None
        self.extended = False

    # --- collecting -------------------------------------------------------------------------

    def add(self, result: Any) -> None:
        obs, values = result.observation, result.values
        if self.started is None:
            self.started = obs.t
        self.frames.append(Frame(obs.t, bool(obs.present), dict(values)))
        for event in result.events:
            if event.type == "blink":
                self.blinks.append((event.t, event.t + event.fields["duration_ms"] / 1000))
            elif event.type == "yawn":
                self.yawns.append((event.t, event.t + event.fields["duration_s"]))

    @property
    def elapsed(self) -> float:
        return self.frames[-1].t - self.frames[0].t if len(self.frames) > 1 else 0.0

    @property
    def target(self) -> float:
        return self.seconds + (self.max_extra if self.extended else 0.0)

    # --- deciding ---------------------------------------------------------------------------

    def evaluate(self) -> Verdict:
        """Call any time; only decides once `seconds` (or the extension) have passed."""
        if self.elapsed < self.seconds - 0.1:          # (frames never land exactly on it)
            return Verdict("collect")
        checks, values = self._checks()
        enough = next((c["ok"] for c in checks if c["check"] == "enough_clean_data"), True)
        if not enough:
            # Too little usable data to judge the values themselves: they are not "unusual",
            # just unknown. Keep collecting (the extension) instead of failing on them.
            for c in checks:
                if c["kind"] == "fail" and c["check"] not in ("frame_rate",):
                    c.update(ok=True, kind="skipped", message="")
        hard = [c for c in checks if not c["ok"] and c["kind"] == "fail"]
        soft = [c for c in checks if not c["ok"] and c["kind"] == "extend"]
        if hard:
            return Verdict("fail", hard[0]["message"], "calibration_failed", checks)
        if soft and self.elapsed < self.seconds + self.max_extra - 0.1:
            if not self.extended:
                self.extended = True
                return Verdict("extend", soft[0]["message"], "calibration_baseline_extend", checks)
            return Verdict("collect", checks=checks)
        if soft:
            return Verdict("fail", soft[0]["message"], "calibration_failed", checks)
        return Verdict("accept", "", "calibration_done", checks)

    def _durations(self) -> List[float]:
        """Seconds each frame stands for (time to the next frame, at most 0.5 s)."""
        ts = [f.t for f in self.frames]
        return [min(b - a, 0.5) for a, b in zip(ts, ts[1:])] + [0.0]

    def _excluded(self, frame: Frame, yaw_mid: Optional[float]) -> bool:
        if not frame.present:
            return True
        t, v = frame.t, frame.values
        if any(a - BLINK_MARGIN <= t <= b + BLINK_MARGIN for a, b in self.blinks):
            return True
        if any(a <= t <= b for a, b in self.yawns):
            return True
        zone = v.get("gaze_zone")
        if zone in OFF_WORK:
            return True
        yaw = v.get("head_yaw")
        return yaw_mid is not None and yaw is not None and abs(yaw - yaw_mid) > LOOK_AWAY_DEG

    def _off_work(self, frame: Frame, yaw_mid: Optional[float]) -> bool:
        if not frame.present:
            return True
        zone = frame.values.get("gaze_zone")
        if zone is not None:
            return zone in OFF_WORK
        yaw = frame.values.get("head_yaw")
        return yaw_mid is not None and yaw is not None and abs(yaw - yaw_mid) > LOOK_AWAY_DEG

    def _checks(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        durations = self._durations()
        elapsed = max(self.elapsed, 1e-6)
        yaws = [f.values["head_yaw"] for f in self.frames if f.present and f.values.get("head_yaw") is not None]
        yaw_mid = _median(yaws)
        present = sum(d for f, d in zip(self.frames, durations) if f.present)
        clean = sum(d for f, d in zip(self.frames, durations) if not self._excluded(f, yaw_mid))
        off, longest, run = 0.0, 0.0, 0.0
        for f, d in zip(self.frames, durations):
            if self._off_work(f, yaw_mid):
                off += d
                run += d
                longest = max(longest, run)
            else:
                run = 0.0
        # Frame rate WHILE pictures arrive; pauses (the camera moved, covered, taken
        # by another program) are their own check -- else a pause read as "slow camera".
        steps = [b.t - a.t for a, b in zip(self.frames, self.frames[1:])]
        flowing = [d for d in steps if d <= 1.0]
        fps = len(flowing) / sum(flowing) if flowing and sum(flowing) > 0 else 0.0
        paused = sum(d for d in steps if d > 1.0)
        values = self.values()
        min_fps = 0.9 * self.config.get("", "min_blink_fps")
        checks: List[Dict[str, Any]] = []

        def check(name: str, ok: bool, kind: str, measured: Any, message: str) -> None:
            checks.append({"check": name, "ok": bool(ok), "kind": kind, "measured": measured,
                           "message": "" if ok else message})

        check("camera_kept_sending", paused <= 3.0, "extend", round(paused, 1),
              f"The camera stopped sending pictures for {paused:.0f} seconds (was the computer moved, "
              "the camera covered, or taken by another program?). Keep it still and in view; "
              "the minute continues.")
        seen = max(sum(durations), 1e-6)            # time covered by pictures (pauses excluded)
        check("face_visible", present / seen >= 0.85, "extend", round(present / seen, 2),
              "Your face was out of view too much. Please sit so your face stays inside "
              "the blue rectangle, facing the screen.")
        check("frame_rate", fps >= min_fps, "fail", round(fps, 1),
              f"The camera delivered only {fps:.0f} frames per second; at least {min_fps:.0f} are "
              "needed to measure blinks. Close other programs that use the computer heavily, "
              "or use a better-lit room, then try again.")
        check("looked_away", off / seen <= 0.20 and longest <= 8.0, "extend",
              {"share": round(off / seen, 2), "longest_s": round(longest, 1)},
              "You looked away quite a lot. Just keep working normally at your screen and notes "
              "for a little longer.")
        check("enough_clean_data", clean >= 30.0, "extend", round(clean, 1),
              "There was not enough calm, ordinary working time yet. Please keep working normally.")
        ear, floor = values.get("ear_open"), values.get("ear_floor")
        check("eye_openness", ear is not None and 0.15 <= ear <= 0.6, "fail", ear,
              "Your eyes could not be measured reliably (very unusual eye openness). Check the "
              "lighting on your face and that nothing covers your eyes, then try again.")
        check("closed_eye", ear is None or floor is None or floor < 0.6 * ear, "fail", floor,
              "Your blinks could not be told apart from open eyes. Check the lighting on your "
              "face, then try again.")
        mar = values.get("mar_closed")
        check("mouth_rest", mar is None or mar <= 0.2, "fail", mar,
              "Your mouth seemed open most of the time. Please keep it relaxed and closed while "
              "working, then try again.")
        for axis in ("yaw0", "pitch0"):
            angle = values.get(axis)
            check(f"head_{axis[:-1]}", angle is None or abs(angle) <= 35, "fail", angle,
                  "Your head was turned far from the camera the whole time. Sit facing the screen "
                  "(and the camera), then try again.")
        size = values.get("face_size0")
        check("distance", size is None or 0.1 <= size <= 0.9, "fail", size,
              "You sat too far from (or too close to) the camera. Sit at your normal working "
              "distance, then try again.")
        rate = values.get("blink_rate")
        check("blink_rate", rate is None or 2 <= rate <= 60, "warning", rate,
              f"Unusual blink rate ({rate} per minute) -- accepted, but worth a look." if rate else "")
        return checks, values

    # --- the baseline -----------------------------------------------------------------------

    def values(self) -> Dict[str, Any]:
        """The baseline values (robust statistics over the clean frames)."""
        yaws = [f.values["head_yaw"] for f in self.frames if f.present and f.values.get("head_yaw") is not None]
        yaw_mid = _median(yaws)
        clean = [f for f in self.frames if not self._excluded(f, yaw_mid)]

        def col(name: str) -> List[float]:
            return robust([float(f.values[name]) for f in clean if f.values.get(name) is not None])

        out: Dict[str, Any] = {}
        ear = col("ear")
        out["ear_open"] = _round(_median(ear), 4)
        out["ear_open_left"] = _round(_median(col("ear_left")), 4)
        out["ear_open_right"] = _round(_median(col("ear_right")), 4)
        minima = []
        for a, b in self.blinks:
            dips = [f.values["ear"] for f in self.frames if a <= f.t <= b and f.values.get("ear") is not None]
            if dips:
                minima.append(min(dips))
        if len(minima) >= MIN_BLINKS_FOR_FLOOR:
            out["ear_floor"] = _round(_median(minima), 4)
        elif out["ear_open"] is not None:
            out["ear_floor"] = round(0.18 * out["ear_open"], 4)
        out["mar_closed"] = _round(_percentile(col("mar"), 0.25), 4)
        for axis in ("yaw", "pitch", "roll"):
            out[f"{axis}0"] = _round(_median(col(f"head_{axis}")), 2)
        out["face_size0"] = _round(_median(col("face_size")), 4)
        present_time = sum(d for f, d in zip(self.frames, self._durations()) if f.present)
        out["blink_rate"] = round(len(self.blinks) / (present_time / 60), 1) if present_time > 10 else None
        out["blink_ms"] = _round(_median([(b - a) * 1000 for a, b in self.blinks]), 1)
        au_rest = {}
        for key in sorted({k for f in clean for k in f.values if k.startswith("au_AU") and not k.endswith("_rel")}):
            xs = col(key)
            if len(xs) >= 10:
                au_rest[key[3:]] = round(_median(xs), 4)
        out["au_rest"] = au_rest
        return out

    def spreads(self) -> Dict[str, Any]:
        yaws = [f.values["head_yaw"] for f in self.frames if f.present and f.values.get("head_yaw") is not None]
        clean = [f for f in self.frames if not self._excluded(f, _median(yaws))]
        out = {}
        for name in ("ear", "mar", "head_yaw", "head_pitch", "head_roll", "face_size"):
            xs = robust([float(f.values[name]) for f in clean if f.values.get(name) is not None])
            out[name] = {"p10": _round(_percentile(xs, 0.1), 4), "p90": _round(_percentile(xs, 0.9), 4)}
        return out

    def section(self, checks: List[Dict[str, Any]], when: Optional[float] = None) -> Dict[str, Any]:
        """The "baseline" section to store: values, spreads, quality, and when."""
        local = time.localtime(when if when is not None else time.time())
        hour = local.tm_hour
        return {
            "values": self.values(),
            "spread": self.spreads(),
            "quality": checks,
            "frames": len(self.frames),
            "seconds": round(self.elapsed, 1),
            "fps": round((len(self.frames) - 1) / max(self.elapsed, 1e-6), 1),
            "taken_at": time.strftime("%Y-%m-%d %H:%M", local),
            "local_hour": hour,
            "late_evening": hour >= LATE_FROM or hour < LATE_UNTIL,
            "kss": None,          # sleepiness rating at calibration time: filled by A15
        }


def _round(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(float(value), digits)
