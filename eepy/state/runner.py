"""Running an estimator on the live features, and recording what it says.

    tracker = StateTracker(make_estimator("rules"), gap_seconds=3)
    pipeline.subscribe(StateFromPipeline(tracker))  # feeds it each window summary
    tracker.idle(now)                               # in the main loop: no pictures -> unknown

StateTracker is shared by live and replay: the same features, in the same order,
with the same gap filling, give the same states. Its listeners get every
estimate: StateRecorder (state_changed / state_model events + state.csv),
StatePublisher (the Wizard panel's status bar, over the camera relay), the
preview overlay.

The vision process feeds the tracker from the pipeline thread and calls idle()
from its main thread: a re-entrant lock keeps them apart. SessionEstimators
swaps the estimator (rules / HMM) and the HMM's session prior at every session
change (--estimator auto).

Standard library only, Python 3.9 (the HMM itself needs numpy; it is imported
only when chosen).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .estimate import STATES, StateEstimate, StateEstimator, for_mode, make_estimator
from .features import Features, assemble, empty

log = logging.getLogger("eepy.state")
Listener = Callable[[StateEstimate], None]


class StateTracker:
    """Feeds an estimator, fills camera gaps with "no data", tells the listeners."""

    def __init__(self, estimator: StateEstimator, gap_seconds: Optional[float] = None,
                 listeners: Optional[List[Listener]] = None) -> None:
        self.estimator = estimator
        self.gap = gap_seconds if gap_seconds is not None else estimator_gap(estimator)
        self.listeners: List[Listener] = list(listeners or [])
        self.latest: Optional[StateEstimate] = None
        self._last_t: Optional[float] = None
        self._was_empty = False
        # Re-entrant: use() resets, and listeners may read the tracker while it holds the lock.
        self.lock = threading.RLock()

    def reset(self) -> None:
        with self.lock:
            self.estimator.reset()
            self.latest, self._last_t, self._was_empty = None, None, False
            for listener in self.listeners:
                forget = getattr(listener, "reset", None)
                if callable(forget):
                    forget()

    def use(self, estimator: StateEstimator) -> None:
        """Switch to another estimator (and its gap setting), from a clean start."""
        with self.lock:
            self.estimator = estimator
            self.gap = estimator_gap(estimator)
            self.reset()

    def feed(self, features: Features) -> Optional[StateEstimate]:
        with self.lock:
            t = float(features["t"])
            if self._last_t is not None and t <= self._last_t:
                return None                          # older than what was already handled
            self._fill(t, inclusive=False)
            return self._step(features)

    def idle(self, now: float, slack: float = 1.0) -> List[StateEstimate]:
        """Call regularly: once no features came for gap_seconds, "no data" once a
        second. `slack` leaves time for a frame still being processed."""
        with self.lock:
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


def estimator_gap(estimator: Any, default: float = 3.0) -> float:
    """The estimator's own gap setting (rules: state_rules.yaml; HMM: state_hmm.yaml)."""
    gap = getattr(estimator, "gap_seconds", None)
    return float(gap) if isinstance(gap, (int, float)) else default


