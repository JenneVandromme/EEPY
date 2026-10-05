"""The `gaze:` section of config/eepy.yaml (readable from both virtualenvs).

Same pattern as eepy/perception/config.py: one SPEC table with every setting,
its default and its allowed range; eepy/settings.py validates the same section
through parse(). What each setting means: eepy/perception/gaze/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from eepy.camera.config import SETTINGS_PATH, CameraConfigError

SPEC: Dict[Tuple[str, str], Tuple[float, float, float]] = {
    # Hysteresis: how long a change must last before it counts.
    ("", "min_away_seconds"): (1.0, 0.0, 30),
    ("", "min_back_seconds"): (0.5, 0.0, 30),
    ("", "smoothing_seconds"): (0.15, 0.0, 2),
    # Eye-in-head angle at full iris deflection (degrees).
    ("", "eye_gain_yaw"): (35, 5, 60),
    ("", "eye_gain_pitch"): (25, 5, 60),
    # When the iris is trusted (otherwise: head pose only).
    ("", "min_eye_width_px"): (18, 4, 200),
    ("", "max_head_yaw_for_iris"): (40, 10, 90),
    ("", "max_eye_disagreement"): (0.6, 0.05, 2),
    ("", "max_iris_jitter_deg"): (6, 0.5, 45),
    # Zones.
    ("", "edge_softness_deg"): (3, 0.5, 20),
    ("", "screen_margin_deg"): (4, 0, 30),
    ("", "notes_margin_deg"): (8, 0, 30),
    # "Look at user": the robot camera's real horizontal field of view (the SDK's
    # lens calibration says ~87). With the laptop camera the blue box is the view.
    ("", "robot_lens_fov_deg"): (87, 20, 170),
    ("", "user_report_rate"): (5, 0.5, 30),
    # Uncalibrated defaults: "head roughly facing the camera/screen".
    ("default_screen", "yaw"): (20, 2, 90),
    ("default_screen", "pitch_min"): (-10, -60, 60),
    ("default_screen", "pitch_max"): (20, -60, 60),
    ("default_notes", "yaw"): (30, 2, 90),
    ("default_notes", "pitch_min"): (20, -60, 90),
    ("default_notes", "pitch_max"): (45, -60, 90),
}
SECTIONS = ("default_screen", "default_notes")


@dataclass(frozen=True)
class GazeConfig:
    values: Dict[Tuple[str, str], float] = field(
        default_factory=lambda: {k: float(v[0]) for k, v in SPEC.items()})

    def get(self, key: str, section: str = "") -> float:
        return self.values[(section, key)]

    def replace(self, **changes: float) -> "GazeConfig":
        """Tests: replace(min_away_seconds=2, default_screen__yaw=10)."""
        values = dict(self.values)
        for name, value in changes.items():
            section, _, key = name.rpartition("__")
            if (section, key) not in SPEC:
                raise KeyError(name)
            values[(section, key)] = float(value)
        return GazeConfig(values)


def _number(where: str, value: Any, spec: Tuple[float, float, float]) -> float:
    _, low, high = spec
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CameraConfigError(f"{where} must be a number, not {value!r}.")
    if not low <= value <= high:
        raise CameraConfigError(f"{where} must be between {low:g} and {high:g}, not {value}.")
    return float(value)


def parse(raw: Optional[Mapping[str, Any]]) -> GazeConfig:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise CameraConfigError("gaze must be a section with settings under it.")
    values = {k: float(v[0]) for k, v in SPEC.items()}
    top = [k for s, k in SPEC if s == ""] + list(SECTIONS)
    for name, value in raw.items():
        if name not in top:
            raise CameraConfigError(f'"{name}" is not a gaze setting. Known: {", ".join(top)}.')
        if name in SECTIONS:
            if not isinstance(value, Mapping):
                raise CameraConfigError(f"gaze.{name} must be a section with settings under it.")
            known = [k for s, k in SPEC if s == name]
            for key, inner in value.items():
                if key not in known:
                    raise CameraConfigError(f'"{key}" is not a gaze.{name} setting. Known: {", ".join(known)}.')
                values[(name, key)] = _number(f"gaze.{name}.{key}", inner, SPEC[(name, key)])
        else:
            values[("", name)] = _number(f"gaze.{name}", value, SPEC[("", name)])
    for section in SECTIONS:
        if values[(section, "pitch_min")] >= values[(section, "pitch_max")]:
            raise CameraConfigError(f"gaze.{section}.pitch_min must be below pitch_max.")
    return GazeConfig(values)


def load(path: Optional[Path] = None) -> GazeConfig:
    path = Path(path) if path is not None else SETTINGS_PATH
    if not path.is_file():
        return GazeConfig()
    try:
        import yaml
    except ImportError:
        raise CameraConfigError("pyyaml is not installed here: pip install -r requirements-vision.txt") from None
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return parse(data.get("gaze") if isinstance(data, Mapping) else None)
