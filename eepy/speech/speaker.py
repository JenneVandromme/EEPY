"""The Speaker: says lines and free text, one at a time, without blocking anyone.

    speaker = Speaker(PiperEngine(), fallback=SystemEngine(), output="auto",
                      lines=lambda: library.lines)
    speaker.start(backend)                  # picks the output, warms up, fills the cache
    speaker.speak_line("refocus_break_offer", "direct", minutes=5)
    speaker.speak_text("Need a hand?")
    speaker.stop()                          # cuts speech within ~20 ms

HOW IT BEHAVES

  * speak_line / speak_text choose the words at once (so a missing placeholder
    raises in the caller's face) and return immediately. Synthesis and playback
    happen on the speaker's own thread.
  * One voice at a time. A new request interrupts whatever is being said, and
    anything still waiting its turn is dropped -- the newest request wins.
  * stop() never waits. It only sets a flag; the speaker's thread cuts the
    audio within about 20 ms. That matters because the BehaviorPlayer calls it
    from its 50 Hz motion loop.
  * Fixed lines come from the pre-generated cache and start at once. Free text
    and lines with placeholders are synthesized live.
  * If the main engine fails, the fallback engine is tried; if that fails too,
    the words are shown and logged instead (speech_failed), and everything else
    carries on.

The end of playback is never reported by the robot, so a clip counts as
finished once its own length has passed (a WAV knows its length exactly). The
desktop output can tell when its player exits, and that is used too.

EVENTS (through `emit`, which the BehaviorPlayer connects to its event bus):

    speech_requested    the words were chosen          line_id, variant, text
    speech_started      audio began                    + engine, latency_ms, cached,
                                                         output, duration_s
    speech_finished     the clip played to the end
    speech_interrupted  cut short                      + by
    speech_failed       nothing could be played        + error

Every one also carries `utterance` (a number), so a start and its end can be
paired, and `source` / `behavior` when the caller gave them.
"""

from __future__ import annotations

import itertools
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from eepy.logs.clock import monotonic

from . import audio
from .cache import SpeechCache
from .engines import Engine, EngineError
from .lines import LineSelector, SpeechError, placeholders
from .outputs import NullOutput, Output, OutputError, make_output

log = logging.getLogger("eepy.speech")

FREE_TEXT = "free_text"
POLL = 0.02         # how often a playing clip checks for stop / end
ROBOT_LEAD = 0.2    # the robot's audio pipeline starts this much late: count it in

Emit = Callable[[str, dict], None]


def _log_event(kind: str, data: dict) -> None:
    log.info("[%s] %s", kind, {k: v for k, v in data.items() if v is not None})


