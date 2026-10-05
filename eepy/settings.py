"""config/eepy.yaml: system-wide settings, most importantly the MODE.

The mode is the experimental condition -- which controller decides what the
robot does. It is chosen once, at start-up, from this file or `--mode`, and
recorded in every session.

Main process only: this module uses pydantic and pyyaml, which the vision
virtualenv does not have. The vision side never needs it -- it follows the
session the panel started (see eepy/logs/features.py).
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from eepy.behaviors.loader import BehaviorFileError, read_yaml
from eepy.logs.schema import IMPLEMENTED_MODES, MODES

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = REPO_ROOT / "config" / "eepy.yaml"


class SettingsError(Exception):
    """config/eepy.yaml is unusable. The message is for a non-programmer."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Privacy(_Strict):
    store_raw_speech: bool = False


class Logging(_Strict):
    flush_seconds: float = Field(default=1.0, gt=0, le=60)


class Speech(_Strict):
    """How the robot speaks. See eepy/speech/README.md."""

    engine: Literal["piper", "system", "google", "gemini", "none"] = "piper"
    # None means the engine's default: en_US-lessac-medium for piper, the
    # system's own voice for system, "en" for google, "Kore" for gemini.
    voice: Optional[str] = "en_US-lessac-medium"
    fallback: Literal["system", "none"] = "system"
    fallback_voice: Optional[str] = None
    output: Literal["auto", "robot", "desktop", "none"] = "auto"
    pregenerate: bool = True


def _check_camera(value: object) -> object:
    """The camera section is checked by eepy/camera/config.py, which the
    vision process (no pydantic) uses too -- one set of rules, two readers."""
    from eepy.camera.config import CameraConfigError, parse

    try:
        parse(value)  # type: ignore[arg-type]
    except CameraConfigError as exc:
        raise ValueError(str(exc)) from None
    return value


class Settings(_Strict):
    mode: str = "wizard"
    data_dir: str = "data/sessions"
    participant_pattern: str = r"^P[0-9]{2,3}$"
    privacy: Privacy = Field(default_factory=Privacy)
    logging: Logging = Field(default_factory=Logging)
    speech: Speech = Field(default_factory=Speech)
    camera: Optional[dict] = None

    @field_validator("camera")
    @classmethod
    def _valid_camera(cls, value: Optional[dict]) -> Optional[dict]:
        return _check_camera(value)  # type: ignore[return-value]

    @property
    def camera_config(self):
        """The camera settings, as eepy.camera.config.CameraConfig."""
        from eepy.camera.config import parse

        return parse(self.camera)

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        return check_mode(value)

    @field_validator("participant_pattern")
    @classmethod
    def _valid_regex(cls, value: str) -> str:
        import re
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"participant_pattern is not a valid pattern: {exc}") from None
        return value

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def mode_implemented(self) -> bool:
        return self.mode in IMPLEMENTED_MODES


def check_mode(value: str) -> str:
    mode = str(value).strip().lower()
    if mode not in MODES:
        match = difflib.get_close_matches(mode, MODES, n=1)
        hint = f' Did you mean "{match[0]}"?' if match else ""
        raise ValueError(f'"{value}" is not a mode.{hint} Use one of: {", ".join(MODES)}.')
    return mode


def load_settings(path: Path = SETTINGS_PATH, mode: str | None = None) -> Settings:
    """Read config/eepy.yaml (defaults if it does not exist). `mode` overrides."""
    raw: dict = {}
    if Path(path).is_file():
        try:
            raw = read_yaml(Path(path))
        except BehaviorFileError as exc:
            where = f", line {exc.line}" if exc.line else ""
            raise SettingsError(f"{Path(path).name}{where}: {exc.message}") from None
    if mode is not None:
        raw["mode"] = mode
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"])
            if err["type"] == "extra_forbidden":
                known = ["mode", "data_dir", "participant_pattern", "privacy", "logging",
                         "store_raw_speech", "flush_seconds", "speech", "engine", "voice",
                         "fallback", "fallback_voice", "output", "pregenerate", "camera"]
                match = difflib.get_close_matches(str(err["loc"][-1]), known, n=1)
                hint = f' Did you mean "{match[0]}"?' if match else ""
                lines.append(f'eepy.yaml: "{err["loc"][-1]}" is not a setting I know.{hint}')
            else:
                lines.append(f"eepy.yaml, {where}: {str(err['msg']).replace('Value error, ', '')}")
        raise SettingsError("\n".join(lines)) from None
