"""The Wizard-of-Oz panel's server: one web page that drives the BehaviorPlayer.

WHY THIS SHAPE

FastAPI plus one plain HTML file, with Server-Sent Events for live updates.
Everything it needs is already installed with the robot SDK, there is no build
step, and the page is one file a designer can open and read.

Most importantly, the panel runs in the SAME Python process as the player.
Every timestamp -- an operator's "yawn" marker, the robot's
"behavior_started", a perception feature from the vision process -- comes from
eepy.logs.clock.monotonic(), so they can be lined up exactly when fitting the
HMM. All logging goes through the central logger, eepy.logs.log.

STATE LIVES HERE, NOT IN THE BROWSER

The session, the idle setting, the live log and the last error all live in this
process. The page only shows them -- so refreshing the browser, or opening the
panel on a second laptop, loses nothing and changes nothing.

ROBUSTNESS

  * Buttons only post a request; the player's own preemption handles overlap.
    Pressing them as fast as you like cannot crash anything.
  * STOP never waits for anything else. It does not take the panel's lock.
  * If the robot is unreachable at start-up, or drops later, the panel keeps
    running: status goes red, Reconnect tries again, and annotations and the
    session log keep working throughout.
  * A broken behaviour file or panel config on Reload keeps the last good
    version and shows the explanation on the page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from eepy.behaviors.backends import Backend, make_backend
from eepy.behaviors.events import Event
from eepy.behaviors.loader import BEHAVIORS_DIR, SOUNDS_DIR, Library, load_library
from eepy.behaviors.player import BehaviorPlayer, PlayerError
from eepy.behaviors.validate import Validator, report

from eepy.logs import SessionLog
from eepy.logs import log as central_log
from eepy.logs.check import clear_stale_pointer, stale_pointer
from eepy.logs.clock import monotonic as shared_clock
from eepy.logs.schema import IMPLEMENTED_MODES
from eepy.logs.session import SessionError

from .config import CONFIG_PATH, ConfigError, Panel, PanelConfig, build_panel, load_config

log = logging.getLogger("eepy.wizard")

STATIC = Path(__file__).parent / "static"
LOG_SIZE = 200          # records kept for the live log
STREAM_PERIOD = 0.15    # how often the page is updated, seconds


class WizardError(Exception):
    """An operator request that cannot be honoured. The message is for humans."""


def _validation_report(behaviors_dir: Path, sounds_dir: Path) -> str | None:
    """The validator's explanation, or None if the files are fine."""
    import contextlib
    import io

    validator = Validator(directory=behaviors_dir, sounds=sounds_dir)
    validator.run()
    if not validator.errors:
        return None
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        report(validator, behaviors_dir.resolve().parent, strict=False)
    return buffer.getvalue().strip()


