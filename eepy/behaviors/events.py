"""What the player tells the outside world, as it happens.

The player emits an Event for every interesting moment. Anything can subscribe:
the default subscriber prints them, and the logger (task A7) will write them to
disk. Subscribers are called on a separate thread, so a slow one -- writing to a
file, posting over the network -- can never delay the robot's motion.

Event kinds:

    behavior_started      a behaviour begins (data: intensity, tone, idle, ...)
    keyframe_reached      a keyframe's pose has been reached (index, source)
    line_requested        a spoken line was handed to speech (line_id, text)
    sound_played          a sound was sent to the speaker (file)
    behavior_finished     a behaviour played to its end
    behavior_interrupted  something cut it short (by: what interrupted it)
    behavior_refused      it was never started, because it would break a
                          safety rule from the robot's current pose (reason)

`t` is eepy.logs.clock.monotonic() at the moment it happened -- not when the
subscriber got round to it -- so events can be lined up precisely against each
other and against other processes' data.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("eepy.events")



@dataclass(frozen=True)
class Event:
    kind: str
    t: float
    behavior: str | None
    data: dict[str, Any] = field(default_factory=dict)


Subscriber = Callable[[Event], None]


class EventBus:
    """Fan events out to subscribers, on a thread of its own."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self._queue: "queue.Queue[Event | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="eepy-events", daemon=True)
        self._thread.start()

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Add a subscriber. Returns a function that removes it again."""
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, event: Event) -> None:
        """Queue an event. Never blocks the caller."""
        self._queue.put(event)

    def flush(self, timeout: float = 2.0) -> None:
        """Wait until every queued event has been delivered. For tests."""
        done = threading.Event()
        self._queue.put(_Marker(done))  # type: ignore[arg-type]
        done.wait(timeout)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                return
            if isinstance(event, _Marker):
                event.done.set()
                continue
            with self._lock:
                subscribers = list(self._subscribers)
            for subscriber in subscribers:
                try:
                    subscriber(event)
                except Exception:  # a broken subscriber must not stop the others
                    log.exception("event subscriber %r failed on %s", subscriber, event.kind)


class _Marker:
    def __init__(self, done: threading.Event) -> None:
        self.done = done


class EventPrinter:
    """The default subscriber: one readable line per event.

    Times are shown relative to the first event it sees, so a log reads as
    "0.000, 0.812, 1.304 ..." rather than as raw monotonic clock values.
    """

    def __init__(self, show_keyframes: bool = True, show_idle: bool = False) -> None:
        self.show_keyframes = show_keyframes
        self.show_idle = show_idle
        self._t0: float | None = None

    def __call__(self, event: Event) -> None:
        if not self.show_idle and event.data.get("idle"):
            return
        if not self.show_keyframes and event.kind == "keyframe_reached":
            return
        if self._t0 is None:
            self._t0 = event.t
        detail = ", ".join(
            f"{k}={v!r}" for k, v in event.data.items() if k not in ("idle",) and v is not None
        )
        print(f"  {event.t - self._t0:7.3f}s  {event.kind:21s}  {event.behavior or '':26s}  {detail}",
              flush=True)
