"""Loading and checking config/escalation.yaml.

Every mistake stops the panel with a message saying what and where, e.g.

    config/escalation.yaml: ladders.refocus.reaction: level "5" -- the refocus
    ladder has levels 0-4 (behaviors/refocus.yaml).

The ladder levels are checked against the behaviour library by check_library().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "config" / "escalation.yaml"

STATES = ("focused", "distracted", "drowsy", "very_drowsy", "absent", "unknown")
ROLES = ("study_buddy", "refocus", "bedtime", "paused", "none")
LADDER_ROLES = ("refocus", "bedtime")
TOP_KEYS = {"roles", "role_switch", "onset", "ladders", "focused", "guards", "absent", "break",
            "wind_down", "false_alarm_seconds", "tick_seconds", "confidence", "hmm"}
DECISIONS = ("belief", "gate")


class EscalationConfigError(ValueError):
    """config/escalation.yaml is wrong. The message is for a person."""


@dataclass(frozen=True)
class LadderConfig:
    name: str
    start_level: int
    reaction: Dict[int, float]            # nudge level -> reaction window (s)
    intensity: Dict[int, float]
    cooldown: float
    restart_level: int
    start_level_very_drowsy: Optional[int] = None

    @property
    def levels(self) -> Tuple[int, ...]:
        return tuple(sorted(self.reaction))

    @property
    def top(self) -> int:
        return max(self.reaction)


@dataclass(frozen=True)
class BeliefConfig:
    """hmm mode only: decisions on the HMM's full belief (A19, docs/A19_BELIEF_DECISIONS.md).
    Probabilities are P(family): refocus = distracted, bedtime = drowsy + very drowsy."""

    decisions: str = "belief"            # belief | gate (A16's gate: the shown state above confidence.act)
    floor: float = 0.55                  # evidence builds while P is above this, drains below it
    needed: Dict[str, float] = field(default_factory=lambda: {"refocus": 8.0, "bedtime": 12.0, "very_drowsy": 6.0})
    keep: float = 0.55                   # a running ladder climbs on while P stays above this ...
    hold_if_focused: float = 0.30        # ... and P(focused) below this (focus coming back: no climbing)
    reset_focused: float = 0.75          # P(focused) at least this ...
    reset_seconds: float = 10.0          # ... this long: back to focused, the ladder resets
    wait_uncertainty: float = 0.6        # wait for more evidence above this uncertainty ...
    wait_gap: float = 0.15               # ... or when the top two states are closer than this
    wait_consider: float = 0.40          # only when a ladder's state is at least this likely (else: focused)
    wait_margin: float = 0.05            # a wait ends only this much clearer (no on/off at the line)
    ambiguous_each: float = 0.30         # distracted and sleepy both at least this: ambiguous
    ambiguous_needed: float = 12.0
    long_work: float = 2700.0            # seconds of work since the start / last break: offer the break
    switch_step_back: int = 1            # a switched ladder starts this many levels below the old one
    switch_max_level: int = 3            # ... and never above this level
    intensity_scale: bool = True
    intensity_min: float = 0.7           # the movement scale at the act threshold ...
    intensity_full_at: float = 0.9       # ... rising to the designer's full intensity at this P
    evidence_top: int = 3                # signs attached to every decision


@dataclass(frozen=True)
class EscalationConfig:
    roles: Dict[str, str]
    role_switch: Dict[str, str]
    onset: Dict[str, float]
    ladders: Dict[str, LadderConfig]
    reset_after: float
    approving_nod: str
    nod_only_after_success: bool
    nod_min_interval: float
    min_gap: float
    max_per_hour: int
    after_calibration: float
    absent_reset_after: float
    went_to_bed_after: float
    went_to_bed_level: int
    break_offer_level: int
    break_absent_within: float
    break_seconds: float
    break_welcome_line: str
    wind_down_offer_level: int
    wind_down_seconds: float
    wind_down_snoozes: int
    wind_down_snooze_seconds: float
    wind_down_reminder_line: str
    wind_down_reminder_before: float
    goodnight_level: int
    false_alarm_seconds: float = 5.0
    tick_seconds: float = 0.5
    # How sure the state estimate must be (HMM only; the rules give no confidence).
    act_confidence: Dict[str, float] = field(default_factory=dict)    # ladder -> minimum P to act
    direct_confidence: float = 0.85                                    # minimum P for the direct tone
    belief: Optional[BeliefConfig] = None                              # hmm: decisions on the full belief (A19)
    path: str = "config/escalation.yaml"
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def role_for(self, state: Optional[str]) -> str:
        return self.roles.get(state or "unknown", "none")


def load(path: Optional[Path] = None) -> EscalationConfig:
    import yaml

    path = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EscalationConfigError(f"Cannot read {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise EscalationConfigError(f"{_show(path)} is not valid YAML (check the indentation):\n{exc}") from None
    return parse(raw, where=_show(path))


def _show(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def parse(raw: Any, where: str = "config/escalation.yaml") -> EscalationConfig:
    def fail(message: str) -> EscalationConfigError:
        return EscalationConfigError(f"{where}: {message}")

    if not isinstance(raw, dict):
        raise fail("expected settings (roles, onset, ladders, ...), found nothing usable.")
    extra = set(raw) - TOP_KEYS
    if extra:
        raise fail(f"unknown setting(s) {sorted(extra)}; allowed: {sorted(TOP_KEYS)}.")

    def section(name: str) -> Dict[str, Any]:
        value = raw.get(name)
        if not isinstance(value, dict):
            raise fail(f"`{name}` is missing or not a list of settings.")
        return value

    def seconds(value: Any, label: str, positive: bool = False) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or (positive and value == 0):
            raise fail(f"{label} must be a number of seconds {'> 0' if positive else '>= 0'}, not {value!r}.")
        return float(value)

    def whole(value: Any, label: str, low: int = 0) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < low:
            raise fail(f"{label} must be a whole number >= {low}, not {value!r}.")
        return value

    def text(value: Any, label: str, allow_empty: bool = False) -> str:
        if value is None and allow_empty:
            return ""
        if not isinstance(value, str) or (not value and not allow_empty):
            raise fail(f"{label} must be a name (text), not {value!r}.")
        return value

    def known(sec: Dict[str, Any], label: str, allowed: set) -> None:
        bad = set(sec) - allowed
        if bad:
            raise fail(f"{label}: unknown key(s) {sorted(bad)}; allowed: {sorted(allowed)}.")

    roles = section("roles")
    missing = [s for s in STATES if s not in roles]
    if missing or set(roles) - set(STATES):
        raise fail(f"roles must give a role for each state {list(STATES)}"
                   + (f"; missing {missing}" if missing else f"; unknown {sorted(set(roles) - set(STATES))}") + ".")
    for state, role in roles.items():
        if role not in ROLES:
            raise fail(f'roles.{state}: "{role}" is not a role; use one of {list(ROLES)}.')
    if roles["focused"] != "study_buddy":
        raise fail("roles.focused must be study_buddy: EEPY never interrupts real focus.")

    switch = raw.get("role_switch") or {}
    if not isinstance(switch, dict):
        raise fail("role_switch must be role: behaviour-name pairs.")
    known(switch, "role_switch", set(ROLES))
    role_switch = {k: text(v, f"role_switch.{k}", allow_empty=True) for k, v in switch.items()}

    onset_raw = section("onset")
    known(onset_raw, "onset", {s for s, r in roles.items() if r in LADDER_ROLES})
    onset = {k: seconds(v, f"onset.{k}") for k, v in onset_raw.items()}
    for state, role in roles.items():
        if role in LADDER_ROLES and state not in onset:
            raise fail(f"onset.{state} is missing: how long must {state} last before the first nudge?")

    ladders_raw = section("ladders")
    needed = {r for r in roles.values() if r in LADDER_ROLES}
    known(ladders_raw, "ladders", set(LADDER_ROLES))
    ladders: Dict[str, LadderConfig] = {}
    for name in sorted(needed):
        sec = ladders_raw.get(name)
        if not isinstance(sec, dict):
            raise fail(f"ladders.{name} is missing.")
        known(sec, f"ladders.{name}", {"start_level", "start_level_very_drowsy", "reaction", "intensity",
                                       "cooldown", "restart_level"})
        reaction_raw = sec.get("reaction")
        if not isinstance(reaction_raw, dict) or not reaction_raw:
            raise fail(f"ladders.{name}.reaction must give seconds per level, like {{1: 30, 2: 30}}.")
        reaction: Dict[int, float] = {}
        for level, value in reaction_raw.items():
            if not isinstance(level, int) or level < 1:
                raise fail(f"ladders.{name}.reaction: levels are whole numbers from 1 (0 is the "
                           f"background presence, never a nudge), not {level!r}.")
            reaction[level] = seconds(value, f"ladders.{name}.reaction.{level}", positive=True)
        levels = sorted(reaction)
        if levels != list(range(levels[0], levels[-1] + 1)):
            raise fail(f"ladders.{name}.reaction: levels must have no gaps, found {levels}.")
        intensity_raw = sec.get("intensity") or {}
        if not isinstance(intensity_raw, dict):
            raise fail(f"ladders.{name}.intensity must give 0-1 per level.")
        intensity: Dict[int, float] = {}
        for level in levels:
            value = intensity_raw.get(level, 1.0)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1:
                raise fail(f"ladders.{name}.intensity.{level} must be between 0 and 1, not {value!r}.")
            intensity[level] = float(value)
        if set(intensity_raw) - set(levels):
            raise fail(f"ladders.{name}.intensity has levels without a reaction time: "
                       f"{sorted(set(intensity_raw) - set(levels))}.")

        def level_in(key: str, required: bool = True) -> Optional[int]:
            value = sec.get(key)
            if value is None and not required:
                return None
            if not isinstance(value, int) or isinstance(value, bool) or value not in reaction:
                raise fail(f"ladders.{name}.{key} must be one of the levels {levels}, not {value!r}.")
            return value

        ladders[name] = LadderConfig(
            name, level_in("start_level"), reaction, intensity,
            seconds(sec.get("cooldown"), f"ladders.{name}.cooldown"), level_in("restart_level"),
            level_in("start_level_very_drowsy", required=False))

    focused = section("focused")
    known(focused, "focused", {"reset_after", "approving_nod", "nod_only_after_success", "nod_min_interval"})
    guards = section("guards")
    known(guards, "guards", {"min_gap", "max_per_hour", "after_calibration"})
    absent = section("absent")
    known(absent, "absent", {"reset_after", "went_to_bed_after", "went_to_bed_level"})
    brk = section("break")
    known(brk, "break", {"offer_level", "absent_within", "seconds", "welcome_line"})
    wind = section("wind_down")
    known(wind, "wind_down", {"offer_level", "seconds", "snoozes", "snooze_seconds", "reminder_line",
                              "reminder_before", "goodnight_level"})
    conf = raw.get("confidence") or {}
    if not isinstance(conf, dict):
        raise fail("confidence must have `act` (per ladder) and/or `direct`.")
    known(conf, "confidence", {"act", "direct"})

    def probability(value: Any, label: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1:
            raise fail(f"{label} must be a number between 0 and 1 (like 0.7), not {value!r}.")
        return float(value)

    act_raw = conf.get("act") or {}
    if not isinstance(act_raw, dict):
        raise fail("confidence.act must give a number per ladder, like {refocus: 0.7, bedtime: 0.7}.")
    act_confidence: Dict[str, float] = {}
    for ladder_name, value in act_raw.items():
        if ladder_name not in LADDER_ROLES:
            raise fail(f'confidence.act: "{ladder_name}" is not a ladder; use {" or ".join(LADDER_ROLES)}.')
        act_confidence[ladder_name] = probability(value, f"confidence.act.{ladder_name}")
    direct_confidence = probability(conf.get("direct", 0.85), "confidence.direct")
    belief = _belief(raw.get("hmm"), fail, probability, seconds, whole)
    if belief is not None and act_confidence and belief.keep > min(act_confidence.values()):
        raise fail(f"hmm.keep ({belief.keep}) must not be above confidence.act ({min(act_confidence.values())}): "
                   "going on with a ladder should be easier than starting one (hysteresis).")
    nod_flag = focused.get("nod_only_after_success", True)
    if not isinstance(nod_flag, bool):
        raise fail(f"focused.nod_only_after_success must be true or false, not {nod_flag!r}.")

    config = EscalationConfig(
        roles=dict(roles), role_switch=role_switch, onset=onset, ladders=ladders,
        reset_after=seconds(focused.get("reset_after"), "focused.reset_after"),
        approving_nod=text(focused.get("approving_nod"), "focused.approving_nod", allow_empty=True),
        nod_only_after_success=nod_flag,
        nod_min_interval=seconds(focused.get("nod_min_interval", 0), "focused.nod_min_interval"),
        min_gap=seconds(guards.get("min_gap"), "guards.min_gap"),
        max_per_hour=whole(guards.get("max_per_hour"), "guards.max_per_hour", low=1),
        after_calibration=seconds(guards.get("after_calibration"), "guards.after_calibration"),
        absent_reset_after=seconds(absent.get("reset_after"), "absent.reset_after", positive=True),
        went_to_bed_after=seconds(absent.get("went_to_bed_after"), "absent.went_to_bed_after", positive=True),
        went_to_bed_level=whole(absent.get("went_to_bed_level"), "absent.went_to_bed_level", low=1),
        break_offer_level=whole(brk.get("offer_level"), "break.offer_level", low=1),
        break_absent_within=seconds(brk.get("absent_within"), "break.absent_within", positive=True),
        break_seconds=seconds(brk.get("seconds"), "break.seconds", positive=True),
        break_welcome_line=text(brk.get("welcome_line"), "break.welcome_line", allow_empty=True),
        wind_down_offer_level=whole(wind.get("offer_level"), "wind_down.offer_level", low=1),
        wind_down_seconds=seconds(wind.get("seconds"), "wind_down.seconds", positive=True),
        wind_down_snoozes=whole(wind.get("snoozes", 0), "wind_down.snoozes"),
        wind_down_snooze_seconds=seconds(wind.get("snooze_seconds", 0), "wind_down.snooze_seconds"),
        wind_down_reminder_line=text(wind.get("reminder_line"), "wind_down.reminder_line", allow_empty=True),
        wind_down_reminder_before=seconds(wind.get("reminder_before", 0), "wind_down.reminder_before"),
        goodnight_level=whole(wind.get("goodnight_level"), "wind_down.goodnight_level", low=1),
        false_alarm_seconds=seconds(raw.get("false_alarm_seconds", 5), "false_alarm_seconds"),
        tick_seconds=seconds(raw.get("tick_seconds", 0.5), "tick_seconds", positive=True),
        act_confidence=act_confidence, direct_confidence=direct_confidence, belief=belief,
        path=where, raw=raw)
    if "refocus" in ladders and config.break_offer_level not in ladders["refocus"].reaction:
        raise fail(f"break.offer_level {config.break_offer_level} is not a refocus level "
                   f"{list(ladders['refocus'].levels)}.")
    if "bedtime" in ladders:
        bed = ladders["bedtime"]
        if config.wind_down_offer_level != bed.top:
            raise fail(f"wind_down.offer_level ({config.wind_down_offer_level}) must be the top bedtime "
                       f"nudge level in ladders.bedtime.reaction ({bed.top}): after it, only the timer "
                       "leads to goodnight.")
        if config.goodnight_level <= bed.top:
            raise fail("wind_down.goodnight_level must be above the bedtime nudge levels.")
    if config.wind_down_reminder_before >= config.wind_down_seconds:
        raise fail("wind_down.reminder_before must be shorter than wind_down.seconds.")
    return config


def _belief(raw: Any, fail: Any, probability: Any, seconds: Any, whole: Any) -> Optional[BeliefConfig]:
    """The `hmm:` section (A19). Missing: None, the hmm mode uses A16's confidence gate."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise fail("hmm must be a list of settings (decisions, accumulate, keep, reset, wait, ...).")
    allowed = {"decisions", "accumulate", "keep", "hold_if_focused", "reset", "wait", "ambiguous", "switch",
               "intensity", "evidence_top"}
    bad = set(raw) - allowed
    if bad:
        raise fail(f"hmm: unknown key(s) {sorted(bad)}; allowed: {sorted(allowed)}.")

    def sub(name: str, keys: set) -> Dict[str, Any]:
        value = raw.get(name) or {}
        if not isinstance(value, dict):
            raise fail(f"hmm.{name} must be a list of settings.")
        extra = set(value) - keys
        if extra:
            raise fail(f"hmm.{name}: unknown key(s) {sorted(extra)}; allowed: {sorted(keys)}.")
        return value

    d = BeliefConfig()
    decisions = raw.get("decisions", d.decisions)
    if decisions not in DECISIONS:
        raise fail(f'hmm.decisions must be {" or ".join(DECISIONS)}, not {decisions!r}.')
    acc = sub("accumulate", {"floor", "needed"})
    needed_raw = acc.get("needed", d.needed)
    if not isinstance(needed_raw, dict) or set(needed_raw) != {"refocus", "bedtime", "very_drowsy"}:
        raise fail("hmm.accumulate.needed must give refocus, bedtime and very_drowsy (probability-seconds).")
    needed = {k: seconds(v, f"hmm.accumulate.needed.{k}", positive=True) for k, v in needed_raw.items()}
    reset = sub("reset", {"focused", "seconds"})
    wait = sub("wait", {"uncertainty", "top_two_gap", "consider", "margin"})
    amb = sub("ambiguous", {"each", "needed", "long_work"})
    switch = sub("switch", {"step_back", "max_level"})
    inten = sub("intensity", {"scale", "min", "full_at"})
    scale = inten.get("scale", d.intensity_scale)
    if not isinstance(scale, bool):
        raise fail(f"hmm.intensity.scale must be true or false, not {scale!r}.")
    out = BeliefConfig(
        decisions=decisions,
        floor=probability(acc.get("floor", d.floor), "hmm.accumulate.floor"),
        needed=needed,
        keep=probability(raw.get("keep", d.keep), "hmm.keep"),
        hold_if_focused=probability(raw.get("hold_if_focused", d.hold_if_focused), "hmm.hold_if_focused"),
        reset_focused=probability(reset.get("focused", d.reset_focused), "hmm.reset.focused"),
        reset_seconds=seconds(reset.get("seconds", d.reset_seconds), "hmm.reset.seconds"),
        wait_uncertainty=probability(wait.get("uncertainty", d.wait_uncertainty), "hmm.wait.uncertainty"),
        wait_gap=probability(wait.get("top_two_gap", d.wait_gap), "hmm.wait.top_two_gap"),
        wait_consider=probability(wait.get("consider", d.wait_consider), "hmm.wait.consider"),
        wait_margin=_margin(wait.get("margin", d.wait_margin), fail),
        ambiguous_each=probability(amb.get("each", d.ambiguous_each), "hmm.ambiguous.each"),
        ambiguous_needed=seconds(amb.get("needed", d.ambiguous_needed), "hmm.ambiguous.needed", positive=True),
        long_work=seconds(amb.get("long_work", d.long_work), "hmm.ambiguous.long_work"),
        switch_step_back=whole(switch.get("step_back", d.switch_step_back), "hmm.switch.step_back"),
        switch_max_level=whole(switch.get("max_level", d.switch_max_level), "hmm.switch.max_level", low=1),
        intensity_scale=scale,
        intensity_min=probability(inten.get("min", d.intensity_min), "hmm.intensity.min"),
        intensity_full_at=probability(inten.get("full_at", d.intensity_full_at), "hmm.intensity.full_at"),
        evidence_top=whole(raw.get("evidence_top", d.evidence_top), "hmm.evidence_top"))
    if out.ambiguous_each * 2 <= out.floor:
        raise fail(f"hmm.ambiguous.each ({out.ambiguous_each}) x 2 must be above hmm.accumulate.floor "
                   f"({out.floor}): otherwise an ambiguous belief can never build evidence.")
    if out.reset_focused <= 0.5:
        raise fail(f"hmm.reset.focused must be above 0.5 (more likely focused than not), not {out.reset_focused}.")
    return out


