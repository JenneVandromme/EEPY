"""A simulated user who REACTS to nudges (A22), so the bandit (A24) has something to learn from.

The synthetic dataset's users follow a fixed timeline: a nudge never changes what they do, so
every nudge ends "no reaction". This user follows a timeline too ("focused 3m, distracted 6m,
drowsy 10m"), but each nudge may change it:

  refocus, while distracted: back to focused after a few seconds, with a chance that grows with
                             the level (and a little for the direct tone); sometimes only for a
                             while before drifting off again
  bedtime, while drowsy:     goes to bed (leaves; confirms the goodnight first), likelier at
                             higher levels and after 23:00
  spoken levels (3+):        may snooze or dismiss instead

The belief moves smoothly towards what the user really does (the HMM needs time too), so a
user who drifts back by themselves shows a rising P(focused) before a nudge -- exactly what the
recovery discount is for.

THE NUMBERS ARE MADE UP. They exist to exercise the bandit's code and to check that it can learn
a known pattern; they are not evidence about real users.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

REFOCUS = {1: 0.15, 2: 0.25, 3: 0.45, 4: 0.55}          # P(back to focus) per level
BEDTIME = {1: 0.03, 2: 0.06, 3: 0.15, 4: 0.25, 5: 0.6}   # P(goes to bed) per level
SNOOZE = {3: 0.10, 4: 0.25, 5: 0.10}                     # spoken levels: P(snooze)
DISMISS = {3: 0.05, 4: 0.10, 5: 0.05}                    # spoken levels: P(dismiss)
DIRECT_BONUS = 0.05
LATE_BEDTIME = 1.5                                       # bedtime nudges after 23:00 work better
PERSONAS = {"average": (1.0, 1.0), "responsive": (1.5, 0.5), "stubborn": (0.4, 2.0)}  # (comply x, refuse x)
SMOOTH = 0.15                                            # the belief's step towards the truth, per second


@dataclass
class Override:
    state: str
    start: float
    until: float


def _probs(state: str) -> Dict[str, float]:
    sure = 0.95
    out = {s: 0.0 for s in ("focused", "distracted", "drowsy", "very_drowsy")}
    out[state] = sure
    out["focused" if state != "focused" else "distracted"] += 1 - sure
    return out


def run_responsive(spec: str, seed: int = 1, persona: str = "average", start_hour: float = 21.0,
                   config: Any = None, library: Any = None, calibrated: bool = True) -> Tuple[Any, float]:
    """Run the escalation against a reactive user. Returns (the simulation's Recorder, t0)."""
    from eepy.behaviors.loader import load_library

    from eepy.interaction.actuator import SimActuator
    from eepy.interaction.config import check_library, load
    from eepy.interaction.engine import EscalationEngine
    from eepy.interaction.policy import FixedEscalation
    from eepy.interaction.simulate import Line, Recorder, VirtualClock, belief_estimate, parse_spec

    if persona not in PERSONAS:
        raise ValueError(f"persona must be one of {', '.join(PERSONAS)}")
    comply, refuse = PERSONAS[persona]
    scenario = parse_spec(spec)
    if any("@" in s or s in ("accepted", "snoozed", "dismissed") for s, _ in scenario.segments):
        raise ValueError("a reactive user's timeline is plain states and times; it answers by itself")
    config = config or load()
    library = library or load_library()
    check_library(config, library)
    rng = random.Random(seed)
    clock = VirtualClock(0.0)
    recorder = Recorder()
    engine = EscalationEngine(config, FixedEscalation(config), SimActuator(library, clock), recorder)
    engine.session_started(0.0)

    bounds: List[Tuple[float, float, str]] = []
    t = 0.0
    for state, seconds in scenario.segments:
        bounds.append((t, t + seconds, state))
        t += seconds
    end = t
    override: Optional[Override] = None
    replies: List[Tuple[float, str]] = []
    gone_at: Optional[float] = None
    probs = _probs("focused")
    seen = 0
    t = 0.0
    while t < end:
        clock.now = t
        base = next((s for a, b, s in bounds if a <= t < b), bounds[-1][2])
        segment_end = next((b for a, b, s in bounds if a <= t < b), end)
        if gone_at is not None and t >= gone_at:
            actual = "absent"
        elif override is not None and override.start <= t < override.until:
            actual = override.state
        else:
            actual = base
        if actual in ("absent", "unknown"):
            estimate = belief_estimate(actual, calibrated, probs)
        else:
            target = _probs(actual)
            probs = {k: probs[k] + SMOOTH * (target[k] - probs[k]) for k in probs}
            rest = {k: round(v, 3) for k, v in probs.items() if k != "focused" and v > 0.0005}
            estimate = belief_estimate("+".join(f"{k}@{v}" for k, v in rest.items()) or "focused", calibrated, probs)
        while replies and replies[0][0] <= t:
            _, kind = replies.pop(0)
            recorder.lines.append(Line(t, f"[user] {kind}: {engine.reply(kind, t)}"))
        engine.on_state(estimate, t)
        for kind, te, f in recorder.events[seen:]:
            if kind != "nudge_decision":
                continue
            ladder, level, tone = f["ladder"], int(f["level"]), f.get("tone")
            window = f.get("reaction_window") or 60.0
            hour = start_hour + te / 3600.0
            r = rng.random()
            if ladder == "refocus" and actual == "distracted":
                p = min(0.95, (REFOCUS.get(level, 0.3) + (DIRECT_BONUS if tone == "direct" else 0.0)) * comply)
                if r < p:
                    start = te + rng.uniform(3.0, max(4.0, 0.6 * window))
                    stays = rng.random() < 0.7
                    override = Override("focused", start, segment_end if stays else start + rng.uniform(60, 180))
                    if level == config.break_offer_level:
                        replies.append((start - 1.0, "accepted"))
                    continue
            if ladder == "bedtime" and actual in ("drowsy", "very_drowsy"):
                p = min(0.95, BEDTIME.get(level, 0.1) * comply * (LATE_BEDTIME if hour >= 23 else 1.0))
                if r < p:
                    later = te + rng.uniform(5.0, 30.0)
                    if level >= config.goodnight_level:
                        replies.append((later - 2.0, "accepted"))
                    gone_at = later if gone_at is None else min(gone_at, later)
                    continue
            if level >= 3:
                q = rng.random()
                if q < SNOOZE.get(level, 0.0) * refuse:
                    replies.append((te + rng.uniform(2.0, 8.0), "snoozed"))
                elif q < (SNOOZE.get(level, 0.0) + DISMISS.get(level, 0.0)) * refuse:
                    replies.append((te + rng.uniform(2.0, 8.0), "dismissed"))
        replies.sort()
        seen = len(recorder.events)
        t += 1.0
    clock.now = end
    engine.session_ended(end)
    return recorder, 0.0
