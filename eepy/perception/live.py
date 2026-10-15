"""The live debug page: camera + rules + HMM + the explanation viewer, in ONE browser window.

    python -m eepy.perception.live                      # http://127.0.0.1:8770 opens by itself
    python -m eepy.perception.live --scenario           # the guided self-test parts, with a score
    python -m eepy.perception.live --record P99         # also record it as a session (replay later)

The page shows a small camera picture (face box, gaze zone, are the eyes judged), the raw
signs, and the HMM's decision pipeline (signs -> belief -> shown state -> sure enough? )
updated every second, with a growing timeline you can pause and scroll back. Looking at
the page counts as looking at your work (the screen), so it never spoils a test.

Only on this computer (127.0.0.1). Nothing is written unless --record: then the FEATURES
are recorded as an ordinary session (never pictures), so `python -m eepy.state.replay` and
`python -m eepy.state.explain` work on it afterwards, with other HMM numbers too.

Guide: docs/EXPLANATION_VIEWER.md ("Live"). Run it in .venv-vision.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

RAW_KEYS = ("eyes_measured_s_60s", "eyes_measured_s_10s", "perclos_60s", "blink_mean_ms_rel_60s",
            "blink_mean_ms_60s", "blink_count_60s", "long_blinks_60s", "blink_max_ms_60s", "eyes_closed_now_s",
            "yawns_60s", "nods_60s", "gaze_off_ratio_60s", "away_current_s", "face_present_ratio_10s",
            "fps_10s", "AU43_active_60s", "AU26_active_60s", "baseline_calibrated", "gaze_calibrated")
# What each scenario part marks, so replay's marker agreement and the score line up.
PART_MARKERS = {"Phone": "phone_picked_up", "Back to work": "back_on_task", "Act sleepy": "eyes_closing",
                "Leave": "user_went_to_bed"}


class LiveState:
    """Everything the page reads, filled from the pipeline thread."""

    def __init__(self, params: Any) -> None:
        from eepy.state.explain import _thresholds, build

        self.lock = threading.Lock()
        self.params = params
        self.ids = [f.id for f in params.features]
        self.t0: Optional[float] = None
        self.steps: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.rules: Dict[float, str] = {}
        self.frame: Optional[bytes] = None
        self.live: Dict[str, Any] = {}
        self.banner: Dict[str, Any] = {}
        self.run = 0                      # +1 at every reset: the page then starts its timeline over
        # today's config/escalation.yaml: step 4 shows the 70 % threshold instead of "no gate"
        empty = build([], params, 0.0, meta={"title": "Live", "notes": [], "parts": []},
                      thresholds=_thresholds(None))
        self.base = {k: v for k, v in empty.items() if k not in ("steps", "events")}

    def add(self, estimate: Any, rules_state: Optional[str], raw: Dict[str, Any]) -> None:
        from eepy.state.explain import _step

        with self.lock:
            if self.t0 is None:
                self.t0 = estimate.t
            step = _step(estimate, self.t0, self.ids, rules_state)
            step["raw"] = raw
            self.steps.append(step)

    def reset(self) -> None:
        """A fresh timeline (the vision program's estimator was reset: a new session)."""
        with self.lock:
            self.t0, self.steps, self.events, self.run = None, [], [], self.run + 1

    def event(self, t: float, kind: str, text: str) -> None:
        with self.lock:
            if self.t0 is None:
                self.t0 = t
            self.events.append({"t": round(t - self.t0, 3), "kind": kind, "text": text})

    def snapshot(self, since: int, run: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            if run is not None and run != self.run:
                since = 0                 # the page still shows an older run: send everything
            out = dict(self.base)
            out["steps"] = self.steps[since:]
            out["events"] = list(self.events)
            out["total"] = len(self.steps)
            out["run"] = self.run
            out["live"] = dict(self.live, banner=self.banner)
            return out


def _jpeg(result: Any, width: int = 360) -> Optional[bytes]:
    """A small picture with the face box, the gaze zone and whether the eyes are judged.
    Only kept in memory for the page; never written."""
    try:
        import cv2
    except ImportError:
        return None
    frame = getattr(result.observation, "frame", None)
    image = getattr(frame, "image", None)
    if image is None:
        return None
    image = image.copy()
    obs, values = result.observation, result.values
    box = getattr(obs, "face_box", None)
    judged = values.get("eye_closure") is not None
    if obs.present and box is not None:
        cv2.rectangle(image, (box.x, box.y), (box.x + box.w, box.y + box.h), (80, 200, 80) if judged else (0, 165, 255), 3)
    scale = width / float(image.shape[1])
    image = cv2.resize(image, (width, int(image.shape[0] * scale)))
    ok, data = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    return data.tobytes() if ok else None


def _handler(state: LiveState, page: str) -> Any:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:   # quiet
            pass

        def _send(self, body: bytes, kind: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 -- http.server's name
            path, _, query = self.path.partition("?")
            if path == "/":
                self._send(page.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/data":
                numbers: Dict[str, int] = {}
                for part in query.split("&"):
                    key, _, value = part.partition("=")
                    try:
                        numbers[key] = max(0, int(value))
                    except ValueError:
                        pass
                body = json.dumps(state.snapshot(numbers.get("since", 0), numbers.get("run")),
                                  separators=(",", ":"), default=str)
                self._send(body.encode("utf-8"), "application/json")
            elif path == "/frame.jpg" and state.frame is not None:
                self._send(state.frame, "image/jpeg")
            else:
                self.send_response(404)
                self.end_headers()

    return Handler


def serve(state: LiveState, port: int) -> Any:
    """Start the page's web server (127.0.0.1 only) in the background; OSError if the port is busy."""
    from eepy.state.explain import VIEWER

    page = VIEWER.read_text(encoding="utf-8").replace("/*__LIVE__*/false", "true").replace(
        "__TITLE__", "Live").replace("/*__DATA__*/null", "null")
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(state, page))
    threading.Thread(target=server.serve_forever, name="eepy-live-http", daemon=True).start()
    return server


class SessionFeed:
    """The live page inside the vision program (python -m eepy.perception --live): it shows
    the SAME HMM the panel acts on (the vision program's tracker), and the robot's decisions
    from the session's event logs. Subscribe it to the pipeline before StateFromPipeline (it
    keeps the picture and the raw signs), and add it to the tracker's listeners."""

    def __init__(self, params: Any, config: Any, baseline: Any) -> None:
        from eepy.state.estimate import make_estimator
        from eepy.state.runner import StateTracker

        self.state = LiveState(params)
        self.config, self.baseline = config, baseline
        self.rules = StateTracker(make_estimator("rules"))    # only for the page's "the rules said"
        self._raw: Dict[str, Any] = {}
        self._rules_state: Optional[str] = None
        self._last_frame = 0.0

    def on_result(self, result: Any) -> None:
        """Pipeline subscriber: the picture, the live status, the raw signs."""
        from eepy.state.runner import from_result

        now = time.perf_counter()
        if now - self._last_frame >= 0.2:
            self._last_frame = now
            picture = _jpeg(result)
            if picture is not None:
                self.state.frame = picture
        self.state.live = {"face": bool(result.observation.present),
                           "eyes_judged": result.values.get("eye_closure") is not None,
                           "zone": result.values.get("gaze_zone"),
                           "eye_closure": result.values.get("eye_closure"),
                           "calibrated": bool(self.baseline.current().calibrated)}
        f = from_result(result, self.config.windows)
        if f is None:
            return
        self._raw = {k: f.get(k) for k in RAW_KEYS if f.get(k) is not None}
        rules = self.rules.feed(f)
        self._rules_state = rules.state if rules is not None else None

    def __call__(self, estimate: Any) -> None:
        """Tracker listener: every HMM step goes on the page (the rules mode has no belief)."""
        if getattr(estimate, "estimator", None) == "hmm":
            self.state.add(estimate, self._rules_state, self._raw)

    def reset(self) -> None:                  # called by the tracker at a session change
        self.state.reset()
        self.rules.reset()

    def refresh(self, session: Optional[str], folder: Any, estimator: str) -> None:
        """Call every few seconds: the banner, and the robot's decisions from the session's logs."""
        from eepy.state.explain import _events

        if session is None:
            self.state.banner = {"part": "No session yet",
                                 "text": "Start a session in the Wizard panel (--mode hmm); the HMM starts with it."}
        elif estimator != "hmm":
            self.state.banner = {"part": f"Session {session}: rules",
                                 "text": "This session runs the rules, which have no belief to show. "
                                         "Start the panel with --mode hmm."}
        else:
            self.state.banner = {"part": f"Session {session} (hmm)",
                                 "text": "The robot acts on this HMM: its decisions show in step 4, "
                                         "step 5 and the timeline."}
        if folder is None or self.state.t0 is None:
            return
        try:
            events = _events(folder, self.state.t0)
        except Exception:  # noqa: BLE001 -- a log being written: try again next time
            return
        with self.state.lock:
            self.state.events = events


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.perception.live",
                                     description="Camera, rules and HMM live in one browser page, for debugging.")
    parser.add_argument("--camera", choices=("auto", "robot", "laptop", "fake"))
    parser.add_argument("--index", type=int, help="laptop camera number")
    parser.add_argument("--profile", help="machine profile for action units")
    parser.add_argument("--no-aus", action="store_true", help="no action units")
    parser.add_argument("--user", help="whose calibration (default: the --record participant, else 'default')")
    parser.add_argument("--hmm-params", help="the HMM's parameter file (default config/state_hmm.yaml)")
    parser.add_argument("--scenario", action="store_true",
                        help="guide through the self-test parts (work, phone, back, act sleepy, leave) and score them")
    parser.add_argument("--record", metavar="PARTICIPANT",
                        help="also record the features as a session (e.g. P99), for replay and explain afterwards")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--seconds", type=float, default=0, help="stop by itself after this long (0: until Ctrl+C)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)

    import logging

    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    from eepy.camera import CameraConfigError, CameraError, open_camera
    from eepy.logs.clock import monotonic
    from eepy.state.estimate import make_estimator
    from eepy.state.hmm_params import HMMConfigError
    from eepy.state.hmm_params import load as load_hmm
    from eepy.state.runner import StateTracker, from_result

    from .config import load
    from .extractors import default_extractors
    from .pipeline import FacePipeline

    try:
        config = load()
        params = load_hmm(args.hmm_params)
        rules_tracker = StateTracker(make_estimator("rules"))
        hmm_tracker = StateTracker(make_estimator("hmm", params=params))
        camera = open_camera(args.camera, **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError, HMMConfigError, ValueError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2

    session_log = events_log = features = state_stream = None
    if args.record:
        from eepy.logs import configure, log as session_log
        from eepy.logs.features import ProcessEventLog
        from eepy.settings import load_settings
        from eepy.state.runner import StateRecorder, describe_estimator

        settings = load_settings()
        configure(data_dir=settings.data_path, participant_pattern=settings.participant_pattern,
                  flush_seconds=settings.logging.flush_seconds)
        try:
            session_log.start(args.record, mode="hmm", backend="none", notes="live debug page (eepy.perception.live)")
        except Exception as exc:  # noqa: BLE001 -- e.g. a panel session is running
            print(f"\n  Cannot record: {exc}", file=sys.stderr)
            return 2
        events_log = ProcessEventLog("vision")
        state_stream = StateRecorder.make_stream()
        hmm_tracker.listeners.append(StateRecorder(events_log, state_stream,
                                                   describe=lambda: describe_estimator(hmm_tracker.estimator)))

    extractors = default_extractors(config)
    au = None
    if not args.no_aus:
        try:
            from .aus import ActionUnitExtractor
            from .aus import load_config as load_au_config

            au = ActionUnitExtractor(load_au_config(profile=args.profile), config)
            extractors.append(au)
        except Exception as exc:  # noqa: BLE001 -- AUs are optional
            print(f"  (No action units: {exc})")
    from .baseline import StoredBaselineProvider
    from .gaze import CalibrationStore, GazeExtractor, resolve_user
    from .gaze import load_config as load_gaze_config

    gaze = GazeExtractor(load_gaze_config(), config, user=resolve_user(args.user or args.record))
    extractors.append(gaze)
    baseline = StoredBaselineProvider(config, store=CalibrationStore(), user=gaze.user, source=camera.name)
    if args.record:
        from eepy.logs.features import FeatureStream

        from .extractors import all_columns

        features = FeatureStream(all_columns(extractors) + ["process_ms"], rate_hz=camera.config.fps)
    pipeline = FacePipeline(camera, config, extractors=extractors, features=features, events=events_log,
                            baseline=baseline)

    state = LiveState(params)
    feed_lock = threading.Lock()
    last_frame = [0.0]

    def on_result(result: Any) -> None:
        now = time.perf_counter()
        if now - last_frame[0] >= 0.2:
            last_frame[0] = now
            picture = _jpeg(result)
            if picture is not None:
                state.frame = picture
        state.live = {"face": bool(result.observation.present),
                      "eyes_judged": result.values.get("eye_closure") is not None,
                      "zone": result.values.get("gaze_zone"),
                      "eye_closure": result.values.get("eye_closure"),
                      "calibrated": bool(baseline.current().calibrated)}
        f = from_result(result, config.windows)
        if f is None:
            return
        with feed_lock:
            rules = rules_tracker.feed(f)
            estimate = hmm_tracker.feed(f)
        if estimate is not None:
            state.add(estimate, rules.state if rules is not None else None,
                      {k: f.get(k) for k in RAW_KEYS if f.get(k) is not None})

    pipeline.subscribe(on_result)
    try:
        server = serve(state, args.port)
    except OSError as exc:
        print(f"\n  Port {args.port} is busy ({exc}). Try --port {args.port + 1}.", file=sys.stderr)
        return 2
    pipeline.start()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  Live debug page: {url}   (camera: {camera.describe()}; Ctrl+C to stop)")
    if args.record:
        print(f"  Recording the features as session {session_log.session}.")
    if not args.no_browser:
        webbrowser.open(url)

    timed: List[Any] = []
    started = monotonic()
    try:
        if args.scenario:
            from eepy.state.scenario import SCENARIO

            for n, phase in enumerate(SCENARIO, start=1):
                start = monotonic()
                state.event(start, "marker", phase.title.lower())
                if args.record and phase.title in PART_MARKERS:
                    session_log.event("annotation", source="wizard", t_mono=start, marker=PART_MARKERS[phase.title])
                print(f"  {n}/{len(SCENARIO)} {phase.title}: {phase.instruction}")
                end = start + phase.seconds
                while monotonic() < end:
                    state.banner = {"part": f"{n}/{len(SCENARIO)} {phase.title}", "text": phase.instruction,
                                    "left": round(end - monotonic()), "expected": list(phase.expected)}
                    _tick(rules_tracker, hmm_tracker, feed_lock, pipeline)
                    time.sleep(0.25)
                timed.append((phase, start, monotonic()))
            state.banner = {"part": "done", "text": "Scenario finished: the score is in the terminal.", "left": 0}
        deadline = time.monotonic() + args.seconds if args.seconds else None
        while not args.scenario and (deadline is None or time.monotonic() < deadline):
            _tick(rules_tracker, hmm_tracker, feed_lock, pipeline)
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("  (stopped)")
    finally:
        pipeline.stop()
        server.shutdown()
        if args.record:
            for item in (state_stream, features, events_log):
                if item is not None:
                    item.close()
            session_log.end("operator")
    if timed:
        _score(timed, rules_tracker, hmm_tracker, state, started)
    if args.record:
        print(f"\n  Recorded. Afterwards:\n      python -m eepy.state.explain {session_log.session} --open"
              f"\n      python -m eepy.state.replay {session_log.session} --estimator both\n")
    return 0


def _tick(rules_tracker: Any, hmm_tracker: Any, lock: Any, pipeline: Any) -> None:
    from eepy.logs.clock import monotonic

    if pipeline.error:
        raise KeyboardInterrupt
    with lock:
        rules_tracker.idle(monotonic())
        hmm_tracker.idle(monotonic())


def _score(timed: List[Any], rules_tracker: Any, hmm_tracker: Any, state: LiveState, started: float) -> None:
    """The self-test score for both estimators, from what the page collected."""
    from eepy.state.estimate import StateEstimate
    from eepy.state.scenario import judge, report_both

    t0 = state.t0 or started

    def rebuild(key: str) -> List[StateEstimate]:
        out = []
        for s in state.steps:
            scores = dict(zip(("focused", "distracted", "drowsy", "very_drowsy"), s["p"]))
            st = s["s"] if key == "hmm" else (s["rules"] or "unknown")
            out.append(StateEstimate(t=s["t"] + t0, state=st, scores=scores if key == "hmm" else {}, reasons=[],
                                     calibrated=bool(s["cal"]), confidence=s["c"] if key == "hmm" else None))
        return out

    hmm, rules = rebuild("hmm"), rebuild("rules")
    for line in report_both(judge(timed, rules), judge(timed, hmm), rules, hmm, started):
        print(line)


if __name__ == "__main__":
    raise SystemExit(main())
