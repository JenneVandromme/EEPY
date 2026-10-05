"""ActionUnitExtractor: Py-Feat AUs as a FacePipeline plug-in, running on its own.

    pipeline = FacePipeline(camera, extractors=default_extractors() + [ActionUnitExtractor()])

NEVER SLOWS THE FACE LOOP. update() only drops the newest face into a one-place
slot and returns at once. A worker thread takes whatever face is newest when it
is free: if the model is slower than the camera, faces are skipped, never
queued. So MediaPipe keeps its full rate on every machine.

ADAPTIVE RATE. The worker runs back-to-back, but never more often than the
profile's max_rate. Its achieved rate depends only on the machine (GPU or
not). If a single result takes so long that fewer than `min_rate` results per
second are possible, for `slow_grace_seconds`, the AUs PAUSE (values empty, a
clear au_status message) instead of feeding old values, and retry after
`retry_seconds`.

STALENESS. Every result keeps the timestamp of the frame it was computed on.
When it is more than `stale_seconds` older than the frame being processed, it
is stale: its values are not handed on (au_stale = 1) and not counted in the
windows.

RECORDS. Per result: a row in the session's aus.csv (t_mono = the SOURCE
frame's time). About once a second: au_window (per AU: mean probability and
share of time active, over the same 10 s / 60 s windows as A9; the achieved
rate; the cross-checks). On changes: au_status (device, profile, on/off, why).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from eepy.logs.clock import monotonic

from ..baseline import Baseline
from ..config import PerceptionConfig
from ..extractors import Extractor, FaceEvent, Output
from ..observation import FaceObservation
from .config import AUConfig, load as load_au_config
from .detector import AUDetector, AUUnavailable, aus_available, load_detector
from .device import gpu_advice

log = logging.getLogger("eepy.perception.aus")

RATE_WINDOW = 5.0     # seconds over which the achieved rate is measured
MAX_WEIGHT = 0.5      # seconds one result can stand for in the windows (as in A9)


@dataclass(frozen=True)
class AUResult:
    t: float                    # time of the frame it was computed on
    frame_index: int
    values: Dict[str, float]    # AU -> probability 0-1
    infer_ms: float
    done: float                 # when it finished (monotonic)


class ActionUnitExtractor(Extractor):
    name = "aus"
    columns: Tuple[str, ...] = ()   # per-result rows go to aus.csv, not features.csv

    def __init__(self, config: Optional[AUConfig] = None,
                 perception: Optional[PerceptionConfig] = None,
                 detector_factory: Optional[Callable[[AUConfig], AUDetector]] = None,
                 features: Any = None, clock: Callable[[], float] = monotonic,
                 start: bool = True) -> None:
        super().__init__(perception)
        self.au = config or load_au_config()
        self.units = self.au.units
        self.features = features              # FeatureStream(name="aus") or None
        self._clock = clock
        self._factory = detector_factory or self._default_factory
        self._custom_factory = detector_factory is not None    # tests: no Py-Feat needed
        self._slot: Optional[FaceObservation] = None
        self._lock = threading.Condition()
        self._stop = threading.Event()
        self._latest: Optional[AUResult] = None
        self._results: Deque[AUResult] = deque()        # for the windows
        self._done: Deque[float] = deque()              # finish times, for the rate
        self._infer: Deque[float] = deque(maxlen=50)
        self._records: List[Tuple[str, Dict[str, Any]]] = []
        self._closure: Deque[Tuple[float, float]] = deque()   # (t, eye_closure) from A9
        self._yawns: Deque[Tuple[float, float]] = deque()     # (start, end) from A9
        self.detector: Optional[AUDetector] = None
        self.status = "starting"
        self.reason = ""
        self._slow_since: Optional[float] = None
        self._paused_until = 0.0
        self._thread: Optional[threading.Thread] = None
        if start:
            self.start()

    # --- the plug-in interface ------------------------------------------------------------

    def start(self) -> None:
        profile = self.au.profile
        if profile.enabled is False:
            self._set_status("off", f'switched off in profile "{profile.name}"')
            return
        ok, why = aus_available()
        if not ok and not self._custom_factory:
            level = "warning" if profile.enabled is True else "info"
            self._set_status("off", why, level=level)
            return
        self._thread = threading.Thread(target=self._run, name="au-worker", daemon=True)
        self._thread.start()

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        if obs.present and obs.face_box is not None and obs.frame is not None and self._thread:
            with self._lock:
                self._slot = obs                 # replaces any face not yet taken: no backlog
                self._lock.notify()
        return Output(self.current(obs.t))

    def current(self, now: float) -> Dict[str, Any]:
        """The newest result as values for this frame: au_AU04 ..., au_age_ms, au_stale."""
        latest = self._latest
        values: Dict[str, Any] = {f"au_{u}": None for u in self.units}
        if latest is None or self.status != "running":
            values.update(au_age_ms=None, au_stale=None)
            return values
        age = max(0.0, now - latest.t)
        stale = age > self.au.stale_seconds
        if not stale:
            values.update({f"au_{u}": round(v, 4) for u, v in latest.values.items()})
        values.update(au_age_ms=round(age * 1000, 1), au_stale=1 if stale else 0)
        return values

    def notice(self, t: float, values: Dict[str, Optional[float]], events: List[FaceEvent]) -> None:
        closure = values.get("eye_closure")
        if closure is not None:
            self._closure.append((t, float(closure)))
        for event in events:
            if event.type == "yawn":
                self._yawns.append((event.t, event.t + float(event.fields.get("duration_s", 0))))
        horizon = t - max(self.config.windows) - 5
        while self._closure and self._closure[0][0] < horizon:
            self._closure.popleft()
        while self._yawns and self._yawns[0][1] < horizon:
            self._yawns.popleft()

    def drain(self) -> List[Tuple[str, Dict[str, Any]]]:
        with self._lock:
            records, self._records = self._records, []
        return records

    def summary(self, now: float) -> List[Tuple[str, Dict[str, Any]]]:
        if self.status == "off":
            return []
        fields: Dict[str, Any] = {"status": self.status, "rate": self.achieved_rate(),
                                  "infer_ms": self.infer_ms()}
        with self._lock:
            results = [r for r in self._results if not self._is_stale(r, r.done)]
        for seconds in sorted(set(self.config.windows)):
            suffix = f"_{int(seconds)}s"
            fields.update({k + suffix: v for k, v in self._window(results, now, seconds).items()})
        return [("au_window", fields)]

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._lock.notify_all()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
        if self.detector is not None:
            self.detector.close()

    # --- measurements -----------------------------------------------------------------------

    def achieved_rate(self) -> float:
        """AU results per second over the last few seconds."""
        now = time.monotonic()
        with self._lock:
            recent = [d for d in self._done if now - d <= RATE_WINDOW]
        if len(recent) < 2:
            return 0.0
        return round((len(recent) - 1) / max(recent[-1] - recent[0], 1e-6), 1)

    def infer_ms(self) -> Optional[float]:
        times = sorted(self._infer)
        return round(times[len(times) // 2], 1) if times else None

    def possible_rate(self) -> Optional[float]:
        """Results per second the machine could deliver back-to-back."""
        ms = self.infer_ms()
        return None if not ms else 1000.0 / ms

    # --- the worker -----------------------------------------------------------------------------

    def _default_factory(self, config: AUConfig) -> AUDetector:
        profile = config.profile
        return load_detector(config.units, profile.device, profile.cpu_threads)

    def _run(self) -> None:
        self._set_status("loading", "loading the AU model")
        try:
            self.detector = self._factory(self.au)
        except AUUnavailable as exc:
            self._set_status("off", str(exc), level="warning")
            return
        except Exception as exc:  # noqa: BLE001 -- AUs must never take perception down
            log.exception("The AU model failed to load")
            self._set_status("off", f"the AU model failed to load: {exc}", level="warning")
            return
        detector = self.detector
        self.units = tuple(u for u in self.units if u not in detector.missing)
        advice = gpu_advice(detector.device, self.au.profile.require_gpu)
        note = getattr(detector, "device_note", None)
        detail = ", ".join(x for x in (note, advice) if x)
        self._set_status("running", detail or f"running on {detector.device}",
                         level="warning" if advice else "info")
        if advice and self.au.profile.require_gpu:
            log.warning("\n%s\n  AUs: %s\n%s", "!" * 70, advice, "!" * 70)
        min_interval = 1.0 / self.au.profile.max_rate
        last_start = 0.0
        while not self._stop.is_set():
            if time.monotonic() < self._paused_until:
                self._stop.wait(0.5)
                continue
            if self.status == "paused":
                self._slow_since = None
                self._infer.clear()
                self._set_status("running", "retrying after a pause")
            with self._lock:
                while self._slot is None and not self._stop.is_set():
                    self._lock.wait(0.5)
                obs, self._slot = self._slot, None
            if obs is None:
                continue
            wait = last_start + min_interval - time.monotonic()
            if wait > 0:                      # max_rate: never faster than the profile allows
                self._stop.wait(wait)
                with self._lock:              # a newer face may have arrived meanwhile
                    if self._slot is not None:
                        obs, self._slot = self._slot, None
            last_start = time.monotonic()
            try:
                values = detector.predict(obs.frame.image, obs.face_box)
            except Exception as exc:  # noqa: BLE001
                log.warning("AU model failed on a frame: %s", exc)
                continue
            infer = (time.monotonic() - last_start) * 1000
            self._deliver(obs, values, infer)
            self._check_speed()

    def _deliver(self, obs: FaceObservation, values: Dict[str, float], infer_ms: float) -> None:
        result = AUResult(obs.t, obs.frame_index, {u: values[u] for u in self.units if u in values},
                          round(infer_ms, 1), self._clock())
        horizon = result.t - max(self.config.windows)
        with self._lock:
            self._latest = result
            self._results.append(result)
            while self._results and self._results[0].t < horizon:
                self._results.popleft()
            self._done.append(time.monotonic())
            while len(self._done) > 2 and self._done[-1] - self._done[0] > RATE_WINDOW:
                self._done.popleft()
            self._infer.append(infer_ms)
        if self.features is not None:
            row = {u: round(v, 4) for u, v in result.values.items()}
            row["infer_ms"] = result.infer_ms
            self.features.row(row, t_mono=result.t, frame=result.frame_index)

    def _check_speed(self) -> None:
        possible = self.possible_rate()
        if possible is None or possible >= self.au.min_rate:
            self._slow_since = None
            return
        now = time.monotonic()
        self._slow_since = self._slow_since or now
        if now - self._slow_since >= self.au.slow_grace_seconds:
            self._paused_until = now + self.au.retry_seconds
            self._latest = None
            self._set_status("paused", f"too slow: {possible:.1f} results/s possible, at least "
                             f"{self.au.min_rate:g} needed. Retrying in {self.au.retry_seconds:g} s.",
                             level="warning")

    def _is_stale(self, result: AUResult, at: float) -> bool:
        return (at - result.t) > self.au.stale_seconds

    def _set_status(self, status: str, reason: str, level: str = "info") -> None:
        self.status, self.reason = status, reason
        detector = self.detector
        fields: Dict[str, Any] = {
            "status": status, "reason": reason[:400], "profile": self.au.profile.name,
            "device": getattr(detector, "device", None),
            "precision": getattr(detector, "precision", None),
            "max_rate": self.au.profile.max_rate, "units": " ".join(self.units),
        }
        if detector is not None and getattr(detector, "missing", ()):
            fields["missing_units"] = " ".join(detector.missing)
        getattr(log, level)("AUs %s: %s", status, reason)
        with self._lock:
            self._records.append(("au_status", fields))

    # --- windows and cross-checks -----------------------------------------------------------

    def _window(self, results: List[AUResult], now: float, seconds: float) -> Dict[str, Any]:
        inside = [r for r in results if r.t >= now - seconds]
        out: Dict[str, Any] = {"results": len(inside)}
        weights = [min(b.t - a.t, MAX_WEIGHT) for a, b in zip(inside, inside[1:])] + \
                  ([min(now - inside[-1].t, MAX_WEIGHT)] if inside else [])
        total = sum(weights)
        threshold = self.au.active_threshold
        for unit in self.units:
            pairs = [(r.values[unit], w) for r, w in zip(inside, weights) if unit in r.values]
            if not pairs or total <= 0:
                out[f"{unit}_mean"] = out[f"{unit}_active"] = None
                continue
            out[f"{unit}_mean"] = round(sum(v * w for v, w in pairs) / total, 3)
            out[f"{unit}_active"] = round(sum(w for v, w in pairs if v >= threshold) / total, 3)
        out.update(self._crosscheck(inside))
        return out

    def _closure_at(self, t: float) -> Optional[float]:
        """A9's eye closure on the frame nearest to `t` (within 0.2 s)."""
        best, best_dt = None, 0.2
        for ct, value in self._closure:
            dt = abs(ct - t)
            if dt <= best_dt:
                best, best_dt = value, dt
        return best

    def _crosscheck(self, results: List[AUResult]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        threshold = self.au.active_threshold
        if "AU43" in self.units:
            # AU43 (eyes closed) vs. A9's eye closure at the P80 level, per result.
            closed_level = self.config.get("eyes", "perclos_closure")
            both = au_only = eyes_only = neither = 0
            for r in results:
                closure = self._closure_at(r.t)
                if closure is None or "AU43" not in r.values:
                    continue
                au, eyes = r.values["AU43"] >= threshold, closure >= closed_level
                both += au and eyes
                au_only += au and not eyes
                eyes_only += eyes and not au
                neither += not au and not eyes
            n = both + au_only + eyes_only + neither
            out.update(au43_eyes_both=both, au43_only=au_only, eyes_only=eyes_only,
                       au43_eyes_agree=round((both + neither) / n, 3) if n else None,
                       au43_eyes_kappa=_kappa(both, au_only, eyes_only, neither))
        if "AU26" in self.units or "AU25" in self.units:
            unit = "AU26" if "AU26" in self.units else "AU25"
            during = [r.values[unit] for r in results if unit in r.values and self._in_yawn(r.t)]
            outside = [r.values[unit] for r in results if unit in r.values and not self._in_yawn(r.t)]
            out.update(jaw_in_yawn=round(sum(during) / len(during), 3) if during else None,
                       jaw_outside_yawn=round(sum(outside) / len(outside), 3) if outside else None,
                       jaw_unit=unit)
        return out

    def _in_yawn(self, t: float) -> bool:
        return any(start <= t <= end for start, end in self._yawns)


def _kappa(both: int, a_only: int, b_only: int, neither: int) -> Optional[float]:
    """Cohen's kappa: agreement between two yes/no raters, corrected for chance
    (1 = perfect, 0 = no better than chance)."""
    n = both + a_only + b_only + neither
    if n == 0:
        return None
    observed = (both + neither) / n
    a_yes, b_yes = (both + a_only) / n, (both + b_only) / n
    expected = a_yes * b_yes + (1 - a_yes) * (1 - b_yes)
    if expected >= 1.0:
        return None
    return round((observed - expected) / (1 - expected), 3)
