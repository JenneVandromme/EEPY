"""The escalation (A14): when EEPY nudges, how it climbs the ladders, and what
came of each nudge. In virtual time, with the real behaviour library and its
real behaviour lengths; nothing moves. Every logged event is checked against
the real schema.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

from eepy.behaviors.loader import load_library
from eepy.interaction.actuator import SimActuator
from eepy.interaction.config import DEFAULT_PATH, EscalationConfigError, check_library, load, parse
from eepy.interaction.engine import EscalationEngine
from eepy.interaction.policy import FixedEscalation, NudgeContext
from eepy.interaction.simulate import SCENARIOS, VirtualClock, main as simulate_main, parse_spec, \
    run_scenario
from eepy.logs.schema import check_event

REPO = Path(__file__).resolve().parents[1]
RAW = yaml.safe_load(DEFAULT_PATH.read_text())
LIBRARY = load_library()


class Harness:
    """The engine in virtual time. run("distracted", 60) feeds one estimate a second."""

    def __init__(self, raw: Optional[dict] = None, calibrated: bool = True, grace: bool = False) -> None:
        if raw is None:
            raw = copy.deepcopy(RAW)
            if not grace:
                raw["guards"]["after_calibration"] = 0      # most tests: no settling-in period
                raw["actions"]["break_after_work"] = 0      # ... and the break offer at once (A23's rule: test_actions.py)
        self.config = parse(raw)
        check_library(self.config, LIBRARY)
        self.clock = VirtualClock()
        self.actuator = SimActuator(LIBRARY, self.clock)
        self.events: List[tuple] = []
        self.engine = EscalationEngine(self.config, FixedEscalation(self.config), self.actuator, self.log)
        self.calibrated = calibrated
        self.engine.session_started(0.0)

    def log(self, event_type: str, t: float, **fields: Any) -> None:
        source = "auto"
        check_event(event_type, source, fields)               # the real schema
        json.dumps(fields)                                     # and loggable
        self.events.append((event_type, t, fields))

    @property
    def t(self) -> float:
        return self.clock.now

    def run(self, state: str, seconds: float, pending: Optional[str] = None,
            **extra: Any) -> "Harness":
        """extra: more estimate fields, e.g. the HMM's confidence=0.8, say="..."."""
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.engine.on_state({"state": state, "reason": f"{state} (test)", "pending": pending,
                                  "calibrated": self.calibrated, **extra}, self.clock.now)
            self.clock.now += 1.0
        return self

    def reply(self, kind: str) -> str:
        return self.engine.reply(kind, self.clock.now)

    def of(self, kind: str) -> List[Dict[str, Any]]:
        return [dict(f, t=t) for k, t, f in self.events if k == kind]

    def nudges(self) -> List[tuple]:
        return [(d["t"], d["ladder"], d["level"]) for d in self.of("nudge_decision")]

    def outcomes(self) -> List[tuple]:
        return [(o["decision_id"], o["outcome"]) for o in self.of("nudge_outcome")]

    def what(self, name: str) -> List[Dict[str, Any]]:
        return [e for e in self.of("escalation") if e["what"] == name]


# --- the config -----------------------------------------------------------------------------


def test_the_shipped_config_loads_and_matches_the_behaviours():
    config = load()
    check_library(config, LIBRARY)
    assert config.ladders["refocus"].levels == (1, 2, 3, 4)
    assert config.ladders["bedtime"].top == config.wind_down_offer_level == 4
    assert config.role_for("focused") == "study_buddy" and config.role_for("absent") == "paused"


def broken(change):
    raw = copy.deepcopy(RAW)
    change(raw)
    return raw


