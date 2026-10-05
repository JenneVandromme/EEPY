"""CameraSource: the background thread every camera shares.

    camera = open_camera()            # see eepy/camera/__init__.py
    camera.start()
    while running:
        frame = camera.get_latest()   # the NEWEST frame, or None before the first
        ...
    camera.stop()

LATEST-FRAME SEMANTICS. The capture thread keeps exactly one frame: each new
frame replaces the previous one, whether or not anyone looked at it. A slow
consumer therefore skips frames instead of falling behind -- for a real-time
loop a stale frame is worse than a missing one. Skipped frames are counted as
`dropped` in the statistics.

RATE. Frames are handed on at most `camera.fps` times a second (15 by default,
what the robot's camera manages), whatever the camera itself delivers.

RECONNECTING. If the camera fails or stops delivering, the thread logs
camera_error, closes it, and tries again every `reconnect_seconds` until it
works (camera_reconnected) or stop() is called. `status` says what is going on.

PRIVACY. Frames live only in memory. Only statistics are ever logged.

Subclasses implement _open(), _read() and _close(), and may override _make_frame().
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, List, Optional, Tuple

from eepy.logs.clock import monotonic

from .config import CameraConfig
from .errors import CameraError
from .frame import Frame

log = logging.getLogger("eepy.camera")

# A camera that is open but sends nothing for this long counts as lost.
STALL_SECONDS = 3.0

Callback = Callable[[Frame], None]


class _NoEvents:
    """Stands in for eepy.logs.features.ProcessEventLog when nothing is logged."""

    def event(self, event_type: str, **data: Any) -> bool:
        return False


class CameraSource:
    """Base class. Thread-safe: start/stop/get_latest may be called from anywhere."""

    name = "camera"

    def __init__(self, config: Optional[CameraConfig] = None, events: Any = None,
                 clock: Callable[[], float] = monotonic) -> None:
        self.config = config or CameraConfig()
        self._events = events if events is not None else _NoEvents()
        self._clock = clock
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)
        self._latest: Optional[Frame] = None
        self._latest_taken = True
        self._polling = False          # has anyone called get_latest()?
        self._subscribers: List[Callback] = []
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._status = "stopped"
        self._error: Optional[str] = None
        self._index = 0
        # statistics since the last camera_stats event
        self._window_start = 0.0
        self._window_frames = 0
        self._window_latency = 0.0
        self._window_latency_n = 0
        self._dropped = 0
        self._dropped_total = 0
        self._last_stats: dict = {}

    # --- for subclasses ------------------------------------------------------------

    def _open(self) -> None:
        """Connect to the camera. Raise CameraError with a friendly message."""
        raise NotImplementedError

    def _read(self) -> Optional[Tuple[Any, float]]:
        """Block briefly for the next picture: (BGR image, capture time on the
        monotonic clock), or None if none arrived yet. Raise CameraError if
        the camera is gone."""
        raise NotImplementedError

    def _close(self) -> None:
        """Release the camera. Must not raise."""

    def _make_frame(self, image: Any, t: float, index: int) -> Frame:
        return Frame(image=image, t=t, index=index, source=self.name)

    def describe(self) -> str:
        """Human-readable name, e.g. 'laptop camera 0 (AVFoundation)'."""
        return f"{self.name} camera"

    # --- the consumer API ----------------------------------------------------------

    def start(self) -> "CameraSource":
        """Start capturing in the background. Returns at once; frames follow."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stopping.clear()
        self._status = "starting"
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.name}", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 3.0) -> None:
        """Stop capturing and release the camera."""
        self._stopping.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None
        self._status = "stopped"
        with self._lock:
            self._new_frame.notify_all()

    def get_latest(self) -> Optional[Frame]:
        """The newest frame, or None if none has arrived yet. Never blocks.
        Call it again and you may get the same frame: compare `frame.index`."""
        now = self._clock()
        with self._lock:
            frame = self._latest
            self._polling = True
            if frame is not None and not self._latest_taken:
                self._latest_taken = True
                self._window_latency += now - frame.t
                self._window_latency_n += 1
            return frame

    def wait_for_frame(self, newer_than: Optional[Frame] = None,
                       timeout: float = 1.0) -> Optional[Frame]:
        """Block until there is a frame newer than `newer_than` (or any frame),
        then return the newest. None on timeout or after stop()."""
        after = newer_than.index if newer_than is not None else 0
        deadline = self._clock() + timeout
        with self._lock:
            while (self._latest is None or self._latest.index <= after) and not self._stopping.is_set():
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None
                self._new_frame.wait(remaining)
        frame = self.get_latest()
        return frame if frame is not None and frame.index > after else None

    def subscribe(self, callback: Callback) -> Callable[[], None]:
        """Call `callback(frame)` for every frame, on the capture thread.
        Keep it quick (a slow callback slows capture for everyone): hand heavy
        work to your own thread and use get_latest() there. Returns a function
        that unsubscribes."""
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
        return unsubscribe

    @property
    def status(self) -> str:
        """starting, running, reconnecting, stopped."""
        return self._status

    @property
    def error(self) -> Optional[str]:
        """The latest problem, in words for a person; None while all is well."""
        return self._error

    def stats(self) -> dict:
        """The last statistics window: fps, latency_ms, dropped (see camera_stats)."""
        return dict(self._last_stats)

    def __enter__(self) -> "CameraSource":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # --- the thread ----------------------------------------------------------------

    def _run(self) -> None:
        down_since: Optional[float] = None   # set while the camera is failing
        ever_started = False
        while not self._stopping.is_set():
            try:
                self._open()
            except CameraError as exc:
                down_since = self._fail(exc, down_since)
                continue
            try:
                started = self._capture_loop(ever_started, down_since)
                ever_started = ever_started or started
                down_since = None if started else down_since
            except CameraError as exc:
                ever_started = True
                down_since = self._fail(exc, down_since)
            except Exception as exc:  # noqa: BLE001 -- a bug must not kill the stream silently
                log.exception("Unexpected camera error")
                down_since = self._fail(CameraError(f"unexpected error: {exc}"), down_since)
            finally:
                self._safe_close()
        self._safe_close()

    def _fail(self, exc: CameraError, down_since: Optional[float]) -> float:
        self._safe_close()
        message = str(exc)
        if message != self._error:   # say each new problem once, not every retry
            log.error("%s: %s", self.describe(), message)
            self._events.event("camera_error", camera=self.name, message=message)
        self._error = message
        if not self._stopping.is_set():
            self._status = "reconnecting"
        self._stopping.wait(self.config.reconnect_seconds)
        return down_since if down_since is not None else self._clock()

    def _safe_close(self) -> None:
        try:
            self._close()
        except Exception:  # noqa: BLE001
            log.debug("Closing the camera failed", exc_info=True)

    def _capture_loop(self, ever_started: bool, down_since: Optional[float]) -> bool:
        """Read until stopped or failed. True once a frame got through."""
        period = 1.0 / self.config.fps
        next_due = 0.0
        last_picture = self._clock()
        got_one = False
        while not self._stopping.is_set():
            item = self._read()
            now = self._clock()
            if item is None:
                if now - last_picture > STALL_SECONDS:
                    raise CameraError(f"the {self.describe()} stopped sending pictures.")
                continue
            last_picture = now
            image, t = item
            if t < next_due:
                continue   # faster than camera.fps: skip, the next one is due soon
            next_due = max(next_due + period, t - period / 2) if next_due else t + period
            if not got_one:
                got_one = True
                self._on_first_frame(image, ever_started, down_since)
            self._publish(image, t)
        return got_one

    def _on_first_frame(self, image: Any, ever_started: bool, down_since: Optional[float]) -> None:
        self._error = None
        self._status = "running"
        self._window_start = self._clock()
        frame_h, frame_w = image.shape[:2]
        if ever_started and down_since is not None:
            down = round(self._clock() - down_since, 1)
            log.info("%s is back after %.1f s.", self.describe(), down)
            self._events.event("camera_reconnected", camera=self.name, down_seconds=down)
        else:
            log.info("%s started: %dx%d, delivering up to %g frames a second.",
                     self.describe(), frame_w, frame_h, self.config.fps)
            self._events.event("camera_started", camera=self.name, width=int(frame_w),
                               height=int(frame_h), fps=self.config.fps)

    def _publish(self, image: Any, t: float) -> None:
        with self._lock:
            self._index += 1
            index = self._index
        frame = self._make_frame(image, t, index)
        with self._lock:
            if self._polling and not self._latest_taken:
                self._dropped += 1   # replaced before get_latest() fetched it
            self._latest, self._latest_taken = frame, False
            self._window_frames += 1
            subscribers = list(self._subscribers)
            self._new_frame.notify_all()
        if subscribers:
            self._add_latency(self._clock() - frame.t)
        for callback in subscribers:
            try:
                callback(frame)
            except Exception:  # noqa: BLE001 -- one bad subscriber must not stop the camera
                log.exception("A camera subscriber failed")
        self._maybe_report()

    def _add_latency(self, seconds: float) -> None:
        with self._lock:
            self._window_latency += seconds
            self._window_latency_n += 1

    def _maybe_report(self) -> None:
        now = self._clock()
        elapsed = now - self._window_start
        if elapsed < self.config.stats_seconds:
            return
        with self._lock:
            fps = self._window_frames / elapsed if elapsed > 0 else 0.0
            latency = (self._window_latency / self._window_latency_n * 1000
                       if self._window_latency_n else None)
            dropped = self._dropped
            self._dropped_total += dropped
            self._window_start, self._window_frames = now, 0
            self._window_latency, self._window_latency_n, self._dropped = 0.0, 0, 0
        stats = {"fps": round(fps, 1), "dropped": dropped}
        if latency is not None:   # unknown until something consumes frames
            stats["latency_ms"] = round(latency, 1)
        self._last_stats = dict(stats, dropped_total=self._dropped_total)
        log.debug("%s: %s", self.describe(), self._last_stats)
        self._events.event("camera_stats", camera=self.name, **stats)
