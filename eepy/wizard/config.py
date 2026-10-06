"""config/wizard_panel.yaml: keys, markers and defaults, and the buttons they make.

The panel's buttons are never written by hand. They are generated from two
things: the behaviour library (so a behaviour added to behaviors/*.yaml shows up
after Reload, with no code change) and this config (which only says which key
goes where). `build_panel` combines the two.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from eepy.behaviors.loader import BehaviorFileError, Library, read_yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "wizard_panel.yaml"

NAMED_KEYS = ("escape", "space", "tab")
_SINGLE = re.compile(r"^[a-z0-9,./;'\-=\[\]`]$")
# Handed out, in this order, to behaviours and lines that have no key yet.
AUTO_KEYS = list("uiopdfghjkl") + list("./;'[]=`")


def _check_key(value: str) -> str:
    key = str(value).strip().lower()
    if key in NAMED_KEYS or _SINGLE.match(key):
        return key
    raise ValueError(
        f'"{value}" is not a key the panel can use. Use one character -- a '
        "letter, a digit, or one of , . / ; ' - = [ ] ` -- or escape, space, tab"
    )


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Marker(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    key: str

    _key = field_validator("key", mode="before")(lambda cls, v: _check_key(v))


class Keys(Strict):
    stop: list[str] = Field(default_factory=lambda: ["escape"], min_length=1)
    idle_toggle: str = "a"
    tone_toggle: str = "tab"
    behaviors: dict[str, str] = Field(default_factory=dict)
    ladders: dict[str, dict[int, str]] = Field(default_factory=dict)
    lines: dict[str, str] = Field(default_factory=dict)

    @field_validator("stop", mode="before")
    @classmethod
    def _stop_keys(cls, value):
        values = value if isinstance(value, list) else [value]
        return [_check_key(v) for v in values]

    @field_validator("idle_toggle", "tone_toggle", mode="before")
    @classmethod
    def _one_key(cls, value):
        return _check_key(value)

    @field_validator("behaviors", "lines", mode="before")
    @classmethod
    def _key_map(cls, value):
        return {name: _check_key(key) for name, key in (value or {}).items()}

    @field_validator("ladders", mode="before")
    @classmethod
    def _ladder_map(cls, value):
        return {
            ladder: {int(level): _check_key(key) for level, key in (rungs or {}).items()}
            for ladder, rungs in (value or {}).items()
        }


class PanelConfig(Strict):
    default_intensity: float = Field(default=0.7, ge=0.0, le=1.0)
    idle: str | None = None
    keys: Keys = Field(default_factory=Keys)
    markers: list[Marker] = Field(default_factory=list)


class ConfigError(Exception):
    """The config file is unusable. The message is for a non-programmer."""


def load_config(path: Path = CONFIG_PATH) -> PanelConfig:
    """Read and check the panel config. Raises ConfigError, explained."""
    try:
        raw = read_yaml(path)
    except BehaviorFileError as exc:
        where = f", line {exc.line}" if exc.line else ""
        raise ConfigError(f"{path.name}{where}: {exc.message}") from None
    try:
        config = PanelConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError("\n".join(_explain(err) for err in exc.errors())) from None
    _check_duplicates(config)
    return config


def _explain(err: dict) -> str:
    where = " > ".join(str(p) for p in err["loc"] if not str(p).startswith("function"))
    if err["type"] == "extra_forbidden":
        field = err["loc"][-1]
        known = {"default_intensity", "idle", "keys", "markers", "stop", "idle_toggle",
                 "tone_toggle", "behaviors", "ladders", "lines", "id", "label", "key"}
        match = difflib.get_close_matches(str(field), known, n=1)
        hint = f' Did you mean "{match[0]}"?' if match else ""
        return f'wizard_panel.yaml: "{field}" is not a setting I know.{hint}'
    message = str(err["msg"]).replace("Value error, ", "")
    if err["type"] == "string_pattern_mismatch":
        message = ("marker ids use lower-case letters, numbers and underscores, "
                   "like phone_picked_up")
    return f"wizard_panel.yaml, {where}: {message}"


def _check_duplicates(config: PanelConfig) -> None:
    """Every key may do one thing only. Collect them all, then complain."""
    uses: dict[str, list[str]] = {}

    def add(key: str, what: str) -> None:
        uses.setdefault(key, []).append(what)

    for key in config.keys.stop:
        add(key, "STOP")
    add(config.keys.idle_toggle, "idle on/off")
    add(config.keys.tone_toggle, "hedged/direct toggle")
    for name, key in config.keys.behaviors.items():
        add(key, f'behaviour "{name}"')
    for ladder, rungs in config.keys.ladders.items():
        for level, key in rungs.items():
            add(key, f"{ladder} level {level}")
    for line, key in config.keys.lines.items():
        add(key, f'line "{line}"')
    ids: set[str] = set()
    for marker in config.markers:
        add(marker.key, f'marker "{marker.label}"')
        if marker.id in ids:
            raise ConfigError(f'wizard_panel.yaml: two markers share the id "{marker.id}".')
        ids.add(marker.id)
    clashes = [f'"{key}" is used for {" and ".join(what)}' for key, what in uses.items()
               if len(what) > 1]
    if clashes:
        raise ConfigError("wizard_panel.yaml: a key can only do one thing.\n  "
                          + "\n  ".join(clashes))


# --- turning library + config into buttons -----------------------------------


@dataclass
class Button:
    id: str                     # unique on the page
    kind: Literal["behavior", "line", "marker"]
    label: str
    detail: str                 # "level 2", "refocus_curious", ...
    key: str | None
    target: str                 # behaviour name, line id or marker id
    auto_key: bool = False      # the panel picked the key; not in the config


@dataclass
class Panel:
    groups: list[tuple[str, str, list[Button]]]   # (id, title, buttons)
    stop_keys: list[str]
    idle_key: str
    tone_key: str
    idle: str | None
    default_intensity: float
    warnings: list[str] = field(default_factory=list)


def build_panel(library: Library, config: PanelConfig) -> Panel:
    keys = config.keys
    taken = set(keys.stop) | {keys.idle_toggle, keys.tone_toggle}
    taken |= set(keys.behaviors.values()) | set(keys.lines.values())
    taken |= {k for rungs in keys.ladders.values() for k in rungs.values()}
    taken |= {m.key for m in config.markers}
    free = [k for k in AUTO_KEYS if k not in taken]
    warnings: list[str] = []

    def pick(configured: str | None, what: str) -> tuple[str | None, bool]:
        if configured:
            return configured, False
        if free:
            key = free.pop(0)
            warnings.append(f'{what} has no key in wizard_panel.yaml, so it got "{key}" '
                            "for now. Add it to the config to make that permanent.")
            return key, True
        warnings.append(f"{what} has no key, and there are no free keys left.")
        return None, False

    idle = config.idle
    if idle is not None and idle not in library.behaviors:
        warnings.append(f'The idle behaviour "{idle}" in wizard_panel.yaml does not '
                        "exist, so the idle button is off.")
        idle = None

    groups: list[tuple[str, str, list[Button]]] = []

    basics = []
    for name, behavior in sorted(library.behaviors.items()):
        if behavior.ladder is None and name != idle:
            key, auto = pick(keys.behaviors.get(name), f'"{name}"')
            basics.append(Button(f"b:{name}", "behavior", name.replace("_", " "),
                                 behavior.group or "", key, name, auto))
    groups.append(("basics", "Basics", basics))

    ladders = library.ladders()
    order = [l for l in ("refocus", "bedtime") if l in ladders]
    order += sorted(l for l in ladders if l not in order)
    for ladder in order:
        rungs = []
        for level in ladders[ladder]:
            behavior = library.get_behavior(ladder, level)
            key, auto = pick(keys.ladders.get(ladder, {}).get(level),
                             f"{ladder} level {level}")
            rungs.append(Button(f"b:{behavior.name}", "behavior", f"L{level}",
                                behavior.name.replace(f"{ladder}_", "").replace("_", " "),
                                key, behavior.name, auto))
        groups.append((ladder, f"{ladder.capitalize()} ladder", rungs))

    lines = []
    # Lines with a configured key first, in the config's order (so the keys
    # read 6 7 8 9 0 - across the row), then any new ones alphabetically.
    configured = [l for l in keys.lines if l in library.lines]
    # Lines the calibration speaks by itself get no button (lines.yaml says so).
    spoken_by_system = {l for l in library.lines if l.startswith("calibration_")}
    for line_id in configured + sorted(set(library.lines) - set(configured) - spoken_by_system):
        key, auto = pick(keys.lines.get(line_id), f'line "{line_id}"')
        lines.append(Button(f"l:{line_id}", "line", line_id.replace("_", " "), "",
                            key, line_id, auto))
    groups.append(("lines", "Say", lines))

    markers = [Button(f"m:{m.id}", "marker", m.label, m.id, m.key, m.id)
               for m in config.markers]
    groups.append(("markers", "I observed…", markers))

    for name in keys.behaviors:
        if name not in library.behaviors:
            warnings.append(f'wizard_panel.yaml gives a key to "{name}", which does not exist.')
    for line_id in keys.lines:
        if line_id not in library.lines:
            warnings.append(f'wizard_panel.yaml gives a key to line "{line_id}", which does not exist.')

    return Panel(groups=groups, stop_keys=list(keys.stop), idle_key=keys.idle_toggle,
                 tone_key=keys.tone_toggle, idle=idle,
                 default_intensity=config.default_intensity, warnings=warnings)
