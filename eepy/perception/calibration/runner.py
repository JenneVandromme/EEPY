"""Running a SessionCalibration: results in, screen + speech + progress out.

Shared by the command-line tool and the vision process (when the Wizard panel
asks it to calibrate), so both behave identically.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from .flow import SessionCalibration


def run(pipeline: Any, flow: SessionCalibration, screen: Any, voice: Any,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_stage: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None) -> str:
    """Drive the flow to the end. Returns "done", "failed" or "stopped"."""
    result = None
    stage = flow.stage
    last_report = 0.0
    while not flow.done:
        newest = pipeline.wait_for_result(newer_than=result, timeout=0.1)
        now = time.monotonic()
        if newest is not None:
            result = newest
            flow.tick(result, now)
        _speak(flow, voice)
        if flow.stage != stage:
            stage = flow.stage
            if on_stage is not None:
                on_stage(stage)
        key = screen.show(flow, result)
        if key in (ord("q"), ord("Q"), 27) or (should_stop is not None and should_stop()):
            return "stopped"
        if on_progress is not None and now - last_report >= 0.5:
            last_report = now
            on_progress(progress(flow))
    _speak(flow, voice)
    if on_progress is not None:
        on_progress(progress(flow))
    screen.linger(flow, 2.5)
    return "failed" if flow.failed else "done"


def progress(flow: SessionCalibration) -> Dict[str, Any]:
    """What the panel's status indicator shows."""
    return {"stage": flow.stage, "step": flow.stage_number(), "progress": round(flow.progress, 2),
            "instruction": flow.instruction, "message": flow.message,
            "state": "failed" if flow.failed else ("done" if flow.stage == "done" else "running")}


def _speak(flow: SessionCalibration, voice: Any) -> None:
    for line in flow.pop_lines():
        if line == "beep":
            print("\a", end="", flush=True)
        else:
            voice.say(line)
