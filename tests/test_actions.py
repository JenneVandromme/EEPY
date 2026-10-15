"""Tests for the allowed actions (A23): the sets per state, the step-up limit, ignored levels,
the special levels' conditions, guards -> wait only, the YAML validation, the policy switch and
safety net, and the fixed escalation's choices always being inside the allowed set.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from eepy.interaction.actions import allowed_actions, contains, key, label
from eepy.interaction.config import DEFAULT_PATH, EscalationConfigError, parse
from eepy.interaction.policy import Action, FixedEscalation, NudgeContext, make_policy

RAW = yaml.safe_load(DEFAULT_PATH.read_text())
CFG = parse(copy.deepcopy(RAW))


def ctx(state="distracted", ladder="refocus", level=None, confidence=0.95, calibrated=True, **kw):
    base = dict(now=0.0, estimate={"state": state, "confidence": confidence, "calibrated": calibrated,
                                   "estimator": "hmm" if confidence is not None else "rules"},
                state_seconds=60.0, role=ladder or "none", ladder=ladder, level=level, last_outcome=None,
                restarting=False, nudges_session=0, nudges_last_hour=0, since_last_nudge=None, since_break=None,
                since_calibration=None, session_seconds=3600.0)
    base.update(kw)
    return NudgeContext(**base)


def labels(context, config=CFG):
    return [label(a) for a in allowed_actions(context, config)]


def levels(context, config=CFG):
    return sorted({a.level for a in allowed_actions(context, config) if a.kind == "play"})


# --- the sets per state ----------------------------------------------------------------------


def test_wait_is_always_allowed_and_first():
    for state in ("focused", "distracted", "drowsy", "very_drowsy", "absent", "unknown"):
        for ladder in ("refocus", "bedtime", None):
            assert labels(ctx(state, ladder))[0] == "wait"


def test_nothing_but_wait_while_focused_absent_or_unknown():
    for state in ("focused", "absent", "unknown"):
        for ladder in ("refocus", "bedtime"):
            assert labels(ctx(state, ladder)) == ["wait"]


def test_the_ladder_must_match_the_state():
    assert labels(ctx("distracted", "bedtime")) == ["wait"]          # no bedtime action while distracted
    assert labels(ctx("drowsy", "refocus")) == ["wait"]
    assert {a.ladder for a in allowed_actions(ctx("distracted", "refocus"), CFG)} == {None, "refocus"}
    assert {a.ladder for a in allowed_actions(ctx("very_drowsy", "bedtime"), CFG)} == {None, "bedtime"}


def test_first_nudge_starts_low():
    assert levels(ctx("distracted", "refocus")) == [1, 2]
    assert levels(ctx("drowsy", "bedtime")) == [1, 2]
    assert levels(ctx("very_drowsy", "bedtime")) == [1, 2, 3]       # strong evidence may start with words
    assert levels(ctx("distracted", "refocus", restarting=True)) == [1, 2]   # restart_level 2
    assert levels(ctx("drowsy", "bedtime", restarting=True)) == [1, 2, 3]    # bedtime restart_level 3
    assert levels(ctx("drowsy", "bedtime", start_level=3)) == [1, 2, 3]      # A19's switch carry-over


def test_the_step_up_limit():
    assert levels(ctx("distracted", "refocus", level=1)) == [1, 2, 3]
    assert max(levels(ctx("drowsy", "bedtime", level=1))) == 3              # never 1 -> 4
    cfg = parse({**copy.deepcopy(RAW), "actions": {**RAW["actions"], "max_step_up": 1}})
    assert levels(ctx("distracted", "refocus", level=1), cfg) == [1, 2]


def test_ignored_levels_are_not_offered_again():
    c = ctx("distracted", "refocus", level=2, ignored_levels=(1, 2))
    assert levels(c) == [3, 4]
    cfg = parse({**copy.deepcopy(RAW), "actions": {**RAW["actions"], "no_ignored_levels": False}})
    assert levels(c, cfg) == [1, 2, 3, 4]


def test_the_break_offer_needs_fifteen_minutes_of_work():
    assert 4 not in levels(ctx("distracted", "refocus", level=3, session_seconds=600))
    assert 4 in levels(ctx("distracted", "refocus", level=3, session_seconds=1000))
    assert 4 not in levels(ctx("distracted", "refocus", level=3, session_seconds=5000, since_break=300))
    assert 4 in levels(ctx("distracted", "refocus", level=3, session_seconds=5000, since_break=1200))


def test_goodnight_only_early_when_very_drowsy_sure_calibrated_and_after_spoken():
    gn = CFG.goodnight_level
    ok = dict(level=3, ignored_levels=(3,), confidence=0.95, calibrated=True)
    assert gn in levels(ctx("very_drowsy", "bedtime", **ok))
    assert gn not in levels(ctx("drowsy", "bedtime", **ok))
    assert gn not in levels(ctx("very_drowsy", "bedtime", **{**ok, "confidence": 0.85}))
    assert gn not in levels(ctx("very_drowsy", "bedtime", **{**ok, "calibrated": False}))
    assert gn not in levels(ctx("very_drowsy", "bedtime", **{**ok, "ignored_levels": (1, 2)}))
    assert gn not in levels(ctx("very_drowsy", "bedtime", **{**ok, "confidence": None}))   # the rules never
    assert not any(a.ladder == "bedtime" for a in allowed_actions(ctx("distracted", "refocus", level=3), CFG))


def test_the_direct_tone_needs_calibration_and_confidence():
    tones = lambda c: {a.tone for a in allowed_actions(c, CFG) if a.kind == "play"}   # noqa: E731
    assert tones(ctx(confidence=0.95)) == {"hedged", "direct"}
    assert tones(ctx(confidence=0.75)) == {"hedged"}
    assert tones(ctx(calibrated=False)) == {"hedged"}
    assert tones(ctx(confidence=None)) == {"hedged", "direct"}       # the rules: no confidence to check


def test_ambiguity_allows_only_the_gentle_option_once():
    c = ctx("focused", "refocus", ambiguous=True, start_level=1)
    assert labels(c) == ["wait", "refocus 1 hedged"]
    assert labels(ctx("drowsy", "refocus", ambiguous=True, start_level=4, session_seconds=3000)) == \
        ["wait", "refocus 4 hedged"]                                  # A19: the break offer after long work
    assert labels(ctx("distracted", "refocus", ambiguous=True, level=1)) == ["wait"]
    assert labels(ctx("absent", "refocus", ambiguous=True, start_level=1)) == ["wait"]


def test_a_guard_blocking_leaves_only_wait():
    assert labels(ctx(blocked="minimum gap between nudges")) == ["wait"]


def test_actions_are_ordered_and_marked_by_intrusiveness():
    acts = [a for a in allowed_actions(ctx("distracted", "refocus", level=2, session_seconds=2000), CFG)
            if a.kind == "play"]
    ranks = [a.extra["rank"] for a in acts]
    assert ranks == sorted(ranks)
    assert {a.level for a in acts if a.extra["most_intrusive"]} == {3, 4}
    cfg = parse({**copy.deepcopy(RAW), "actions": {**RAW["actions"], "intrusiveness": {"refocus:3": 0.5}}})
    first = [a.level for a in allowed_actions(ctx("distracted", "refocus", level=2, session_seconds=2000), cfg)
             if a.kind == "play"][0]
    assert first == 3                                                  # the designers' ranking wins


# --- the YAML ------------------------------------------------------------------------------


@pytest.mark.parametrize("change, message", [
    ({"max_step_up": 0}, "max_step_up"),
    ({"max_start_level": {"refocus": 9}}, "top level"),
    ({"max_start_level": {"walking": 1}}, "max_start_level"),
    ({"goodnight_early": {"enabled": True, "confidence": 0.2}}, "between 0.5 and 1"),
    ({"ambiguous_tone": "loud"}, "hedged or direct"),
    ({"intrusiveness": {"refocus:x": 2}}, "intrusiveness"),
    ({"nagging": True}, "unknown key"),
])
def test_the_actions_section_is_validated(change, message):
    raw = copy.deepcopy(RAW)
    raw["actions"] = {**raw["actions"], **change}
    with pytest.raises(EscalationConfigError, match=message):
        parse(raw)


def test_the_policy_is_one_setting_and_fixed_is_the_default():
    assert CFG.policy == "fixed" and isinstance(make_policy(CFG), FixedEscalation)
    raw = copy.deepcopy(RAW)
    raw["policy"] = "magic"
    with pytest.raises(EscalationConfigError, match="policy"):
        parse(raw)
    raw.pop("policy")
    raw.pop("actions")
    assert parse(raw).policy == "fixed" and parse(raw).actions.max_step_up == 2   # older files: the defaults


# --- the engine: the set is logged, the policy stays inside it -------------------------------


def _run(spec=None, scenario=None, belief=False):
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.interaction.simulate import SCENARIOS, parse_spec, run_scenario

    s = SCENARIOS[scenario] if scenario else parse_spec(spec)
    s.belief = s.belief or belief
    recorder, _, engine = run_scenario(s)
    return recorder, engine


def test_the_fixed_escalation_always_chooses_inside_the_allowed_set():
    from eepy.interaction.simulate import SCENARIOS

    decisions = 0
    for name in SCENARIOS:
        recorder, _ = _run(scenario=name)
        for kind, _, f in recorder.events:
            if kind == "nudge_decision" and f.get("policy") != "wind_down_timer":
                decisions += 1
                assert f["chosen_in_allowed"] is True, (name, f["ladder"], f["level"], f["allowed"])
                assert f"{f['ladder']} {f['level']} {f['tone']}" in f["allowed"]
            assert not (kind == "escalation" and f.get("what") == "policy_outside_allowed"), name
    assert decisions > 20


def test_the_fixed_escalation_stays_inside_on_simulated_faces_and_a_reactive_user():
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.bandit.simuser import run_responsive
    from eepy.interaction.simulate import run_estimates, synthetic_estimates

    runs = [run_estimates(synthetic_estimates("work 16m, phone 5m, work 2m, sleepy 10m")[0], [])[0]]
    runs += [run_responsive("focused 15m, distracted 6m, focused 2m, very_drowsy 12m", seed=s)[0] for s in (1, 2, 3)]
    for recorder in runs:
        for kind, _, f in recorder.events:
            if kind == "nudge_decision" and f.get("policy") != "wind_down_timer":
                assert f["chosen_in_allowed"] is True, (f["ladder"], f["level"], f["allowed"])


def test_no_break_offer_in_the_first_fifteen_minutes():
    recorder, _ = _run("focused 4m, distracted 8m")
    levels_played = [f["level"] for k, _, f in recorder.events if k == "nudge_decision"]
    assert levels_played and 4 not in levels_played and max(levels_played) == 3
    assert any(k == "escalation" and f.get("what") == "cooldown_started" for k, _, f in recorder.events)


class Broken:
    name = "broken"

    def decide(self, context):
        raise RuntimeError("the bandit fell over")


class Rogue:
    name = "rogue"

    def decide(self, context):
        return Action("play", "bedtime", 4, 1.0, "direct", reason="anything goes")


@pytest.mark.parametrize("policy, what", [(Broken(), "policy_failed"), (Rogue(), "policy_outside_allowed")])
def test_a_failing_or_rogue_policy_falls_back_to_the_fixed_escalation(policy, what):
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.behaviors.loader import load_library
    from eepy.interaction.actuator import SimActuator
    from eepy.interaction.engine import EscalationEngine
    from eepy.interaction.simulate import Recorder, VirtualClock

    raw = copy.deepcopy(RAW)
    raw["guards"]["after_calibration"] = 0
    config = parse(raw)
    clock = VirtualClock(0.0)
    recorder = Recorder()
    engine = EscalationEngine(config, policy, SimActuator(load_library(), clock), recorder)
    engine.session_started(0.0)
    for t in range(200):
        clock.now = float(t)
        engine.on_state({"state": "focused" if t < 10 else "distracted", "reason": "", "pending": None,
                         "calibrated": True}, float(t))
    nudges = [f for k, _, f in recorder.events if k == "nudge_decision"]
    assert nudges and all(f["ladder"] == "refocus" and f["chosen_in_allowed"] for f in nudges)
    assert nudges[0]["level"] == 1                                    # the fixed escalation's choice
    assert any(k == "escalation" and f.get("what") == what for k, _, f in recorder.events)


def test_goodnight_chosen_early_ends_in_the_goodnight_flow():
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.behaviors.loader import load_library
    from eepy.interaction.actuator import SimActuator
    from eepy.interaction.engine import EscalationEngine
    from eepy.interaction.simulate import Recorder, VirtualClock

    class Sleepy:
        name = "sleepy"

        def decide(self, context):
            gn = [a for a in context.allowed if a.level == CFG.goodnight_level]
            return gn[0] if gn else FixedEscalation(CFG).decide(context)

    raw = copy.deepcopy(RAW)
    raw["guards"]["after_calibration"] = 0
    config = parse(raw)
    clock = VirtualClock(0.0)
    recorder = Recorder()
    engine = EscalationEngine(config, Sleepy(), SimActuator(load_library(), clock), recorder)
    engine.session_started(0.0)
    for t in range(600):
        clock.now = float(t)
        engine.on_state({"state": "focused" if t < 10 else "very_drowsy", "reason": "", "pending": None,
                         "calibrated": True, "estimator": "hmm", "confidence": 0.97,
                         "probs": {"focused": 0.01, "distracted": 0.02, "drowsy": 0.0, "very_drowsy": 0.97}}, float(t))
    levels_played = [f["level"] for k, _, f in recorder.events if k == "nudge_decision"]
    assert CFG.goodnight_level in levels_played and engine.flow == "goodnight"
    i = levels_played.index(CFG.goodnight_level)
    assert any(lv >= 3 for lv in levels_played[:i])                   # only after an ignored spoken nudge


def test_key_and_contains():
    a = Action("play", "refocus", 2, 0.7, "hedged")
    assert key(a) == ("refocus", 2, "hedged") and key(Action.wait("x")) == (None, None, "wait")
    assert contains([Action("play", "refocus", 2, 1.0, "hedged")], a)          # intensity is not a choice
    assert not contains([Action("play", "refocus", 2, 1.0, "direct")], a)


def test_the_launcher_builds_the_decisions_command():
    from eepy.launcher import decisions as tab

    assert tab.command({"kind": "scenario", "spec": "takes_the_break"})[-2:] == ["--scenario", "takes_the_break"]
    assert tab.command({"kind": "session", "session": "2026-10-15_P99_hmm_11"})[-1] == "2026-10-15_P99_hmm_11"
    for bad in ({"kind": "session", "session": "../x"}, {"kind": "scenario", "spec": ""}, {"kind": "x"}):
        with pytest.raises(ValueError):
            tab.command(bad)
