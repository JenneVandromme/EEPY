"""Simulate the escalation against a scripted state timeline -- no camera, no robot.

    python -m eepy.interaction.simulate phone_break
    python -m eepy.interaction.simulate "focused 5m, distracted 2m, focused 3m"
    python -m eepy.interaction.simulate "drowsy 6m, accepted, drowsy 20m"
    python -m eepy.interaction.simulate --session P03       # a recorded session's states
    python -m eepy.interaction.simulate --list
    python -m eepy.interaction.simulate belief_shift                     # hmm mode: beliefs (A19)
    python -m eepy.interaction.simulate "focused 4m, distracted@0.65 3m, distracted@0.4+drowsy@0.4 3m"
    python -m eepy.interaction.simulate --session P03 --estimator hmm
    python -m eepy.interaction.simulate --synthetic "work 2m, sleepy 6m"   # simulated faces -> HMM (A20)

The states are what the state estimator (A13) would say, once a second. Replies
("accepted", "snoozed", "dismissed") stand for the operator's buttons. It runs in
virtual time (a 30-minute scenario takes a second), with the real behaviours
from behaviors/ and their real lengths; --realtime instead plays it live on the
DryRunBackend (silent; for short scenarios). Prints every decision, level and
outcome over time.

Beliefs (hmm mode, A19): "distracted@0.65" is P(distracted) = 0.65, the rest focused;
"distracted@0.4+drowsy@0.4" mixes. A scenario with a belief (or --belief) runs as HMM
estimates, so config/escalation.yaml's `hmm:` decisions apply; a plain state is then
95 % sure.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import EscalationConfigError, check_library, load
from .engine import REPLIES, EscalationEngine
from .policy import make_policy

STATES = ("focused", "distracted", "drowsy", "very_drowsy", "absent", "unknown")


@dataclass
class Scenario:
    name: str
    description: str
    segments: List[Tuple[str, float]] = field(default_factory=list)   # (state, belief or reply, seconds)
    calibrated: bool = True
    belief: bool = False              # run as HMM estimates (A19); also when a segment is a belief

    @property
    def uses_belief(self) -> bool:
        return self.belief or any("@" in w for w, _ in self.segments)


BELIEF_STATES = ("focused", "distracted", "drowsy", "very_drowsy")
PLAIN_SURE = 0.95                     # a plain state in a belief scenario


def parse_belief(text: str) -> Dict[str, float]:
    """ "distracted@0.45+drowsy@0.4" -> the four probabilities (the rest is focused)."""
    probs = {s: 0.0 for s in BELIEF_STATES}
    for part in text.split("+"):
        name, _, value = part.strip().partition("@")
        name = name.strip().replace(" ", "_")
        try:
            p = float(value)
        except ValueError:
            p = -1.0
        if name not in BELIEF_STATES or not 0 <= p <= 1:
            raise ValueError(f'"{part.strip()}": write a belief as state@probability, like distracted@0.65 '
                             f'(states: {", ".join(BELIEF_STATES)}).')
        probs[name] += p
    rest = 1.0 - sum(probs.values())
    if rest < -1e-9:
        raise ValueError(f'"{text}": the probabilities add up to more than 1.')
    probs["focused"] += max(0.0, rest)
    return probs


def belief_estimate(what: str, calibrated: bool, last: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """A scripted HMM estimate: the belief, the shown state (as the HMM reports it: the most
    likely family; very drowsy when it leads within sleepy), its confidence and uncertainty."""
    if what in ("absent", "unknown"):
        probs = dict(last or {"focused": 1.0, "distracted": 0.0, "drowsy": 0.0, "very_drowsy": 0.0})
        return {"state": what, "reason": f"(scripted: {what})", "pending": None, "calibrated": calibrated,
                "estimator": "hmm", "probs": probs, "confidence": None, "uncertainty": None, "updated": False,
                "say": "", "evidence": []}
    if "@" in what:
        probs = parse_belief(what)
    else:
        probs = {s: (PLAIN_SURE if s == what else 0.0) for s in BELIEF_STATES}
        probs["focused" if what != "focused" else "distracted"] += 1.0 - PLAIN_SURE
    fam = {"focused": probs["focused"], "distracted": probs["distracted"],
           "sleepy": probs["drowsy"] + probs["very_drowsy"]}
    family = max(fam, key=lambda k: fam[k])
    state = family if family != "sleepy" else ("very_drowsy" if probs["very_drowsy"] > probs["drowsy"] else "drowsy")
    entropy = -sum(p * math.log(p) for p in probs.values() if p > 0) / math.log(len(probs))
    return {"state": state, "reason": f"(scripted: {what})", "pending": None, "calibrated": calibrated,
            "estimator": "hmm", "probs": {k: round(v, 3) for k, v in probs.items()},
            "confidence": round(fam[family], 3), "uncertainty": round(entropy, 3), "most_likely": state,
            "updated": True, "say": "",
            "evidence": [{"feature": "scripted", "text": f"scripted belief: {what}", "supports": family,
                          "against": None, "strength": 1.0, "impact": 0.1}]}


def parse_spec(text: str, name: str = "custom") -> Scenario:
    """ "focused 5m, distracted 90s, accepted, drowsy 10m" -> a Scenario."""
    scenario = Scenario(name, text)
    for raw in [p.strip() for p in re.split(r"[,;\n]|->|→", text) if p.strip()]:
        if raw in REPLIES:
            scenario.segments.append((raw, 0.0))
            continue
        match = re.match(r"^([a-z_ @.+\d]+?)\s+([\dmins. ]+)$", raw)
        seconds = _seconds(match.group(2)) if match else None
        if match is None or seconds is None:
            raise ValueError(f'"{raw}": write a state and a time ("distracted 2m", "focused 30s", '
                             f'"drowsy 4m30s"), a belief and a time ("distracted@0.6 2m"), or a reply '
                             f'({", ".join(REPLIES)}).')
        state = match.group(1).strip().replace(" ", "_")
        if "@" in state:
            parse_belief(state)                        # checks it
            scenario.segments.append((state, seconds))
            continue
        if state not in STATES:
            raise ValueError(f'"{state}" is not a state; use one of {", ".join(STATES)}.')
        scenario.segments.append((state, seconds))
    if not any(s in STATES or "@" in s for s, _ in scenario.segments):
        raise ValueError("a scenario needs at least one state with a time.")
    return scenario


def _seconds(text: str) -> Optional[float]:
    """ "5m", "90s", "4m30s", "2 min", "45" -> seconds; None when unreadable."""
    text = text.replace(" ", "").replace("min", "m").replace("sec", "s")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?", text)
    if not match or not any(match.groups()):
        return None
    return float(match.group(1) or 0) * 60 + float(match.group(2) or 0)


def _flicker() -> Scenario:
    s = Scenario("flicker", "focused and distracted every 15 s (never long enough), then very "
                 "drowsy: the onset delay ignores the flicker; very drowsy starts at bedtime level 2")
    s.segments = [("focused", 240.0)] + [x for _ in range(8) for x in (("distracted", 15.0), ("focused", 15.0))]
    s.segments += [("very_drowsy", 240.0), ("focused", 60.0)]
    return s


SCENARIOS: Dict[str, Scenario] = {
    "phone_break": parse_spec("focused 4m, distracted 2m, focused 3m", "phone_break"),
    "takes_the_break": parse_spec("focused 15m, distracted 3m, absent 12m, focused 3m", "takes_the_break"),
    "late_evening": parse_spec("focused 4m, drowsy 4m30s, accepted, drowsy 25m", "late_evening"),
    "flicker": _flicker(),
    # hmm mode (A19): beliefs instead of states
    "belief_sure_vs_borderline": parse_spec(
        "focused 4m, distracted@0.95 1m30s, focused 2m, distracted@0.72 2m30s, focused 2m",
        "belief_sure_vs_borderline"),
    "belief_unsure": parse_spec("focused 4m, distracted@0.5 3m, focused 1m", "belief_unsure"),
    "belief_ambiguous": parse_spec("focused 4m, distracted@0.4+drowsy@0.4 3m, focused 1m", "belief_ambiguous"),
    "belief_ambiguous_late": parse_spec("focused 46m, distracted@0.4+drowsy@0.4 3m, focused 1m",
                                        "belief_ambiguous_late"),
    "belief_shift": parse_spec("focused 4m, distracted@0.9 2m, drowsy@0.9 4m, focused 2m", "belief_shift"),
    "belief_wavering": parse_spec("focused 4m, distracted@0.9 50s, distracted@0.6+drowsy@0.15 40s, distracted@0.45+drowsy@0.3 1m30s, "
                                  "distracted@0.9 1m, focused 1m", "belief_wavering"),
}
SCENARIOS["phone_break"].description = (
    "a phone for 2 minutes: the wordless nudges climb until they look back; ladder reset + nod")
SCENARIOS["takes_the_break"].description = (
    "after 15 minutes of work (the break offer's minimum), ignores the whole refocus ladder, then leaves right after the break offer: a break, "
    "welcomed back 12 minutes later")
SCENARIOS["late_evening"].description = (
    "drowsy: the bedtime ladder up to the wind-down offer, accepted on the panel, reminder, "
    "goodnight when the 15 minutes are over")
SCENARIOS["belief_sure_vs_borderline"].description = (
    "hmm: a sure belief (95 %) nudges after ~20 s; a borderline one (72 %) only after ~50 s")
SCENARIOS["belief_unsure"].description = (
    "hmm: distracted 50 % / focused 50 %: waits for more evidence (logged), never nudges")
SCENARIOS["belief_ambiguous"].description = (
    "hmm: distracted and drowsy both 40 %: one wordless glance (refocus level 1), no climbing")
SCENARIOS["belief_ambiguous_late"].description = (
    "hmm: the same after 46 minutes of work: the break offer instead")
SCENARIOS["belief_shift"].description = (
    "hmm: distracted, then drowsy mid-ladder: switches to bedtime one level below, no restart")
SCENARIOS["belief_wavering"].description = (
    "hmm: once started, a refocus ladder goes on at 60 % (hysteresis), holds at 45 % (no climbing), "
    "goes on again at 90 %")


class VirtualClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@dataclass
class Line:
    t: float
    text: str
    explanation: Optional[Dict[str, Any]] = None      # a nudge's explanation (A20)
    outcome_of: Optional[Any] = None                  # a nudge_outcome's decision_id (for its reward, A22)


class Recorder:
    """Collects the engine's events as readable lines."""

    def __init__(self) -> None:
        self.lines: List[Line] = []
        self.events: List[Tuple[str, float, Dict[str, Any]]] = []

    def __call__(self, event_type: str, t: float, **fields: Any) -> None:
        self.events.append((event_type, t, fields))
        if event_type == "nudge_decision":
            text = (f"NUDGE  #{fields['decision_id']} {fields['ladder']} level {fields['level']} "
                    f"({fields['behavior']}, {fields['tone']}) -- {fields['reason']}")
        elif event_type == "nudge_outcome":
            text = (f"  outcome #{fields['decision_id']}: {fields['outcome'].upper()} after "
                    f"{fields['seconds']:.0f} s ({fields['detail']})")
        elif event_type == "role_changed":
            text = f"role: {fields['from']} -> {fields['to']}"
        elif event_type == "policy_decision":
            if fields["decision"] == "act":
                return                                 # the NUDGE line says it
            text = f"decision: {fields['decision'].upper()} -- {fields['reason']}"
        else:
            rest = ", ".join(f"{k} {v}" for k, v in fields.items() if k != "what" and v is not None)
            text = f"{fields['what'].replace('_', ' ')}" + (f" ({rest})" if rest else "")
        self.lines.append(Line(t, text, fields.get("explanation") if event_type == "nudge_decision" else None,
                               fields.get("decision_id") if event_type == "nudge_outcome" else None))


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def run_scenario(scenario: Scenario, config: Any = None, library: Any = None,
                 step: float = 1.0) -> Tuple[Recorder, Any, EscalationEngine]:
    from eepy.behaviors.loader import load_library

    from .actuator import SimActuator

    config = config or load()
    library = library or load_library()
    check_library(config, library)
    clock = VirtualClock()
    actuator = SimActuator(library, clock)
    recorder = Recorder()
    engine = EscalationEngine(config, make_policy(config), actuator, recorder)
    engine.session_started(0.0)
    t = 0.0
    belief = scenario.uses_belief
    last: Optional[Dict[str, float]] = None
    for what, seconds in scenario.segments:
        if what in REPLIES:
            clock.now = t
            answer = engine.reply(what, t)
            recorder.lines.append(Line(t, f"[panel] user {what}: {answer}"))
            continue
        label = "belief" if "@" in what else "state"
        recorder.lines.append(Line(t, f"--- {label}: {what.replace('_', ' ')} for {_clock(seconds)}"))
        end = t + seconds
        while t < end:
            clock.now = t
            if belief:
                estimate = belief_estimate(what, scenario.calibrated, last)
                last = estimate["probs"]
            else:
                estimate = {"state": what, "reason": f"(scripted: {what})", "pending": None,
                            "calibrated": scenario.calibrated}
            engine.on_state(estimate, t)
            t += step
    clock.now = t
    engine.session_ended(t)
    return recorder, actuator, engine


