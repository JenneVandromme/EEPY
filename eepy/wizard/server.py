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
import math
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

# The modes where the robot nudges on its own, and the state estimator each acts on.
AUTONOMY_MODES = {"rules": "rules", "hmm": "hmm"}
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
        session_prior: Path | None = None,
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
        # --session-prior FILE.json (HMM, A16): read at each session start, written to prior.json.
        self.session_prior_path = Path(session_prior) if session_prior is not None else None
        self._session_started_at = -1e9
        self._estimator_warned = False
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
        # The rules and hmm modes (A14, A16): the robot nudges on its own, from the state
        # the vision process sends. eepy/interaction/README.md.
        self.autonomy: Any = None
        self._autonomy_thread: threading.Thread | None = None
        if mode in AUTONOMY_MODES:
            self._start_autonomy()

        # The camera relay: the robot's camera and head pose, for the vision
        # process, on 127.0.0.1 only (eepy/camera/relay.py). (port, fps); None
        # in tests. A busy port is a warning, not a reason to refuse to start.
        # "look_at: user" follows the real student when the vision process reports
        # the face (relay USER message); otherwise the default from _blocks.yaml.
        # One provider for the panel's lifetime, handed to every player it creates.
        from eepy.behaviors.pose import Pose
        from eepy.behaviors.targets import StaticTargetProvider, TrackingTargetProvider

        self._static_targets = StaticTargetProvider(lambda: self.library)
        self.user_target = TrackingTargetProvider(
            self._static_targets,
            pose=lambda: self.player.pose if self.player is not None else Pose())
        # The robot's resting direction, aimed at the participant's face (see aim()).
        # None: straight ahead, as before. Kept across reconnects and sessions.
        self.base: Any = None
        self.aim_state: dict = {"state": "none"}
        self._aim_thread: threading.Thread | None = None
        self._off_since: float | None = None          # the face off-centre since (automatic aim)
        self._last_auto_aim = -1e9
        # The calibration step of a session (A12). It runs IN the vision process (that
        # program has the camera); the panel queues commands for it, speaks its lines
        # and shows its progress. See eepy/perception/calibration/remote.py.
        self.calibration: dict = {"state": "none"}
        self.calibration_store = calibration_store          # None: data/calibration/
        self._calibration_commands: deque = deque()
        self._vision_seen = -1e9
        # The participant's state as the vision process estimates it (A13): shown in
        # the status bar. A14's escalation will act on it here.
        self.user_state: dict | None = None
        self._user_state_seen = -1e9
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
            if self.base is not None:                 # a reconnect keeps the aim
                player.set_base(self.base)
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
        if self.autonomy is not None:
            self.autonomy.operator_played(behavior, pressed)

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
        if self.autonomy is not None:
            self.autonomy.operator_played(line_id or "free text", pressed)
        return spoken

    def annotate(self, marker: str, via: str = "key") -> dict:
        markers = {m.id: m for m in self.config.markers}
        if marker not in markers:
            raise WizardError(f'There is no marker called "{marker}".')
        record = self._note("annotation", "wizard", marker=marker,
                            label=markers[marker].label, via=via)
        if self.autonomy is not None and marker in self.REPLY_MARKERS:
            # The participant's answer steers the escalation (later speech does, A28).
            record = dict(record, autonomy=self.autonomy.reply(self.REPLY_MARKERS[marker], record["t_mono"]))
        return record

    def note(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise WizardError("The note is empty.")
        return self._note("note", "wizard", text=text)

    def start_session(self, participant: str, notes: str | None = None) -> dict:
        prior = self._read_session_prior()
        try:
            record = self.sessions.start(
                participant, mode=self.mode, backend=self.backend_name, notes=notes,
                intensity_default=self.config.default_intensity,
                behaviors=sorted(self.library.behaviors),
                markers=[m.id for m in self.config.markers], prior=prior)
        except SessionError as exc:
            raise WizardError(str(exc)) from None
        self._session_started_at = shared_clock()
        self._estimator_warned = False
        self._push(record)
        if not self.mode_implemented:
            self._note("mode_not_implemented", "system", mode=self.mode)
        self._offer_calibration(participant)
        if self.autonomy is not None:
            self.autonomy.session_started(shared_clock())
        self.panel_version += 1
        return record

    def end_session(self, reason: str = "operator", notes: str | None = None) -> dict:
        if self.autonomy is not None and self.sessions.active:
            self.autonomy.session_ended(shared_clock(), f"session ended ({reason})")
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
                            user_fn=self._on_user, say_fn=self._calibration_say,
                            command_fn=self._next_calibration_command,
                            calib_fn=self._calibration_report, state_fn=self._state_report)
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
            if self.autonomy is not None:
                self.autonomy.calibration("skipped", shared_clock())
            return self.calibration
        if action == "reuse" and not self.calibration.get("reuse"):
            raise WizardError("There is no recent calibration to reuse for this participant.")
        if action == "reuse":
            self._restore_aim(user)
        if action == "calibrate" and self._aim_first():
            # Aim at the face first, so the calibration is taken from the robot's new view;
            # the command goes to the vision process when the aim is done (or gave up).
            command = {"command": action, "user": user, **({"parts": list(parts)} if parts else {})}
            self.calibration = dict(self.calibration, state="requested",
                                    message="aiming at the participant first")
            self.aim(then=command)
            return self.calibration
        command = {"command": action, "user": user}
        if parts:
            command["parts"] = list(parts)
        self._calibration_commands.append(command)
        if action != "stop":
            self.calibration = dict(self.calibration, state="requested", message="")
        return self.calibration

    # ------------------------------------------------------- aiming at the participant

    def _on_user(self, offset: dict | None) -> None:
        """The vision process reports where the face is (relay USER, ~5 a second)."""
        self.user_target.observe(offset)
        try:
            self._maybe_auto_aim()
        except Exception:  # noqa: BLE001 -- aiming must never break the face reports
            log.exception("Automatic aim failed")

    def _maybe_auto_aim(self) -> None:
        """Aim by itself: the face has been more than aim.tolerance_deg from where the
        robot rests for aim.auto_after_s, while no behaviour plays (the idle may) and no
        calibration runs; at most every aim.auto_min_gap_s."""
        cfg = self.config.aim
        player = self.player
        if (not cfg.enabled or not cfg.auto or player is None or player.error is not None
                or (self._aim_thread is not None and self._aim_thread.is_alive())
                or self.calibration.get("state") in ("requested", "running") or player.is_playing()):
            self._off_since = None
            return
        face = self.user_target.current()
        if face is None:
            self._off_since = None
            return
        yaw0 = float(self.base.yaw or 0.0) if self.base is not None else 0.0
        pitch0 = float(self.base.pitch or 0.0) if self.base is not None else 0.0
        if max(abs(face[0] - yaw0), abs(face[1] - pitch0)) <= cfg.tolerance_deg or (
                self.aim_state.get("state") == "limited" and self._same_limit(face)):
            self._off_since = None
            return
        now = time.monotonic()
        if self._off_since is None:
            self._off_since = now
            return
        if now - self._off_since < cfg.auto_after_s or now - self._last_auto_aim < cfg.auto_min_gap_s:
            return
        self._off_since, self._last_auto_aim = None, now
        self.aim()

    def _same_limit(self, face: tuple) -> bool:
        """Already aimed as far as is safe toward about this face: don't try again and again."""
        last = self.aim_state.get("face")
        return last is not None and max(abs(face[0] - last[0]), abs(face[1] - last[1])) <= self.config.aim.tolerance_deg

    def _aim_first(self) -> bool:
        cfg = self.config.aim
        return (cfg.enabled and cfg.on_calibrate and self.player is not None
                and self.player.error is None and self.user_target.tracking)

    def aim(self, then: dict | None = None) -> dict:
        """Point the robot's resting direction at the participant's face: centred,
        within aim.tolerance_deg, and never further than every behaviour can still
        play safely from. Runs in the background (a few seconds); `then` is a
        calibration command to queue when it is done. Returns the aim state."""
        cfg = self.config.aim
        if not cfg.enabled:
            self.aim_state = {"state": "off", "message": "aiming is switched off (config/wizard_panel.yaml, aim)"}
            if then is not None:
                self._calibration_commands.append(then)
            return self.aim_state
        if self.player is None or self.player.error is not None:
            raise WizardError("The robot is not connected.")
        if self._aim_thread is not None and self._aim_thread.is_alive():
            return self.aim_state
        self.aim_state = {"state": "aiming", "message": "looking for the face..."}
        self._aim_thread = threading.Thread(target=self._aim_run, args=(then,), name="eepy-aim", daemon=True)
        self._aim_thread.start()
        return self.aim_state

    def reset_aim(self) -> dict:
        """Straight ahead again."""
        self._apply_base(None, reason="reset")
        self.aim_state = {"state": "none", "message": "resting straight ahead"}
        return self.aim_state

    def _aim_run(self, then: dict | None) -> None:
        from eepy.behaviors.schema import HeadPose

        cfg = self.config.aim
        moved = False
        try:
            limited = False
            for _ in range(cfg.rounds):
                face = self.user_target.current()
                if face is None:
                    self.aim_state = {"state": "no_face", "message": "no face seen: is the vision program "
                                      "running (python -m eepy.perception) and the participant in view?"}
                    return
                yaw0 = float(self.base.yaw or 0.0) if self.base is not None else 0.0
                pitch0 = float(self.base.pitch or 0.0) if self.base is not None else 0.0
                off = (face[0] - yaw0, face[1] - pitch0)
                if max(abs(off[0]), abs(off[1])) <= cfg.tolerance_deg:
                    self.aim_state = self._aimed_state(off, limited)
                    return
                want = HeadPose(yaw=max(-cfg.yaw_limit_deg, min(cfg.yaw_limit_deg, face[0])),
                                pitch=max(-cfg.up_limit_deg, min(cfg.down_limit_deg, face[1])))
                safe, limited = self._safe_base(want)
                if (abs(float(safe.yaw or 0) - yaw0) < 0.5 and abs(float(safe.pitch or 0) - pitch0) < 0.5):
                    self.aim_state = self._aimed_state(off, True)      # as far as it can go
                    return
                self._apply_base(safe, reason="aim", face_offset=[round(off[0], 1), round(off[1], 1)],
                                 limited=limited)
                moved = True
                time.sleep(cfg.settle_s)
            face = self.user_target.current()
            off = (0.0, 0.0) if face is None or self.base is None else (
                face[0] - float(self.base.yaw or 0), face[1] - float(self.base.pitch or 0))
            self.aim_state = self._aimed_state(off, limited)
        except Exception as exc:  # noqa: BLE001 -- shown to the operator, never stops the panel
            log.exception("Aiming failed")
            self.aim_state = {"state": "failed", "message": str(exc)}
        finally:
            face = self.user_target.current()
            if face is not None:
                self.aim_state["face"] = [round(face[0], 1), round(face[1], 1)]
            if (self.aim_state.get("state") in ("aimed", "limited") and then is None
                    and self.calibration.get("state") in ("done", "reused") and moved):
                self.aim_state["message"] += ("; the robot's view changed after the calibration: "
                                              "calibrate again if looking away is not detected well")
            if self.aim_state.get("state") in ("aimed", "limited"):
                self._save_aim()
            if then is not None:
                self._calibration_commands.append(then)

    def _aimed_state(self, off: tuple, limited: bool) -> dict:
        yaw = float(self.base.yaw or 0.0) if self.base is not None else 0.0
        pitch = float(self.base.pitch or 0.0) if self.base is not None else 0.0
        tol = self.config.aim.tolerance_deg
        state = {"state": "aimed", "yaw": round(yaw, 1), "pitch": round(pitch, 1),
                 "face_offset": [round(off[0], 1), round(off[1], 1)]}
        where = f"resting at yaw {yaw:+.0f}°, pitch {pitch:+.0f}°"
        if max(abs(off[0]), abs(off[1])) <= tol:
            state["message"] = f"aimed at the face ({where}; face within {tol:g}°)"
            return state
        side = ("above" if off[1] < 0 else "below") if abs(off[1]) >= abs(off[0]) else "beside"
        state.update(state="limited", message=(
            f"{where}: as far as every behaviour can still play safely. The face is still "
            f"{max(abs(off[0]), abs(off[1])):.0f}° {side} the centre"
            + (": raise the robot a little (e.g. a book under it)" if side == "above" else "")
            if limited else f"{where}; the face is still {max(abs(off[0]), abs(off[1])):.0f}° off the centre"))
        return state

    def _safe_base(self, want: Any) -> tuple:
        """The resting direction closest to `want`, on the way from straight ahead, from
        which every behaviour still passes the safety checks. (base, limited)."""
        from eepy.behaviors.schema import HeadPose

        player = self.player
        if not player.check_base(want):
            return want, False
        low, high = 0.0, 1.0                           # straight ahead is always safe
        for _ in range(12):
            mid = (low + high) / 2
            cand = HeadPose(yaw=float(want.yaw or 0) * mid, pitch=float(want.pitch or 0) * mid)
            if player.check_base(cand):
                high = mid
            else:
                low = mid
        # Rounded TOWARD straight ahead (0.1 degree), so rounding never leaves the safe side.
        def trunc(x: float) -> float:
            return math.trunc(x * 10) / 10

        return HeadPose(yaw=trunc(float(want.yaw or 0) * low), pitch=trunc(float(want.pitch or 0) * low)), True

    def _apply_base(self, base: Any, reason: str, **extra: Any) -> None:
        self.base = base
        if base is None:
            self._static_targets.clear("user")
        else:
            self._static_targets.set("user", base)         # no face for a moment: still look at them
        player = self.player
        if player is not None and player.error is None:
            player.set_base(base)
        self._note("robot_base", "wizard", reason=reason,          # saved when a session runs
                   yaw=None if base is None else round(float(base.yaw or 0), 1),
                   pitch=None if base is None else round(float(base.pitch or 0), 1), **extra)

    def _calibration_file(self) -> Any:
        from eepy.perception.gaze.calibration import CalibrationStore

        return self.calibration_store or CalibrationStore()

    def _save_aim(self) -> None:
        """Kept with the participant's calibration, so Reuse restores it."""
        user = self.calibration.get("user") or (self.sessions.participant if self.sessions.active else None)
        if user is None or self.base is None:
            return
        try:
            self._calibration_file().save_section(user, self._calibration_setup(), "robot_base", {
                "yaw": round(float(self.base.yaw or 0), 1), "pitch": round(float(self.base.pitch or 0), 1),
                "taken_at": time.strftime("%Y-%m-%d %H:%M")})
        except Exception:  # noqa: BLE001 -- the aim still works this session
            log.exception("Could not save the aim")

    def _restore_aim(self, user: str) -> None:
        from eepy.behaviors.schema import HeadPose

        try:
            saved = self._calibration_file().section(user, self._calibration_setup(), "robot_base")
        except Exception:  # noqa: BLE001
            log.exception("Could not read the saved aim")
            return
        if not saved or self.player is None or self.player.error is not None:
            return
        base = HeadPose(yaw=float(saved.get("yaw", 0)), pitch=float(saved.get("pitch", 0)))
        if self.player.check_base(base):               # behaviours changed since: do not use it
            self.aim_state = {"state": "none", "message": "the saved aim is no longer safe for every "
                              "behaviour: aim again"}
            return
        self._apply_base(base, reason="reuse")
        self.aim_state = {"state": "aimed", "yaw": base.yaw, "pitch": base.pitch,
                          "message": f"aim restored from the calibration (yaw {base.yaw:+.0f}°, pitch {base.pitch:+.0f}°)"}

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
        if self.autonomy is not None and state:
            self.autonomy.calibration(str(state), shared_clock())
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

    def _read_session_prior(self) -> dict | None:
        """The --session-prior file, read at each session start. A problem is shown and
        recorded as {"_error": ...}, so the fall-back to the hand-set values is visible."""
        if self.session_prior_path is None:
            return None
        try:
            data = json.loads(self.session_prior_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("it is not a JSON object ({...})")
            return data
        except (OSError, ValueError) as exc:
            problem = f"{self.session_prior_path}: {exc}"
            self._error(f"The session prior cannot be used, so the HMM starts from its hand-set values:\n{problem}")
            return {"_error": problem}

    def _state_report(self, estimate: dict) -> None:
        """The vision process's newest state estimate (about once a second). In a session of
        the rules or hmm mode, only an estimate from the matching estimator reaches the
        escalation; a mismatch (after a 3-s grace at the session start, while the vision
        program switches) is reported once per session, and autonomy waits."""
        self.user_state = estimate
        self._user_state_seen = time.monotonic()
        if self.autonomy is None:
            return
        expected = AUTONOMY_MODES.get(self.mode)
        sent = estimate.get("estimator") or "rules"          # older vision programs: always the rules
        if expected is not None and self.sessions.active and sent != expected:
            if shared_clock() - self._session_started_at < 3.0:
                return
            if not self._estimator_warned:
                self._error(f'Mode "{self.mode}" acts on the {expected} estimate, but the vision program '
                            f'sends the {sent} one. Restart it (python -m eepy.perception) '
                            "or check its --estimator option (see the vision terminal for why the HMM did "
                            "not start). Autonomy waits until then.")
                self._estimator_warned = True
            return                     # not fed: the engine goes stale -> unknown -> nothing happens
        self.autonomy.on_state(estimate, shared_clock())

    # ------------------------------------------------------------- autonomy (A14)

    REPLY_MARKERS = {"user_accepted": "accepted", "user_snoozed": "snoozed",
                     "user_dismissed": "dismissed"}

    def _start_autonomy(self) -> None:
        from eepy.interaction.actuator import PlayerActuator
        from eepy.interaction.config import EscalationConfigError, check_library
        from eepy.interaction.config import load as load_escalation
        from eepy.interaction.engine import EscalationEngine
        from eepy.interaction.policy import FixedEscalation

        try:
            config = load_escalation()
            check_library(config, self.library)
        except EscalationConfigError as exc:
            self._error(f"The {self.mode} mode cannot nudge: the escalation config has a problem.\n{exc}")
            return
        actuator = PlayerActuator(lambda: self.player, on_error=self._error, library=lambda: self.library)
        self.autonomy = EscalationEngine(
            config, FixedEscalation(config), actuator,
            log=lambda event_type, t, **fields: self._note(event_type, "auto", t=t, **fields),
            idle=self.panel.idle)
        self._autonomy_thread = threading.Thread(target=self._autonomy_loop, args=(config.tick_seconds,),
                                                 name="eepy-autonomy", daemon=True)
        self._autonomy_running = True
        self._autonomy_thread.start()

    def _autonomy_loop(self, period: float) -> None:
        while getattr(self, "_autonomy_running", False):
            try:
                self.autonomy.tick(shared_clock())
            except Exception as exc:  # noqa: BLE001 -- shown, and the loop goes on
                log.exception("The escalation failed")
                self._error(f"The escalation hit a bug (it carries on): {exc}")
                time.sleep(5)
            time.sleep(period)

    def set_autonomy_paused(self, paused: bool, via: str = "click") -> dict:
        if self.autonomy is None:
            raise WizardError("Autonomy only runs in the rules and hmm modes (start the panel with "
                              "--mode rules or --mode hmm).")
        self._note("wizard_command", "wizard", action="autonomy_pause" if paused else "autonomy_resume", via=via)
        self.autonomy.set_paused(paused, shared_clock())
        return self.autonomy.status(shared_clock())

    def autonomy_status(self) -> dict | None:
        return None if self.autonomy is None else self.autonomy.status(shared_clock())

    def state_status(self) -> dict | None:
        """The state for the status bar; `fresh` False when the vision process went quiet."""
        if self.user_state is None:
            return None
        out = dict(self.user_state, fresh=time.monotonic() - self._user_state_seen < 3.0)
        expected = AUTONOMY_MODES.get(self.mode)
        if expected is not None:
            out["expected_estimator"] = expected
        return out

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
        self._autonomy_running = False
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
            "aim": dict(self.aim_state, enabled=self.config.aim.enabled,
                        base=None if self.base is None else
                        {"yaw": round(float(self.base.yaw or 0), 1), "pitch": round(float(self.base.pitch or 0), 1)}),
            "user_state": self.state_status(),
            "autonomy": self.autonomy_status(),
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


class AutonomyIn(BaseModel):
    paused: bool
    via: str = "click"


class AimIn(BaseModel):
    action: str = "aim"                 # aim | reset


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
        return _ok(saved=record["saved"], t=record["t_mono"], autonomy=record.get("autonomy"))

    @app.post("/api/autonomy")
    def autonomy(body: AutonomyIn):
        return _ok(autonomy=wizard.set_autonomy_paused(body.paused, body.via))

    @app.post("/api/note")
    def note(body: TextIn):
        record = wizard.note(body.text)
        return _ok(saved=record["saved"], t=record["t_mono"])

    @app.post("/api/session/start")
    def session_start(body: ParticipantIn):
        return _ok(session=wizard.start_session(body.participant, body.notes)["session"])

    @app.post("/api/aim")
    def aim(body: AimIn):
        if body.action not in ("aim", "reset"):
            raise WizardError(f'"{body.action}" is not an aim action (aim or reset).')
        return _ok(aim=wizard.reset_aim() if body.action == "reset" else wizard.aim())

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
