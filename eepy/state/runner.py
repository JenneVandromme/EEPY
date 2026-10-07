"""Running an estimator on the live features, and recording what it says.

    tracker = StateTracker(make_estimator("rules"), gap_seconds=3)
    pipeline.subscribe(StateFromPipeline(tracker))  # feeds it each window summary
    tracker.idle(now)                               # in the main loop: no pictures -> unknown

StateTracker is shared by live and replay: the same features, in the same order,
with the same gap filling, give the same states. Its listeners get every
estimate: StateRecorder (state_changed events + state.csv), StatePublisher
(the Wizard panel's status bar, over the camera relay), the preview overlay.

Standard library only, Python 3.9.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .estimate import STATES, StateEstimate, StateEstimator
from .features import Features, assemble, empty

log = logging.getLogger("eepy.state")
Listener = Callable[[StateEstimate], None]


class StateTracker:
    """Feeds an estimator, fills camera gaps with "no data", tells the listeners."""

    def __init__(self, estimator: StateEstimator, gap_seconds: float = 3.0,
                 listeners: Optional[List[Listener]] = None) -> None:
        self.estimator = estimator
        self.gap = gap_seconds
        self.listeners: List[Listener] = list(listeners or [])
        self.latest: Optional[StateEstimate] = None
        self._last_t: Optional[float] = None
        self._was_empty = False

    def reset(self) -> None:
        self.estimator.reset()
        self.latest, self._last_t, self._was_empty = None, None, False

    def feed(self, features: Features) -> Optional[StateEstimate]:
        t = float(features["t"])
        if self._last_t is not None and t <= self._last_t:
            return None                          # older than what was already handled
        self._fill(t, inclusive=False)
        return self._step(features)

    def idle(self, now: float, slack: float = 1.0) -> List[StateEstimate]:
        """Call regularly: once no features came for gap_seconds, "no data" once a
        second. `slack` leaves time for a frame still being processed."""
        return self._fill(now - slack, inclusive=True)

    def _fill(self, until: float, inclusive: bool) -> List[StateEstimate]:
        out: List[StateEstimate] = []
        if self._last_t is None:
            return out
        while True:
            # The first "no data" gap_seconds after the last features, then each second.
            nxt = self._last_t + (self.gap if not self._in_gap() else 1.0)
            if nxt > until or (nxt == until and not inclusive):
                return out
            out.append(self._step(empty(round(nxt, 6))))

    def _in_gap(self) -> bool:
        return bool(self.latest is not None and self._was_empty)

    def _step(self, features: Features) -> StateEstimate:
        self._was_empty = bool(features.get("no_data"))
        estimate = self.estimator.update(features)
        self._last_t = float(features["t"])
        self.latest = estimate
        for listener in list(self.listeners):
            try:
                listener(estimate)
            except Exception:  # noqa: BLE001 -- a listener must never stop the estimator
                log.exception("A state listener failed")
        return estimate


def from_result(result: Any, windows: Any = (10, 60)) -> Optional[Features]:
    """The features of a FacePipeline result -- only results that carry a window
    summary (about once a second); else None."""
    if result.window is None:
        return None
    records: Dict[str, Dict[str, Any]] = {}
    for event_type, fields in result.records:
        if event_type in ("gaze_window", "au_window"):
            records[event_type] = {k: v for k, v in fields.items() if v is not None and k != "t_mono"}
    face = {k: v for k, v in result.window.items() if v is not None}
    # Rounded like the event log (t_mono to the microsecond): replay sees the same times.
    return assemble(round(result.t, 6), face, records.get("gaze_window"), records.get("au_window"), windows)


class StateFromPipeline:
    """A FacePipeline subscriber: each window summary -> the tracker. Resets the
    estimator when the session changes, so live matches a replay of the session."""

    def __init__(self, tracker: StateTracker, windows: Any = (10, 60),
                 session: Optional[Callable[[], Optional[str]]] = None) -> None:
        self.tracker, self.windows, self.session = tracker, windows, session
        self._session: Optional[str] = None

    def __call__(self, result: Any) -> None:
        features = from_result(result, self.windows)
        if features is None:
            return
        if self.session is not None:
            name = self.session()
            if name != self._session:
                self._session = name
                self.tracker.reset()
        self.tracker.feed(features)


# --- recording and showing -------------------------------------------------------------

STATE_COLUMNS = ["state", "pending", "calibrated", "rules", "reason"] + [f"p_{s}" for s in STATES]


def _short(text: str, limit: int = 64) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


class StateRecorder:
    """state_changed events (events.vision.jsonl) and one row per estimate in
    state.csv, into the running session (nothing outside a session)."""

    def __init__(self, events: Any = None, stream: Any = None) -> None:
        self.events, self.stream = events, stream
        self._last: Optional[str] = None

    @staticmethod
    def make_stream(**kwargs: Any) -> Any:
        from eepy.logs.features import FeatureStream

        return FeatureStream(STATE_COLUMNS, rate_hz=1.0, name="state", **kwargs)

    def __call__(self, estimate: StateEstimate) -> None:
        if self.stream is not None:
            row: Dict[str, Any] = {
                "state": estimate.state, "pending": estimate.pending, "calibrated": estimate.calibrated,
                "rules": _short(",".join(r.rule for r in estimate.reasons)),
                "reason": _short(estimate.reason_text)}
            row.update({f"p_{s}": estimate.scores.get(s, 0.0) for s in STATES})
            self.stream.row(row, t_mono=estimate.t)
        if estimate.state != self._last:
            if self.events is not None:
                self.events.event("state_changed", t_mono=estimate.t, **{
                    "from": self._last, "to": estimate.state, "estimator": estimate.estimator,
                    "reason": estimate.reason_text, "calibrated": estimate.calibrated,
                    "rules": [r.rule for r in estimate.reasons],
                    "evidence": [{"rule": r.rule, "feature": r.feature, "value": r.value,
                                  "threshold": r.threshold} for r in estimate.reasons if r.feature],
                    "values": estimate.values, "skipped": estimate.skipped[:12]})
            self._last = estimate.state


class StatePublisher:
    """Sends each estimate to the Wizard panel (camera relay STATE). Silent and
    cheap when no panel runs."""

    RETRY_SECONDS = 2.0

    def __init__(self, port: int, client: Any = None) -> None:
        if client is None:
            from eepy.camera.relay import RelayClient

            client = RelayClient(port, timeout=0.3)
        self.client = client
        self._retry_at = 0.0
        self.sent = 0

    def __call__(self, estimate: StateEstimate) -> None:
        now = time.perf_counter()
        if now < self._retry_at:
            return
        try:
            self.client.send_state(panel_view(estimate))
            self.sent += 1
        except Exception:  # noqa: BLE001 -- no panel running: try again later
            self._retry_at = now + self.RETRY_SECONDS

    def close(self) -> None:
        self.client.close()


def panel_view(estimate: StateEstimate) -> Dict[str, Any]:
    """What the panel shows: small, JSON-ready."""
    return {"state": estimate.state, "reason": estimate.reason_text, "pending": estimate.pending,
            "calibrated": estimate.calibrated, "since": round(estimate.since, 3),
            "t": round(estimate.t, 3), "estimator": estimate.estimator,
            "rules": [r.rule for r in estimate.reasons]}
