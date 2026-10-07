"""The common interface of every user-state estimator: the rules now (A13), the
HMM later (A16). Everything downstream -- escalation (A14), the panel, logging,
replay -- uses only this, so switching between rules and HMM is just choosing
another estimator.

    estimator = make_estimator("rules")
    estimate = estimator.update(features)       # one Features per second
    estimate.state, estimate.reasons, estimate.scores

Standard library only, Python 3.9 (the vision environment).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import import_module
from typing import Any, Callable, Dict, List, Optional

try:                                    # Python 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

STATES = ("focused", "distracted", "drowsy", "very_drowsy", "absent", "unknown")

# What each state means, in words the panel shows (and the docs repeat).
MEANING = {
    "focused": "working: face on the screen or notes, no signs of drowsiness",
    "distracted": "looking away from the work (phone, window, ...) for a while",
    "drowsy": "signs of sleepiness: eyes often closed, long blinks, yawning, nodding",
    "very_drowsy": "strong signs of sleepiness: eyes closed for long, repeatedly nodding off",
    "absent": "no face in view: left the desk or turned fully away",
    "unknown": "not enough reliable camera data to say",
}


@dataclass(frozen=True)
class Reason:
    """One piece of evidence: a rule that fired, with the value that made it fire."""

    rule: str                       # rule id from config/state_rules.yaml
    state: str
    text: str                       # plain language, for the panel
    feature: Optional[str] = None
    value: Any = None
    threshold: Any = None
    points: Optional[float] = None


@dataclass(frozen=True)
class StateEstimate:
    t: float                          # on the logging clock (eepy.logs.clock)
    state: str                        # one of STATES
    scores: Dict[str, float]          # rules: one-hot; HMM: probabilities
    reasons: List[Reason]             # why: the evidence for `state`
    calibrated: bool                  # were the features relative to this person?
    skipped: List[str] = field(default_factory=list)   # evidence ignored, and why
    since: float = 0.0                # when the current state began
    pending: Optional[str] = None     # a state waiting out its dwell time
    estimator: str = "rules"
    values: Dict[str, Any] = field(default_factory=dict)   # the key feature values

    @property
    def reason_text(self) -> str:
        """The main reason in one line, for the panel's status bar."""
        return "; ".join(r.text for r in self.reasons[:2]) or MEANING[self.state]

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["reason_text"] = self.reason_text
        return out


class StateEstimator(Protocol):
    name: str

    def update(self, features: "Any") -> StateEstimate:
        """One step: the newest Features (about once a second) -> the state."""
        ...

    def reset(self) -> None:
        """Forget the history (a new session)."""
        ...


# Estimators by name. A16 adds "hmm" here; the mode switch picks by name.
ESTIMATORS: Dict[str, Callable[[], StateEstimator]] = {}


def register(name: str, factory: Callable[[], StateEstimator]) -> None:
    ESTIMATORS[name] = factory


def make_estimator(name: str = "rules") -> StateEstimator:
    if name not in ESTIMATORS:
        import_module(".rules", __package__)      # registers "rules"
    if name not in ESTIMATORS:
        raise ValueError(f'No state estimator called "{name}". Available: {", ".join(sorted(ESTIMATORS))}.')
    return ESTIMATORS[name]()


def for_mode(mode: Optional[str]) -> str:
    """Which estimator a session mode uses: the HMM modes use "hmm" once it exists
    (A16); everything else -- and the HMM modes until then -- uses the rules."""
    if mode in ("hmm", "hmm_bandit") and "hmm" in ESTIMATORS:
        return "hmm"
    return "rules"