@pytest.mark.parametrize("change, words", [
    (lambda r: r["roles"].update(focused="refocus"), "roles.focused must be study_buddy"),
    (lambda r: r["roles"].pop("absent"), "missing ['absent']"),
    (lambda r: r["roles"].update(drowsy="sleepy"), '"sleepy" is not a role'),
    (lambda r: r["onset"].pop("drowsy"), "onset.drowsy is missing"),
    (lambda r: r["onset"].update(distracted=-5), "must be a number of seconds >= 0"),
    (lambda r: r["ladders"]["refocus"]["reaction"].update({0: 10}), "levels are whole numbers from 1"),
    (lambda r: r["ladders"]["refocus"]["reaction"].pop(2), "must have no gaps"),
    (lambda r: r["ladders"]["refocus"].update(restart_level=9), "restart_level must be one of the levels"),
    (lambda r: r["ladders"]["bedtime"]["intensity"].update({2: 1.5}), "must be between 0 and 1"),
    (lambda r: r["guards"].update(max_per_hour=0), "guards.max_per_hour must be a whole number >= 1"),
    (lambda r: r["guards"].update(min_gapp=3), "unknown key(s) ['min_gapp']"),
    (lambda r: r["wind_down"].update(offer_level=3), "must be the top bedtime nudge level"),
    (lambda r: r["wind_down"].update(reminder_before=2000), "reminder_before must be shorter"),
    (lambda r: r["break"].update(offer_level=7), "is not a refocus level"),
    (lambda r: r.update(escalation={}), "unknown setting(s) ['escalation']"),
    (lambda r: r["confidence"]["act"].update(refocus=70),
     "confidence.act.refocus must be a number between 0 and 1 (like 0.7), not 70."),
    (lambda r: r["confidence"]["act"].update(paused=0.7),
     'confidence.act: "paused" is not a ladder; use refocus or bedtime.'),
    (lambda r: r["confidence"].update(direct=0), "confidence.direct must be a number between 0 and 1"),
])
def test_mistakes_in_the_config_are_explained(change, words):
    with pytest.raises(EscalationConfigError) as error:
        parse(broken(change))
    assert words in str(error.value) and str(error.value).startswith("config/escalation.yaml:")


def test_the_config_is_checked_against_the_behaviour_files():
    raw = broken(lambda r: r["ladders"]["refocus"]["reaction"].update({5: 30}))
    raw["break"]["offer_level"] = 4
    with pytest.raises(EscalationConfigError, match=r"ladders.refocus uses level\(s\) \[5\]"):
        check_library(parse(raw), LIBRARY)
    with pytest.raises(EscalationConfigError, match="is not a line in behaviors/lines.yaml"):
        check_library(parse(broken(lambda r: r["break"].update(welcome_line="hello_again"))), LIBRARY)


# --- the policy ---------------------------------------------------------------------------------


def context(**kw: Any) -> NudgeContext:
    base = dict(now=0.0, estimate={"state": "distracted", "calibrated": True}, state_seconds=30.0,
                role="refocus", ladder="refocus", level=None, last_outcome=None, restarting=False,
                nudges_session=0, nudges_last_hour=0, since_last_nudge=None, since_break=None,
                since_calibration=None, session_seconds=100.0)
    base.update(kw)
    return NudgeContext(**base)


def test_the_fixed_policy_climbs_one_level_at_a_time():
    policy = FixedEscalation(parse(RAW))
    first = policy.decide(context())
    assert (first.kind, first.level, first.tone, first.propensity) == ("play", 1, "hedged", 1.0)
    second = policy.decide(context(level=1, last_outcome="no_reaction"))
    assert (second.level, second.tone) == (2, "direct")       # calibrated + repeat
    assert policy.decide(context(level=4, last_outcome="no_reaction")).kind == "wait"
    assert policy.decide(context(restarting=True)).level == 2  # after the cooldown


def test_very_drowsy_starts_higher_and_tone_needs_a_calibration():
    policy = FixedEscalation(parse(RAW))
    very = {"state": "very_drowsy", "calibrated": True}
    action = policy.decide(context(estimate=very, ladder="bedtime", role="bedtime"))
    assert (action.level, action.tone) == (2, "direct")
    uncal = policy.decide(context(estimate=dict(very, calibrated=False), ladder="bedtime", role="bedtime"))
    assert uncal.tone == "hedged"


# --- onset, step up, reset ------------------------------------------------------------------------


def test_a_short_distraction_never_triggers_anything():
    h = Harness().run("focused", 30).run("distracted", 19).run("focused", 30)
    assert h.nudges() == []


