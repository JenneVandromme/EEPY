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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REFOCUS = {1: 0.15, 2: 0.25, 3: 0.45, 4: 0.55}          # P(back to focus) per level
BEDTIME = {1: 0.03, 2: 0.06, 3: 0.15, 4: 0.25, 5: 0.6}   # P(goes to bed) per level
SNOOZE = {3: 0.10, 4: 0.25, 5: 0.10}                     # spoken levels: P(snooze)
DISMISS = {3: 0.05, 4: 0.10, 5: 0.05}                    # spoken levels: P(dismiss)
DIRECT_BONUS = 0.05
LATE_BEDTIME = 1.5                                       # bedtime nudges after 23:00 work better
PERSONAS = {"average": (1.0, 1.0), "responsive": (1.5, 0.5), "stubborn": (0.4, 2.0)}  # (comply x, refuse x)
SMOOTH = 0.15                                            # the belief's step towards the truth, per second
PERSONAS_FILE = Path(__file__).with_name("personas.yaml")  # personas with hidden preferences (A24)


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


def load_personas(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """The personas with hidden preferences (eepy/bandit/personas.yaml)."""
    import yaml

    data = yaml.safe_load((path or PERSONAS_FILE).read_text(encoding="utf-8")) or {}
    return dict(data.get("personas") or {})


def _response(persona: Any, ladder: str, level: int, tone: Optional[str], hour: float,
              goodnight_level: int) -> Tuple[float, float, float]:
    """(comply, snooze, dismiss) for this nudge: from a persona table, or the built-in tables
    scaled by a named persona (average / responsive / stubborn)."""
    late = hour >= 23 or hour < 5
    if isinstance(persona, dict):
        table = persona.get("bedtime_late") if ladder == "bedtime" and late and persona.get("bedtime_late") \
            else persona.get(ladder) or {}
        entry = table.get(level) or table.get(str(level)) or {}
        return float(entry.get("comply", 0.0)), float(entry.get("snooze", 0.0)), float(entry.get("dismiss", 0.0))
    comply_x, refuse_x = PERSONAS[persona]
    if ladder == "refocus":
        comply = (REFOCUS.get(level, 0.3) + (DIRECT_BONUS if tone == "direct" else 0.0)) * comply_x
    else:
        comply = BEDTIME.get(level, 0.1) * comply_x * (LATE_BEDTIME if late else 1.0)
    if level < 3:
        return min(0.95, comply), 0.0, 0.0
    return min(0.95, comply), SNOOZE.get(level, 0.0) * refuse_x, DISMISS.get(level, 0.0) * refuse_x


def run_responsive(spec: str, seed: int = 1, persona: Any = "average", start_hour: float = 21.0,
                   config: Any = None, library: Any = None, calibrated: bool = True,
                   policy: Any = None, learner: Any = None) -> Tuple[Any, float]:
    """Run the escalation against a reactive user. Returns (the simulation's Recorder, t0).

    persona: "average" / "responsive" / "stubborn", or a table from personas.yaml.
    policy:  who chooses WHAT (default: config/escalation.yaml's, i.e. the fixed escalation).
    learner: something with observe(RewardEvent) -- e.g. the bandit -- fed each nudge's reward
             as soon as it is known (online, as live).
    The Recorder also gets `truth`: (t, decision_id, what the user was really doing) per nudge."""
    from eepy.behaviors.loader import load_library

    from eepy.interaction.actuator import SimActuator
    from eepy.interaction.config import check_library, load
    from eepy.interaction.engine import EscalationEngine
    from eepy.interaction.policy import make_policy
    from eepy.interaction.simulate import Line, Recorder, VirtualClock, belief_estimate, parse_spec

    if not isinstance(persona, dict) and persona not in PERSONAS:
        raise ValueError(f"persona must be one of {', '.join(PERSONAS)}, or a table from personas.yaml")
    scenario = parse_spec(spec)
    if any("@" in s or s in ("accepted", "snoozed", "dismissed") for s, _ in scenario.segments):
        raise ValueError("a reactive user's timeline is plain states and times; it answers by itself")
    config = config or load()
    library = library or load_library()
    check_library(config, library)
    rng = random.Random(seed)
    clock = VirtualClock(0.0)
    recorder = Recorder()
    recorder.truth = []
    hour_of = lambda t: (start_hour + t / 3600.0) % 24.0          # noqa: E731
    engine = EscalationEngine(config, policy or make_policy(config), SimActuator(library, clock), recorder,
                              hour=hour_of)
    engine.session_started(0.0)
    tracker = None
    if learner is not None:
        from .rewards import RewardTracker, load_config as load_rewards, sim_wall

        tracker = RewardTracker(load_rewards(), config, emit=learner.observe)
        wall = lambda t: sim_wall(t, 0.0, start_hour)               # noqa: E731
        tracker.on_record({"type": "session_start", "t_mono": 0.0, "t_wall": wall(0.0), "session": f"sim-{seed}"})

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
            if tracker is not None:
                tracker.on_record(dict(f, type=kind, t_mono=te, t_wall=wall(te)))
            if kind != "nudge_decision":
                continue
            recorder.truth.append((te, f.get("decision_id"), actual))
            ladder, level, tone = f["ladder"], int(f["level"]), f.get("tone")
            window = f.get("reaction_window") or 60.0
            comply, snooze, dismiss = _response(persona, ladder, level, tone, hour_of(te), config.goodnight_level)
            r = rng.random()
            if ladder == "refocus" and actual == "distracted" and r < comply:
                start = te + rng.uniform(3.0, max(4.0, 0.6 * window))
                stays = rng.random() < 0.7
                override = Override("focused", start, segment_end if stays else start + rng.uniform(60, 180))
                if level == config.break_offer_level:
                    replies.append((start - 1.0, "accepted"))
                continue
            if ladder == "bedtime" and actual in ("drowsy", "very_drowsy") and r < comply:
                later = te + rng.uniform(5.0, 30.0)
                if level >= config.goodnight_level:
                    replies.append((later - 2.0, "accepted"))
                gone_at = later if gone_at is None else min(gone_at, later)
                continue
            q = rng.random()
            if q < snooze:
                replies.append((te + rng.uniform(2.0, 8.0), "snoozed"))
            elif q < snooze + dismiss:
                replies.append((te + rng.uniform(2.0, 8.0), "dismissed"))
        replies.sort()
        seen = len(recorder.events)
        t += 1.0
    clock.now = end
    engine.session_ended(end)
    if tracker is not None:
        for kind, te, f in recorder.events[seen:]:
            tracker.on_record(dict(f, type=kind, t_mono=te, t_wall=wall(te)))
        tracker.finish(end)
    return recorder, 0.0
