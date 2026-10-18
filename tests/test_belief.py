"""Belief-based decisions in the hmm mode (A19): accumulated evidence instead of timers,
hysteresis, waiting for more evidence, ambiguity, switching ladders mid-way, confidence ->
intensity, the evidence on every decision, the simulator's belief scenarios and the nudge
comparison on the synthetic dataset. And: the rules mode is unaffected.

In virtual time, with the real behaviour library; every event is checked against the schema.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

import pytest
import yaml

from eepy.behaviors.loader import load_library
from eepy.interaction.actuator import SimActuator
from eepy.interaction.config import DEFAULT_PATH, EscalationConfigError, check_library, load, parse
from eepy.interaction.engine import EscalationEngine
from eepy.interaction.policy import FixedEscalation
from eepy.interaction.simulate import SCENARIOS, VirtualClock, belief_estimate, parse_belief, parse_spec, \
    run_scenario
from eepy.logs.schema import check_event
from eepy.state.dataset import score_nudges

RAW = yaml.safe_load(DEFAULT_PATH.read_text())
LIBRARY = load_library()


def raw_config(**belief: Any) -> dict:
    raw = copy.deepcopy(RAW)
    raw["guards"]["after_calibration"] = 0
    raw["actions"]["break_after_work"] = 0          # the break offer at once (A23's rule: test_actions.py)
    for key, value in belief.items():
        raw["hmm"][key] = value
    return raw


class Run:
    """The engine fed with scripted HMM beliefs, one a second."""

    def __init__(self, raw: Optional[dict] = None, calibrated: bool = True) -> None:
        self.config = parse(raw if raw is not None else raw_config())
        check_library(self.config, LIBRARY)
        self.clock = VirtualClock()
        self.events: List[tuple] = []
        self.engine = EscalationEngine(self.config, FixedEscalation(self.config), SimActuator(LIBRARY, self.clock),
                                       self.log)
        self.engine.session_started(0.0)
        self.calibrated = calibrated
        self.last: Optional[Dict[str, float]] = None

    def log(self, event_type: str, t: float, **fields: Any) -> None:
        check_event(event_type, "auto", fields)
        json.dumps(fields)
        self.events.append((event_type, t, fields))

    def run(self, what: str, seconds: float, plain: bool = False) -> "Run":
        end = self.clock.now + seconds
        while self.clock.now < end:
            if plain:                                   # a rules estimate: no belief
                est = {"state": what, "reason": "test", "pending": None, "calibrated": self.calibrated}
            else:
                est = belief_estimate(what, self.calibrated, self.last)
                self.last = est["probs"]
            self.engine.on_state(est, self.clock.now)
            self.clock.now += 1.0
        return self

    def of(self, kind: str) -> List[Dict[str, Any]]:
        return [dict(f, t=t) for k, t, f in self.events if k == kind]

    def nudges(self) -> List[tuple]:
        return [(d["t"], d["ladder"], d["level"]) for d in self.of("nudge_decision")]

    def decisions(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        return [d for d in self.of("policy_decision") if kind is None or d["decision"] == kind]


# --- the config -----------------------------------------------------------------------------------


def test_shipped_config_has_the_belief_section():
    b = load().belief
    assert b is not None and b.decisions == "belief"
    assert b.needed == {"refocus": 8.0, "bedtime": 12.0, "very_drowsy": 6.0}
    assert b.keep < min(load().act_confidence.values())          # hysteresis: going on is easier than starting


@pytest.mark.parametrize("change, message", [
    (lambda h: h.update(decisions="maybe"), "hmm.decisions must be belief or gate"),
    (lambda h: h["accumulate"]["needed"].pop("very_drowsy"), "hmm.accumulate.needed must give refocus, bedtime"),
    (lambda h: h.update(keep=0.8), "hmm.keep (0.8) must not be above confidence.act"),
    (lambda h: h["ambiguous"].update(each=0.2), "hmm.ambiguous.each (0.2) x 2 must be above"),
    (lambda h: h.update(colour="red"), "hmm: unknown key(s) ['colour']"),
    (lambda h: h["reset"].update(focused=0.5), "hmm.reset.focused must be above 0.5"),
])
def test_config_mistakes_are_explained(change, message):
    raw = copy.deepcopy(RAW)
    change(raw["hmm"])
    with pytest.raises(EscalationConfigError, match=message.replace("(", r"\(").replace(")", r"\)")
                       .replace("[", r"\[").replace("]", r"\]")):
        parse(raw)


def test_without_the_section_the_hmm_mode_uses_the_gate():
    raw = copy.deepcopy(RAW)
    raw.pop("hmm")
    assert parse(raw).belief is None


# --- 1. accumulated evidence instead of a timer ---------------------------------------------------


@pytest.mark.parametrize("p, low, high", [(0.95, 18, 22), (0.8, 30, 34), (0.72, 44, 50)])
def test_confident_beliefs_act_sooner(p, low, high):
    r = Run().run("focused", 30).run(f"distracted@{p}", 90)
    first = r.nudges()[0]
    assert first[1:] == ("refocus", 1) and low <= first[0] - 30 <= high         # [19 / 32 / 47]


def test_below_the_act_threshold_nothing_starts():
    r = Run().run("focused", 30).run("distracted@0.65", 180)
    assert r.nudges() == [] and r.engine.role == "study_buddy"


def test_very_drowsy_fast_path_only_when_calibrated():
    fast = Run(calibrated=True).run("focused", 30).run("very_drowsy@0.95", 60).nudges()[0]
    slow = Run(calibrated=False).run("focused", 30).run("very_drowsy@0.95", 60).nudges()[0]
    assert 13 <= fast[0] - 30 <= 17 and 28 <= slow[0] - 30 <= 32          # [~15 s vs ~30 s, as bedtime]
    assert fast[1:] == ("bedtime", 2)                                     # very drowsy skips the faintest hint


# --- 2. hysteresis ----------------------------------------------------------------------------------


def test_a_running_ladder_goes_on_below_the_start_threshold_and_holds_lower():
    r = Run().run("focused", 30).run("distracted@0.9", 30).run("distracted@0.6+drowsy@0.15", 70)
    assert [n[2] for n in r.nudges()] == [1, 2, 3]            # 60 %: would not start, but goes on
    r.run("distracted@0.45+drowsy@0.3", 120)
    assert [n[2] for n in r.nudges()] == [1, 2, 3]            # 45 %: holds, no climbing
    holds = r.decisions("hold")
    assert len(holds) == 1 and "less sure now: distracted 45%" in holds[0]["reason"]


def test_reset_needs_a_clear_focus_for_long_enough():
    r = Run().run("focused", 30).run("distracted@0.9", 30)
    r.run("focused@0.7+distracted@0.3", 60)                   # more likely focused, but not 75 %
    assert r.engine.episode is not None and r.decisions("reset") == []
    r.run("focused", 12)
    assert r.engine.episode is None and len(r.decisions("reset")) == 1
    assert r.engine.role == "study_buddy"


def test_focus_coming_back_stops_the_climbing():
    r = Run().run("focused", 30).run("distracted@0.9", 30)
    r.run("distracted@0.6+focused@0.4", 90)                   # P(focused) back at 40 %: hold
    assert [n[2] for n in r.nudges()] == [1]
    assert "focus coming back" in r.decisions("hold")[0]["reason"]


# --- 3. wait for more evidence ------------------------------------------------------------------------


def test_an_unclear_belief_waits_and_logs_it_once():
    r = Run().run("focused", 30).run("distracted@0.5", 120).run("focused", 20)
    assert r.nudges() == []
    waits = r.decisions("wait")
    assert len(waits) == 1 and waits[0]["reason"].startswith("not sure yet (focused 50%, distracted 50%")
    for key in ("probs", "confidence", "uncertainty", "evidence", "evidence_progress"):
        assert key in waits[0]
    assert r.decisions("resume")                              # the wait's end is logged too


def test_a_ready_but_spread_belief_waits():
    """70 % distracted, but the rest spread over three states: high entropy, so no nudge yet."""
    r = Run().run("focused", 30).run("distracted@0.7+drowsy@0.15+very_drowsy@0.1", 90)
    assert r.nudges() == [] and r.decisions("wait")


def test_a_clearly_focused_belief_never_waits():
    r = Run().run("focused@0.62+distracted@0.2+drowsy@0.18", 120)
    assert r.decisions() == []                                # distracted, drowsy < 40 %: just focused


# --- 4. ambiguity -------------------------------------------------------------------------------------


def test_ambiguous_early_one_wordless_glance_no_climbing():
    r = Run().run("focused", 30).run("distracted@0.4+drowsy@0.4", 240)
    assert [n[1:] for n in r.nudges()] == [("refocus", 1)]
    nudge = r.of("nudge_decision")[0]
    assert nudge["tone"] == "hedged" and "one gentle nudge" in nudge["reason"]


def test_ambiguous_after_long_work_offers_the_break():
    r = Run().run("focused", 46 * 60).run("distracted@0.4+drowsy@0.4", 60)
    assert [n[1:] for n in r.nudges()] == [("refocus", 4)]
    assert "offering a break" in r.of("nudge_decision")[0]["reason"]


def test_ambiguity_that_resolves_to_drowsy_switches_cleanly():
    r = Run().run("focused", 30).run("distracted@0.4+drowsy@0.4", 90).run("drowsy@0.9", 60)
    assert [n[1:] for n in r.nudges()] == [("refocus", 1), ("bedtime", 1)]
    assert r.decisions("switch")[0]["start_level"] == 1


# --- 5. switching ladders mid-way ---------------------------------------------------------------------


def test_switch_carries_the_level_over():
    r = Run().run("focused", 30).run("distracted@0.9", 100).run("drowsy@0.9", 60)
    levels = r.nudges()
    assert [n[1:] for n in levels][:3] == [("refocus", 1), ("refocus", 2), ("refocus", 3)]
    switch = r.decisions("switch")[0]
    assert (switch["from_ladder"], switch["from_level"], switch["start_level"]) == ("refocus", 3, 2)
    assert ("bedtime", 2) in [n[1:] for n in levels]                    # one level below, not from 1
    roles = [e["to"] for e in r.of("role_changed")]
    assert roles[-1] == "bedtime"
    outcomes = {o["decision_id"]: o["outcome"] for o in r.of("nudge_outcome")}
    assert len(outcomes) == len(levels) - 1                             # every closed nudge has one outcome


def test_switch_never_jumps_to_the_top():
    raw = raw_config()
    raw["guards"]["max_per_hour"] = 100
    r = Run(raw).run("focused", 30).run("distracted@0.95", 200)
    assert max(n[2] for n in r.nudges()) == 4                           # the break offer reached
    r.run("drowsy@0.95", 40)
    assert r.decisions("switch")[0]["start_level"] == 3                # capped at switch.max_level


def test_switch_plays_the_role_switch_behaviour_when_designed(monkeypatch):
    r = Run()
    played: List[str] = []
    monkeypatch.setattr(r.engine.actuator, "has_behavior", lambda name: name == "role_to_bedtime" or
                        name in LIBRARY.behaviors)
    monkeypatch.setattr(r.engine.actuator, "play", lambda name: played.append(name))
    r.run("focused", 30).run("distracted@0.9", 40).run("drowsy@0.9", 60)
    assert "role_to_bedtime" in played


# --- 6. confidence -> behaviour --------------------------------------------------------------------------


def test_intensity_scales_with_confidence_within_the_designers_value():
    borderline = Run().run("focused", 30).run("distracted@0.72", 60).of("nudge_decision")[0]
    sure = Run().run("focused", 30).run("distracted@0.95", 30).of("nudge_decision")[0]
    assert 0.7 <= borderline["intensity"] < 0.8                          # [0.73]
    assert sure["intensity"] == 1.0                                      # never above the designer's value
    raw = raw_config()
    raw["hmm"]["intensity"]["scale"] = False
    assert Run(raw).run("focused", 30).run("distracted@0.72", 60).of("nudge_decision")[0]["intensity"] == 1.0


def test_tone_hedged_when_borderline_direct_when_sure():
    hedged = Run().run("focused", 30).run("distracted@0.72", 90).of("nudge_decision")
    direct = Run().run("focused", 30).run("distracted@0.95", 60).of("nudge_decision")
    assert [n["tone"] for n in hedged[:2]] == ["hedged", "hedged"]
    assert [n["tone"] for n in direct[:2]] == ["hedged", "direct"]        # a repeat, calibrated, >= 85 %


# --- 7. + 9. evidence and logging ---------------------------------------------------------------------


def test_every_decision_carries_the_belief_and_the_evidence():
    r = Run().run("focused", 30).run("distracted@0.9", 40)
    nudge = r.of("nudge_decision")[0]
    assert nudge["context"]["evidence"][0]["text"] == "scripted belief: distracted@0.9"
    assert nudge["context"]["probs"]["distracted"] == 0.9
    act = r.decisions("act")[0]
    assert act["decision_id"] == nudge["decision_id"] and act["evidence"] and act["confidence"] == 0.9


def test_status_says_in_plain_words_what_the_policy_does():
    r = Run().run("focused", 30).run("distracted@0.5", 10)
    status = r.engine.status(r.clock.now)
    assert status["decisions"] == "belief"
    assert status["next"] == "Waiting: not sure yet (focused 50%, distracted 50%)"
    assert status["decision"]["decision"] == "wait" and "evidence_progress" in status


# --- 11. rules mode unaffected ----------------------------------------------------------------------


def _plain_run(raw: dict) -> List[tuple]:
    r = Run(raw)
    for state, seconds in (("focused", 60), ("distracted", 150), ("focused", 30), ("drowsy", 200),
                           ("absent", 60), ("focused", 30)):
        r.run(state, seconds, plain=True)
    r.engine.session_ended(r.clock.now)
    return r.events


def test_rules_estimates_are_unaffected_by_the_hmm_section():
    with_section = _plain_run(raw_config())
    without = raw_config()
    without.pop("hmm")
    assert with_section == _plain_run(without)
    assert not [e for e in with_section if e[0] == "policy_decision"]
    for kind, _, fields in with_section:
        if kind == "nudge_decision":
            assert "start_level" not in fields["context"] and "evidence" not in fields["context"]


def test_decisions_gate_keeps_a16_behaviour():
    raw = raw_config(decisions="gate")
    r = Run(raw).run("focused", 30).run("distracted@0.9", 60)
    assert r.decisions() == []
    assert r.nudges()[0][0] - 30 >= 20                                  # the onset timer, not the evidence


# --- 10. the simulator --------------------------------------------------------------------------------


def test_belief_specs():
    assert parse_belief("distracted@0.45+drowsy@0.4") == pytest.approx(
        {"focused": 0.15, "distracted": 0.45, "drowsy": 0.4, "very_drowsy": 0.0})
    with pytest.raises(ValueError, match="more than 1"):
        parse_belief("distracted@0.7+drowsy@0.5")
    with pytest.raises(ValueError, match="state@probability"):
        parse_belief("sleepy@0.5")
    s = parse_spec("focused 1m, distracted@0.6 30s")
    assert s.uses_belief and s.segments[1] == ("distracted@0.6", 30.0)
    est = belief_estimate("distracted@0.4+drowsy@0.4", True)
    assert est["state"] == "distracted" and est["confidence"] == 0.4 and est["uncertainty"] > 0.6


@pytest.mark.parametrize("name, expected", [
    ("belief_unsure", []),
    ("belief_ambiguous", [("refocus", 1)]),
    ("belief_ambiguous_late", [("refocus", 4)]),
])
def test_belief_scenarios(name, expected):
    recorder, _, _ = run_scenario(SCENARIOS[name])
    assert [(f["ladder"], f["level"]) for k, _, f in recorder.events if k == "nudge_decision"] == expected


def test_belief_shift_scenario_switches_without_restarting():
    recorder, _, _ = run_scenario(SCENARIOS["belief_shift"])
    levels = [(f["ladder"], f["level"]) for k, _, f in recorder.events if k == "nudge_decision"]
    assert levels[:4] == [("refocus", 1), ("refocus", 2), ("refocus", 3), ("bedtime", 2)]


# --- 10. the nudge comparison ---------------------------------------------------------------------------


TRUTH = [(0, 300, "focused"), (300, 360, "distracted"), (360, 600, "focused"), (600, 800, "drowsy"),
         (800, 1000, "focused")]


def test_false_nudges_with_and_without_the_tolerance():
    nudges = [(365, "refocus"), (500, "refocus"), (620, "bedtime")]
    s = score_nudges(nudges, TRUTH, tolerance=10)
    assert (s["false"], s["false_tol"]) == (2, 1)                   # 365: 5 s after refocusing, tolerated
    assert score_nudges(nudges, TRUTH, tolerance=3)["false_tol"] == 2


def test_missed_episodes_by_length_and_by_design():
    s = score_nudges([(620, "bedtime")], TRUTH, tolerance=10)
    assert s["episodes"]["distracted"] == [(60, None)] and s["episodes"]["drowsy"] == [(200, 20)]
    late = score_nudges([], TRUTH, settle=330)
    assert late["episodes"]["distracted"] == [(30, None)]           # only the part after settling in
    assert score_nudges([], TRUTH, until=500)["episodes"]["drowsy"] == []   # after the goodnight


def test_nudge_eval_runs_on_a_small_dataset(tmp_path, capsys):
    from eepy.state.dataset import main
    assert main(["build", "--data", str(tmp_path), "--users", "3", "--hard-users", "0", "--minutes", "8", "9"]) == 0
    out = tmp_path / "scores.json"
    assert main(["eval", "--data", str(tmp_path), "--split", "all", "--nudges", "--json", str(out)]) == 0
    scores = json.loads(out.read_text())
    assert set(scores) == {"rules", "hmm gate", "hmm belief"}
    for s in scores.values():
        assert set(s["missed"]) == {"distracted>=45", "distracted>=60", "distracted>=90",
                                    "drowsy>=90", "drowsy>=120", "drowsy>=180"}
        assert s["false_per_hour_tolerant"] <= s["false_per_hour"]
    assert "not within 10 s of a change" in capsys.readouterr().out


# --- the panel view carries the evidence ----------------------------------------------------------------


def test_panel_view_sends_the_top_evidence():
    from eepy.state.estimate import make_estimator
    from eepy.state.runner import panel_view
    from eepy.state.synthetic import SCENARIO, simulate

    hmm, rules = make_estimator("hmm"), make_estimator("rules")
    for f in simulate(SCENARIO, seed=1)[:60]:
        h, r = hmm.update(f), rules.update(f)
    view = panel_view(h)
    assert 1 <= len(view["evidence"]) <= 3 and {"feature", "text", "supports", "strength"} <= set(view["evidence"][0])
    assert "evidence" not in panel_view(r)


def test_a_wait_does_not_flicker_at_the_line():
    """Live (2026-10-13): the uncertainty hovered at 0.6 and the wait went on and off. Once waiting,
    the belief must be wait.margin clearer before the wait ends."""
    r = Run().run("focused", 30)
    for _ in range(6):                                  # gap 0.10 (wait), then 0.17: clear without the margin
        r.run("distracted@0.55", 4).run("distracted@0.585", 4)
    assert len(r.decisions("wait")) == 1 and r.decisions("resume") == []
    raw = raw_config()
    raw["hmm"]["wait"]["margin"] = 0.0
    no_margin = Run(raw).run("focused", 30)
    for _ in range(6):
        no_margin.run("distracted@0.55", 4).run("distracted@0.585", 4)
    assert len(no_margin.decisions("wait")) == 6                # the flicker the margin prevents
    r.run("distracted@0.95", 3)
    assert len(r.decisions("resume")) == 1 or r.decisions("act")