def _margin(value: Any, fail: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value < 0.5:
        raise fail(f"hmm.wait.margin must be a number from 0 (no margin) to below 0.5, like 0.05, not {value!r}.")
    return float(value)


def check_library(config: EscalationConfig, library: Any) -> None:
    """Every level the config uses exists in behaviors/, and every line too.
    Raises EscalationConfigError naming what is missing."""
    have = library.ladders()
    for name, ladder in config.ladders.items():
        levels = have.get(name)
        if levels is None:
            raise EscalationConfigError(f"{config.path}: there is no {name} ladder in behaviors/.")
        wanted = set(ladder.levels) | {0}
        if name == "bedtime":
            wanted.add(config.goodnight_level)
        missing = sorted(wanted - set(levels))
        if missing:
            raise EscalationConfigError(
                f"{config.path}: ladders.{name} uses level(s) {missing}, but behaviors/{name}.yaml "
                f"has levels {levels}.")
    if config.approving_nod and config.approving_nod not in library.behaviors:
        raise EscalationConfigError(
            f'{config.path}: focused.approving_nod "{config.approving_nod}" is not a behaviour in behaviors/.')
    for label, line in (("break.welcome_line", config.break_welcome_line),
                        ("wind_down.reminder_line", config.wind_down_reminder_line)):
        if line and line not in library.lines:
            raise EscalationConfigError(
                f'{config.path}: {label} "{line}" is not a line in behaviors/lines.yaml.')
