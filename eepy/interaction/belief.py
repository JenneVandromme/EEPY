"""Belief-based decisions for the hmm mode (A19): WHEN the HMM's belief is enough to act.

The engine (engine.py) asks this tracker instead of "the shown state has lasted N seconds
and is above 70 %". Fed with every estimate, it keeps, per ladder, the evidence so far:

    evidence += (P(family) - floor) * dt        kept between 0 and what is needed

so a confident belief fills it fast, a borderline one slowly, and a coin flip never
(refocus = distracted, bedtime = drowsy + very drowsy). It also answers: is the belief too
unclear to act on (wait), is it ambiguous between distracted and drowsy, may a running
ladder go on (keep), and is the person back to focused (reset).

All numbers: config/escalation.yaml, `hmm:` (EscalationConfig.belief). Plain words:
eepy/interaction/README.md; the choices and limits: docs/A19_BELIEF_DECISIONS.md.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import BeliefConfig

LADDERS = ("refocus", "bedtime")
BELIEF_STATES = ("focused", "distracted", "drowsy", "very_drowsy")
NAME = {"focused": "focused", "distracted": "distracted", "drowsy": "drowsy", "very_drowsy": "very drowsy"}
FAMILY_NAME = {"refocus": "distracted", "bedtime": "drowsy", "ambiguous": "distracted or drowsy"}
MAX_DT = 2.0          # a longer gap between estimates counts as 2 s (the HMM itself bridges gaps)


def is_belief(estimate: Dict[str, Any]) -> bool:
    """An HMM estimate that carries its belief (older vision programs send no probs)."""
    return estimate.get("estimator") == "hmm" and isinstance(estimate.get("probs"), dict)


def belief_text(probs: Dict[str, float], n: int = 2) -> str:
    """ "distracted 48 %, focused 41 %": the n most likely states."""
    top = sorted(BELIEF_STATES, key=lambda s: -float(probs.get(s, 0.0)))[:n]
    return ", ".join(f"{NAME[s]} {float(probs.get(s, 0.0)):.0%}" for s in top)


class BeliefTracker:
    def __init__(self, config: BeliefConfig, act: Dict[str, float]) -> None:
        self.config = config
        self.act = act                                  # confidence.act: the start threshold per ladder
        self.reset()

    def reset(self) -> None:
        self.evidence: Dict[str, float] = {"refocus": 0.0, "bedtime": 0.0, "ambiguous": 0.0}
        self.probs: Dict[str, float] = {}
        self.estimate: Dict[str, Any] = {}
        self.t: Optional[float] = None
        self.focused_since: Optional[float] = None
        self.waiting = False                            # unsure() last said "wait" (for its margin)
        self.since: Dict[str, Optional[float]] = {k: None for k in self.evidence}   # evidence building since

    # --- input ---------------------------------------------------------------------------

    def update(self, estimate: Dict[str, Any], now: float) -> None:
        """One estimate. Absent / unknown / no new evidence: the evidence stands still."""
        if not is_belief(estimate):
            return
        self.estimate = estimate
        dt = 1.0 if self.t is None else min(max(now - self.t, 0.0), MAX_DT)
        self.t = now
        if estimate.get("state") in ("absent", "unknown") or estimate.get("updated") is False:
            self.focused_since = None
            return
        self.probs = {s: float(estimate["probs"].get(s, 0.0)) for s in BELIEF_STATES}
        c = self.config
        for ladder in LADDERS:
            self._add(ladder, self.p(ladder), dt, self.cap(ladder))
        both = self.p("refocus") >= c.ambiguous_each and self.p("bedtime") >= c.ambiguous_each
        self._add("ambiguous", self.p("refocus") + self.p("bedtime") if both else 0.0, dt, c.ambiguous_needed)
        if self.probs["focused"] >= c.reset_focused:
            self.focused_since = now if self.focused_since is None else self.focused_since
        else:
            self.focused_since = None

    def _add(self, key: str, p: float, dt: float, cap: float) -> None:
        self.evidence[key] = min(cap, max(0.0, self.evidence[key] + (p - self.config.floor) * dt))
        if self.evidence[key] <= 0.0:
            self.since[key] = None
        elif self.since[key] is None:
            self.since[key] = (self.t or 0.0) - dt

    def clear(self, *keys: str) -> None:
        for key in keys:
            self.evidence[key], self.since[key] = 0.0, None

    def built_for(self, key: str, now: float) -> float:
        """How long this evidence has been building (s)."""
        since = self.since.get(key)
        return 0.0 if since is None else max(0.0, now - since)

    # --- questions -----------------------------------------------------------------------

    def p(self, ladder: str) -> float:
        if ladder == "refocus":
            return self.probs.get("distracted", 0.0)
        if ladder == "bedtime":
            return self.probs.get("drowsy", 0.0) + self.probs.get("very_drowsy", 0.0)
        return self.probs.get(ladder, 0.0)

    def cap(self, ladder: str) -> float:
        n = self.config.needed
        return n["refocus"] if ladder == "refocus" else max(n["bedtime"], n["very_drowsy"])

    def fast_very_drowsy(self) -> bool:
        """The shorter bedtime path: the HMM says very drowsy AND the features are calibrated."""
        return self.estimate.get("state") == "very_drowsy" and bool(self.estimate.get("calibrated"))

    def needed(self, ladder: str) -> float:
        n = self.config.needed
        if ladder == "bedtime":
            return n["very_drowsy"] if self.fast_very_drowsy() else n["bedtime"]
        if ladder == "ambiguous":
            return self.config.ambiguous_needed
        return n["refocus"]

    def progress(self, key: str) -> float:
        return min(1.0, self.evidence[key] / self.needed(key))

    def ready(self, ladder: str) -> bool:
        """Start (or switch to) this ladder: enough evidence AND sure enough right now."""
        return (self.evidence[ladder] >= self.needed(ladder) - 1e-9
                and self.p(ladder) >= self.act.get(ladder, 0.0))

    def keeps(self, ladder: str) -> bool:
        """A running ladder may go on climbing: the lower threshold (hysteresis), and focus not
        coming back yet."""
        return self.p(ladder) >= self.config.keep and not self.focus_returning()

    def focus_returning(self) -> bool:
        return self.probs.get("focused", 0.0) >= self.config.hold_if_focused

    def ambiguous(self) -> bool:
        """Distracted and drowsy both moderately likely, neither ladder ready."""
        c = self.config
        return (self.p("refocus") >= c.ambiguous_each and self.p("bedtime") >= c.ambiguous_each
                and not any(self.ready(x) for x in LADDERS))

    def ambiguous_ready(self) -> bool:
        return self.ambiguous() and self.evidence["ambiguous"] >= self.config.ambiguous_needed - 1e-9

    def building(self) -> List[str]:
        """The ladders whose family is above the floor right now (something to watch)."""
        return [x for x in LADDERS if self.p(x) >= self.config.floor]

    def considered(self) -> List[str]:
        """The ladders likely enough that an unclear belief is worth a deliberate wait."""
        return [x for x in LADDERS if self.p(x) >= self.config.wait_consider]

    def unsure(self) -> Optional[str]:
        """Why the belief is too unclear to act on, or None. Once waiting, the belief must be
        `wait.margin` clearer before the wait ends (hysteresis, as for the ladders)."""
        if not self.probs:
            return None
        c = self.config
        m = c.wait_margin if self.waiting else 0.0
        u = self.estimate.get("uncertainty")
        top = sorted(self.probs.values(), reverse=True)
        why = None
        if u is not None and float(u) > c.wait_uncertainty - m:
            why = f"not sure yet ({belief_text(self.probs)}; uncertainty {float(u):.2f})"
        elif len(top) > 1 and top[0] - top[1] < c.wait_gap + m:
            why = f"not sure yet ({belief_text(self.probs)})"
        self.waiting = why is not None
        return why

    def reset_ready(self, now: float) -> bool:
        return self.focused_since is not None and now - self.focused_since >= self.config.reset_seconds - 1e-9

    def intensity(self, base: float, confidence: Optional[float], ladder: str) -> float:
        """The designer's intensity, scaled by how sure the belief is; never above `base`."""
        c = self.config
        if not c.intensity_scale or confidence is None:
            return base
        low = self.act.get(ladder, c.keep)
        span = max(1e-6, c.intensity_full_at - low)
        share = min(1.0, max(0.0, (float(confidence) - low) / span))
        return round(base * (c.intensity_min + (1.0 - c.intensity_min) * share), 3)

    def view(self) -> Dict[str, Any]:
        """For the panel and the log: how far each ladder's evidence is."""
        return {k: round(self.progress(k), 3) for k in self.evidence}
