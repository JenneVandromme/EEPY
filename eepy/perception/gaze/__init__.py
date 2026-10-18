"""Gaze on the work area (task A11): is the person looking at their work?

    from eepy.perception.gaze import GazeExtractor
    pipeline = FacePipeline(camera, extractors=default_extractors() + [GazeExtractor(user="P03")])

Calibrate the work area per person and camera:  python -m eepy.perception.calibrate_gaze
See eepy/perception/gaze/README.md.
"""

from __future__ import annotations

from typing import Optional

from .calibration import CALIBRATION_DIR, CalibrationStore, GazeCalibration, setup_name, zone_model_from
from .config import GazeConfig, load as load_config
from .estimate import MODE_HEAD, MODE_IRIS, GazeEstimate, GazeEstimator
from .extractor import GazeExtractor
from .zones import WORK, ZONES, ZoneModel


def resolve_user(user: Optional[str] = None) -> str:
    """--user, else the participant of the session the panel is running, else "default"."""
    if user:
        return user
    try:
        from eepy.logs.features import current_session

        session = current_session()
    except Exception:  # noqa: BLE001 -- no session info is not an error
        session = None
    return (session or {}).get("participant") or "default"


__all__ = ["GazeExtractor", "GazeCalibration", "CalibrationStore", "CALIBRATION_DIR", "GazeConfig",
           "GazeEstimate", "GazeEstimator", "ZoneModel", "WORK", "ZONES", "MODE_IRIS", "MODE_HEAD",
           "load_config", "resolve_user", "setup_name", "zone_model_from"]
