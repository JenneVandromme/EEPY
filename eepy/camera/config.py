"""The `camera:` section of config/eepy.yaml, readable from either virtualenv.

eepy/settings.py validates the whole file with pydantic, but the vision
process (Python 3.9, no pydantic) has to read the camera settings too. So this
module reads the section with plain pyyaml and checks it by hand. The two must
agree: tests/test_camera.py loads the real file through both.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "config" / "eepy.yaml"

SOURCES = ("auto", "robot", "laptop", "fake")


class CameraConfigError(Exception):
    """The camera settings are unusable. The message is for a non-programmer."""


@dataclass(frozen=True)
class LaptopSettings:
    index: Union[int, str] = "auto"          # "auto" or a camera number
    resolution: Tuple[int, int] = (1280, 720)
    fov_deg: float = 78.0


@dataclass(frozen=True)
class RobotSettings:
    resolution: Tuple[int, int] = (1280, 720)
    fov_deg: float = 50.0

    @property
    def aspect(self) -> float:
        return self.resolution[0] / self.resolution[1]


@dataclass(frozen=True)
class CameraConfig:
    source: str = "auto"
    fps: float = 15.0
    stats_seconds: float = 10.0
    relay_port: int = 8766
    reconnect_seconds: float = 2.0
    laptop: LaptopSettings = field(default_factory=LaptopSettings)
    robot: RobotSettings = field(default_factory=RobotSettings)

    def with_source(self, source: Optional[str]) -> "CameraConfig":
        return self if source is None else replace(self, source=_source(source))


def _source(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in SOURCES:
        raise CameraConfigError(
            f'camera.source "{value}" is not a camera. Use one of: {", ".join(SOURCES)}.')
    return text


def _number(raw: Mapping[str, Any], key: str, default: float, where: str,
            low: float, high: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CameraConfigError(f"{where}.{key} must be a number, not {value!r}.")
    if not low <= value <= high:
        raise CameraConfigError(f"{where}.{key} must be between {low:g} and {high:g}, not {value}.")
    return float(value)


def _resolution(raw: Mapping[str, Any], default: Tuple[int, int], where: str) -> Tuple[int, int]:
    value = raw.get("resolution", list(default))
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and 16 <= v <= 8192
                       for v in value)):
        raise CameraConfigError(
            f"{where}.resolution must be [width, height], e.g. [1280, 720], not {value!r}.")
    return int(value[0]), int(value[1])


def _section(raw: Mapping[str, Any], key: str, known: Tuple[str, ...]) -> Mapping[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, Mapping):
        raise CameraConfigError(f"{key} must be a section with settings under it.")
    for name in value:
        if name not in known:
            raise CameraConfigError(
                f'"{name}" is not a {key} setting. Known: {", ".join(known)}.')
    return value


def parse(raw: Optional[Mapping[str, Any]]) -> CameraConfig:
    """Check the `camera:` section (a dict, or None for all defaults)."""
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise CameraConfigError("camera must be a section with settings under it.")
    top = _section({"camera": raw}, "camera",
                   ("source", "fps", "stats_seconds", "relay_port", "reconnect_seconds",
                    "laptop", "robot"))
    laptop_raw = _section(top, "laptop", ("index", "resolution", "fov_deg"))
    robot_raw = _section(top, "robot", ("resolution", "fov_deg"))

    index = laptop_raw.get("index", "auto")
    if not (index == "auto" or (isinstance(index, int) and not isinstance(index, bool)
                                and 0 <= index <= 63)):
        raise CameraConfigError(f'camera.laptop.index must be "auto" or a number like 0, not {index!r}.')

    port = _number(top, "relay_port", 8766, "camera", 1024, 65535)
    if port != int(port):
        raise CameraConfigError("camera.relay_port must be a whole number.")
    return CameraConfig(
        source=_source(top.get("source", "auto")),
        fps=_number(top, "fps", 15, "camera", 1, 120),
        stats_seconds=_number(top, "stats_seconds", 10, "camera", 1, 3600),
        relay_port=int(port),
        reconnect_seconds=_number(top, "reconnect_seconds", 2, "camera", 0.1, 60),
        laptop=LaptopSettings(
            index=index,
            resolution=_resolution(laptop_raw, (1280, 720), "camera.laptop"),
            fov_deg=_number(laptop_raw, "fov_deg", 78, "camera.laptop", 10, 179),
        ),
        robot=RobotSettings(
            resolution=_resolution(robot_raw, (1280, 720), "camera.robot"),
            fov_deg=_number(robot_raw, "fov_deg", 50, "camera.robot", 10, 179),
        ),
    )


def load(path: Optional[os.PathLike] = None) -> CameraConfig:
    """Read the camera settings from config/eepy.yaml (defaults if absent)."""
    path = Path(path) if path is not None else SETTINGS_PATH
    if not path.is_file():
        return CameraConfig()
    try:
        import yaml
    except ImportError:
        raise CameraConfigError(
            "pyyaml is not installed in this environment, so config/eepy.yaml cannot be read.\n"
            "    pip install -r requirements-vision.txt") from None
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise CameraConfigError(f"{path.name} is not valid YAML: {exc}") from None
    if not isinstance(data, Mapping):
        raise CameraConfigError(f"{path.name} must contain settings, one per line.")
    return parse(data.get("camera"))