def test_onset_delay_then_one_level_up_per_reaction_window():
    h = Harness().run("focused", 10).run("distracted", 200)
    assert h.nudges() == [(30.0, "refocus", 1), (60.0, "refocus", 2), (90.0, "refocus", 3),
                          (135.0, "refocus", 4)]
    assert h.outcomes()[:3] == [(1, "no_reaction"), (2, "no_reaction"), (3, "no_reaction")]
    first = h.of("nudge_decision")[0]
    assert first["reaction_window"] == 30 and first["policy"] == "fixed_escalation"
    assert first["context"]["state"] == "distracted" and first["context"]["state_seconds"] == 20.0
    assert [r["to"] for r in h.of("role_changed")] == ["study_buddy", "refocus"]


def test_the_status_lists_the_levels_played_in_this_climb():
    """For the panel's ladder view: which rungs were played, with their outcomes."""
    h = Harness().run("focused", 10).run("distracted", 70)
    status = h.engine.status(h.t)
    assert status["ladder"] == "refocus" and status["level"] == 2
    assert status["played"] == [{"level": 1, "outcome": "no_reaction"}, {"level": 2, "outcome": None}]
    h.run("focused", 30)                                         # back to work: the climb is over
    assert h.engine.status(h.t)["played"] == []
    v = Harness().run("focused", 10).run("very_drowsy", 60)      # very drowsy starts at level 2
    assert [p["level"] for p in v.engine.status(v.t)["played"]] == [2]


def test_back_to_focused_is_a_success_resets_the_ladder_and_nods_once():
    h = Harness().run("focused", 10).run("distracted", 70).run("focused", 30)   # L1 30, L2 60
    assert h.outcomes() == [(1, "no_reaction"), (2, "success")]
    success = h.of("nudge_outcome")[-1]
    assert success["detail"].startswith("back to focused after")
    assert len(h.what("approving_nod")) == 1 and h.what("episode_reset")[0]["reason"] == "back to focused"
    # Again within 10 minutes: success, but no second nod.
    h.run("distracted", 30).run("focused", 30)
    assert h.outcomes()[-1][1] == "success" and len(h.what("approving_nod")) == 1
    # The next distraction starts at level 1 again.
    assert h.nudges()[-1][2] == 1


def test_no_nod_when_they_came_back_without_a_nudge_working():
    h = Harness().run("focused", 10).run("distracted", 60)      # L1 no reaction, L2 open...
    h.engine.operator_played("refocus_curious", h.t)          # ...the operator takes over: censored
    h.run("focused", 30)
    assert h.outcomes()[-1] == (2, "censored") and h.what("approving_nod") == []


def test_top_of_the_ladder_cools_down_then_restarts_lower():
    h = Harness().run("focused", 10).run("distracted", 600)
    levels = [n[2] for n in h.nudges()]
    assert levels == [1, 2, 3, 4, 2, 3]
    cooldown = h.what("cooldown_started")[0]
    assert cooldown["seconds"] == 300 and cooldown["restart_level"] == 2
    restart = h.nudges()[4][0]
    assert restart == pytest.approx(135 + 120 + 300)            # L4 + its window + cooldown


# --- guards -----------------------------------------------------------------------------------------


def test_no_nudges_while_settling_in_after_the_calibration():
    h = Harness(grace=True)
    h.engine.calibration("running", 0.0)
    h.run("distracted", 60)
    assert h.nudges() == []                                    # calibrating
    h.engine.calibration("done", h.t)
    h.run("distracted", 200)
    assert h.nudges()[0][0] == pytest.approx(60 + 180)


def test_minimum_gap_counts_the_operator_too():
    h = Harness().run("focused", 10)
    h.engine.operator_played("approving_nod", 25.0)
    h.run("distracted", 40)
    assert h.nudges()[0][0] == 45.0                            # 25 + 20, not 30


def test_max_nudges_per_hour():
    raw = copy.deepcopy(RAW)
    raw["guards"].update(after_calibration=0, max_per_hour=3)
    h = Harness(raw).run("focused", 10).run("distracted", 400)
    assert len(h.nudges()) == 3
    assert "3 nudges in the last hour" in h.engine.status(h.t)["next"]


