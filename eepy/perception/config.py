"""The `perception:` section of config/eepy.yaml, readable from either virtualenv.

Like eepy/camera/config.py: plain pyyaml and hand-written checks, because the
vision process (Python 3.9) has no pydantic. eepy/settings.py validates the
same section through parse() below, so the two can never disagree.

Every threshold lives here so the team can experiment without touching code.
What each one means, in plain words, is in eepy/perception/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from eepy.camera.config import SETTINGS_PATH, CameraConfigError

# (section, key) -> (default, lowest, highest). The single source of truth:
# defaults, ranges and the list of known names all come from here.
SPEC: Dict[Tuple[str, str], Tuple[float, float, float]] = {
    ("", "window_seconds_short"): (10, 2, 600),
    ("", "window_seconds_long"): (60, 5, 3600),
    ("", "window_every_seconds"): (1.0, 0.2, 60),
    ("", "min_blink_fps"): (15, 1, 120),
    ("", "look_away_deg"): (30, 5, 90),

    ("eyes", "ear_open"): (0.28, 0.05, 1.0),      # default baseline: a normally open eye
    ("eyes", "ear_floor"): (0.05, 0.0, 0.5),      # EAR of a fully closed eye
    ("eyes", "perclos_closure"): (0.8, 0.5, 1.0),  # P80

    ("blink", "close"): (0.5, 0.1, 1.0),          # eye_closure that starts a blink
    ("blink", "open"): (0.35, 0.0, 1.0),          # ...and ends it (lower: hysteresis)
    ("blink", "min_ms"): (50, 0, 1000),
    ("blink", "long_ms"): (500, 100, 5000),

    ("yawn", "jaw_open"): (0.5, 0.05, 1.0),       # MediaPipe jawOpen blendshape
    ("yawn", "mar"): (0.45, 0.05, 2.0),           # mouth opening above the closed-mouth baseline
    ("yawn", "min_seconds"): (1.5, 0.2, 10),
    ("yawn", "gap_seconds"): (0.3, 0.0, 2),

    ("nod", "drop_deg"): (12, 3, 60),
    ("nod", "drop_seconds"): (1.5, 0.2, 10),
    ("nod", "recover_fraction"): (0.6, 0.1, 1.0),
    ("nod", "recover_seconds"): (3.0, 0.2, 20),
    ("nod", "median_seconds"): (5.0, 1, 60),

    ("face", "lost_seconds"): (1.0, 0.1, 30),
    ("face", "back_seconds"): (0.5, 0.0, 30),
}
SECTIONS = ("eyes", "blink", "yawn", "nod", "face")


@dataclass(frozen=True)
class PerceptionConfig:
    """All perception settings: `values[(section, key)]`, plus attribute-style
    access for the common ones (config.get("blink", "close"))."""

    values: Dict[Tuple[str, str], float] = field(
        default_factory=lambda: {k: float(v[0]) for k, v in SPEC.items()})

    def get(self, section: str, key: str) -> float:
        return self.values[(section, key)]

    @property
    def windows(self) -> Tuple[float, float]:
        return self.get("", "window_seconds_short"), self.get("", "window_seconds_long")

    def replace(self, **changes: float) -> "PerceptionConfig":
        """Tests: replace(blink__close=0.6, min_blink_fps=10)."""
        values = dict(self.values)
        for name, value in changes.items():
            section, _, key = name.rpartition("__")
            if (section, key) not in SPEC:
                raise KeyError(name)
            values[(section, key)] = float(value)
        return PerceptionConfig(values)


def parse(raw: Optional[Mapping[str, Any]]) -> PerceptionConfig:
    """Check the `perception:` section (a dict, or None for all defaults)."""
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise CameraConfigError("perception must be a section with settings under it.")
    values = {k: float(v[0]) for k, v in SPEC.items()}
    top_known = [k for s, k in SPEC if s == ""] + list(SECTIONS)
    for name, value in raw.items():
        if name not in top_known:
            raise CameraConfigError(f'"{name}" is not a perception setting. Known: {", ".join(top_known)}.')
        if name in SECTIONS:
            if not isinstance(value, Mapping):
                raise CameraConfigError(f"perception.{name} must be a section with settings under it.")
            known = [k for s, k in SPEC if s == name]
            for key, inner in value.items():
                if key not in known:
                    raise CameraConfigError(
                        f'"{key}" is not a perception.{name} setting. Known: {", ".join(known)}.')
                values[(name, key)] = _number(f"perception.{name}.{key}", inner, SPEC[(name, key)])
        else:
            values[("", name)] = _number(f"perception.{name}", value, SPEC[("", name)])
    if values[("blink", "open")] >= values[("blink", "close")]:
        raise CameraConfigError("perception.blink.open must be lower than perception.blink.close.")
    if values[("eyes", "ear_floor")] >= values[("eyes", "ear_open")]:
        raise CameraConfigError("perception.eyes.ear_floor must be lower than perception.eyes.ear_open.")
    if values[("", "window_seconds_short")] > values[("", "window_seconds_long")]:
        raise CameraConfigError("perception.window_seconds_short must not exceed window_seconds_long.")
    return PerceptionConfig(values)


def _number(where: str, value: Any, spec: Tuple[float, float, float]) -> float:
    _, low, high = spec
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CameraConfigError(f"{where} must be a number, not {value!r}.")
    if not low <= value <= high:
        raise CameraConfigError(f"{where} must be between {low:g} and {high:g}, not {value}.")
    return float(value)


def load(path: Optional[Path] = None) -> PerceptionConfig:
    path = Path(path) if path is not None else SETTINGS_PATH
    if not path.is_file():
        return PerceptionConfig()
    try:
        import yaml
    except ImportError:
        raise CameraConfigError("pyyaml is not installed here: pip install -r requirements-vision.txt") from None
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return parse(data.get("perception") if isinstance(data, Mapping) else None)