def describe_estimator(estimator: Any) -> Dict[str, Any]:
    """For the state_model event: which estimator, which parameter file (and prior)."""
    describe = getattr(estimator, "describe", None)
    if callable(describe):
        return describe()
    return {"estimator": getattr(estimator, "name", "?")}


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
    estimator when the session changes, so live matches a replay of the session.
    `on_session(name)`, if given, is called instead of the plain reset (it picks the
    estimator and prior for the new session, then resets)."""

    def __init__(self, tracker: StateTracker, windows: Any = (10, 60),
                 session: Optional[Callable[[], Optional[str]]] = None,
                 on_session: Optional[Callable[[Optional[str]], None]] = None) -> None:
        self.tracker, self.windows, self.session = tracker, windows, session
        self.on_session = on_session
        self._session: Optional[str] = None

    def __call__(self, result: Any) -> None:
        features = from_result(result, self.windows)
        if features is None:
            return
        with self.tracker.lock:
            if self.session is not None:
                name = self.session()
                if name != self._session:
                    self._session = name
                    if self.on_session is not None:
                        self.on_session(name)
                    else:
                        self.tracker.reset()
            self.tracker.feed(features)


class SessionEstimators:
    """Which estimator runs, per session: `fixed` ("rules" / "hmm"), or the session's mode
    (auto: hmm sessions get the HMM, everything else -- and no session -- the rules).
    The HMM also gets the session's prior (prior.json), so one session's prior never
    leaks into the next. Called by StateFromPipeline at every session change."""

    def __init__(self, tracker: StateTracker, fixed: Optional[str] = None, hmm_params: Any = None,
                 data_dir: Optional[Path] = None, say: Callable[[str], None] = print) -> None:
        self.tracker, self.fixed, self.hmm_params = tracker, fixed, hmm_params
        self.data_dir, self.say = data_dir, say
        self._failed: Optional[str] = None

    def _folder(self, name: Optional[str]) -> Optional[Path]:
        if name is None:
            return None
        from eepy.logs.features import current_session
        from eepy.logs.session import DATA_DIR

        info = current_session(self.data_dir or DATA_DIR)
        if info is not None and info.get("session") == name:
            return Path(info["folder"])
        return None

    def __call__(self, name: Optional[str]) -> None:
        from eepy.logs.features import read_prior, session_mode

        folder = self._folder(name)
        want = self.fixed or (for_mode(session_mode(folder)) if folder is not None else "rules")
        with self.tracker.lock:
            if self.tracker.estimator.name != want:
                try:
                    self.tracker.use(self.build(want))
                except ValueError as exc:            # the HMM cannot load (numpy missing)
                    if self._failed != str(exc):
                        self.say(f"  {exc} Running the rules instead.")
                        self._failed = str(exc)
            estimator = self.tracker.estimator
            if estimator.name == "hmm":
                from .hmm_prior import resolve

                applied = resolve(read_prior(folder) if folder is not None else None, estimator.params)
                estimator.set_session_prior(applied)
                if applied.source != "default" or applied.fell_back:
                    self.say(f"  Session prior: {applied.reason}.")
            self.tracker.reset()

    def build(self, name: str) -> StateEstimator:
        if name == "hmm":
            return make_estimator("hmm", params=self.hmm_params)
        return make_estimator(name)


# --- recording and showing -------------------------------------------------------------

STATE_COLUMNS = (["state", "pending", "calibrated", "rules", "reason"] + [f"p_{s}" for s in STATES]
                 + ["estimator", "most_likely", "confidence", "uncertainty", "updated", "n_features", "evidence"])


def _short(text: str, limit: int = 64) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _short_ascii(text: str, limit: int = 64) -> str:
    text = text.encode("ascii", "replace").decode("ascii")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def evidence_summary(estimate: StateEstimate, top: int = 4) -> str:
    """The HMM's strongest signs this step, e.g. "perclos->sleepy +0.12; yawns->sleepy +0.05"."""
    parts = [f"{d['feature']}->{d.get('supports') or '-'} {d['impact']:+.2f}"
             for d in estimate.evidence[:top] if d.get("impact")]
    return _short_ascii("; ".join(parts))