def test_no_nudge_while_the_robot_is_moving_or_they_are_getting_back():
    h = Harness().run("focused", 10)
    h.actuator.busy_until = 60.0                               # e.g. the operator's long behaviour
    h.run("distracted", 60)
    assert h.nudges()[0][0] == 60.0
    h2 = Harness().run("focused", 10).run("distracted", 40, pending="focused")
    assert h2.nudges() == []


# --- absent ----------------------------------------------------------------------------------------


def test_absent_pauses_nudging_and_freezes_the_window():
    h = Harness().run("focused", 10).run("distracted", 25)    # L1 at 30, window until 60
    h.run("absent", 100)
    assert len(h.nudges()) == 1 and h.outcomes() == []          # frozen, no empty-chair nudges
    h.run("distracted", 26)                                     # 5 s + 25 s = the 30 s window
    assert h.outcomes() == [(1, "no_reaction")] and h.nudges()[1][2] == 2


def test_gone_five_minutes_resets_the_ladder():
    h = Harness().run("focused", 10).run("distracted", 25).run("absent", 301)
    assert h.outcomes() == [(1, "censored")]
    assert h.of("nudge_outcome")[0]["detail"] == "left the desk"
    h.run("distracted", 40)
    assert h.nudges()[-1][2] == 1                              # fresh start


def test_leaving_after_bedtime_nudges_is_going_to_bed():
    h = Harness().run("focused", 10).run("drowsy", 230)        # L1 40, L2 100, L3 160 (window 90)
    assert [n[2] for n in h.nudges()] == [1, 2, 3]
    h.run("absent", 301)
    assert h.outcomes()[-1] == (3, "success")
    assert "went to bed" in h.of("nudge_outcome")[-1]["detail"] and h.what("went_to_bed")
    assert h.engine.flow == "went_to_bed" and h.actuator.idle is None


# --- the break offer --------------------------------------------------------------------------------


def take_to_break_offer(h: Harness) -> Harness:
    h.run("focused", 10).run("distracted", 140)                # L4 at 135
    assert h.nudges()[-1][1:] == ("refocus", 4)
    return h


def test_leaving_after_the_break_offer_is_a_break_then_welcome_back():
    h = take_to_break_offer(Harness()).run("absent", 400)
    assert h.outcomes()[-1] == (4, "success") and h.of("nudge_outcome")[-1]["detail"] == "took the break"
    assert h.what("break_started") and h.engine.flow == "break" and h.engine.status(h.t)["break_left"] > 0
    assert len(h.nudges()) == 4                                 # no nudges during the break
    h.run("distracted", 30)                                     # back early (and distracted): welcome
    assert h.what("welcome_back") and h.what("break_ended")[0]["reason"] == "back"
    assert any(c[1] == "say" and c[2] == "break_welcome_back" for c in h.actuator.calls)


def test_accepting_the_break_offer_on_the_panel():
    h = take_to_break_offer(Harness())
    assert h.reply("accepted").startswith("break: no nudges for 10 minutes")
    assert h.outcomes()[-1] == (4, "success") and h.engine.flow == "break"
    h.run("distracted", 601)                                   # stayed at the desk the whole break
    assert h.what("break_ended")[0]["reason"] == "break time over" and h.what("welcome_back") == []


# --- the wind-down --------------------------------------------------------------------------------------


def to_wind_down(h: Harness) -> Harness:
    h.run("focused", 10).run("drowsy", 260 + 120)               # L4 at 250
    assert h.nudges()[-1][1:] == ("bedtime", 4) and h.engine.flow == "wind_down"
    return h


def test_wind_down_reminder_then_goodnight():
    h = to_wind_down(Harness())
    offer = h.nudges()[-1][0]
    h.run("focused", 900)                                       # finishing their work: fine
    assert h.what("wind_down_reminder")[0]["t"] == offer + 900 - 60
    goodnight = h.of("nudge_decision")[-1]
    assert (goodnight["level"], goodnight["policy"], goodnight["t"]) == (5, "wind_down_timer", offer + 900)
    assert h.engine.flow == "goodnight" and h.what("goodnight")
    h.run("distracted", 600)
    assert len(h.nudges()) == 5                                 # nothing after goodnight
    assert h.reply("accepted") == "goodnight confirmed" and h.outcomes()[-1] == (5, "success")


