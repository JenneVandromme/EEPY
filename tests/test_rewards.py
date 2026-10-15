"""Tests for the bandit's rewards (A22): the price of each outcome, latency grading, the
recovery discount, censored and overlapping nudges, the tracker's delayed rewards, no future
information in the context, offline = online, the report, the reactive simulated user, and
the launcher's Rewards tab.
"""

from __future__ import annotations

import json

import pytest

from eepy.bandit.context import FocusTrend, hour_of, snapshot
from eepy.bandit.rewards import (RewardConfig, RewardConfigError, RewardTracker, discount, load_config, price,
                                 records_from_recorder, report, rewards_from_stream)

CFG = RewardConfig()


class Esc:
    """The parts of the escalation config the tracker uses."""
    reset_after = 10.0
    false_alarm_seconds = 5.0
    went_to_bed_after = 300.0
    break_offer_level = 4
    break_absent_within = 60.0
    goodnight_level = 5

    class _L:
        def __init__(self, reaction):
            self.reaction = reaction
    ladders = {"refocus": _L({1: 30, 2: 30, 3: 45, 4: 120}), "bedtime": _L({1: 60, 2: 60, 3: 90, 4: 120})}


def decision(t, did=1, ladder="refocus", level=1, **context):
    ctx = {"state": "distracted", "probs": {"focused": 0.1, "distracted": 0.9, "drowsy": 0.0, "very_drowsy": 0.0},
           "confidence": 0.9, "estimator": "hmm", "calibrated": True, "session_seconds": 600, "nudges_session": 0}
    ctx.update(context)
    return {"type": "nudge_decision", "t_mono": t, "t_wall": "2026-10-15T22:15:00.000+02:00", "decision_id": did,
            "ladder": ladder, "level": level, "tone": "hedged", "intensity": 1.0, "behavior": "x", "policy": "fixed",
            "propensity": 1.0, "reaction_window": Esc.ladders[ladder].reaction.get(level), "context": ctx}


def outcome(t, did=1, out="success", **kw):
    return dict({"type": "nudge_outcome", "t_mono": t, "decision_id": did, "outcome": out, "detail": "", "seconds": 0},
                **kw)


def tracker():
    got = []
    tr = RewardTracker(CFG, Esc(), emit=got.append)
    tr.on_record({"type": "session_start", "t_mono": 0.0, "session": "S"})
    return tr, got


# --- the price of each outcome --------------------------------------------------------------


def test_each_outcome_gets_its_reward():
    ctx = {}
    assert price(CFG, "success", "bedtime", "bed", None, None, ctx)[0] == 1.0
    assert price(CFG, "success", "refocus", "break", None, 120, ctx)[0] == 1.0
    assert price(CFG, "success", "refocus", "reply", None, 30, ctx)[0] == 0.6
    assert price(CFG, "snoozed", "bedtime", None, None, 120, ctx)[0] == 0.1
    assert price(CFG, "no_reaction", "refocus", None, None, 30, ctx)[0] == -0.1
    assert price(CFG, "dismissed", "refocus", None, None, 30, ctx)[0] == -0.5
    assert price(CFG, "interrupted_focus", "refocus", None, None, 30, ctx)[0] == -1.0
    assert price(CFG, "censored", "refocus", None, None, 30, ctx)[0] is None
    with pytest.raises(ValueError):
        price(CFG, "exploded", "refocus", None, None, 30, ctx)


def test_a_faster_return_to_focus_earns_more():
    r = lambda back: price(CFG, "success", "refocus", "focus", back, 40, {})[0]   # noqa: E731
    assert r(0) == 1.0 and r(40) == pytest.approx(0.4) and r(20) == pytest.approx(0.7)
    assert r(80) == pytest.approx(0.4)                    # past the window: never below "worst"
    assert r(5) > r(15) > r(30)


