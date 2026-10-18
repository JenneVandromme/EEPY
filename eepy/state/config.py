"""Loading and checking config/state_rules.yaml.

Every mistake stops the program with a message saying what and where, e.g.

    config/state_rules.yaml, rule "slow_blinks": unknown feature "blink_mean_ms_6s"
    (did you mean "blink_mean_ms_60s"?). The features are listed in eepy/state/README.md.

pyyaml + standard library (both environments have pyyaml).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from .estimate import STATES
from .features import WINDOWS, catalog, known

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "config" / "state_rules.yaml"

RULE_STATES = ("very_drowsy", "drowsy", "absent", "distracted")
OPERATORS = {
    ">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}
CONDITION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*(\S+)\s*$")
RULE_KEYS = {"id", "state", "when", "needs", "uncalibrated", "points", "says", "says_uncalibrated"}
UNKNOWN_KEYS = {"id", "when", "says"}
TOP_KEYS = {"priority", "points_needed", "dwell", "gap_seconds", "unknown", "rules"}


class RulesConfigError(ValueError):
    """config/state_rules.yaml is wrong. The message is for a person."""


@dataclass(frozen=True)
class Condition:
    feature: str
    op: str
    value: Any                     # a number, a word ("ok", "running") or a bool
    text: str

    def test(self, features: Dict[str, Any]) -> Optional[bool]:
        """True / False, or None when the feature has no value (cannot tell)."""
        have = features.get(self.feature)
        if have is None:
            return None
        if isinstance(self.value, bool) or isinstance(have, bool):
            have = bool(have) if not isinstance(have, str) else have.lower() == "true"
        elif isinstance(self.value, (int, float)):
            try:
                have = float(have)
            except (TypeError, ValueError):
                return None
        elif self.op not in ("==", "!="):
            return None
        return bool(OPERATORS[self.op](have, self.value))


@dataclass(frozen=True)
class Rule:
    id: str
    state: str
    when: Condition
    says: str
    needs: Tuple[Condition, ...] = ()
    uncalibrated: Optional[Condition] = None
    says_uncalibrated: Optional[str] = None
    points: Optional[float] = None

    @property
    def uses_points(self) -> bool:
        return self.when.feature.endswith("_points")


@dataclass(frozen=True)
class RulesConfig:
    priority: Tuple[str, ...]
    points_needed: Dict[str, float]
    enter: Dict[str, float]
    leave: Dict[str, float]
    gap_seconds: float
    unknown: Tuple[Rule, ...]
    rules: Tuple[Rule, ...]
    path: str = ""
    windows: Tuple[int, ...] = field(default=WINDOWS)

    def severity(self, state: str) -> int:
        """Higher = more severe (earlier in priority). unknown: -1."""
        return len(self.priority) - self.priority.index(state) if state in self.priority else -1


def fill(template: str, value: Any, threshold: Any) -> str:
    """`says` with {value} and {threshold} filled in; never fails (a word value
    with a number format just shows the word)."""
    try:
        return template.format(value=value, threshold=threshold)
    except (ValueError, TypeError, KeyError, IndexError):
        plain = re.sub(r"\{(value|threshold)(:[^}]*)?\}", r"{\1}", template)
        return plain.format(value=value, threshold=threshold)


def _value(text: str) -> Any:
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return float(text)
    except ValueError:
        return text.strip("\"'")


def load(path: Optional[Path] = None, windows: Sequence[float] = WINDOWS) -> RulesConfig:
    import yaml

    path = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RulesConfigError(f"Cannot read {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise RulesConfigError(f"{_show(path)} is not valid YAML (check the indentation):\n{exc}") from None
    return parse(raw, where=_show(path), windows=windows)


def _show(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def parse(raw: Any, where: str = "config/state_rules.yaml", windows: Sequence[float] = WINDOWS) -> RulesConfig:
    def fail(message: str) -> "RulesConfigError":
        return RulesConfigError(f"{where}: {message}")

    if not isinstance(raw, dict):
        raise fail("expected settings (priority, dwell, rules, ...), found nothing usable.")
    extra = set(raw) - TOP_KEYS
    if extra:
        raise fail(f"unknown setting(s) {sorted(extra)}; allowed: {sorted(TOP_KEYS)}.")
    priority = tuple(raw.get("priority") or ())
    wanted = set(RULE_STATES) | {"focused"}
    if set(priority) != wanted or len(priority) != len(wanted):
        raise fail(f"priority must list each of {sorted(wanted)} exactly once, found {list(priority)}.")
    if priority[-1] != "focused":
        raise fail("priority must end with focused (it is what is left when nothing fires).")

    points_needed = _numbers(raw.get("points_needed") or {}, "points_needed", RULE_STATES, fail)
    dwell = raw.get("dwell") or {}
    if not isinstance(dwell, dict) or set(dwell) - {"enter", "leave"}:
        raise fail("dwell must have `enter` and/or `leave`, each with seconds per state.")
    enter = {s: 0.0 for s in STATES}
    enter.update(_numbers(dwell.get("enter") or {}, "dwell.enter", STATES, fail))
    leave = _numbers(dwell.get("leave") or {}, "dwell.leave", STATES, fail)
    gap = raw.get("gap_seconds", 3)
    if not isinstance(gap, (int, float)) or isinstance(gap, bool) or gap <= 0:
        raise fail(f"gap_seconds must be a positive number of seconds, not {gap!r}.")

    names = catalog(windows, point_states=points_needed)
    point_names = {f"{s}_points" for s in points_needed}

    def condition(text: Any, label: str) -> Condition:
        return parse_condition(text, label, names, windows, fail)

    def rule(item: Any, n: int, allowed: set, state: Optional[str]) -> Rule:
        if not isinstance(item, dict):
            raise fail(f"entry {n} must be a rule with id, state, when and says.")
        label = f'rule "{item.get("id", n)}"'
        extra_keys = set(item) - allowed
        if extra_keys:
            raise fail(f"{label}: unknown key(s) {sorted(extra_keys)}; allowed: {sorted(allowed)}.")
        for key in ("id", "when", "says") + (("state",) if state is None else ()):
            if not item.get(key):
                raise fail(f"{label} needs `{key}`.")
        rid = str(item["id"])
        if not re.match(r"^[a-z][a-z0-9_]*$", rid):
            raise fail(f'{label}: an id is lower-case letters, digits and _ (like "perclos_high").')
        rule_state = state or item["state"]
        if rule_state not in RULE_STATES and state is None:
            raise fail(f'{label}: state must be one of {list(RULE_STATES)}, not "{rule_state}" '
                       "(focused is what is left when nothing fires).")
        when = condition(item["when"], label)
        points = item.get("points")
        if rule_state in points_needed and state is None:
            if not isinstance(points, (int, float)) or isinstance(points, bool) or points <= 0:
                raise fail(f"{label}: {rule_state} works with points (points_needed); "
                           "give this rule `points`, a positive number.")
        elif points is not None:
            raise fail(f"{label}: only {sorted(points_needed) or 'point'} rules take `points`.")
        if when.feature in point_names and when.feature == f"{rule_state}_points":
            raise fail(f"{label}: a {rule_state} rule cannot depend on its own points.")
        needs_raw = item.get("needs") or []
        if not isinstance(needs_raw, list):
            raise fail(f"{label}: `needs` is a list, like [face_present_ratio_10s >= 0.5].")
        needs = tuple(condition(c, label) for c in needs_raw)
        uncal = condition(item["uncalibrated"], label) if item.get("uncalibrated") else None
        if item.get("says_uncalibrated") and uncal is None:
            raise fail(f"{label}: says_uncalibrated without an `uncalibrated` condition.")
        for key in ("says", "says_uncalibrated"):
            if item.get(key):
                try:
                    str(item[key]).format(value=0.5, threshold=0.5)
                except (ValueError, KeyError, IndexError) as exc:
                    raise fail(f"{label}: `{key}` cannot be filled in ({exc}); use {{value}} "
                               "and {threshold} only.") from None
        return Rule(rid, rule_state, when, str(item["says"]), needs, uncal,
                    str(item["says_uncalibrated"]) if item.get("says_uncalibrated") else None,
                    float(points) if points is not None else None)

    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise fail("`rules` must be a list of rules.")
    rules = tuple(rule(item, n, RULE_KEYS, None) for n, item in enumerate(rules_raw, 1))
    unknown_raw = raw.get("unknown") or []
    if not isinstance(unknown_raw, list):
        raise fail("`unknown` must be a list of rules with id, when and says.")
    unknown = tuple(rule(item, n, UNKNOWN_KEYS, "unknown") for n, item in enumerate(unknown_raw, 1))
    ids = [r.id for r in rules + unknown]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise fail(f"rule ids must be unique: {duplicates}.")
    for state in points_needed:
        if not any(r.state == state for r in rules):
            raise fail(f"points_needed has {state}, but no {state} rule gives points.")
    return RulesConfig(priority, points_needed, enter, leave, float(gap), unknown, rules,
                       path=where, windows=tuple(int(w) for w in windows))


def parse_condition(text: Any, label: str, names: Dict[str, str], windows: Sequence[float],
                    fail: Callable[[str], Exception]) -> Condition:
    """One condition like "perclos_60s >= 0.12", checked against the feature names.
    Shared by the rules and the HMM parameters (same syntax, same messages)."""
    match = CONDITION.match(str(text)) if isinstance(text, str) else None
    if match is None:
        raise fail(f'{label}: "{text}" is not a condition like "perclos_60s >= 0.12" '
                   f"(compare with one of {' '.join(OPERATORS)}).")
    feature, op, value = match.group(1), match.group(2), _value(match.group(3))
    if not known(feature, names, windows):
        close = difflib.get_close_matches(feature, list(names), n=1)
        hint = f' (did you mean "{close[0]}"?)' if close else ""
        raise fail(f'{label}: unknown feature "{feature}"{hint}. The features are listed '
                   "in eepy/state/README.md.")
    if isinstance(value, str) and op not in ("==", "!="):
        raise fail(f'{label}: "{text}" compares with a word; use a number, or == / != for words.')
    return Condition(feature, op, value, str(text).strip())


def _numbers(section: Any, label: str, allowed: Sequence[str], fail: Any) -> Dict[str, float]:
    if not isinstance(section, dict):
        raise fail(f"{label} must be state: number pairs.")
    out: Dict[str, float] = {}
    for key, value in section.items():
        if key not in allowed:
            raise fail(f'{label}: "{key}" is not a state here; use {list(allowed)}.')
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise fail(f"{label}.{key} must be a number >= 0, not {value!r}.")
        out[key] = float(value)
    return out