def test_snooze_restarts_the_timer_once():
    h = to_wind_down(Harness())
    h.run("drowsy", 600)
    assert h.reply("snoozed").startswith("snoozed: goodnight in 15 minutes")
    snoozed_at = h.t
    assert h.reply("snoozed") == "no snoozes left: goodnight when the timer ends"
    h.run("drowsy", 901)
    assert h.of("nudge_decision")[-1]["t"] == snoozed_at + 900


def test_dismissing_the_wind_down_cools_down_then_restarts_at_level_3():
    h = to_wind_down(Harness())
    said_no = h.t
    h.reply("dismissed")
    assert h.engine.flow == "normal" and h.what("wind_down_cancelled")
    h.run("drowsy", 650)
    assert h.nudges()[-1][1:] == ("bedtime", 3)
    assert h.nudges()[-1][0] == said_no + 600                  # the bedtime cooldown


def test_session_end_after_they_left_a_bedtime_nudge_is_a_success():
    h = Harness().run("focused", 10).run("drowsy", 50).run("absent", 20)   # L1 open, then they left
    h.engine.session_ended(h.t)
    assert h.outcomes() == [(1, "success")]
    out = h.of("nudge_outcome")[0]
    assert out["detail"] == "the session ended after they left (went to bed)" and out["how"] == "bed"
    assert out["back_after"] == 20.0                           # timed from when they left


def test_session_end_while_still_at_the_desk_is_no_verdict():
    """The operator ending the session (time up, a test) is not "went to bed": no false success."""
    h = Harness().run("focused", 10).run("drowsy", 50)          # L1 open, still drowsy at the desk
    h.engine.session_ended(h.t)
    assert h.outcomes() == [(1, "censored")]
    assert h.of("nudge_outcome")[0]["detail"] == "the session ended while they were still at the desk"


# --- pause autonomy -------------------------------------------------------------------------------------


def test_pause_autonomy_censors_and_stops_everything_until_resumed():
    h = Harness().run("focused", 10).run("distracted", 40)      # L1 at 30, open
    h.engine.set_paused(True, h.t)
    assert h.outcomes()[-1] == (1, "censored") and h.what("autonomy_paused")
    h.run("distracted", 300)
    assert len(h.nudges()) == 1
    assert h.engine.status(h.t)["next"] == "autonomy paused: the operator is in control"
    h.engine.set_paused(False, h.t)
    h.run("distracted", 5)
    assert h.nudges()[-1][2] == 1                              # resumed afresh (onset already met)


# --- outcomes ----------------------------------------------------------------------------------------------


def test_every_decision_gets_exactly_one_outcome():
    for scenario in SCENARIOS.values():
        recorder, _, _ = run_scenario(scenario)
        decided = [f["decision_id"] for k, _, f in recorder.events if k == "nudge_decision"]
        closed = [f["decision_id"] for k, _, f in recorder.events if k == "nudge_outcome"]
        assert sorted(closed) == decided, scenario.name
        for kind, _, fields in recorder.events:
            check_event(kind, "auto", fields)


def test_interrupted_focus_when_they_were_already_back():
    """A13 says "focused" only after 5 s of focus: focused within 5 s of the nudge
    means they were back before it came -- a false alarm."""
    h = Harness().run("focused", 10).run("distracted", 21).run("focused", 20)
    assert h.outcomes() == [(1, "interrupted_focus")]


def test_dismissed_and_snoozed_from_the_panel():
    h = Harness().run("focused", 10).run("distracted", 25)
    assert h.reply("dismissed").startswith("dismissed: this ladder pauses")
    assert h.outcomes() == [(1, "dismissed")] and h.what("cooldown_started")[0]["reason"] == "dismissed"
    h2 = Harness().run("focused", 10).run("distracted", 25)
    h2.reply("snoozed")
    assert h2.outcomes() == [(1, "snoozed")]


def test_switching_ladders_censors_the_open_nudge():
    h = Harness().run("focused", 10).run("distracted", 25).run("drowsy", 40)
    assert h.outcomes() == [(1, "censored")]
    assert h.of("nudge_outcome")[0]["detail"] == "the state changed to drowsy"
    assert h.nudges()[-1][1:] == ("bedtime", 1)