class Utterance:
    """One thing to say. speak_line / speak_text return it straight away."""

    _ids = itertools.count(1)

    def __init__(self, line_id: str, variant: Optional[str], text: str, cacheable: bool,
                 source: Optional[str], behavior: Optional[str]) -> None:
        self.id = next(self._ids)
        self.line_id, self.variant, self.text = line_id, variant, text
        self.cacheable = cacheable
        self.source, self.behavior = source, behavior
        self.requested = monotonic()
        self.outcome: Optional[str] = None       # finished / interrupted / failed
        self.by: Optional[str] = None
        self._cancel = threading.Event()
        self._done = threading.Event()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until it ends. True only if it was spoken to the end."""
        self._done.wait(timeout)
        return self.outcome == "finished"

    def __repr__(self) -> str:
        return f"<Utterance {self.id} {self.line_id} {self.outcome or 'pending'}: {self.text!r}>"


class Speaker:
    def __init__(
        self,
        engine: Optional[Engine] = None,
        *,
        fallback: Optional[Engine] = None,
        output: Union[str, Output] = "auto",
        cache: Optional[SpeechCache] = None,
        lines: Optional[Callable[[], Mapping[str, Any]]] = None,
        emit: Optional[Emit] = None,
        on_text: Optional[Callable[[str, str], None]] = None,
        prepare: bool = True,
    ) -> None:
        """engine=None means text only: nothing is synthesized, the words are
        handed to on_text and logged. That is the BehaviorPlayer's default."""
        self.engine = engine
        self.fallback = fallback
        self._output_spec = output
        self.output: Output = output if isinstance(output, Output) else NullOutput()
        self.cache = cache if cache is not None else (SpeechCache() if engine else None)
        self._lines = lines or (lambda: {})
        self.selector = LineSelector(lambda: self._lines())
        self.emit: Emit = emit or _log_event
        self.on_text = on_text
        self.prepare_on_start = prepare

        self._cond = threading.Condition()
        self._pending: Optional[Utterance] = None
        self._current: Optional[Utterance] = None
        self._closing = False
        self._thread: Optional[threading.Thread] = None
        self._warned: set[str] = set()
        self.ready = threading.Event()          # the cache has been prepared
        self.last_prepare: Optional[dict] = None

    # ------------------------------------------------------------------ setup

    def set_lines(self, lines: Callable[[], Mapping[str, Any]]) -> None:
        self._lines = lines

    def start(self, backend=None) -> "Speaker":
        """Choose the output (now that the robot is connected), start the
        speaking thread, and prepare the cache in the background."""
        if isinstance(self._output_spec, str):
            if self.engine is None:
                self.output = NullOutput()
            else:
                self.output = make_output(self._output_spec, backend)
        reason = self.output.unavailable()
        if reason:
            log.warning("Speech output %s is unavailable (%s); words are shown instead.",
                        self.output.name, reason)
            self.output = NullOutput()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="eepy-speech", daemon=True)
            self._thread.start()
        if self.prepare_on_start and self.engine is not None:
            self.prepare_async()
        else:
            self.ready.set()
        return self

    def active_engine(self) -> Optional[Engine]:
        """The engine that will actually speak: the main one if it can, else the fallback."""
        for engine in (self.engine, self.fallback):
            if engine is None:
                continue
            reason = engine.unavailable()
            if reason is None:
                return engine
            self._warn_once(engine.key, f"Speech engine {engine.label} cannot be used: {reason}")
        return None

    def prepare(self) -> Optional[dict]:
        """Generate every fixed line not yet cached, delete stale ones, and
        get the clips onto the robot. Blocks; see prepare_async."""
        engine = self.active_engine()
        if engine is None or self.cache is None:
            self.ready.set()
            return None
        # Even when every line is already cached, load the voice now: otherwise
        # the first piece of live text pays for it (measured: ~0.5 s extra).
        try:
            engine.warm_up()
        except Exception as exc:  # noqa: BLE001 -- it will be tried again when needed
            log.warning("Could not warm up %s: %s", engine.label, exc)
        report = self.cache.prepare(engine, self._lines())
        self.output.preload(report.pop("clips"))
        for failure in report["failed"]:
            log.warning("Could not prepare a line (it will be tried live): %s", failure)
        log.info("Speech ready (%s): %d lines reused, %d generated, %d removed, in %.1fs.",
                 report["engine"], report["reused"], report["generated"], report["removed"],
                 report["seconds"])
        self.last_prepare = report
        self.ready.set()
        return report

    def prepare_async(self) -> None:
        """prepare() on a background thread -- after a reload, say."""
        self.ready.clear()
        threading.Thread(target=self._safe_prepare, name="eepy-speech-prepare",
                         daemon=True).start()

    def _safe_prepare(self) -> None:
        try:
            self.prepare()
        except Exception:  # noqa: BLE001 -- lines are then simply spoken live
            log.exception("Preparing speech failed; lines will be synthesized live.")
            self.ready.set()

    def close(self, timeout: float = 3.0) -> None:
        self.stop(by="close")
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
        self.output.close()

    # ------------------------------------------------------------------ speaking

    def speak_line(self, line_id: str, variant: str = "hedged", *,
                   source: Optional[str] = None, behavior: Optional[str] = None,
                   **values: Any) -> Utterance:
        """Say a line from lines.yaml. Returns at once.

        variant is "hedged" or "direct". Placeholders are filled from the
        keyword arguments: speak_line("bedtime_negotiate", "direct", minutes=10).
        Raises SpeechError -- with an explanation -- if the line does not exist
        or no wording of that variant can be filled in.
        """
        text = self.selector.pick(line_id, variant, **values)
        cacheable = text in self.selector.wordings(line_id, variant) and not placeholders(text)
        return self._enqueue(Utterance(line_id, variant, text, cacheable, source, behavior))

    def speak_text(self, text: str, *, source: Optional[str] = None,
                   behavior: Optional[str] = None) -> Utterance:
        """Say any text, as written. Synthesized live. Returns at once."""
        text = " ".join(str(text or "").split())
        if not text:
            raise SpeechError("There is nothing to say -- the text is empty.")
        return self._enqueue(Utterance(FREE_TEXT, None, text, False, source, behavior))

    def stop(self, by: str = "stop") -> None:
        """Cut whatever is being said, and drop anything waiting. Never blocks."""
        with self._cond:
            pending, self._pending = self._pending, None
            current = self._current
            if current is not None and current.by is None:
                current.by = by
                current._cancel.set()
            self._cond.notify_all()
        if pending is not None:
            self._finish(pending, "interrupted", by=by)

    def is_speaking(self) -> bool:
        """True while something is being said or is about to be."""
        with self._cond:
            return self._pending is not None or (
                self._current is not None and not self._current.done)

    # ------------------------------------------------------------------ internals

    def _enqueue(self, utterance: Utterance) -> Utterance:
        self._emit("speech_requested", utterance)
        with self._cond:
            dropped, self._pending = self._pending, utterance
            current = self._current
            if current is not None and not current.done and current.by is None:
                current.by = "newer speech"
                current._cancel.set()
            self._cond.notify_all()
        if dropped is not None:
            self._finish(dropped, "interrupted", by="newer speech")
        if self._thread is None:
            # Not started (no robot yet, or a bare Speaker in a script): speak
            # on a thread of our own so the caller still never waits.
            self.start()
        return utterance

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._pending is None and not self._closing:
                    self._cond.wait()
                if self._pending is None:
                    return
                utterance, self._pending = self._pending, None
                self._current = utterance
            try:
                self._say(utterance)
            except Exception as exc:  # noqa: BLE001 -- one bad clip must not end the speaker
                log.exception("Speaking failed unexpectedly")
                self._fail(utterance, f"{type(exc).__name__}: {exc}")
            finally:
                with self._cond:
                    if self._current is utterance:
                        self._current = None

    def _say(self, u: Utterance) -> None:
        if u._cancel.is_set():
            self._finish(u, "interrupted", by=u.by)
            return

        path, engine_label, cached, errors = None, None, False, []
        for engine in (self.engine, self.fallback):
            if engine is None or engine.unavailable() is not None:
                continue
            try:
                path, cached = self._clip(engine, u)
                engine_label = engine.label
                break
            except EngineError as exc:
                errors.append(str(exc))
                log.warning("Speech engine %s failed: %s", engine.label, exc)

        if u._cancel.is_set():
            self._finish(u, "interrupted", by=u.by)
            return

        if self.engine is None:                     # text only, by design
            self._started(u, "text-only", False, 0.0)
            self._finish(u, "finished")
            return
        if path is None:
            reason = "; ".join(errors) or "no speech engine is available"
            self._fail(u, reason)
            return

        duration = audio.file_duration(path)
        try:
            self.output.play(path)
        except OutputError as exc:
            self._fail(u, str(exc), engine=engine_label)
            return
        started = monotonic()
        self._started(u, engine_label, cached, duration)

        lead = ROBOT_LEAD if self.output.name == "robot" else 0.0
        limit = duration + lead + 0.5          # a stuck player can never hang us
        while True:
            if u._cancel.wait(POLL):
                self.output.stop()
                self._finish(u, "interrupted", by=u.by)
                return
            elapsed = monotonic() - started
            finished = self.output.done()
            if finished is True and elapsed >= min(duration, 0.05):
                break
            if finished is None and elapsed >= duration + lead:
                break
            if elapsed >= limit:
                break
        self._finish(u, "finished")

    def _clip(self, engine: Engine, u: Utterance) -> tuple[Path, bool]:
        """The audio file for an utterance: from the cache if there, else made now."""
        cache = self.cache or SpeechCache()
        if u.cacheable:
            hit = cache.get(engine, u.text)
            if hit is not None:
                return hit, True
            return cache.put(engine, u.text, engine.synthesize(u.text)), False
        return cache.live(engine, engine.synthesize(u.text)), False

    # ------------------------------------------------------------------ reporting

    def _data(self, u: Utterance, **extra: Any) -> dict:
        return {"utterance": u.id, "line_id": u.line_id, "variant": u.variant, "text": u.text,
                "source": u.source, "behavior": u.behavior, **extra}

    def _emit(self, kind: str, u: Utterance, **extra: Any) -> None:
        try:
            self.emit(kind, self._data(u, **extra))
        except Exception:  # noqa: BLE001 -- reporting must never stop speech
            log.exception("speech event %s could not be delivered", kind)

    def _started(self, u: Utterance, engine: str, cached: bool, duration: float) -> None:
        latency = (monotonic() - u.requested) * 1000
        self._emit("speech_started", u, engine=engine, latency_ms=round(latency, 1),
                   cached=cached, output=self.output.name, duration_s=round(duration, 3))
        if self.on_text is not None:
            try:
                self.on_text(u.line_id, u.text)
            except Exception:  # noqa: BLE001 -- a broken hook must not stop speech
                log.exception("on_text hook failed")

    def _finish(self, u: Utterance, outcome: str, by: Optional[str] = None) -> None:
        if u.done:
            return
        u.outcome, u.by = outcome, by if outcome == "interrupted" else None
        if outcome == "finished":
            self._emit("speech_finished", u)
        elif outcome == "interrupted":
            self._emit("speech_interrupted", u, by=by or "stop")
        u._done.set()

    def _fail(self, u: Utterance, error: str, engine: Optional[str] = None) -> None:
        if u.done:
            return
        log.error('Could not speak "%s": %s', u.text, error)
        u.outcome = "failed"
        self._emit("speech_failed", u, error=error, engine=engine)
        if self.on_text is not None:
            try:
                self.on_text(u.line_id, u.text)     # at least the words get out
            except Exception:  # noqa: BLE001
                log.exception("on_text hook failed")
        u._done.set()

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)
