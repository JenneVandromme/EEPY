"""BehaviorPlayer: performs behaviours from behaviors/*.yaml on the robot.

    player = BehaviorPlayer(make_backend("sim"))
    player.start()
    player.set_idle("study_buddy_idle")
    player.play("refocus_joint_attention")              # returns at once
    player.play_level("bedtime", 3, intensity=0.5, tone="direct",
                      context={"minutes": 40})
    player.stop()                                      # back to neutral
    player.close()

HOW MOTION IS SENT, AND WHY

The player streams poses itself: 50 times a second it works out where the head
should be right now and sends that with set_target(). It does NOT use the SDK's
goto_target(), even though goto_target interpolates on the robot and would be
immune to network jitter. Reading the SDK source showed three reasons:

  1. The daemon silently DROPS a goto while another one is running ("Ignoring
     play_move request: another move is running"). A new behaviour would not
     interrupt the old one -- it would vanish. The daemon does have a stop
     command, but no public client method sends it.
  2. goto_target blocks until the daemon acknowledges completion, so chaining
     keyframes adds a network round trip at every boundary, and the timeline
     drifts later and later.
  3. While any goto runs, the daemon ignores streamed targets, so the two
     approaches cannot be mixed freely.

Streaming makes interrupting instant and lets every single pose be safety-
checked before it leaves. Each tick sends the pose for NOW on time.monotonic(),
so a late tick corrects itself rather than piling up delay. The SDK's own
play_move streams the same way, but sends three commands per tick on the wall
clock; this sends one.

The exceptions are the SDK's built-in routines -- goto_sleep and wake_up --
which only the SDK can perform, because the real sleep pose needs a sideways
head shift no behaviour file can express. They run to completion and cannot be
interrupted half way.

THREADS

play() and stop() only post a request and return; they never wait for the
robot. One control thread owns the robot and runs the 50 Hz loop. Sounds,
speech and event delivery each get a worker thread of their own, so a slow
upload, a slow text-to-speech call or a slow event subscriber can never
delay a frame of motion.

SAFETY -- nothing is ever clamped

  * Before a behaviour starts, its whole timeline is resolved from the robot's
    CURRENT pose and checked: ranges, the neck rule, reachability, peak speed,
    and cartoon overshoot. If any of it fails, the behaviour is refused and the
    one that was already playing carries on undisturbed.
  * If the live start pose would make a move too fast -- a quick antenna flick
    that is a no-op from neutral but a lunge from a drooped pose -- that move is
    made to take longer. The target is never changed. This is logged.
  * Every frame is checked again just before it is sent. If one ever fails
    (it should not: this is defence in depth), the behaviour is abandoned and
    the robot returns to neutral.
  * stop(), close(), and Ctrl+C in the command-line tool, always end in a
    neutral pose, unless the robot is asleep -- sleep is already safe.
"""

from __future__ import annotations

import difflib
import logging
import queue
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import safety
from .backends import Backend
from .events import Event, EventBus, EventPrinter
from .loader import BEHAVIORS_DIR, SOUNDS_DIR, Library, Step, load_library
from .pose import NEUTRAL, Pose
from .schema import Behavior
from .targets import StaticTargetProvider, TargetProvider
from .trajectory import Trajectory

log = logging.getLogger("eepy.player")

SpeechCallback = Callable[[str, str], None]


def log_speech(line_id: str, text: str) -> None:
    """The default speech hook: speech is task A5, so for now just say so."""
    log.info('[speech] %s: "%s"', line_id, text)


class PlayerError(RuntimeError):
    """A request the player cannot act on. The message is for humans."""


@dataclass
class ReloadResult:
    ok: bool
    message: str
    problems: list = field(default_factory=list)