def test_the_recovery_discount_follows_the_belief_trend():
    assert discount(CFG, {"focused_trend_20s": 0.3}) == 0.75
    assert discount(CFG, {"focused_trend_20s": 0.15}) == pytest.approx(0.375)
    assert discount(CFG, {"focused_trend_20s": 0.9}) == 0.75
    assert discount(CFG, {"focused_trend_20s": -0.2}) == 0.0
    assert discount(CFG, {"estimator": "rules", "pending": "focused"}) == 0.5
    assert discount(CFG, {"estimator": "rules", "pending": None}) == 0.0
    full = price(CFG, "success", "refocus", "focus", 0, 30, {})[0]
    half = price(CFG, "success", "refocus", "focus", 0, 30, {"focused_trend_20s": 0.15})
    assert half[0] == pytest.approx(full * 0.625) and half[1] == full and half[2] == pytest.approx(0.375)
    # only successes, and only on the refocus ladder
    assert price(CFG, "no_reaction", "refocus", None, None, 30, {"focused_trend_20s": 0.3})[0] == -0.1
    assert price(CFG, "success", "bedtime", "bed", None, None, {"focused_trend_20s": 0.3})[0] == 1.0


def test_the_config_loads_and_refuses_nonsense(tmp_path):
    cfg = load_config()
    assert cfg.interrupted_focus == -1.0 and cfg.snoozed == 0.1 and cfg.path == "config/rewards.yaml"
    bad = tmp_path / "r.yaml"
    bad.write_text("outcomes:\n  refocus_success: {best: 0.2, worst: 0.9}\n")
    with pytest.raises(RewardConfigError):
        load_config(bad)
    bad.write_text("recovery: {max_discount: 2}\n")
    with pytest.raises(RewardConfigError):
        load_config(bad)


# --- the tracker ---------------------------------------------------------------------------


def test_an_escalation_nudge_is_priced_when_its_outcome_comes():
    tr, got = tracker()
    tr.on_record(decision(100.0, focused_trend_20s=0.0))
    assert got == []                                     # pending until the outcome
    tr.on_record(outcome(140.0, how="focus", back_after=15.0, seconds=40.0))
    (e,) = got
    assert (e.source, e.outcome, e.how, e.latency) == ("auto", "success", "focus", 15.0)
    assert e.reward == pytest.approx(1.0 - 0.6 * 15 / 30) and e.included
    assert e.t_session == 100.0 and e.context["hour"] == 22.25 and e.context["level"] == 1


def test_older_logs_without_how_are_read_from_the_detail():
    tr, got = tracker()
    tr.on_record(decision(0.0))
    tr.on_record(outcome(25.0, detail="back to focused after 12 s", seconds=25.0))
    assert got[0].how == "focus" and got[0].latency == 12.0
    tr.on_record(decision(50.0, did=2, ladder="bedtime", level=3))
    tr.on_record(outcome(60.0, did=2, detail="goodnight confirmed"))
    assert got[1].how == "bed" and got[1].reward == 1.0


def test_censored_nudges_are_counted_but_get_no_reward():
    tr, got = tracker()
    tr.on_record(decision(0.0))
    tr.on_record(outcome(10.0, out="censored", detail="the belief moved to drowsy"))
    assert got[0].reward is None and not got[0].included
    rep = report(got)
    assert rep["censored"] == 1 and rep["censored_share"] == 1.0
    assert rep["censored_reasons"] == {"the belief moved to drowsy": 1}


def test_operator_nudges_are_judged_with_the_escalations_rules():
    tr, got = tracker()
    tr.on_state(90.0, "distracted")
    tr.on_record({"type": "wizard_nudge", "t_mono": 100.0, "ladder": "refocus", "level": 2, "behavior": "b",
                  "reaction_window": 30, "context": {"state": "distracted"}})
    tr.on_state(120.0, "focused")                         # back after 20 s
    tr.tick(125.0)
    assert got == []                                      # 10 s of focus needed
    tr.tick(131.0)
    (e,) = got
    assert (e.source, e.outcome, e.latency, e.policy) == ("wizard", "success", 20.0, None)
    assert e.reward == pytest.approx(1.0 - 0.6 * 20 / 30)


def test_a_pending_nudge_is_emitted_only_after_its_window_and_at_the_same_time_whenever_checked():
    tr, got = tracker()
    tr.on_state(0.0, "distracted")
    tr.on_record({"type": "wizard_nudge", "t_mono": 10.0, "ladder": "refocus", "level": 1, "behavior": "b",
                  "reaction_window": 30, "context": {}})
    tr.tick(39.0)
    assert got == []
    tr.tick(95.0)                                         # checked late: still decided at the window's end
    assert got[0].outcome == "no_reaction" and got[0].latency == 30.0 and got[0].reward == -0.1