class Wizard:
    """Owns the player, the session and the panel state. No web code in here."""

    def __init__(
        self,
        backend_factory: Callable[[], Backend],
        *,
        backend_name: str = "dry",
        behaviors_dir: Path = BEHAVIORS_DIR,
        sounds_dir: Path = SOUNDS_DIR,
        config_path: Path = CONFIG_PATH,
        mode: str = "wizard",
        session_log: SessionLog | None = None,
        speech_callback: Callable[[str, str], None] | None = None,
        speaker_factory: Callable[[], Any] | None = None,
        camera_relay: tuple[int, float] | None = None,
        calibration_store: Any = None,
    ) -> None:
        self.backend_factory = backend_factory
        self.backend_name = backend_name
        self.behaviors_dir = Path(behaviors_dir)
        self.sounds_dir = Path(sounds_dir)
        self.config_path = Path(config_path)
        self.speech_callback = speech_callback
        # A new Speaker per connection: its output (robot or desktop) depends on
        # what the backend can do. None: text only, through speech_callback.
        self.speaker_factory = speaker_factory
        self.mode = mode
        self.mode_implemented = mode in IMPLEMENTED_MODES
        # The central logger, unless a test passes its own.
        self.sessions = session_log if session_log is not None else central_log
        self.clock = self.sessions.clock

        # Two locks, both only ever held for an instant -- never across a slow
        # call like connecting to the robot or running the validator. A lock
        # held for seconds once stalled the web server's event loop during a
        # reconnect, and with it the STOP button.
        self._lock = threading.RLock()        # the live log and panel state
        self._conn_lock = threading.Lock()    # connection state transitions
        self._reconnecting = False
        self._log: deque[tuple[int, dict]] = deque(maxlen=LOG_SIZE)
        self._seq = 0
        self.panel_version = 0
        self.last_error: dict | None = None
        self.player: BehaviorPlayer | None = None
        self.connection_error: str | None = None
        self._was_connected: bool | None = None
        self._unsubscribe: Callable[[], None] | None = None

        report_text = _validation_report(self.behaviors_dir, self.sounds_dir)
        if report_text:
            # The page can still start -- with nothing to play -- so whoever
            # is at the panel sees the problem instead of a dead server.
            self.library = Library()
            self._error("The behaviour files have errors, so nothing can be "
                        "played until they are fixed and you press Reload.\n\n" + report_text)
        else:
            self.library = load_library(self.behaviors_dir)
        try:
            self.config = load_config(self.config_path)
        except ConfigError as exc:
            self.config = PanelConfig()
            self._error(f"The panel config has a problem, so defaults are in use:\n{exc}")
        self.panel: Panel = build_panel(self.library, self.config)
        self.idle_on = self.panel.idle is not None

        crashed = stale_pointer(self.sessions.data_dir)
        if crashed:
            # Its data is kept as it was. Clearing the pointer stops the vision
            # process writing into it, and the check will mark it INCOMPLETE.
            clear_stale_pointer(self.sessions.data_dir)
            self._error(f'The last session ("{crashed}") did not end cleanly -- the panel '
                        "probably crashed. Its data up to that point is kept. Check it with:\n"
                        "    python -m eepy.logs.check")
        if not self.mode_implemented:
            self._error(f'Mode "{mode}" has no controller yet, so nothing decides on its own: '
                        "the robot only does what you press. Sessions are still recorded "
                        f'as mode "{mode}".')

        # The camera relay: the robot's camera and head pose, for the vision
        # process, on 127.0.0.1 only (eepy/camera/relay.py). (port, fps); None
        # in tests. A busy port is a warning, not a reason to refuse to start.
        # "look_at: user" follows the real student when the vision process reports
        # the face (relay USER message); otherwise the default from _blocks.yaml.
        # One provider for the panel's lifetime, handed to every player it creates.
        from eepy.behaviors.pose import Pose
        from eepy.behaviors.targets import StaticTargetProvider, TrackingTargetProvider

        self.user_target = TrackingTargetProvider(
            StaticTargetProvider(lambda: self.library),
            pose=lambda: self.player.pose if self.player is not None else Pose())
        # The calibration step of a session (A12). It runs IN the vision process (that
        # program has the camera); the panel queues commands for it, speaks its lines
        # and shows its progress. See eepy/perception/calibration/remote.py.
        self.calibration: dict = {"state": "none"}
        self.calibration_store = calibration_store          # None: data/calibration/
        self._calibration_commands: deque = deque()
        self._vision_seen = -1e9
        self.camera_relay = None
        if camera_relay is not None:
            self._start_camera_relay(*camera_relay)

        self._watching = True
        threading.Thread(target=self._watch_connection, name="eepy-wizard-watch",
                         daemon=True).start()
        self.connect()

    # ------------------------------------------------------------------ logging

    def _push(self, record: dict) -> None:
        with self._lock:
            self._seq += 1
            self._log.append((self._seq, record))

    def _note(self, kind: str, source: str, t: float | None = None, **data: Any) -> dict:
        """Log an event (saved if a session is running) and show it on the page."""
        record = self.sessions.event(kind, source, t_mono=t, **data)
        self._push(record)
        return record

    def _error(self, message: str) -> None:
        self.last_error = {"message": message, "wall": self.clock.wall(shared_clock())}
        log.error(message)
        try:
            self._note("error", "system", message=message[:2000])
        except Exception:  # noqa: BLE001 -- reporting an error must never raise one
            pass

    def _on_event(self, event: Event) -> None:
        """Every player event goes to the live log, and to the session file."""
        data = dict(event.data)
        idle = data.pop("idle", False)
        source = data.pop("source", None) or ("idle" if idle else "system")
        record = self.sessions.event(event.kind, source, t_mono=event.t,
                                     behavior=event.behavior, **data)
        self._push(record)
        if event.kind == "behavior_refused":
            self._error(f'"{event.behavior}" was refused: {data.get("reason")}')
        elif event.kind == "speech_failed":
            # The words still reach the operator, who can say them if need be.
            words = f'\n\n    "{data["text"]}"' if data.get("text") else ""
            self._error(f"The robot could not speak{words}\n\n({data.get('error')})")

    # --------------------------------------------------------------- the robot

    def connect(self) -> bool:
        # Connecting can take seconds (an unreachable robot times out), so it
        # happens with no lock held; only the swap at the end is locked.
        try:
            backend = self.backend_factory()
            speaker = self.speaker_factory() if self.speaker_factory else None
            player = BehaviorPlayer(backend, behaviors_dir=self.behaviors_dir,
                                    sounds_dir=self.sounds_dir, speaker=speaker,
                                    speech_callback=self.speech_callback,
                                    targets=self.user_target)
            unsubscribe = player.events.subscribe(self._on_event)
            player.start()
        except Exception as exc:  # noqa: BLE001 -- shown to the operator
            with self._conn_lock:
                self.player = None
                self.connection_error = str(exc)
                self._was_connected = False
            self._error(f"Could not connect to the robot ({self.backend_name}):\n{exc}")
            self._note("connection", "system", status="failed", error=str(exc))
            return False
        with self._conn_lock:
            self.player, self._unsubscribe = player, unsubscribe
            self.connection_error = None
            self._was_connected = True
        with self._lock:
            self.library = player.library
        if self.idle_on and self.panel.idle:
            player.set_idle(self.panel.idle)
        self._note("connection", "system", status="connected", backend=self.backend_name)
        return True

    def connected(self) -> bool:
        player = self.player
        if player is None or player.error is not None:
            return False
        try:
            return player.backend.is_connected()
        except Exception:  # noqa: BLE001 -- a link that cannot even be asked is not connected
            return False

    def _check_connection(self) -> bool:
        """Is the robot connected? Logs the moment it stops being.

        Called from EVERY place that looks -- the page's status updates, an
        attempted play, and a background check twice a second. Whichever
        notices a drop first records it, so even a drop shorter than the
        background check's interval reaches the session log. (Checking only in
        the background once let a short outage show red on the page while
        leaving no trace in the data.)
        """
        with self._conn_lock:
            if self._reconnecting:
                # The operator is replacing the connection on purpose; any
                # drop before it was already logged when reconnect() began.
                return False
            # Read AND compare under the same lock. Reading first and locking
            # second let a check that started mid-reconnect write a stale
            # "lost" right after the fresh "connected".
            now = self.connected()
            if self._was_connected and not now:
                reason = (self.player.error if self.player else None) or "the link to the robot dropped"
                self.connection_error = reason
                self._error(f"Lost the connection to the robot: {reason}. "
                            "Annotations still work. Press Reconnect when the robot is back.")
                self._note("connection", "system", status="lost", error=reason)
            self._was_connected = now
        return now

    def _watch_connection(self) -> None:
        """Notice a dropped robot even when nothing is playing and no page is open."""
        while self._watching:
            self._check_connection()
            time.sleep(0.5)

    def reconnect(self) -> bool:
        self._check_connection()          # log a drop nobody has noticed yet
        with self._conn_lock:
            if self._reconnecting:
                raise WizardError("Already reconnecting -- give it a moment.")
            self._reconnecting = True
            old, self.player = self.player, None
            unsubscribe, self._unsubscribe = self._unsubscribe, None
        self._note("wizard_command", "wizard", action="reconnect")
        try:
            if old is not None:
                if unsubscribe:
                    unsubscribe()
                try:
                    old.close(return_to_neutral=old.error is None, timeout=4)
                except Exception:  # noqa: BLE001 -- the old link may already be dead
                    pass
            return self.connect()
        finally:
            with self._conn_lock:
                self._reconnecting = False

    def _require_player(self) -> BehaviorPlayer:
        player = self.player
        if player is None or not self._check_connection():
            raise WizardError("The robot is not connected. Press Reconnect once it is "
                              "back. (Annotations still work.)")
        return player

    # --------------------------------------------------------------- actions

    def play(self, behavior: str, intensity: float, via: str = "click") -> None:
        if not 0.0 <= intensity <= 1.0:
            raise WizardError(f"Intensity must be between 0 and 1, not {intensity}.")
        player = self._require_player()
        # Stamped at the moment of the press, but written only once the player
        # has ACCEPTED it: a misspelled or refused request must not appear in a
        # data log as something the operator played.
        pressed = shared_clock()
        try:
            player.play(behavior, intensity=intensity, source="wizard")
        except PlayerError as exc:
            self._error(str(exc))
            raise WizardError(str(exc)) from None
        self._note("wizard_command", "wizard", t=pressed, action="play", behavior=behavior,
                   intensity=round(intensity, 3), via=via)

    def stop(self, via: str = "click") -> None:
        # Deliberately no lock and no connection check: STOP must never wait
        # behind anything, and must reach the player if there is one at all.
        player = self.player
        self._note("wizard_command", "wizard", action="stop", via=via)
        if player is not None and player.error is None:
            player.stop(return_to_neutral=True)

    def set_idle(self, on: bool, via: str = "click") -> None:
        if self.panel.idle is None:
            raise WizardError("No idle behaviour is set in wizard_panel.yaml.")
        self.idle_on = on
        self._note("wizard_command", "wizard", action="idle_on" if on else "idle_off",
                   behavior=self.panel.idle, via=via)
        player = self.player
        if player is not None and player.error is None:
            player.set_idle(self.panel.idle if on else None)
        self.panel_version += 1

    def say(self, line_id: str | None, text: str | None, tone: str,
            via: str = "click") -> str:
        player = self._require_player()
        pressed = shared_clock()
        try:
            spoken = player.say(line_id, text, tone=tone, source="wizard")
        except PlayerError as exc:
            raise WizardError(str(exc)) from None
        self._note("wizard_command", "wizard", t=pressed, action="say", line_id=line_id,
                   text=text, tone=tone, via=via)
        return spoken

    def annotate(self, marker: str, via: str = "key") -> dict:
        markers = {m.id: m for m in self.config.markers}
        if marker not in markers:
            raise WizardError(f'There is no marker called "{marker}".')
        return self._note("annotation", "wizard", marker=marker,
                          label=markers[marker].label, via=via)

    def note(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise WizardError("The note is empty.")
        return self._note("note", "wizard", text=text)

    def start_session(self, participant: str, notes: str | None = None) -> dict:
        try:
            record = self.sessions.start(
                participant, mode=self.mode, backend=self.backend_name, notes=notes,
                intensity_default=self.config.default_intensity,
                behaviors=sorted(self.library.behaviors),
                markers=[m.id for m in self.config.markers])
        except SessionError as exc:
            raise WizardError(str(exc)) from None
        self._push(record)
        if not self.mode_implemented:
            self._note("mode_not_implemented", "system", mode=self.mode)
        self._offer_calibration(participant)
        self.panel_version += 1
        return record

    def end_session(self, reason: str = "operator", notes: str | None = None) -> dict:
        try:
            record = self.sessions.end(reason, notes=(notes or "").strip() or None)
        except SessionError as exc:
            raise WizardError(str(exc)) from None
        self._push(record)
        self.calibration = {"state": "none"}
        self._calibration_commands.clear()
        self.panel_version += 1
        return record

    def reload(self) -> dict:
        """Re-read behaviours AND panel config. Keep the last good of each.

        Validating takes a moment, so it runs with no lock held; only swapping
        in the new library and buttons is locked.
        """
        self._note("wizard_command", "wizard", action="reload")
        problems: list[str] = []
        library = None
        player = self.player
        if player is not None and player.error is None:
            result = player.reload()
            if result.ok:
                library = load_library(self.behaviors_dir)
            else:
                problems.append(result.message)
        else:
            report_text = _validation_report(self.behaviors_dir, self.sounds_dir)
            if report_text:
                problems.append(report_text)
            else:
                library = load_library(self.behaviors_dir)
        config = None
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            problems.append(str(exc))
        with self._lock:
            if library is not None:
                self.library = library
            if config is not None:
                self.config = config
            self.panel = build_panel(self.library, self.config)
            if self.panel.idle is None:
                self.idle_on = False
            self.panel_version += 1
        ok = not problems
        message = ("Reloaded." if ok else
                   "Kept the last good version. Fix this and press Reload again:\n\n"
                   + "\n\n".join(problems))
        if not ok:
            self._error(message)
        self._note("reload", "system", ok=ok,
                   behaviors=len(self.library.behaviors) if ok else None)
        return {"ok": ok, "message": message}

    # --------------------------------------------------------------- the camera

    def _start_camera_relay(self, port: int, fps: float) -> None:
        from eepy.camera.relay import CameraRelay, RelayPortBusy, pose_dict

        def pose() -> dict | None:
            player = self.player
            return pose_dict(player.pose) if player is not None and player.error is None else None

        def frame() -> bytes | None:
            mini = self._robot_camera_client()
            return mini.media.get_frame_jpeg() if mini is not None else None

        relay = CameraRelay(port, frame_fn=frame, pose_fn=pose, fps=fps,
                            label=lambda: "real" if self._robot_camera_client() is not None else None,
                            user_fn=self.user_target.observe, say_fn=self._calibration_say,
                            command_fn=self._next_calibration_command,
                            calib_fn=self._calibration_report)
        try:
            self.camera_relay = relay.start()
        except RelayPortBusy as exc:
            self._error(f"The camera relay could not start, so the vision process gets no robot "
                        f"camera or head position from this panel.\n{exc}")

    # --------------------------------------------------------------- calibration

    def _calibration_setup(self) -> str:
        return "robot" if self.backend_name == "real" else "laptop"

    def _offer_calibration(self, participant: str) -> None:
        """After Start session: is there a recent calibration to reuse?"""
        try:
            from eepy.perception.calibration import recent
            from eepy.perception.gaze.calibration import CalibrationStore

            store = self.calibration_store or CalibrationStore()
            found = recent(store, participant, self._calibration_setup())
        except Exception:  # noqa: BLE001 -- an unreadable file must not block a session
            log.exception("Could not look for an earlier calibration")
            found = None
        self.calibration = {"state": "pending", "user": participant, "reuse": found}

    def calibration_command(self, action: str, reason: str | None = None,
                            parts: list | None = None, via: str = "click") -> dict:
        """The operator pressed Calibrate / Reuse / Skip / Stop."""
        if not self.sessions.active:
            raise WizardError("Start a session first: the calibration belongs to a participant.")
        if action not in ("calibrate", "reuse", "skip", "stop"):
            raise WizardError(f'"{action}" is not a calibration action.')
        user = self.calibration.get("user") or self.sessions.participant
        self._note("wizard_command", "wizard", action=f"calibration_{action}", via=via)
        if action == "skip":
            # Handled here: it needs no camera, and must be logged even without a vision process.
            reason = (reason or "").strip() or "skipped by the operator"
            self.calibration = {"state": "skipped", "user": user, "reason": reason}
            self.sessions.event("calibration", "wizard", result="skipped", user=user,
                                setup=self._calibration_setup(), reason=reason[:300])
            self.sessions.set_meta("calibration", {"result": "skipped", "reason": reason[:300]})
            return self.calibration
        if action == "reuse" and not self.calibration.get("reuse"):
            raise WizardError("There is no recent calibration to reuse for this participant.")
        command = {"command": action, "user": user}
        if parts:
            command["parts"] = list(parts)
        self._calibration_commands.append(command)
        if action != "stop":
            self.calibration = dict(self.calibration, state="requested", message="")
        return self.calibration

    def _next_calibration_command(self) -> dict | None:
        """The vision process asks (twice a second): anything for me?"""
        self._vision_seen = time.monotonic()
        return self._calibration_commands.popleft() if self._calibration_commands else None

    def _calibration_say(self, line_id: str) -> bool:
        """The vision process asks to speak a calibration line (robot voice)."""
        player = self.player
        if player is None or player.error is not None:
            return False
        try:
            player.say(line_id, tone="hedged", source="system")
            return True
        except PlayerError as exc:
            log.warning("Could not speak %s: %s", line_id, exc)
            return False

    def _calibration_report(self, progress: dict) -> None:
        """Progress or the result from the vision process."""
        state = progress.get("state")
        self.calibration = dict(self.calibration, **progress)
        if state in ("done", "failed", "reused", "stopped") and self.sessions.active:
            summary = {k: progress.get(k) for k in ("taken_at", "late_evening", "message", "age_days")
                       if progress.get(k) not in (None, "")}
            summary["result"] = {"done": "new"}.get(state, state)
            if progress.get("values"):
                summary["values"] = progress["values"]
            try:
                self.sessions.set_meta("calibration", summary)
            except SessionError:
                pass

    def calibration_status(self) -> dict:
        return dict(self.calibration, vision_connected=time.monotonic() - self._vision_seen < 3.0)

    def _robot_camera_client(self) -> Any:
        """The SDK client, when connected to the REAL robot -- the only backend
        whose camera perception uses. (The simulator's is a rendering.)"""
        player = self.player
        if self.backend_name != "real" or player is None or player.error is not None:
            return None
        mini = getattr(player.backend, "mini", None)
        return mini if mini is not None and getattr(mini, "media", None) is not None else None

    def camera_status(self) -> dict | None:
        relay = self.camera_relay
        if relay is None:
            return None
        status = relay.status()
        return {"port": status["port"], "robot_camera": bool(status["camera"]),
                "fps": status["fps"], "viewers": status["clients"], "error": status["error"],
                "source": "robot" if self.backend_name == "real" else "laptop",
                "tracking_user": self.user_target.tracking}

    def shutdown(self) -> None:
        self._watching = False
        if self.camera_relay is not None:
            self.camera_relay.stop()
            self.camera_relay = None
        if self.sessions.active:
            self.end_session(reason="panel stopped")
        player = self.player
        if player is not None:
            try:
                player.close(return_to_neutral=player.error is None, timeout=6)
            except Exception:  # noqa: BLE001
                log.exception("closing the player failed")

    # --------------------------------------------------------------- reading

    def status(self) -> dict:
        player = self.player
        connected = self._check_connection()
        return {
            "backend": self.backend_name,
            "mode": self.mode,
            "mode_implemented": self.mode_implemented,
            "connected": connected,
            "connection_error": None if connected else self.connection_error,
            "playing": player.current() if player else None,
            "idle": self.panel.idle,
            "idle_on": self.idle_on,
            "last_error": self.last_error,
            "session": self.sessions.status(),
            "panel_version": self.panel_version,
            "camera": self.camera_status(),
            "calibration": self.calibration_status(),
        }

    def log_since(self, seq: int) -> tuple[int, list[dict]]:
        with self._lock:
            items = [record for s, record in self._log if s > seq]
            return self._seq, items

    def state(self) -> dict:
        panel = self.panel
        _, recent = self.log_since(0)
        return {
            "status": self.status(),
            "panel": {
                "groups": [
                    {"id": gid, "title": title,
                     "buttons": [b.__dict__ for b in buttons]}
                    for gid, title, buttons in panel.groups
                ],
                "stop_keys": panel.stop_keys,
                "idle_key": panel.idle_key,
                "tone_key": panel.tone_key,
                "default_intensity": panel.default_intensity,
                "warnings": panel.warnings,
            },
            "log": recent[-20:],
            "seq": self._seq,
        }


# ======================================================================= web


class PlayIn(BaseModel):
    behavior: str
    intensity: float
    via: str = "click"


class ViaIn(BaseModel):
    via: str = "click"


class IdleIn(BaseModel):
    on: bool
    via: str = "click"


class SayIn(BaseModel):
    line_id: str | None = None
    text: str | None = None
    tone: str = "hedged"
    via: str = "click"


class AnnotateIn(BaseModel):
    marker: str
    via: str = "key"


class TextIn(BaseModel):
    text: str


class ParticipantIn(BaseModel):
    participant: str
    notes: str | None = None


class EndIn(BaseModel):
    notes: str | None = None


class CalibrationIn(BaseModel):
    action: str                      # calibrate, reuse, skip, stop
    reason: str | None = None
    parts: list[str] | None = None
    via: str = "click"


def _ok(**extra: Any) -> dict:
    return {"ok": True, **extra}


def create_app(wizard: Wizard) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        wizard.shutdown()

    app = FastAPI(title="EEPY Wizard panel", lifespan=lifespan)
    app.state.wizard = wizard

    @app.exception_handler(WizardError)
    async def _wizard_error(request: Request, exc: WizardError):
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=409)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    def state():
        return wizard.state()

    @app.post("/api/play")
    def play(body: PlayIn):
        wizard.play(body.behavior, body.intensity, body.via)
        return _ok()

    @app.post("/api/stop")
    def stop(body: ViaIn | None = None):
        wizard.stop((body or ViaIn()).via)
        return _ok()

    @app.post("/api/idle")
    def idle(body: IdleIn):
        wizard.set_idle(body.on, body.via)
        return _ok()

    @app.post("/api/say")
    def say(body: SayIn):
        return _ok(text=wizard.say(body.line_id, body.text, body.tone, body.via))

    @app.post("/api/annotate")
    def annotate(body: AnnotateIn):
        record = wizard.annotate(body.marker, body.via)
        return _ok(saved=record["saved"], t=record["t_mono"])

    @app.post("/api/note")
    def note(body: TextIn):
        record = wizard.note(body.text)
        return _ok(saved=record["saved"], t=record["t_mono"])

    @app.post("/api/session/start")
    def session_start(body: ParticipantIn):
        return _ok(session=wizard.start_session(body.participant, body.notes)["session"])

    @app.post("/api/calibration")
    def calibration(body: CalibrationIn):
        return _ok(calibration=wizard.calibration_command(body.action, body.reason, body.parts, body.via))

    @app.post("/api/session/end")
    def session_end(body: EndIn | None = None):
        wizard.end_session(notes=(body or EndIn()).notes)
        return _ok()

    @app.post("/api/reload")
    def reload():
        return wizard.reload()

    @app.post("/api/reconnect")
    def reconnect():
        ok = wizard.reconnect()
        return {"ok": ok, "message": "Connected." if ok else wizard.connection_error}

    @app.get("/api/stream")
    async def stream(request: Request):
        """Server-Sent Events: new log lines and status changes, as they happen.

        The browser's EventSource reconnects by itself after a refresh or a
        network blip, and asks for /api/state to catch up.
        """
        async def events():
            seq, _ = wizard.log_since(10**12)
            last_status = None
            last_beat = time.monotonic()
            yield "retry: 1000\n\n"
            while not await request.is_disconnected():
                # Off the event loop: if anything inside ever waited, it would
                # stall every request -- STOP included -- not just this stream.
                seq, items = await asyncio.to_thread(wizard.log_since, seq)
                if items:
                    yield f"event: log\ndata: {json.dumps(items, default=str)}\n\n"
                status = await asyncio.to_thread(wizard.status)
                comparable = {**status, "session": {k: v for k, v in status["session"].items()
                                                    if k != "elapsed_s"}}
                if comparable != last_status:
                    last_status = comparable
                    yield f"event: status\ndata: {json.dumps(status, default=str)}\n\n"
                if time.monotonic() - last_beat > 10:
                    last_beat = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(STREAM_PERIOD)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    return app


def make_wizard(backend: str | None, robot_host: str | None, settings: Any = None,
                **kwargs: Any) -> Wizard:
    """Build the panel for real use: the central logger, configured from eepy.yaml."""
    import os

    from eepy.behaviors.backends import BACKEND_ENV
    from eepy.logs import configure
    from eepy.settings import load_settings

    settings = settings or load_settings()
    configure(data_dir=settings.data_path,
              participant_pattern=settings.participant_pattern,
              store_raw_speech=settings.privacy.store_raw_speech,
              flush_seconds=settings.logging.flush_seconds)
    from eepy.speech import make_speaker

    name = (backend or os.environ.get(BACKEND_ENV) or "dry").lower()
    camera = settings.camera_config
    kwargs.setdefault("camera_relay", (camera.relay_port, camera.fps))
    return Wizard(lambda: make_backend(name, robot_host=robot_host),
                  backend_name=name, mode=settings.mode,
                  speaker_factory=lambda: make_speaker(settings.speech), **kwargs)
