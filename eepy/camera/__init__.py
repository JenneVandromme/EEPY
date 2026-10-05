"""The camera stream that feeds perception (task A8).

    from eepy.camera import open_camera
    from eepy.logs.features import ProcessEventLog

    camera = open_camera(events=ProcessEventLog("vision"))   # picks the right camera
    camera.start()
    frame = camera.wait_for_frame()          # or camera.get_latest(), never blocks
    while running:
        frame = camera.wait_for_frame(newer_than=frame)
        if frame is None:
            continue                         # no new picture within a second
        process(frame.image)                 # BGR, height x width x 3
    camera.stop()

Which camera: `--camera` / open_camera(source=...), else camera.source in
config/eepy.yaml, else the backend: the robot's own camera with EEPY_BACKEND=real,
this laptop's camera with sim or dry. See eepy/camera/README.md.

Nothing in this package writes a frame to disk, logs one, or sends one off
this computer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .config import CameraConfig, CameraConfigError, load as load_config
from .errors import CameraError
from .frame import Frame
from .region import Box, HeadPose
from .source import CameraSource

__all__ = ["open_camera", "choose_source", "CameraSource", "CameraConfig", "CameraConfigError",
           "CameraError", "Frame", "Box", "HeadPose", "load_config"]

BACKEND_ENV = "EEPY_BACKEND"   # same variable as eepy/behaviors/backends.py
REPO_ROOT = Path(__file__).resolve().parents[2]


def _backend_setting() -> str:
    """EEPY_BACKEND from the environment, else from the repo's .env, else dry.
    Read by hand: the vision environment has no python-dotenv."""
    value = os.environ.get(BACKEND_ENV)
    if not value:
        env_file = REPO_ROOT / ".env"
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, _, rest = line.strip().partition("=")
                if key.strip() == BACKEND_ENV:
                    value = rest.split("#", 1)[0].strip().strip("'\"")
        except OSError:
            pass
    return (value or "dry").strip().lower()


def choose_source(config: CameraConfig, override: Optional[str] = None,
                  backend: Optional[str] = None) -> str:
    """robot, laptop or fake: `override`, else camera.source, else the backend."""
    source = config.with_source(override).source
    if source != "auto":
        return source
    return "robot" if (backend or _backend_setting()) == "real" else "laptop"


def open_camera(source: Optional[str] = None, config: Optional[CameraConfig] = None,
                events: Any = None, **options: Any) -> CameraSource:
    """Build (not start) the camera to use.

    source    "robot", "laptop", "fake" or "auto"/None (see choose_source).
    config    default: the camera section of config/eepy.yaml.
    events    something with .event(type, **fields), normally
              eepy.logs.features.ProcessEventLog("vision"); None logs nothing.
    options   passed to the camera class, e.g. keep_full=True or index=1 for
              the laptop camera.
    """
    config = config if config is not None else load_config()
    chosen = choose_source(config, source)
    if chosen == "robot":
        from .robot import RobotCamera
        options.pop("keep_full", None)
        options.pop("index", None)
        return RobotCamera(config, events, **options)
    if chosen == "fake":
        from .fake import FakeCamera
        options.pop("keep_full", None)
        options.pop("index", None)
        return FakeCamera(config, events, **options)
    from .laptop import LaptopCamera
    return LaptopCamera(config, events, **options)