def test_no_state_from_the_vision_process_is_unknown():
    h = Harness().run("focused", 10).run("distracted", 15)
    h.clock.now += 120                                          # nothing arrives
    h.engine.tick(h.clock.now)
    assert h.engine.status(h.t)["state"] == "unknown" and h.nudges() == []


# --- the simulation --------------------------------------------------------------------------------------


def test_the_built_in_scenarios_do_what_they_say():
    def events(name):
        recorder, _, _ = run_scenario(SCENARIOS[name])
        return recorder.events

    phone = events("phone_break")
    assert [f["outcome"] for k, _, f in phone if k == "nudge_outcome"][-1] == "success"
    assert any(k == "escalation" and f["what"] == "approving_nod" for k, _, f in phone)
    brk = events("takes_the_break")
    assert {f["what"] for k, _, f in brk if k == "escalation"} >= {"break_started", "welcome_back", "break_ended"}
    late = events("late_evening")
    assert [f["level"] for k, _, f in late if k == "nudge_decision"] == [1, 2, 3, 4, 5]
    assert any(k == "escalation" and f["what"] == "wind_down_reminder" for k, _, f in late)
    flicker = events("flicker")
    first = [f for k, _, f in flicker if k == "nudge_decision"][0]
    assert (first["ladder"], first["level"]) == ("bedtime", 2)       # no refocus from the flicker


def test_a_written_scenario_and_its_mistakes():
    assert parse_spec("focused 5m, distracted 90s, accepted, drowsy 4m30s").segments == [
        ("focused", 300.0), ("distracted", 90.0), ("accepted", 0.0), ("drowsy", 270.0)]
    for bad, words in (("sleepy 5m", "is not a state"), ("focused five", "write a state and a time"),
                       ("accepted", "at least one state")):
        with pytest.raises(ValueError, match=words):
            parse_spec(bad)


def test_the_simulate_command(capsys):
    assert simulate_main(["phone_break"]) == 0
    out = capsys.readouterr().out
    assert "NUDGE  #1 refocus level 1" in out and "SUCCESS" in out and "outcomes:" in out
    assert simulate_main(["--list"]) == 0 and "late_evening" in capsys.readouterr().out
    assert simulate_main(["focused 5x"]) == 2