class Playback:
    """A handle on one play() request. play() returns it immediately."""

    def __init__(self, name: str, intensity: float) -> None:
        self.name = name
        self.intensity = intensity
        self.outcome: str | None = None        # finished / interrupted / refused
        self.interrupted_by: str | None = None
        self.reason: str | None = None
        self._done = threading.Event()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until it ends. True only if it played to the end."""
        self._done.wait(timeout)
        return self.outcome == "finished"

    def _end(self, outcome: str, by: str | None = None, reason: str | None = None) -> None:
        if self._done.is_set():
            return
        self.outcome, self.interrupted_by, self.reason = outcome, by, reason
        self._done.set()

    def __repr__(self) -> str:
        return f"<Playback {self.name} {self.outcome or 'pending'}>"


@dataclass
class _Run:
    """One behaviour being performed."""

    name: str
    steps: list[Step]
    trajectory: Trajectory
    t0: float
    loops_left: int | None            # None = forever
    playback: Playback | None = None  # None for idle and internal runs
    behavior: Behavior | None = None
    intensity: float = 1.0
    tone: str = "hedged"
    context: dict[str, Any] = field(default_factory=dict)
    idle: bool = False
    internal: bool = False            # the player's own neutral return
    cue_index: int = 0
    started: float = 0.0


class _Worker:
    """A thread that runs jobs in order, so the caller never waits for them."""

    def __init__(self, name: str) -> None:
        self._queue: "queue.Queue[tuple | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, fn: Callable, *args: Any) -> None:
        self._queue.put((fn, args))

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=3.0)

    def _run(self) -> None:
        while (job := self._queue.get()) is not None:
            fn, args = job
            try:
                fn(*args)
            except Exception:
                log.exception("background job %s failed", getattr(fn, "__name__", fn))


class BehaviorPlayer:
    """Plays behaviours on a Backend. See the module docstring."""

    def __init__(
        self,
        backend: Backend,
        *,
        behaviors_dir: Path | str = BEHAVIORS_DIR,
        sounds_dir: Path | str = SOUNDS_DIR,
        targets: TargetProvider | None = None,
        speech_callback: SpeechCallback | None = None,
        events: EventBus | None = None,
        print_events: bool = False,
        rate_hz: float = 50.0,
        check_reach: bool = True,
    ) -> None:
        self.backend = backend
        self.behaviors_dir = Path(behaviors_dir)
        self.sounds_dir = Path(sounds_dir)
        self._library: Library = load_library(self.behaviors_dir)
        self.targets: TargetProvider = targets or StaticTargetProvider(lambda: self._library)
        self.speech_callback: SpeechCallback = speech_callback or log_speech
        self.events = events or EventBus()
        self._owns_events = events is None
        if print_events:
            self.events.subscribe(EventPrinter())
        self.rate_hz = rate_hz
        self.check_reach = check_reach

        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.Lock()
        self._active: _Run | None = None
        self._idle_run: _Run | None = None
        self._internal: _Run | None = None
        self._idle_name: str | None = None      # what the control thread is using
        self._idle_wanted: str | None = None    # what the caller last asked for
        self._idle_refused: str | None = None
        self._quiet = False
        self._asleep = False
        self._pending_plays = 0
        self._commanded: Pose = NEUTRAL
        self._last_send: float | None = None
        self._last_lines: dict[str, str] = {}
        self._lateness: deque[float] = deque(maxlen=20000)
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._closing = False
        self.error: str | None = None

        self._audio = _Worker("eepy-audio")
        self._speech = _Worker("eepy-speech")

    # ======================================================================
    # Public API
    # ======================================================================

    @property
    def library(self) -> Library:
        return self._library

    @property
    def pose(self) -> Pose:
        """The pose most recently sent to the robot."""
        return self._commanded

    def start(self) -> "BehaviorPlayer":
        """Connect to the robot and start the control loop."""
        if self._thread is not None:
            return self
        self._commanded = self.backend.connect()
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="eepy-player", daemon=True)
        self._thread.start()
        # Decision E: settle into neutral before anything else plays.
        self._commands.put(("neutral",))
        return self

    def play(self, name: str, intensity: float = 1.0, wait: bool = False, *,
             tone: str = "hedged", context: dict[str, Any] | None = None) -> Playback:
        """Play a behaviour by name. Returns at once unless wait=True.

        intensity  0-1, multiplies the behaviour's own intensity.
        tone       "hedged" (unsure, softer) or "direct" (confident), for any
                   spoken line.
        context    values for placeholders in spoken lines, e.g. {"minutes": 40}.
        """
        behavior = self._lookup(name)
        if not 0.0 <= intensity <= 1.0:
            raise PlayerError(f"intensity must be between 0 and 1, not {intensity}.")
        if tone not in ("hedged", "direct"):
            raise PlayerError(f'tone must be "hedged" or "direct", not {tone!r}.')
        if wait and behavior.loop == "forever":
            raise PlayerError(
                f'"{name}" loops forever, so wait=True would never return. '
                "Play it without wait, and stop() it when you are done."
            )
        self._require_running()
        playback = Playback(name, intensity)
        with self._lock:
            self._pending_plays += 1
        self._commands.put(("play", behavior, intensity, tone, dict(context or {}), playback))
        if wait:
            playback.wait()
        return playback

    def play_level(self, ladder: str, level: int, intensity: float = 1.0,
                   wait: bool = False, *, tone: str = "hedged",
                   context: dict[str, Any] | None = None) -> Playback:
        """Play the behaviour on a ladder rung, e.g. play_level("bedtime", 3)."""
        try:
            behavior = self._library.get_behavior(ladder, level)
        except KeyError as exc:
            raise PlayerError(str(exc).strip('"')) from None
        return self.play(behavior.name, intensity, wait, tone=tone, context=context)

    def stop(self, return_to_neutral: bool = True) -> None:
        """Interrupt whatever is playing, idle included. Returns at once.

        The robot stays quiet until the next play() or set_idle().
        """
        self._require_running()
        self._commands.put(("stop", return_to_neutral))

    def is_playing(self) -> bool:
        """True while a behaviour (not the idle) is playing or about to."""
        with self._lock:
            return self._pending_plays > 0 or self._active is not None

    def current(self) -> str | None:
        """Name of the behaviour playing now, not counting the idle."""
        with self._lock:
            return self._active.name if self._active else None

    def set_idle(self, name: str | None) -> None:
        """The behaviour to loop whenever nothing else plays. None for none."""
        if name is not None:
            self._lookup(name)
        self._require_running()
        # Recorded here, synchronously, as well as queued: reload() checks it
        # straight away, and must not see a value the control thread has not
        # got round to applying yet.
        with self._lock:
            self._idle_wanted = name
        self._commands.put(("idle", name))

    def reload(self) -> ReloadResult:
        """Re-read and re-validate behaviors/, without stopping anything.

        On success the new files take effect for the NEXT behaviour; whatever
        is playing finishes as it was. On failure nothing changes -- the last
        valid version keeps running -- and the result explains the problem.
        """
        from .validate import Validator, report

        validator = Validator(directory=self.behaviors_dir, sounds=self.sounds_dir,
                              check_reach=self.check_reach)
        validator.run()
        if validator.errors:
            import contextlib, io
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                report(validator, self.behaviors_dir.resolve().parent, strict=False)
            message = buffer.getvalue().strip()
            # One line here; the full explanation is in the result, for the
            # caller to show however suits it.
            log.warning("Reload refused: %d error(s) in the behaviour files. "
                        "Still running the last valid version.", len(validator.errors))
            return ReloadResult(False, message, validator.errors)
        library = load_library(self.behaviors_dir)
        with self._lock:
            idle = self._idle_wanted
        if idle and idle not in library.behaviors:
            message = (f'The idle behaviour "{idle}" no longer exists. '
                       "Reload refused -- still running the last valid version.")
            log.error(message)
            return ReloadResult(False, message)
        self._commands.put(("library", library))
        return ReloadResult(True, f"Reloaded {len(library.behaviors)} behaviours.")

    def close(self, return_to_neutral: bool = True, timeout: float = 10.0) -> None:
        """Stop everything, return to neutral, disconnect. Blocks until done."""
        if self._thread is not None and self._running.is_set():
            self._commands.put(("close", return_to_neutral))
            self._thread.join(timeout)
        self._running.clear()
        self._audio.close()
        self._speech.close()
        try:
            self.backend.close()
        finally:
            if self._owns_events:
                self.events.flush()
                self.events.close()

    def timing_stats(self) -> dict[str, float]:
        """How late each frame was sent relative to its schedule, in ms."""
        values = sorted(self._lateness)
        if not values:
            return {"frames": 0}
        pick = lambda q: values[min(len(values) - 1, int(q * len(values)))] * 1000  # noqa: E731
        return {
            "frames": len(values),
            "mean_ms": sum(values) / len(values) * 1000,
            "p50_ms": pick(0.50),
            "p95_ms": pick(0.95),
            "p99_ms": pick(0.99),
            "max_ms": values[-1] * 1000,
        }

    def __enter__(self) -> "BehaviorPlayer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ======================================================================
    # Helpers for the public API
    # ======================================================================

    def _lookup(self, name: str) -> Behavior:
        behaviors = self._library.behaviors
        if name in behaviors:
            return behaviors[name]
        match = difflib.get_close_matches(name, behaviors, n=1, cutoff=0.5)
        hint = f' Did you mean "{match[0]}"?' if match else ""
        raise PlayerError(f'There is no behaviour called "{name}".{hint}')

    def _require_running(self) -> None:
        if self.error:
            raise PlayerError(f"The player has stopped after an error: {self.error}")
        if not self._running.is_set():
            raise PlayerError("The player is not running. Call start() first.")

    def _emit(self, kind: str, behavior: str | None, **data: Any) -> None:
        self.events.publish(Event(kind, time.monotonic(), behavior, data))

    # ======================================================================
    # The control thread
    # ======================================================================

    def _loop(self) -> None:
        period = 1.0 / self.rate_hz
        next_tick = time.monotonic()
        try:
            while True:
                self._drain_commands()
                if self._closing and not (self._active or self._internal):
                    break
                self._maybe_start_idle()
                run = self._internal or self._active or self._idle_run
                if run is not None:
                    self._advance(run, next_tick)
                next_tick += period
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -period:
                    # Fell more than a frame behind (a blocking SDK action, a
                    # stall). Do not burst to catch up: resume on a fresh beat.
                    next_tick = time.monotonic()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("The player stopped after an error. The robot holds its last pose.")
            for run in (self._active, self._idle_run, self._internal):
                if run and run.playback:
                    run.playback._end("interrupted", by="error", reason=self.error)
        finally:
            self._running.clear()

    # --- commands ----------------------------------------------------------

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            kind = command[0]
            if kind == "play":
                _, behavior, intensity, tone, context, playback = command
                with self._lock:
                    self._pending_plays -= 1
                if self._closing:
                    playback._end("interrupted", by="close")
                    continue
                self._quiet = False
                self._start_behavior(behavior, intensity, tone, context, playback)
            elif kind == "stop":
                self._interrupt_all("stop")
                self._quiet = True
                if command[1]:
                    self._return_to_neutral()
            elif kind == "idle":
                self._idle_name, self._idle_refused = command[1], None
                if self._idle_run is not None:
                    self._end_run(self._idle_run, "interrupted", by="set_idle")
                self._quiet = False
            elif kind == "library":
                self._library = command[1]
                self._idle_refused = None
                log.info("Behaviour files reloaded.")
            elif kind == "neutral":
                self._return_to_neutral()
            elif kind == "close":
                self._closing = True
                self._interrupt_all("close")
                if command[1]:
                    self._return_to_neutral()

    # --- starting things ------------------------------------------------------

    def _wake_if_asleep(self) -> None:
        if self._asleep:
            log.info("The robot is asleep: waking it before playing.")
            self._commanded = self.backend.run_action("wake_up")
            self._last_send = time.monotonic()
            self._asleep = False

    def _plan(self, behavior: Behavior, intensity: float) -> tuple[list[Step], Trajectory]:
        steps = self._library.resolve(
            behavior, start=self._commanded, gaze=self.targets.get,
            intensity_scale=intensity,
        )
        return steps, Trajectory.build(steps, self._commanded, stretch="all")

    def _refusal(self, trajectory: Trajectory) -> str | None:
        problems = trajectory.violations(rate_hz=self.rate_hz, check_reach=self.check_reach)
        if not problems:
            return None
        segment, violation = problems[0]
        return f"keyframe {segment.index + 1} ({segment.step.source}): {violation.message}"

    def _start_behavior(self, behavior: Behavior, intensity: float, tone: str,
                        context: dict, playback: Playback) -> None:
        self._wake_if_asleep()
        try:
            steps, trajectory = self._plan(behavior, intensity)
            reason = self._refusal(trajectory)
        except KeyError as exc:  # e.g. the TargetProvider does not know a name
            reason = str(exc).strip('"')
        if reason is not None:
            # Refused before it began, so whatever was playing carries on.
            log.error('Refused to play "%s" from the current pose: %s', behavior.name, reason)
            playback._end("refused", reason=reason)
            self._emit("behavior_refused", behavior.name, reason=reason)
            return
        self._interrupt_all(behavior.name, keep_idle=False)
        run = _Run(
            name=behavior.name, steps=steps, trajectory=trajectory,
            t0=time.monotonic(), loops_left=_loops(behavior), playback=playback,
            behavior=behavior, intensity=intensity, tone=tone, context=context,
        )
        run.started = run.t0
        with self._lock:
            self._active = run
        self._announce(run)

    def _maybe_start_idle(self) -> None:
        if (self._idle_name is None or self._quiet or self._asleep or self._closing
                or self._active or self._idle_run or self._internal
                or self._idle_refused == self._idle_name):
            return
        behavior = self._library.behaviors.get(self._idle_name)
        if behavior is None:
            return
        try:
            steps, trajectory = self._plan(behavior, 1.0)
            reason = self._refusal(trajectory)
        except KeyError as exc:
            reason = str(exc).strip('"')
        if reason is not None:
            log.error('Refused the idle "%s": %s. Idle is off until set_idle() or reload().',
                      behavior.name, reason)
            self._emit("behavior_refused", behavior.name, reason=reason, idle=True)
            self._idle_refused = self._idle_name
            return
        self._idle_run = _Run(
            name=behavior.name, steps=steps, trajectory=trajectory,
            t0=time.monotonic(), loops_left=None, behavior=behavior, idle=True,
        )
        self._idle_run.started = self._idle_run.t0
        self._announce(self._idle_run)

    def _announce(self, run: _Run) -> None:
        stretched = [
            f"keyframe {s.index + 1}: {s.stretched_from:.2f}s -> {s.duration:.2f}s"
            for s in run.trajectory.segments if s.stretched_from is not None
        ]
        if stretched:
            log.info('"%s": slowed down to stay under the speed limit from the '
                     "current pose (%s)", run.name, "; ".join(stretched))
        self._emit("behavior_started", run.name, intensity=run.intensity,
                   tone=run.tone if run.playback else None,
                   duration=round(run.trajectory.duration, 3),
                   stretched=stretched or None, idle=run.idle)

    def _return_to_neutral(self) -> None:
        if self._asleep:
            return  # sleep is already a safe pose
        self._internal = None
        step = Step(start_s=0.0, duration=1.0, easing="minjerk", hold=0.0,
                    head_yaw=0.0, head_pitch=0.0, head_roll=0.0, head_z=0.0,
                    antenna_right=NEUTRAL.antenna_right,
                    antenna_left=NEUTRAL.antenna_left, body_yaw=0.0,
                    source="return to neutral")
        trajectory = Trajectory.build([step], self._commanded, stretch="all")
        if self._refusal(trajectory) is not None:
            # A straight line home would pass through an unreachable pose
            # (e.g. from a pose the SDK left us in). The SDK's own wake_up
            # routine knows a safe path, so let it do the work.
            log.warning("No safe straight path to neutral; using the SDK's wake_up().")
            self._commanded = self.backend.run_action("wake_up")
            self._last_send = time.monotonic()
            return
        if self._commanded.is_close(NEUTRAL, tolerance=0.2):
            return
        self._internal = _Run(name="return to neutral", steps=[step],
                              trajectory=trajectory, t0=time.monotonic(),
                              loops_left=1, internal=True)

    # --- ending things --------------------------------------------------------

    def _interrupt_all(self, by: str, keep_idle: bool = False) -> None:
        interrupted_sound = False
        for run in (self._active, self._internal) + (() if keep_idle else (self._idle_run,)):
            if run is not None:
                self._end_run(run, "interrupted", by=by)
                interrupted_sound = True
        if interrupted_sound:
            self._audio.submit(self.backend.stop_sound)

    def _end_run(self, run: _Run, outcome: str, by: str | None = None,
                 reason: str | None = None) -> None:
        with self._lock:
            if run is self._active:
                self._active = None
        if run is self._idle_run:
            self._idle_run = None
        if run is self._internal:
            self._internal = None
        if run.internal:
            return
        if outcome == "finished":
            self._emit("behavior_finished", run.name,
                       duration=round(time.monotonic() - run.started, 3), idle=run.idle)
        else:
            self._emit("behavior_interrupted", run.name, by=by, reason=reason,
                       at=round(time.monotonic() - run.started, 3), idle=run.idle)
        if run.playback:
            run.playback._end(outcome, by=by, reason=reason)

    # --- one frame -------------------------------------------------------------

    def _advance(self, run: _Run, scheduled: float) -> None:
        now = time.monotonic()
        t = now - run.t0
        trajectory = run.trajectory

        # Cues -- sounds, lines and keyframe arrivals -- that are now due.
        while run.cue_index < len(trajectory.cues) and trajectory.cues[run.cue_index].t <= t:
            self._fire(run, trajectory.cues[run.cue_index])
            run.cue_index += 1

        segment = trajectory.segment_at(t)
        if segment.action is not None:
            self._perform_action(run, segment)
            return

        if t >= trajectory.duration:
            self._send(trajectory.end_pose, run, scheduled)
            if run is not self._active and run is not self._idle_run and run is not self._internal:
                return  # _send abandoned it
            self._next_loop_or_finish(run)
            return

        self._send(trajectory.sample(t), run, scheduled)

    def _perform_action(self, run: _Run, segment) -> None:
        log.info('"%s": handing over to the SDK for %s()', run.name, segment.action)
        self._commanded = self.backend.run_action(segment.action)
        self._last_send = time.monotonic()
        if segment.action == "goto_sleep":
            self._asleep = True
        # Fire whatever was due at or before the action, then end the run --
        # an action is always the last keyframe (the schema enforces it).
        for cue in run.trajectory.cues[run.cue_index:]:
            self._fire(run, cue)
        run.cue_index = len(run.trajectory.cues)
        self._end_run(run, "finished")

    def _next_loop_or_finish(self, run: _Run) -> None:
        if run.loops_left is not None:
            run.loops_left -= 1
        if run.loops_left is None or run.loops_left > 0:
            # Same resolved steps every pass, so a loop cannot drift; only the
            # blend into the first keyframe is re-planned from where we are.
            trajectory = Trajectory.build(run.steps, self._commanded, stretch="all")
            reason = self._refusal(trajectory)
            if reason is None:
                run.trajectory, run.t0, run.cue_index = trajectory, time.monotonic(), 0
                return
            log.error('"%s": the next loop would break a safety rule (%s); stopping it.',
                      run.name, reason)
        self._end_run(run, "finished")

    def _send(self, pose: Pose, run: _Run, scheduled: float) -> None:
        now = time.monotonic()
        problems = safety.check_pose(pose, check_reach=self.check_reach)
        if self._last_send is not None:
            problems += safety.check_tick_speed(self._commanded, pose, now - self._last_send)
        if problems:
            # Should be impossible after the up-front check. If it happens
            # anyway, refuse the frame, abandon the behaviour, and go home.
            reason = problems[0].message
            log.error('Refused a frame of "%s": %s Abandoning it.', run.name, reason)
            self._end_run(run, "interrupted", by="safety", reason=reason)
            if run.internal:
                log.critical("Even the return to neutral was refused. Holding still.")
                return
            self._return_to_neutral()
            return
        self.backend.send(pose)
        self._commanded, self._last_send = pose, now
        self._lateness.append(now - scheduled)

    # --- sounds and words ------------------------------------------------------

    def _fire(self, run: _Run, cue) -> None:
        if run.internal:
            return
        if cue.kind == "keyframe":
            self._emit("keyframe_reached", run.name, index=cue.segment + 1,
                       source=cue.value, idle=run.idle)
        elif cue.kind == "sound":
            path = self.sounds_dir / cue.value
            self._audio.submit(self.backend.play_sound, path)
            self._emit("sound_played", run.name, file=cue.value, idle=run.idle)
        elif cue.kind == "say":
            text = self._pick_line(cue.value, run.tone, run.context)
            if text is not None:
                self._emit("line_requested", run.name, line_id=cue.value,
                           tone=run.tone, text=text, idle=run.idle)
                self._speech.submit(self.speech_callback, cue.value, text)

    def _pick_line(self, line_id: str, tone: str, context: dict) -> str | None:
        """A random wording, not the one used last time, with slots filled in.

        Prefers the requested tone. A wording with a placeholder the caller gave
        no value for -- say {minutes} with no context -- is passed over in
        favour of one that can be said in full, rather than speaking a literal
        "{minutes}".
        """
        line = self._library.lines.get(line_id)
        if line is None:
            log.error('No line "%s" in lines.yaml.', line_id)
            return None
        other = "direct" if tone == "hedged" else "hedged"
        for pool in (getattr(line.variants, tone), getattr(line.variants, other)):
            usable = [v for v in pool if set(re.findall(r"\{(\w+)\}", v)) <= set(context)]
            if not usable:
                continue
            fresh = [v for v in usable if v != self._last_lines.get(line_id)] or usable
            choice = random.choice(fresh)
            self._last_lines[line_id] = choice
            return re.sub(r"\{(\w+)\}", lambda m: str(context[m.group(1)]), choice)
        log.warning('Every wording of "%s" needs a value that was not given (%s).',
                    line_id, ", ".join(sorted(context)) or "no context")
        return random.choice(getattr(line.variants, tone) or getattr(line.variants, other))


def _loops(behavior: Behavior) -> int | None:
    if behavior.loop == "forever":
        return None
    if behavior.loop == "once":
        return 1
    return int(behavior.loop)
