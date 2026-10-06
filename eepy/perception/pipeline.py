"""FacePipeline: camera frames in, face features and events out.

    from eepy.camera import open_camera
    from eepy.perception import FacePipeline

    pipeline = FacePipeline(open_camera()).start()   # starts the camera too
    result = pipeline.wait_for_result()
    print(result.values["ear"], result.events, result.window)
    pipeline.stop()

It runs in its own thread, always on the NEWEST camera frame: if MediaPipe is
slower than the camera, frames are skipped rather than queued. It runs as fast
as the machine allows (never faster than the camera delivers).

Per frame: MediaPipe Face Landmarker -> FaceObservation -> every extractor ->
per-frame values (+ process_ms: the time all of that took) and events; about
once a second the window summary. Pass `features` (FeatureStream) and
`events` (ProcessEventLog) to record them in the running session.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from .baseline import BaselineProvider
from .config import PerceptionConfig
from .extractors import Extractor, FaceEvent, all_columns, default_extractors
from .observation import FaceObservation, absent, from_mediapipe
from .windows import WindowAggregator

log = logging.getLogger("eepy.perception")

# MediaPipe / TensorFlow Lite chatter that means nothing to the user.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
# Set ONCE, here, at import -- not inside warnings.catch_warnings(), which is not
# thread-safe: the AU worker thread restoring its saved filters would wipe it.
import warnings  # noqa: E402

warnings.filterwarnings("ignore", message=".*SymbolDatabase.GetPrototype.*")


class PerceptionError(Exception):
    """Perception cannot run. The message is for a person."""


@dataclass(frozen=True)
class PerceptionResult:
    """Everything worked out for one frame."""

    observation: FaceObservation
    values: Dict[str, Optional[float]]          # per-frame features, by column name
    events: List[FaceEvent] = field(default_factory=list)
    window: Optional[Dict[str, Any]] = None     # the window summary, when one was due
    # Other records for the event log, from plug-ins: (event type, fields), e.g.
    # ("au_window", {...}) or ("au_status", {...}).
    records: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)

    @property
    def t(self) -> float:
        return self.observation.t

    @property
    def index(self) -> int:
        return self.observation.frame_index


@contextlib.contextmanager
def _quiet_stderr() -> Any:
    """Hide MediaPipe's C++ start-up messages ("WARNING: All log messages before
    absl::InitializeLog()...", "GL version", ...). They come from C++ logging that
    ignores Python and the environment, and look like errors to people who are
    not developers. Only used around loading the model and the first frame."""
    try:
        saved = os.dup(2)
    except OSError:
        yield
        return
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), 2)
            yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


class Landmarker:
    """MediaPipe Face Landmarker in VIDEO mode (it tracks between frames)."""

    def __init__(self, model_path: Optional[str] = None) -> None:
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
        except ImportError:
            raise PerceptionError(
                "MediaPipe is not installed in this environment. Face features run in .venv-vision:\n"
                "    pip install -r requirements-vision.txt") from None
        from .model import ensure_model

        path = model_path or str(ensure_model())
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=path),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            output_face_blendshapes=True, output_facial_transformation_matrixes=True)
        self._mp = mp
        with _quiet_stderr():
            self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ms = -1
        self._first = True

    def detect(self, image_bgr: Any, t: float) -> Any:
        import numpy as np

        stamp = max(int(t * 1000), self._last_ms + 1)   # VIDEO mode: strictly increasing
        self._last_ms = stamp
        rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        if self._first:               # the first frame prints more start-up chatter
            self._first = False
            with _quiet_stderr():
                return self._landmarker.detect_for_video(image, stamp)
        return self._landmarker.detect_for_video(image, stamp)

    def close(self) -> None:
        self._landmarker.close()


class FacePipeline:
    """See the module docstring.

    camera       an eepy.camera CameraSource (started by start() if needed)
    extractors   default: presence, eyes, mouth, head. Add A10/A11 plug-ins here.
    baseline     a BaselineProvider (A12 hook); default: the config's defaults
    features     eepy.logs.features.FeatureStream for per-frame values, or None
    events       eepy.logs.features.ProcessEventLog for events + windows, or None
    """

    def __init__(self, camera: Any, config: Optional[PerceptionConfig] = None,
                 extractors: Optional[List[Extractor]] = None, baseline: Any = None,
                 features: Any = None, events: Any = None,
                 landmarker_factory: Callable[[], Any] = Landmarker) -> None:
        self.camera = camera
        self.config = config or PerceptionConfig()
        self.extractors = extractors if extractors is not None else default_extractors(self.config)
        self.baseline = baseline or BaselineProvider(self.config)
        self.features = features
        self.events = events
        self.landmarker_factory = landmarker_factory
        self.windows = WindowAggregator(self.config)
        self.columns = all_columns(self.extractors) + ["process_ms"]
        self._lock = threading.Condition()
        self._latest: Optional[PerceptionResult] = None
        self._subscribers: List[Callable[[PerceptionResult], None]] = []
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._times: Deque[float] = deque(maxlen=300)     # process_ms of recent frames
        self._stamps: Deque[float] = deque(maxlen=300)    # when they were processed
        self.error: Optional[str] = None

    # --- the API ----------------------------------------------------------------------------

    def start(self) -> "FacePipeline":
        if self._thread is not None and self._thread.is_alive():
            return self
        self.camera.start()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="face-pipeline", daemon=True)
        self._thread.start()
        return self

    def stop(self, stop_camera: bool = True) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
        for extractor in self.extractors:
            extractor.close()
        if stop_camera:
            self.camera.stop()
        with self._lock:
            self._lock.notify_all()

    def get_latest(self) -> Optional[PerceptionResult]:
        return self._latest

    def wait_for_result(self, newer_than: Optional[PerceptionResult] = None,
                        timeout: float = 2.0) -> Optional[PerceptionResult]:
        after = newer_than.index if newer_than is not None else 0
        deadline = time.monotonic() + timeout
        with self._lock:
            while (self._latest is None or self._latest.index <= after) and not self._stopping.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.error:
                    return None
                self._lock.wait(remaining)
            return self._latest if self._latest is not None and self._latest.index > after else None

    def subscribe(self, callback: Callable[[PerceptionResult], None]) -> Callable[[], None]:
        """callback(result) for every processed frame, on the pipeline thread. Keep it quick."""
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback) if callback in self._subscribers else None

    def stats(self) -> Dict[str, Optional[float]]:
        """process_ms (median, p95) and the rate frames were processed at."""
        times = sorted(self._times)
        if not times:
            return {"process_ms": None, "process_ms_p95": None, "fps": None}
        stamps = list(self._stamps)
        span = stamps[-1] - stamps[0]
        return {"process_ms": round(times[len(times) // 2], 1),
                "process_ms_p95": round(times[int(len(times) * 0.95) - 1 if len(times) > 1 else 0], 1),
                "fps": round((len(stamps) - 1) / span, 1) if span > 0 else None}

    # --- one frame (no threads, no camera: the tests call this directly) ---------------------

    def process(self, observation: FaceObservation) -> PerceptionResult:
        started = time.perf_counter()
        baseline = self.baseline.current()
        values: Dict[str, Optional[float]] = {}
        events: List[FaceEvent] = []
        for extractor in self.extractors:
            out = extractor.update(observation, baseline)
            values.update(out.values)
            events += out.events
        for extractor in self.extractors:
            values.update(extractor.derive(values, baseline))
        values["process_ms"] = round(observation.process_ms + (time.perf_counter() - started) * 1000, 2)
        window = self.windows.add(observation.t, values, events, baseline)
        # Optional plug-in hooks (see Extractor): notice() sees every frame's
        # finished values and events, summary() adds to each window, drain()
        # hands over status messages from a plug-in's own thread.
        records: List[Tuple[str, Dict[str, Any]]] = []
        for extractor in self.extractors:
            extractor.notice(observation.t, values, events)
            if window is not None:
                records += extractor.summary(observation.t)
            records += extractor.drain()
        return PerceptionResult(observation, values, events, window, records)

    # --- the thread -----------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            landmarker = self.landmarker_factory()
        except Exception as exc:  # noqa: BLE001 -- shown to the person
            self.error = str(exc)
            log.error("%s", exc)
            with self._lock:
                self._lock.notify_all()
            return
        frame = None
        try:
            while not self._stopping.is_set():
                newest = self.camera.wait_for_frame(newer_than=frame, timeout=0.5)
                if newest is None:
                    continue
                if frame is not None and newest.index < frame.index:   # camera restarted
                    for extractor in self.extractors:
                        extractor.reset()
                frame = newest
                started = time.perf_counter()
                result = landmarker.detect(frame.image, frame.t)
                elapsed = (time.perf_counter() - started) * 1000
                observation = from_mediapipe(frame, result, elapsed) if result is not None \
                    else absent(frame, elapsed)
                self._deliver(self.process(observation))
        finally:
            landmarker.close()

    def _deliver(self, result: PerceptionResult) -> None:
        self._times.append(float(result.values["process_ms"] or 0.0))
        self._stamps.append(time.monotonic())
        with self._lock:
            self._latest = result
            self._lock.notify_all()
        self._record(result)
        for callback in list(self._subscribers):
            try:
                callback(result)
            except Exception:  # noqa: BLE001
                log.exception("A perception subscriber failed")

    def _record(self, result: PerceptionResult) -> None:
        if self.features is not None:
            self.features.row({c: result.values.get(c) for c in self.columns},
                              t_mono=result.t, frame=result.index)
        if self.events is None:
            return
        for event in result.events:   # blink, yawn, nod, face_lost, face_back
            self.events.event(event.type, t_mono=event.t, **event.fields)
        if result.window is not None:
            self.events.event("face_window", t_mono=result.t, **result.window)
        for event_type, fields in result.records:   # au_window, au_status, ...
            self.events.event(event_type, t_mono=fields.pop("t_mono", result.t), **fields)