def run_estimates(estimates: List[Dict[str, Any]], replies: List[Tuple[float, str]],
                  config: Any = None, library: Any = None) -> Tuple[Recorder, Any, EscalationEngine]:
    """Over a recorded session: estimates as dicts with t, state, reason, pending, calibrated."""
    from eepy.behaviors.loader import load_library

    from .actuator import SimActuator

    config = config or load()
    library = library or load_library()
    check_library(config, library)
    clock = VirtualClock(estimates[0]["t"] if estimates else 0.0)
    actuator = SimActuator(library, clock)
    recorder = Recorder()
    engine = EscalationEngine(config, make_policy(config), actuator, recorder)
    engine.session_started(clock.now)
    pending = sorted(replies)
    for estimate in estimates:
        while pending and pending[0][0] <= estimate["t"]:
            t, kind = pending.pop(0)
            clock.now = t
            recorder.lines.append(Line(t, f"[panel] user {kind}: {engine.reply(kind, t)}"))
        clock.now = estimate["t"]
        engine.on_state(estimate, estimate["t"])
    engine.session_ended(clock.now)
    return recorder, actuator, engine


def session_estimates(session: str, data: Optional[str] = None,
                      estimator: str = "rules") -> Tuple[List[Dict[str, Any]], List[Tuple[float, str]], float]:
    """A recorded session's states (re-run with replay: the rules, or the HMM) and the operator's replies."""
    from eepy.logs.check import resolve
    from eepy.logs.session import DATA_DIR
    from eepy.state import make_estimator
    from eepy.state.replay import annotations, run, session_features, session_start
    from eepy.state.runner import panel_view

    folder = resolve(session, Path(data) / "sessions" if data else DATA_DIR)
    features = session_features(folder)
    if not features:
        raise FileNotFoundError(f"{folder.name} has no face features: was python -m eepy.perception "
                                "running during the session?")
    from eepy.state.runner import estimator_gap

    est = make_estimator(estimator)
    estimates = [dict(panel_view(e)) for e in run(features, est, estimator_gap(est))]
    marks = {"user_accepted": "accepted", "user_snoozed": "snoozed", "user_dismissed": "dismissed"}
    replies = [(t, marks[m]) for t, m in annotations(folder) if m in marks]
    return estimates, replies, session_start(folder) or features[0]["t"]


