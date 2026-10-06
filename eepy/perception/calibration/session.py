"""Performing a calibration on a running pipeline: run the flow, then save and
apply the result straight away, and log it. Used by the command-line tool and by
the vision process (when the Wizard panel asks)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from ..gaze.calibration import setup_name, zone_model_from
from . import log_record, save
from .flow import SessionCalibration
from .runner import run


def perform(pipeline: Any, *, user: str, source: str, setup_label: Optional[str], parts: Sequence[str],
            store: Any, voice: Any, screen: Any, gaze: Any = None, baseline: Any = None,
            events: Any = None, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
            should_stop: Optional[Callable[[], bool]] = None, save_result: bool = True,
            **flow_options: Any) -> Tuple[str, SessionCalibration]:
    """Returns (outcome, flow); outcome is "new", "failed" or "stopped"."""
    setup = setup_name(source, setup_label)
    flow = SessionCalibration(parts, config=pipeline.config,
                              gaze_config=gaze.gaze if gaze is not None else None, **flow_options)

    def on_stage(stage: str) -> None:
        # The fresh gaze zones count from the baseline minute on (to tell work from away).
        if stage == "baseline" and gaze is not None and "gaze" in flow.result:
            gaze.zones = zone_model_from(flow.result["gaze"], gaze.gaze, label=f"{user}/{setup} (new)")

    end = run(pipeline, flow, screen, voice, on_progress=on_progress, on_stage=on_stage,
              should_stop=should_stop)
    if end != "done":
        outcome = "stopped" if end == "stopped" else "failed"
        _log(events, log_record(user, setup, outcome, flow.result, reason=flow.message))
        return outcome, flow
    if save_result:
        save(store, user, setup, flow.result)
    if gaze is not None:
        gaze._loaded_for = None                       # re-read the saved file
        gaze.use_calibration(user, source)
    if baseline is not None:
        baseline.forget()
        baseline.use(user, source)
    _log(events, log_record(user, setup, "new", flow.result))
    return "new", flow


def _log(events: Any, record: Dict[str, Any]) -> None:
    if events is not None:
        events.event("calibration", **record)
