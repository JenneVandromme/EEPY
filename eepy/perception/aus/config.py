"""The `aus:` section of config/eepy.yaml, and which machine profile applies.

Readable from both virtualenvs (plain pyyaml, Python 3.9-safe); eepy/settings.py
validates the same section through parse().

The PROFILE is personal -- this laptop vs. the demo desktop -- so it is not in
the shared eepy.yaml but chosen by `--profile`, else EEPY_PROFILE (environment
or your own .env), else "laptop".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from eepy.camera.config import SETTINGS_PATH, CameraConfigError, env_value

PROFILE_ENV = "EEPY_PROFILE"
DEFAULT_PROFILE = "laptop"
# Everything the shipped Py-Feat model (face_multitask_v28) outputs.
KNOWN_UNITS = ("AU01", "AU02", "AU04", "AU05", "AU06", "AU07", "AU09", "AU10", "AU11", "AU12",
               "AU14", "AU15", "AU17", "AU20", "AU23", "AU24", "AU25", "AU26", "AU28", "AU43")
DEFAULT_UNITS = ("AU01", "AU02", "AU04", "AU05", "AU07", "AU25", "AU26", "AU43")
DEVICES = ("auto", "cuda", "mps", "cpu")


@dataclass(frozen=True)
class Profile:
    name: str = DEFAULT_PROFILE
    enabled: Union[bool, str] = "auto"   # "auto", True, False
    device: str = "auto"
    max_rate: float = 10.0
    require_gpu: bool = False
    cpu_threads: Union[int, str] = "auto"


DEFAULT_PROFILES: Dict[str, Profile] = {
    "laptop": Profile("laptop"),
    "demo-desktop": Profile("demo-desktop", enabled=True, max_rate=15.0, require_gpu=True),
}


@dataclass(frozen=True)
class AUConfig:
    units: Tuple[str, ...] = DEFAULT_UNITS
    active_threshold: float = 0.5
    stale_seconds: float = 1.0
    min_rate: float = 2.0
    slow_grace_seconds: float = 10.0
    retry_seconds: float = 60.0
    profiles: Dict[str, Profile] = field(default_factory=lambda: dict(DEFAULT_PROFILES))
    profile: Profile = field(default_factory=lambda: DEFAULT_PROFILES[DEFAULT_PROFILE])

    def with_profile(self, name: Optional[str]) -> "AUConfig":
        """This config with profile `name` (None: EEPY_PROFILE, else laptop) selected."""
        chosen = name or env_value(PROFILE_ENV) or DEFAULT_PROFILE
        if chosen not in self.profiles:
            raise CameraConfigError(
                f'"{chosen}" is not a profile (from --profile or {PROFILE_ENV}). '
                f'Known: {", ".join(self.profiles)} (aus.profiles in config/eepy.yaml).')
        return AUConfig(self.units, self.active_threshold, self.stale_seconds, self.min_rate,
                        self.slow_grace_seconds, self.retry_seconds, self.profiles,
                        self.profiles[chosen])


def _number(where: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CameraConfigError(f"{where} must be a number, not {value!r}.")
    if not low <= value <= high:
        raise CameraConfigError(f"{where} must be between {low:g} and {high:g}, not {value}.")
    return float(value)


def _known(where: str, raw: Mapping[str, Any], names: Tuple[str, ...]) -> None:
    for key in raw:
        if key not in names:
            raise CameraConfigError(f'"{key}" is not an {where} setting. Known: {", ".join(names)}.')


def _profile(name: str, raw: Any) -> Profile:
    where = f"aus.profiles.{name}"
    if not isinstance(raw, Mapping):
        raise CameraConfigError(f"{where} must be a section with settings under it.")
    _known(where, raw, ("enabled", "device", "max_rate", "require_gpu", "cpu_threads"))
    enabled = raw.get("enabled", "auto")
    if enabled not in (True, False, "auto"):
        raise CameraConfigError(f'{where}.enabled must be auto, true or false, not {enabled!r}.')
    device = str(raw.get("device", "auto")).lower()
    if device not in DEVICES:
        raise CameraConfigError(f'{where}.device must be one of {", ".join(DEVICES)}, not {device!r}.')
    require_gpu = raw.get("require_gpu", False)
    if not isinstance(require_gpu, bool):
        raise CameraConfigError(f"{where}.require_gpu must be true or false.")
    threads = raw.get("cpu_threads", "auto")
    if threads != "auto":
        threads = int(_number(f"{where}.cpu_threads", threads, 1, 256))
    return Profile(name, enabled, device, _number(f"{where}.max_rate", raw.get("max_rate", 10), 0.1, 120),
                   require_gpu, threads)


def parse(raw: Optional[Mapping[str, Any]]) -> AUConfig:
    """Check the `aus:` section (a dict, or None for all defaults). The profile
    is NOT chosen here: call .with_profile()."""
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise CameraConfigError("aus must be a section with settings under it.")
    _known("aus", raw, ("units", "active_threshold", "stale_seconds", "min_rate",
                        "slow_grace_seconds", "retry_seconds", "profiles"))
    units = raw.get("units", list(DEFAULT_UNITS))
    if not isinstance(units, (list, tuple)) or not units:
        raise CameraConfigError("aus.units must be a list such as [AU01, AU04, AU43].")
    units = tuple(str(u).upper() for u in units)
    unknown = [u for u in units if u not in KNOWN_UNITS]
    if unknown:
        raise CameraConfigError(f'aus.units: {", ".join(unknown)} not available from Py-Feat. '
                                f'Available: {" ".join(KNOWN_UNITS)}.')
    profiles = dict(DEFAULT_PROFILES)
    raw_profiles = raw.get("profiles") or {}
    if not isinstance(raw_profiles, Mapping):
        raise CameraConfigError("aus.profiles must be a section of named profiles.")
    for name, body in raw_profiles.items():
        profiles[str(name)] = _profile(str(name), body)
    return AUConfig(
        units=units,
        active_threshold=_number("aus.active_threshold", raw.get("active_threshold", 0.5), 0.01, 0.99),
        stale_seconds=_number("aus.stale_seconds", raw.get("stale_seconds", 1.0), 0.1, 30),
        min_rate=_number("aus.min_rate", raw.get("min_rate", 2), 0.0, 60),
        slow_grace_seconds=_number("aus.slow_grace_seconds", raw.get("slow_grace_seconds", 10), 1, 600),
        retry_seconds=_number("aus.retry_seconds", raw.get("retry_seconds", 60), 1, 3600),
        profiles=profiles, profile=profiles[DEFAULT_PROFILE] if DEFAULT_PROFILE in profiles
        else next(iter(profiles.values())))


def load(path: Optional[Path] = None, profile: Optional[str] = None) -> AUConfig:
    """The `aus:` settings with the profile selected (see the module docstring)."""
    path = Path(path) if path is not None else SETTINGS_PATH
    data: Mapping[str, Any] = {}
    if path.is_file():
        try:
            import yaml
        except ImportError:
            raise CameraConfigError("pyyaml is not installed here: pip install -r requirements-vision.txt") from None
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    return parse(data.get("aus") if isinstance(data, Mapping) else None).with_profile(profile)
