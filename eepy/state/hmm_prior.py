"""A starting belief per session for the HMM (A16 hook; off by default).

A session folder may hold a `prior.json` (written by the panel's --session-prior
option; later by a sleepiness question or an LLM). It may set only two things:

    initial                 the belief at the session start (4 probabilities)
    drowsiness_multiplier   how much faster (> 1) or slower (< 1) drowsiness sets in

    {"source": "kss", "initial": {"focused": 0.5, "distracted": 0.1, "drowsy": 0.3,
     "very_drowsy": 0.1}, "drowsiness_multiplier": 2.0, "inputs": {"kss": 7}}

`resolve()` checks it, clips it to the bounds in config/state_hmm.yaml, and moves it
only part of the way from the hand-set values (the per-source `trust`). It never
raises: anything wrong falls back to the hand-set values, with the reason.

Standard library only, Python 3.9.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .hmm_params import HIDDEN, PRIOR_SOURCES, HMMParams

SOURCES = PRIOR_SOURCES
INPUT_KEYS = ("kss", "hours_slept", "exam_tomorrow", "local_hour", "minutes_since_wake", "session_number")
ALLOWED_KEYS = ("source", "initial", "drowsiness_multiplier", "inputs", "model_id")
MODEL_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class AppliedPrior:
    source: str                         # what was applied: default unless a prior was used
    requested_source: str
    initial: Dict[str, float]
    drowsiness_multiplier: float
    trust: float = 0.0
    clipped: Tuple[str, ...] = ()
    fell_back: bool = False
    reason: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    model_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["clipped"] = list(self.clipped)
        out["initial"] = {k: round(v, 6) for k, v in self.initial.items()}
        return out


def reported_initial(params: HMMParams) -> List[float]:
    """The parameter file's initial belief over the four reported states (extra hidden states,
    like a drowsiness ladder's "tired", are added to the state they are shown as)."""
    out = [0.0] * len(HIDDEN)
    for value, shown in zip(params.initial, params.reports):
        out[HIDDEN.index(shown)] += value
    return out


def default(params: HMMParams, reason: str = "no prior given", requested: str = "default",
            fell_back: bool = False) -> AppliedPrior:
    return AppliedPrior("default", requested, dict(zip(HIDDEN, reported_initial(params))), 1.0,
                        reason=reason, fell_back=fell_back)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def water_fill(values: List[float], lo: float, hi: float) -> List[float]:
    """Probabilities clipped into [lo, hi] that still add up to 1: clip, then spread the
    difference over the entries that can still move, in proportion to their room
    (hi - x when adding, x - lo when taking away)."""
    x = [min(max(v, lo), hi) for v in values]
    for _ in range(10):
        d = 1.0 - sum(x)
        if abs(d) < 1e-12:
            break
        room = [(hi - v) if d > 0 else (v - lo) for v in x]
        total = sum(room)
        if total <= 0:
            break
        x = [min(max(v + d * r / total, lo), hi) for v, r in zip(x, room)]
    return x


def resolve(raw: Optional[Dict[str, Any]], params: HMMParams) -> AppliedPrior:
    """What prior.json asked for -> what is applied. Never raises."""
    if raw is None:
        return default(params)
    if not isinstance(raw, dict):
        return default(params, "the prior is not a JSON object", fell_back=True)
    if "_error" in raw:
        return default(params, f"prior.json could not be used: {raw['_error']}", fell_back=True)
    requested = raw.get("source") if isinstance(raw.get("source"), str) else "default"

    def fallback(reason: str) -> AppliedPrior:
        return default(params, reason, requested=requested, fell_back=True)

    if not params.prior_enabled:
        return fallback("session priors are switched off in config/state_hmm.yaml (session_prior.enabled)")
    extra = sorted(k for k in raw if k not in ALLOWED_KEYS)
    if extra:
        return fallback(f"a prior may only set {', '.join(ALLOWED_KEYS)}; it also has {', '.join(map(str, extra))}")
    source = raw.get("source")
    if source not in SOURCES:
        return fallback(f'source must be one of {", ".join(SOURCES)}, not "{source}"')
    inputs = raw.get("inputs") or {}
    if not isinstance(inputs, dict):
        return fallback("inputs must be an object")
    for key, value in inputs.items():
        if key not in INPUT_KEYS:
            return fallback(f'input "{key}" is not allowed (allowed: {", ".join(INPUT_KEYS)})')
        if not (isinstance(value, bool) or _finite(value)):
            return fallback(f'input "{key}" must be a number or true/false, not {value!r}')
    model_id = raw.get("model_id", "")
    if model_id and (not isinstance(model_id, str) or not MODEL_ID.match(model_id)):
        return fallback("model_id is at most 64 letters, digits, '.', '_' or '-'")

    lo, hi = params.prior_initial_bounds
    base = reported_initial(params)
    clipped: List[str] = []
    given = raw.get("initial")
    if given is None:
        target = base
    else:
        if not isinstance(given, dict) or set(given) != set(HIDDEN):
            return fallback(f"initial must give exactly {', '.join(HIDDEN)}")
        values = [given[s] for s in HIDDEN]
        if not all(_finite(v) and v >= 0 for v in values) or sum(values) <= 0:
            return fallback("initial must be numbers >= 0 that do not all add up to 0")
        total = float(sum(values))
        normal = [v / total for v in values]
        target = water_fill(normal, lo, hi)
        if any(abs(a - b) > 1e-12 for a, b in zip(normal, target)):
            clipped.append(f"initial (kept within {lo:g}-{hi:g})")
    mult = raw.get("drowsiness_multiplier", 1.0)
    if not _finite(mult) or mult <= 0:
        return fallback(f"drowsiness_multiplier must be a number > 0, not {mult!r}")
    mlo, mhi = params.prior_multiplier_bounds
    if not mlo <= mult <= mhi:
        clipped.append(f"drowsiness_multiplier {mult:g} -> {min(max(mult, mlo), mhi):g}")
        mult = min(max(mult, mlo), mhi)

    trust = params.prior_trust.get(source, 0.0)
    initial = [(1 - trust) * b + trust * t for b, t in zip(base, target)]
    total = sum(initial)
    initial = [v / total for v in initial]
    multiplier = math.exp(trust * math.log(mult))
    return AppliedPrior(source, requested, dict(zip(HIDDEN, initial)), multiplier, trust=trust,
                        clipped=tuple(clipped), fell_back=False,
                        reason=f"{source} prior, moved {trust:.0%} of the way from the hand-set values",
                        inputs=dict(inputs), model_id=model_id or "")