class StateRecorder:
    """state_changed events (events.vision.jsonl) and one row per estimate in
    state.csv, into the running session (nothing outside a session). Also
    state_model: which estimator and parameters run, once per session -- retried
    until it is written (the event log may only notice a new session a moment later)."""

    def __init__(self, events: Any = None, stream: Any = None,
                 describe: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        self.events, self.stream, self.describe = events, stream, describe
        self._last: Optional[str] = None
        self._model_logged = False

    def reset(self) -> None:
        """A new session (or estimator): log state_model again, and the first state as a change."""
        self._last = None
        self._model_logged = False

    @staticmethod
    def make_stream(**kwargs: Any) -> Any:
        from eepy.logs.features import FeatureStream

        return FeatureStream(STATE_COLUMNS, rate_hz=1.0, name="state", **kwargs)

    def __call__(self, estimate: StateEstimate) -> None:
        hmm = estimate.confidence is not None
        if self.events is not None and self.describe is not None and not self._model_logged:
            self._model_logged = bool(self.events.event("state_model", t_mono=estimate.t, **self.describe()))
        if self.stream is not None:
            row: Dict[str, Any] = {
                "state": estimate.state, "pending": estimate.pending, "calibrated": estimate.calibrated,
                "rules": _short(",".join(r.rule for r in estimate.reasons)),
                "reason": _short(estimate.reason_text), "estimator": estimate.estimator}
            row.update({f"p_{s}": estimate.scores.get(s, 0.0) for s in STATES})
            if hmm:
                row.update(most_likely=estimate.most_likely, confidence=estimate.confidence,
                           uncertainty=estimate.uncertainty, updated=estimate.updated,
                           n_features=estimate.values.get("n_features"), evidence=evidence_summary(estimate))
            self.stream.row(row, t_mono=estimate.t)
        if estimate.state != self._last:
            if self.events is not None:
                fields: Dict[str, Any] = {
                    "from": self._last, "to": estimate.state, "estimator": estimate.estimator,
                    "reason": estimate.reason_text, "calibrated": estimate.calibrated,
                    "rules": [r.rule for r in estimate.reasons],
                    "evidence": [{"rule": r.rule, "feature": r.feature, "value": r.value,
                                  "threshold": r.threshold} for r in estimate.reasons if r.feature],
                    "values": estimate.values, "skipped": estimate.skipped[:12]}
                if hmm:
                    fields.update(
                        probs={s: round(estimate.scores.get(s, 0.0), 4) for s in STATES[:4]},
                        most_likely=estimate.most_likely, confidence=estimate.confidence,
                        uncertainty=estimate.uncertainty, pending=estimate.pending, updated=estimate.updated,
                        hmm_evidence=estimate.evidence[:5])
                self.events.event("state_changed", t_mono=estimate.t, **fields)
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


EVIDENCE_TOP = 3        # the top signs sent along, for the escalation's decisions (A19) and A20
EVIDENCE_KEYS = ("feature", "text", "supports", "against", "strength", "impact")


def panel_view(estimate: StateEstimate) -> Dict[str, Any]:
    """What the panel shows: small, JSON-ready. The HMM adds its belief, confidence and
    `say` (the short text the robot may speak as {evidence}; "" when there is none).
    The reasons next to the belief bar are `reason`; `evidence` holds the step's top 3 signs
    (the full list stays in the logs), which the escalation attaches to its decisions (A19)."""
    view = {"state": estimate.state, "reason": estimate.reason_text, "pending": estimate.pending,
            "calibrated": estimate.calibrated, "since": round(estimate.since, 3),
            "t": round(estimate.t, 3), "estimator": estimate.estimator,
            "rules": [r.rule for r in estimate.reasons],
            # A20: the feature values the explanations put in words
            "values": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in estimate.values.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}}
    if estimate.fired:      # rules: every rule that fired, per state (the explanation's items)
        view["fired"] = {state: [{"rule": r.rule, "feature": r.feature, "value": r.value,
                                  "threshold": r.threshold, "text": r.text} for r in reasons]
                         for state, reasons in estimate.fired.items()}
    if estimate.confidence is not None:
        view.update(probs={s: round(estimate.scores.get(s, 0.0), 3) for s in STATES[:4]},
                    most_likely=estimate.most_likely, confidence=round(estimate.confidence, 3),
                    uncertainty=estimate.uncertainty, updated=estimate.updated, say=estimate.say or "",
                    evidence=[{k: e.get(k) for k in EVIDENCE_KEYS} for e in estimate.evidence[:EVIDENCE_TOP]])
        if estimate.ledger is not None:
            view["ledger"] = estimate.ledger
    return view