def test_operator_nudge_replies_and_focus_at_the_press():
    tr, got = tracker()
    tr.on_state(0.0, "focused")
    tr.on_record({"type": "wizard_nudge", "t_mono": 10.0, "ladder": "refocus", "level": 1, "behavior": "b",
                  "reaction_window": 30, "context": {}})
    assert got[-1].outcome == "interrupted_focus" and got[-1].reward == -1.0
    tr.on_state(20.0, "drowsy")
    tr.on_record({"type": "wizard_nudge", "t_mono": 30.0, "ladder": "bedtime", "level": 4, "behavior": "b",
                  "reaction_window": 120, "context": {}})
    tr.on_record({"type": "annotation", "t_mono": 40.0, "marker": "user_snoozed"})
    assert got[-1].outcome == "snoozed" and got[-1].reward == 0.1
    tr.on_record({"type": "wizard_nudge", "t_mono": 50.0, "ladder": "bedtime", "level": 3, "behavior": "b",
                  "reaction_window": 90, "context": {}})
    tr.on_record({"type": "session_end", "t_mono": 60.0})
    assert got[-1].outcome == "success" and got[-1].how == "bed"


def test_overlap_only_the_newest_nudge_counts():
    tr, got = tracker()
    tr.on_state(0.0, "distracted")
    tr.on_record({"type": "wizard_nudge", "t_mono": 10.0, "ladder": "refocus", "level": 1, "behavior": "b",
                  "reaction_window": 30, "context": {}})
    tr.on_record({"type": "wizard_nudge", "t_mono": 15.0, "ladder": "refocus", "level": 3, "behavior": "c",
                  "reaction_window": 45, "context": {}})
    assert got[-1].decision_id == "w1" and got[-1].outcome == "censored"
    tr.on_record(decision(20.0, did=7))                   # the escalation's nudge starts: the operator's closes
    assert got[-1].decision_id == "w2" and got[-1].outcome == "censored"
    assert tr.wizard is None and "7" in tr.pending


# --- no future information ---------------------------------------------------------------


def test_the_context_holds_only_decision_time_information():
    ctx = {"state": "distracted", "probs": {"focused": 0.2, "distracted": 0.8}, "focused_trend_20s": 0.05,
           "session_seconds": 1200, "since_break": None, "level": 2, "nudges_session": 3, "calibrated": True}
    snap = snapshot(ctx, level=3, t_wall="2026-10-15T23:30:00+02:00")
    assert snap["level"] == 3 and snap["previous_level"] == 2 and snap["hour"] == 23.5
    assert snap["minutes_in_session"] == 20.0 and snap["p_distracted"] == 0.8
    # whatever comes later, the event keeps the snapshot taken at the decision
    tr, got = tracker()
    rec = decision(0.0, focused_trend_20s=0.0)
    tr.on_record(rec)
    before = dict(tr.pending["1"].context)
    rec["context"]["state"] = "focused"                   # a later change to the logged dict
    tr.on_record(outcome(30.0, out="no_reaction", state="focused", seconds=30.0))
    assert got[0].context == before
    assert not {"outcome", "reward", "latency"} & set(got[0].context)


def test_the_focus_trend_never_looks_past_the_decision():
    trend = FocusTrend()
    for t in range(0, 31):
        trend.add({"probs": {"focused": 0.1 + 0.01 * t}, "state": "distracted"}, float(t))
    at_30 = trend.trend(30.0)
    assert at_30 == pytest.approx(0.2)                     # 0.40 - 0.20
    trend.add({"probs": {"focused": 0.95}, "state": "focused"}, 31.0)
    assert trend.trend(30.0) == pytest.approx(0.2)         # a later point never changes a decision's trend
    assert FocusTrend().trend(10.0) is None
    assert hour_of(None) is None and hour_of("2026-10-15T07:05:00") == pytest.approx(7.08)


def test_the_engine_logs_the_trend_from_before_the_decision():
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.interaction.simulate import run_scenario, parse_spec

    recorder, _, _ = run_scenario(parse_spec("focused 4m, distracted@0.9 3m, focused 2m"))
    decisions = [f for k, _, f in recorder.events if k == "nudge_decision"]
    assert decisions
    for d in decisions:
        assert "focused_trend_20s" in d["context"] and d["context"]["level_played"] == d["level"]
    outcomes = [f for k, _, f in recorder.events if k == "nudge_outcome" and f["outcome"] == "success"]
    assert all("how" in o and "back_after" in o for o in outcomes)