def synthetic_estimates(spec: str, estimator: str = "hmm", seed: int = 1) -> Tuple[List[Dict[str, Any]], float]:
    """Simulated face features (eepy/state/synthetic.py: work, phone, sleepy, leave) run through
    the real estimator: the estimates carry real evidence, so the explanations (A20) are real."""
    from eepy.state import make_estimator
    from eepy.state.replay import run
    from eepy.state.runner import estimator_gap, panel_view
    from eepy.state.synthetic import simulate

    phases = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        kind, _, length = part.partition(" ")
        seconds = _seconds(length.strip())
        if kind not in ("work", "phone", "sleepy", "leave") or seconds is None:
            raise ValueError(f'"{part}": a synthetic phase is work, phone, sleepy or leave plus a length, '
                             'e.g. "sleepy 5m"')
        phases.append((kind, int(seconds)))
    t0 = 1000.0
    features = simulate(phases, seed=seed, aus=True, t0=t0)
    est = make_estimator(estimator)
    return [dict(panel_view(e)) for e in run(features, est, estimator_gap(est))], t0


def _rewards(recorder: Recorder, start: float) -> Dict[str, Any]:
    """A22: each nudge's reward (config/rewards.yaml), by decision_id; {} when unavailable."""
    try:
        from eepy.bandit.rewards import load_config, records_from_recorder, rewards_from_stream

        from .config import load as load_escalation
        recs = records_from_recorder(recorder.events, start)
        events = rewards_from_stream([(float(r["t_mono"]), 1, "record", r) for r in recs], load_config(),
                                     load_escalation())
        return {e.decision_id: e for e in events}
    except Exception:  # noqa: BLE001 -- a broken rewards file must not break the simulation
        return {}


