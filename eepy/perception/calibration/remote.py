"""The vision process's side of the panel's "Calibrate" step.

The camera can only be used by one program at a time, and during a session the
vision process (`python -m eepy.perception`) has it. So the Wizard panel cannot
start a calibration program of its own: it ASKS the vision process, over the
camera relay. Twice a second the vision process asks "anything for me?" (POLL)
and gets one of:

    {"command": "calibrate", "user": "P03", "parts": ["face", "gaze", "baseline"]}
    {"command": "reuse", "user": "P03"}
    {"command": "skip", "user": "P03", "reason": "..."}
    {"command": "stop"}                 (while calibrating: give up)

It reports progress back (CALIB, for the panel's status indicator) and has the
panel speak the instructions (SAY). The calibration window opens on the
computer the vision process runs on -- normally the same desk as the panel.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from . import log_record, recent

log = logging.getLogger("eepy.perception.calibration")


class CommandHandler:
    RETRY_SECONDS = 2.0

    def __init__(self, pipeline: Any, camera: Any, store: Any, gaze: Any, baseline: Any,
                 events: Any, setup_label: Optional[str] = None, relay: Any = None) -> None:
        from eepy.camera.relay import RelayClient

        self.pipeline, self.camera, self.store = pipeline, camera, store
        self.gaze, self.baseline, self.events = gaze, baseline, events
        self.setup_label = setup_label
        self.relay = relay or RelayClient(camera.config.relay_port, timeout=0.5)
        self._retry_at = 0.0

    def tick(self) -> Optional[str]:
        """Poll the panel once; carry out a command. Returns what was done, or None."""
        command = self._poll()
        if not command:
            return None
        kind, user = command.get("command"), command.get("user") or "default"
        if kind == "calibrate":
            return self.calibrate(user, command.get("parts") or ["face", "gaze", "baseline"])
        if kind == "reuse":
            return self.reuse(user)
        if kind == "skip":
            self._event(log_record(user, self._setup(), "skipped", reason=command.get("reason", "")))
            self._report({"state": "skipped", "user": user})
            return "skipped"
        return None

    def reuse(self, user: str) -> str:
        found = recent(self.store, user, self._setup())
        if found is None:
            self._report({"state": "failed", "user": user,
                          "message": "No recent calibration to reuse for this person and camera."})
            return "failed"
        self.baseline.forget()
        self.baseline.use(user, self.camera.name)
        self.gaze.user = user
        self._event(dict(log_record(user, self._setup(), "reused"), taken_at=found["taken_at"],
                         late_evening=found["late_evening"]))
        self._report(dict(found, state="reused", user=user))
        return "reused"

    def calibrate(self, user: str, parts: Any) -> str:
        from eepy.camera.laptop import import_cv2

        from .screen import CalibrationScreen
        from .session import perform
        from .voice import Voice

        cv2 = import_cv2()
        import numpy as np

        self.gaze.user = user
        screen = CalibrationScreen(cv2, np)
        stop_check = {"next": 0.0, "stop": False}

        def should_stop() -> bool:
            now = time.monotonic()
            if now >= stop_check["next"]:
                stop_check["next"] = now + 0.5
                command = self._poll()
                stop_check["stop"] = bool(command and command.get("command") == "stop")
            return stop_check["stop"]

        try:
            outcome, flow = perform(self.pipeline, user=user, source=self.camera.name,
                                    setup_label=self.setup_label, parts=parts, store=self.store,
                                    voice=Voice(self.relay), screen=screen, gaze=self.gaze,
                                    baseline=self.baseline, events=self.events,
                                    on_progress=lambda p: self._report(dict(p, user=user)),
                                    should_stop=should_stop)
        finally:
            screen.close()
        summary: Dict[str, Any] = {"state": "done" if outcome == "new" else outcome, "user": user,
                                   "message": flow.message}
        baseline = flow.result.get("baseline")
        if baseline:
            summary.update(taken_at=baseline["taken_at"], late_evening=baseline["late_evening"],
                           values=baseline["values"])
        self._report(summary)
        return outcome

    # --- relay -----------------------------------------------------------------------------

    def _setup(self) -> str:
        from ..gaze.calibration import setup_name

        return setup_name(self.camera.name, self.setup_label)

    def _poll(self) -> Optional[Dict[str, Any]]:
        if time.monotonic() < self._retry_at:
            return None
        try:
            return self.relay.poll()
        except Exception:  # noqa: BLE001 -- no panel running
            self._retry_at = time.monotonic() + self.RETRY_SECONDS
            return None

    def _report(self, progress: Dict[str, Any]) -> None:
        try:
            self.relay.report(progress)
        except Exception:  # noqa: BLE001
            log.debug("Could not report calibration progress", exc_info=True)

    def _event(self, record: Dict[str, Any]) -> None:
        if self.events is not None:
            self.events.event("calibration", **record)