def test_simulate_over_a_recorded_session(tmp_path, capsys):
    """--session: A13's replay gives the states, the panel's reply markers the replies."""
    from eepy.logs.clock import monotonic
    from eepy.logs.features import ProcessEventLog
    from eepy.logs.session import SessionLog

    data = tmp_path / "sessions"
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    log.start("P20", mode="rules")
    events = ProcessEventLog("vision", data_dir=data, flush_seconds=0.05)
    face = {"coverage_s_10s": 10.0, "coverage_s_60s": 60.0, "fps_10s": 15.0, "face_absent_ratio_10s": 0.0,
            "face_absent_ratio_60s": 0.0, "perclos_60s": 0.02, "baseline_calibrated": True}
    t0 = monotonic()
    for second in range(400):
        away = 200 <= second < 300
        gaze = {"calibrated": True, "away_current_s": float(second - 200) if away else 0.0,
                "off_work_ratio_60s": 0.1, "no_face_ratio_60s": 0.0}
        events.event("face_window", t_mono=t0 + second, **face)
        events.event("gaze_window", t_mono=t0 + second, **gaze)
    log.event("annotation", source="wizard", t_mono=t0 + 250, marker="user_dismissed")
    events.close()
    log.end()
    assert simulate_main(["--session", "P20", "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 replies from the panel" in out
    assert "NUDGE  #1 refocus level 1" in out and "DISMISSED" in out


# --- the HMM's confidence (A16) ---------------------------------------------------------------------


def test_rules_estimates_ignore_the_confidence_section():
    """The rules give no confidence: the thresholds change nothing in the rules mode."""
    raw = copy.deepcopy(RAW)
    raw["guards"]["after_calibration"] = 0
    raw["actions"]["break_after_work"] = 0
    raw["confidence"] = {"act": {"refocus": 0.99, "bedtime": 0.99}, "direct": 0.99}
    strict = Harness(raw).run("focused", 10).run("distracted", 200)
    normal = Harness().run("focused", 10).run("distracted", 200)
    assert strict.nudges() == normal.nudges() == [(30.0, "refocus", 1), (60.0, "refocus", 2),
                                                  (90.0, "refocus", 3), (135.0, "refocus", 4)]
    assert [d["tone"] for d in strict.of("nudge_decision")] == [d["tone"] for d in normal.of("nudge_decision")]


def test_low_confidence_starts_no_episode_and_no_role():
    h = Harness().run("focused", 10, confidence=0.95).run("distracted", 40, confidence=0.62)
    assert h.nudges() == [] and h.engine.episode is None
    assert [r["to"] for r in h.of("role_changed")] == ["study_buddy"]
    assert h.engine.status(h.t)["next"] == "not sure enough yet: distracted 62% (acts at 70%)"
    waits = h.what("waiting_for_confidence")                   # logged once, not every tick
    assert len(waits) == 1 and waits[0]["ladder"] == "refocus" and waits[0]["confidence"] == 0.62
    assert waits[0]["needed"] == 0.7
    h.run("distracted", 5, confidence=0.75)
    ended = h.what("confidence_wait_ended")
    assert len(ended) == 1 and ended[0]["reason"] == "sure enough"
    assert [r["to"] for r in h.of("role_changed")] == ["study_buddy", "refocus"]
    assert h.nudges() and h.nudges()[0][1:] == ("refocus", 1)
    assert h.of("nudge_decision")[0]["context"]["confidence"] == 0.75
    assert h.engine.status(h.t)["act_confidence"] == {"act": {"refocus": 0.7, "bedtime": 0.7}, "direct": 0.85}


def test_a_confidence_wait_ends_when_the_state_changes():
    h = Harness().run("focused", 10, confidence=0.95).run("distracted", 40, confidence=0.6)
    h.run("focused", 5, confidence=0.9)
    ended = h.what("confidence_wait_ended")
    assert len(ended) == 1 and ended[0]["reason"] == "the state changed to focused"


def test_low_confidence_holds_later_nudges():
    h = Harness().run("focused", 10, confidence=0.95).run("distracted", 35, confidence=0.9)
    assert len(h.nudges()) == 1                          # level 1 at 30 s
    h.run("distracted", 60, confidence=0.6)              # level 2 would be due at 60 s
    assert len(h.nudges()) == 1
    assert h.engine.status(h.t)["next"].startswith("not sure enough yet")
    h.run("distracted", 3, confidence=0.8)
    assert len(h.nudges()) == 2 and h.nudges()[-1][2] == 2


def test_direct_tone_needs_confidence():
    policy = FixedEscalation(parse(RAW))
    very = {"state": "very_drowsy", "calibrated": True, "estimator": "hmm"}
    unsure = policy.decide(context(estimate=dict(very, confidence=0.8), ladder="bedtime", role="bedtime"))
    sure = policy.decide(context(estimate=dict(very, confidence=0.9), ladder="bedtime", role="bedtime"))
    assert (unsure.tone, sure.tone) == ("hedged", "direct")
    summary = context(estimate=dict(very, confidence=0.9, uncertainty=0.2, most_likely="very_drowsy")).summary()
    assert summary["estimator"] == "hmm" and summary["confidence"] == 0.9 and summary["most_likely"] == "very_drowsy"


def test_the_spoken_evidence_is_the_hmms_short_text_or_nothing():
    spoken: List[str] = []
    h = Harness()
    play = h.actuator.play_level

    def recording(ladder, level, intensity, tone, ctx):
        spoken.append(ctx["evidence"])
        return play(ladder, level, intensity, tone, ctx)

    h.actuator.play_level = recording
    h.run("focused", 10, confidence=0.95, say="").run("drowsy", 31, confidence=0.9,
                                                      say="eyes closed 18% of the last minute")
    h.run("drowsy", 60, confidence=0.9, say="")          # the HMM holds without a clear reason
    assert spoken[:2] == ["eyes closed 18% of the last minute", ""]
    rules = Harness()
    rules.actuator.play_level = lambda ladder, level, intensity, tone, ctx: spoken.append(ctx["evidence"]) or "x"
    rules.run("focused", 10).run("distracted", 31)
    assert spoken[-1] == "distracted (test)"             # the rules: their reason, as before
