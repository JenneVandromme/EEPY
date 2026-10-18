"""Per-user calibration (A12): the face check, the gaze zones (A11) and ~60 s of
baseline, in one ~2-minute flow at the start of a session.

    python -m eepy.perception.calibrate              # the whole flow
    python -m eepy.perception.calibrate --only baseline

See eepy/perception/calibration/README.md (for the designers: what to do, how
long it takes, what the messages mean).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..baseline import Baseline
from .baseline import BaselineCollector, robust
from .face_check import FaceCheck
from .flow import PARTS, SessionCalibration

REUSE_DAYS = 14


def save(store: Any, user: str, setup: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Store each finished section; returns what was saved (for the log)."""
    stamp = time.strftime("%Y-%m-%d %H:%M")
    for name, section in result.items():
        store.save_section(user, setup, name, dict(section, created=section.get("created", stamp)))
    return result


def recent(store: Any, user: str, setup: str, days: float = REUSE_DAYS) -> Optional[Dict[str, Any]]:
    """A reusable calibration for this person and camera: both sections present and at
    most `days` old -> {"age_days", "taken_at", "late_evening"}; else None."""
    data = store.load(user, setup)
    gaze, baseline = data.get("gaze"), data.get("baseline")
    if not isinstance(gaze, dict) or not isinstance(baseline, dict):
        return None
    taken = baseline.get("taken_at") or baseline.get("created")
    try:
        age = max(0.0, (time.time() - time.mktime(time.strptime(taken, "%Y-%m-%d %H:%M"))) / 86400)
    except (TypeError, ValueError):
        return None
    if age > days:
        return None
    return {"age_days": round(age, 1), "taken_at": taken, "late_evening": bool(baseline.get("late_evening"))}


def log_record(user: str, setup: str, outcome: str, result: Optional[Dict[str, Any]] = None,
               reason: str = "") -> Dict[str, Any]:
    """The `calibration` event: new / reused / skipped / failed, with values and checks."""
    record: Dict[str, Any] = {"result": outcome, "user": user, "setup": setup}
    if reason:
        record["reason"] = reason[:300]
    baseline = (result or {}).get("baseline")
    if baseline:
        record.update(values=baseline.get("values"), checks=[
            {k: c[k] for k in ("check", "ok", "kind", "measured")} for c in baseline.get("quality", [])],
            taken_at=baseline.get("taken_at"), late_evening=baseline.get("late_evening"),
            kss=baseline.get("kss"))
    gaze = (result or {}).get("gaze")
    if gaze:
        record.update(gaze_zones=gaze.get("zones", {}).get("gaze"), gaze_warnings=gaze.get("warnings"))
    return record


__all__ = ["SessionCalibration", "BaselineCollector", "FaceCheck", "PARTS", "save", "recent",
           "log_record", "robust", "REUSE_DAYS", "Baseline"]
