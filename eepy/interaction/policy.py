"""The NudgePolicy interface: given the situation, what should EEPY do now?

The EscalationEngine (engine.py) decides WHEN a nudge may happen -- onset
delays, reaction windows, cooldowns, guards, breaks, the wind-down timer -- and
then asks the policy WHAT to do: which ladder level, how strongly, in which
tone. This task's policy is a fixed escalation (one level up after each
nudge that got no reaction). The contextual bandit (A24) implements the same
interface and replaces only this choice; timing, outcomes and logging stay.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Protocol, Tuple

from .config import EscalationConfig


@dataclass(frozen=True)
class NudgeRecord:
    """One nudge of this session, with its outcome once known."""

    decision_id: int
    t: float
    ladder: str
    level: int
    outcome: Optional[str] = None


@dataclass(frozen=True)
class NudgeContext:
    now: float
    estimate: Dict[str, Any]          # the state estimate: state, reason, pending, calibrated (+ HMM: confidence ...)
    state_seconds: float              # how long the state's role has lasted
    role: str                         # study_buddy | refocus | bedtime | paused | none
    ladder: Optional[str]             # the ladder of this episode
    level: Optional[int]              # the last level played in this episode (None: none yet)
    last_outcome: Optional[str]       # of the previous nudge in this episode
    restarting: bool                  # after a top-of-ladder cooldown
    nudges_session: int
    nudges_last_hour: int
    since_last_nudge: Optional[float]
    since_break: Optional[float]
    since_calibration: Optional[float]
    session_seconds: float
    history: Tuple[NudgeRecord, ...] = ()

    @property
    def state(self) -> str:
        return str(self.estimate.get("state") or "unknown")

    @property
    def calibrated(self) -> bool:
        return bool(self.estimate.get("calibrated"))

    @property
    def confidence(self) -> Optional[float]:
        """HMM: P(the reported state's family); None for the rules."""
        value = self.estimate.get("confidence")
        return None if value is None else float(value)

    @property
    def estimator(self) -> str:
        return str(self.estimate.get("estimator") or "rules")

    def summary(self) -> Dict[str, Any]:
        """For the nudge_decision event: what the policy saw (no history)."""
        out = {k: v for k, v in asdict(self).items() if k not in ("history", "estimate", "now")}
        out["state"] = self.state
        out["calibrated"] = self.calibrated
        out["reason_state"] = self.estimate.get("reason")
        out["estimator"] = self.estimator
        for key in ("confidence", "uncertainty", "most_likely"):
            if self.estimate.get(key) is not None:
                out[key] = self.estimate[key]
        for key in ("state_seconds", "since_last_nudge", "since_break", "since_calibration",
                    "session_seconds"):
            if out.get(key) is not None:
                out[key] = round(float(out[key]), 1)
        return out


@dataclass(frozen=True)
class Action:
    kind: str                         # "wait" | "play"
    ladder: Optional[str] = None
    level: Optional[int] = None
    intensity: float = 1.0
    tone: str = "hedged"              # hedged | direct
    reason: str = ""                  # plain language: panel + log
    propensity: float = 1.0           # the chance the policy had of choosing this (A24)
    extra: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def wait(reason: str) -> "Action":
        return Action("wait", reason=reason)


class NudgePolicy(Protocol):
    name: str

    def decide(self, context: NudgeContext) -> Action:
        """Called by the engine only when a nudge is due and allowed."""
        ...


class FixedEscalation:
    """Start low; one level up after each nudge that got no reaction; at the top, wait
    (the engine then cools down and comes back with restarting=True)."""

    name = "fixed_escalation"

    def __init__(self, config: EscalationConfig) -> None:
        self.config = config

    def decide(self, context: NudgeContext) -> Action:
        if context.ladder is None:
            return Action.wait("no ladder for this state")
        ladder = self.config.ladders[context.ladder]
        very = context.state == "very_drowsy"
        if context.level is None:
            start = ladder.restart_level if context.restarting else ladder.start_level
            if very and ladder.start_level_very_drowsy is not None:
                start = max(start, ladder.start_level_very_drowsy)
            level, why = start, ("starting again after a pause" if context.restarting
                                 else f"{context.state.replace('_', ' ')} for {context.state_seconds:.0f} s")
        else:
            if context.level >= ladder.top:
                return Action.wait("top of the ladder")
            level = context.level + 1
            if very and ladder.start_level_very_drowsy is not None:
                level = max(level, ladder.start_level_very_drowsy)
            why = f"no reaction to level {context.level}"
        repeat = context.level is not None and context.last_outcome == "no_reaction"
        conf = context.confidence
        sure = conf is None or conf >= self.config.direct_confidence     # the rules: no confidence
        tone = "direct" if context.calibrated and (very or repeat) and sure else "hedged"
        return Action("play", context.ladder, level, ladder.intensity.get(level, 1.0), tone,
                      reason=f"{context.ladder} level {level}: {why}")