# --- offline, simulation, report -------------------------------------------------------------


def _sim_records(spec="focused 15m, distracted 3m, snoozed, distracted 4m, focused 3m"):
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.interaction.simulate import parse_spec, run_scenario

    recorder, _, _ = run_scenario(parse_spec(spec))
    return records_from_recorder(recorder.events, 0.0)


def test_offline_and_online_give_the_same_rewards():
    from eepy.interaction.config import load as load_escalation

    recs = _sim_records()
    online, got = RewardTracker(CFG, load_escalation(), emit=None), []
    online.emit = got.append
    for r in recs:                                        # live: each event as it is logged
        online.on_record(r)
    offline = rewards_from_stream([(r["t_mono"], 1, "record", r) for r in recs], CFG, load_escalation())
    key = lambda e: (e.decision_id, e.outcome, e.reward, e.latency, e.discount)   # noqa: E731
    assert [key(e) for e in got] == [key(e) for e in offline] and offline
    assert {e.outcome for e in offline} >= {"snoozed", "no_reaction"}


def test_the_report_groups_and_flags():
    from eepy.interaction.config import load as load_escalation

    events = rewards_from_stream([(r["t_mono"], 1, "record", r) for r in _sim_records()], CFG, load_escalation())
    rep = report(events, [{"session": "S", "operator_plays": 2, "wizard_nudges": 0, "states": 0, "notes": [],
                           "logged_rewards": []}])
    assert rep["nudges"] == len(events) and rep["by_action"] and rep["by_state"] and rep["by_hour"]
    assert any("before A22" in s for s in rep["suspicious"])
    assert json.dumps(rep)                                # the launcher gets it as JSON


def test_the_reactive_user_answers_nudges_and_is_reproducible():
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.bandit.simuser import run_responsive
    from eepy.interaction.config import load as load_escalation

    spec = "focused 3m, distracted 6m, focused 2m, drowsy 12m"
    outcomes = []
    for seed in range(1, 9):
        recorder, t0 = run_responsive(spec, seed=seed, persona="responsive")
        recs = records_from_recorder(recorder.events, t0)
        outcomes += [e.outcome for e in rewards_from_stream([(r["t_mono"], 1, "record", r) for r in recs],
                                                             CFG, load_escalation())]
    assert "success" in outcomes and "no_reaction" in outcomes
    a, _ = run_responsive(spec, seed=3)
    b, _ = run_responsive(spec, seed=3)
    assert [(k, t) for k, t, _ in a.events] == [(k, t) for k, t, _ in b.events]
    with pytest.raises(ValueError):
        run_responsive("focused 1m, snoozed, distracted 2m")


def test_the_launcher_builds_the_right_command():
    from eepy.launcher import rewards as tab

    cmd = tab.command({"kind": "responsive", "spec": "focused 1m, distracted 2m", "persona": "stubborn", "runs": 5})
    assert cmd[1:4] == ["-m", "eepy.bandit.rewards", "--json"]
    assert "--responsive" in cmd and "stubborn" in cmd and "--out" in cmd
    assert tab.command({"kind": "recorded", "sessions": ["all"], "rerun": "hmm"})[4:7] == ["all", "--rerun", "hmm"]
    for bad in ({"kind": "recorded", "sessions": ["../x"]}, {"kind": "synthetic", "spec": ""},
                {"kind": "responsive", "spec": "focused 1m", "runs": 0}, {"kind": "nope"}):
        with pytest.raises(ValueError):
            tab.command(bad)


def test_went_to_bed_is_timed_from_leaving_not_from_the_confirmation():
    """Leaving counts as going to bed only after 5 minutes away; the latency is until they LEFT."""
    tr, got = tracker()
    tr.on_state(0.0, "drowsy")
    tr.on_record({"type": "wizard_nudge", "t_mono": 10.0, "ladder": "bedtime", "level": 3, "behavior": "b",
                  "reaction_window": 90, "context": {}})
    tr.on_state(40.0, "absent")
    tr.tick(400.0)
    assert got[0].outcome == "success" and got[0].how == "bed" and got[0].latency == 30.0
    assert not any("window" in s for s in report(got)["suspicious"])