def print_run(title: str, recorder: Recorder, engine: EscalationEngine, start: float = 0.0) -> None:
    print(f"\n  {title}\n")
    rewards = _rewards(recorder, start)
    for line in recorder.lines:
        print(f"  {_clock(line.t - start)}  {line.text}")
        r = rewards.get(str(line.outcome_of)) if line.outcome_of is not None else None
        if r is not None:
            value = "excluded (censored)" if r.reward is None else f"{r.reward:+.2f}"
            off = f", recovery discount {r.discount:.0%}" if r.discount else ""
            print(f"  {'':5}    reward: {value} ({r.rule}{off})")
        x = line.explanation
        if x:
            said = "says" if x.get("spoken") else f"would say ({x.get('not_spoken')})"
            if x.get("sentence"):
                print(f"  {'':5}  explanation: {said}: \"{x['sentence']}\"")
            print(f"  {'':5}    evidence: {x.get('text') or 'none'}; {x['contrast'].get('text')}"
                  + (f"  [sum error {x['checks']['sum_error']:+.1e}]" if x.get("checks") else ""))
    outcomes: Dict[str, int] = {}
    for kind, _, fields in recorder.events:
        if kind == "nudge_outcome":
            outcomes[fields["outcome"]] = outcomes.get(fields["outcome"], 0) + 1
    decided = sum(1 for kind, _, _ in recorder.events if kind == "nudge_decision")
    included = [r.reward for r in rewards.values() if r.reward is not None]
    total = f"; reward total {sum(included):+.2f} over {len(included)} nudge(s)" if included else ""
    print(f"\n  {decided} nudge(s); outcomes: "
          + (", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())) or "none") + total + "\n")


def _realtime(scenario: Scenario, config: Any) -> int:
    from eepy.behaviors.backends import DryRunBackend
    from eepy.behaviors.player import BehaviorPlayer
    from eepy.logs.clock import monotonic

    from .actuator import PlayerActuator

    total = sum(s for w, s in scenario.segments if w not in REPLIES)
    print(f"\n  Real time on the DryRunBackend: {_clock(total)} (Ctrl+C stops). Spoken lines are printed.\n")
    player = BehaviorPlayer(backend=DryRunBackend()).start()
    check_library(config, player.library)
    recorder = Recorder()
    t0 = monotonic()

    def show(event_type: str, t: float, **fields: Any) -> None:
        recorder(event_type, t, **fields)
        print(f"  {_clock(t - t0)}  {recorder.lines[-1].text}", flush=True)

    engine = EscalationEngine(config, make_policy(config), PlayerActuator(player), show)
    engine.session_started(t0)
    try:
        for what, seconds in scenario.segments:
            now = monotonic()
            if what in REPLIES:
                print(f"  {_clock(now - t0)}  [panel] user {what}: {engine.reply(what, now)}")
                continue
            print(f"  {_clock(now - t0)}  --- state: {what} for {_clock(seconds)}")
            end = now + seconds
            while monotonic() < end:
                now = monotonic()
                engine.on_state({"state": what, "reason": f"(scripted: {what})", "pending": None,
                                 "calibrated": scenario.calibrated}, now)
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("  (stopped)")
    finally:
        engine.session_ended(monotonic())
        player.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.interaction.simulate",
                                     description="Run the escalation against scripted states (no camera).")
    parser.add_argument("scenario", nargs="?",
                        help='a scenario name (--list), or e.g. "focused 5m, distracted 2m, focused 3m"')
    parser.add_argument("--list", action="store_true", help="the built-in scenarios")
    parser.add_argument("--session", help="instead: the states of a recorded session (A13 replay)")
    parser.add_argument("--data", help="the data folder (default data/)")
    parser.add_argument("--config", help="another escalation file to try (default config/escalation.yaml)")
    parser.add_argument("--uncalibrated", action="store_true", help="the participant is not calibrated")
    parser.add_argument("--realtime", action="store_true", help="play live on the DryRunBackend (real time)")
    parser.add_argument("--belief", action="store_true",
                        help="run the states as HMM beliefs (95 %% sure), with the hmm: decisions (A19)")
    parser.add_argument("--estimator", choices=("rules", "hmm"), default=None,
                        help="with --session (default rules) or --synthetic (default hmm): the estimator")
    parser.add_argument("--synthetic", help='simulated faces through the real estimator, e.g. "work 2m, sleepy 6m"')
    args = parser.parse_args(argv)
    if args.list or (not args.scenario and not args.session and not args.synthetic):
        print("\n  Scenarios:")
        for s in SCENARIOS.values():
            spec = ", ".join(w if w in REPLIES else f"{w} {_clock(sec)}" for w, sec in s.segments)
            print(f"    {s.name:16} {s.description}\n    {'':16} [{spec if len(spec) < 90 else spec[:87] + '...'}]")
        print('\n  Or write one: "focused 5m, distracted 2m, focused 3m"  (replies: accepted, snoozed, '
              "dismissed)\n")
        return 0
    try:
        config = load(Path(args.config)) if args.config else load()
        if args.synthetic:
            estimates, start = synthetic_estimates(args.synthetic, args.estimator or "hmm")
            recorder, _, engine = run_estimates(estimates, [], config)
            print_run(f"Synthetic: {args.synthetic} ({args.estimator or 'hmm'}, {len(estimates)} estimates)",
                      recorder, engine, start)
            return 0
        if args.session:
            estimates, replies, start = session_estimates(args.session, args.data, args.estimator or "rules")
            recorder, _, engine = run_estimates(estimates, replies, config)
            print_run(f"Session {args.session}: {len(estimates)} seconds of states, "
                      f"{len(replies)} replies from the panel", recorder, engine, start)
            return 0
        scenario = SCENARIOS.get(args.scenario) or parse_spec(args.scenario)
        scenario.calibrated = not args.uncalibrated
        scenario.belief = scenario.belief or args.belief
        if args.realtime:
            return _realtime(scenario, config)
        recorder, _, engine = run_scenario(scenario, config)
    except (EscalationConfigError, ValueError, FileNotFoundError, KeyError) as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2
    print_run(f"{scenario.name}: {scenario.description}", recorder, engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
