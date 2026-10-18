"""Tests for the contextual bandit (A24): posterior updates for each reward type, the buckets, the
prior's centring (an untrained bandit resembles the fixed escalation), sampling only inside the
allowed set, reproducibility, persistence, discounting, the population prior, the wizard flag,
waiting, the fallback switch, the optional linear model, and the smoke simulation.
"""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from eepy.bandit.policy import (BUCKETS, BanditConfig, BanditConfigError, BanditPolicy, MemoryStore, Store,
                                WAIT, arm_of, bucket_of, load_config, parse)
from eepy.interaction.actions import allowed_actions, contains
from eepy.interaction.config import DEFAULT_PATH, parse as parse_escalation
from eepy.interaction.policy import FixedEscalation, NudgeContext, make_policy

RAW = yaml.safe_load(DEFAULT_PATH.read_text())
ESC = parse_escalation(copy.deepcopy(RAW))
CFG = load_config()


def bandit(**kw) -> BanditPolicy:
    cfg = kw.pop("config", CFG)
    return BanditPolicy(ESC, cfg, store=kw.pop("store", MemoryStore()), participant=kw.pop("participant", "P01"),
                        seed=kw.pop("seed", 1))


def ctx(state="distracted", ladder="refocus", level=None, confidence=0.95, calibrated=True, **kw):
    base = dict(now=100.0, estimate={"state": state, "confidence": confidence, "calibrated": calibrated,
                                     "estimator": "hmm"},
                state_seconds=60.0, role=ladder, ladder=ladder, level=level, last_outcome=None, restarting=False,
                nudges_session=0, nudges_last_hour=0, since_last_nudge=None, since_break=None,
                since_calibration=None, session_seconds=1200.0, hour=21.5)
    base.update(kw)
    c = NudgeContext(**base)
    from dataclasses import replace
    return replace(c, allowed=tuple(allowed_actions(c, ESC)))


def reward_event(reward, ladder="refocus", level=1, tone="hedged", source="auto", included=True, **context):
    snap = {"state": "distracted", "minutes_in_session": 20.0, "hour": 21.5}
    snap.update(context)
    return {"source": source, "included": included, "reward": reward, "ladder": ladder, "level": level,
            "tone": tone, "outcome": "x", "context": snap}


# --- the model --------------------------------------------------------------------------------


@pytest.mark.parametrize("reward, y", [(1.0, 1.0), (0.7, 0.85), (0.4, 0.7), (0.6, 0.8), (0.1, 0.55),
                                       (-0.1, 0.45), (-0.5, 0.25), (-1.0, 0.0)])
def test_each_reward_moves_the_posterior_by_its_scaled_value(reward, y):
    """success (graded 1.0 .. 0.4), accepted 0.6, snoozed 0.1, no reaction -0.1, dismissed -0.5,
    interrupted focus -1.0: alpha += (r+1)/2, beta += 1 - that."""
    b = bandit()
    a0, b0, _ = b.posterior("refocus_early", "refocus:1")
    assert b.observe(reward_event(reward))
    a1, b1, n = b.posterior("refocus_early", "refocus:1")
    assert a1 - a0 == pytest.approx(y) and b1 - b0 == pytest.approx(1 - y) and n == 1


def test_censored_and_wizard_nudges_do_not_update_by_default():
    b = bandit()
    assert not b.observe(reward_event(None, included=False))
    assert not b.observe(reward_event(1.0, source="wizard"))
    assert b.data["evidence"] == {}
    learning = bandit(config=parse({**yaml.safe_load(open("config/bandit.yaml")), "learn_from_wizard": True}))
    assert learning.observe(reward_event(1.0, source="wizard"))


def test_the_buckets():
    c = CFG
    assert bucket_of({"state": "distracted", "minutes_in_session": 10}, c) == "refocus_early"
    assert bucket_of({"state": "distracted", "minutes_in_session": 90, "minutes_since_break": 20}, c) == "refocus_early"
    assert bucket_of({"state": "distracted", "minutes_in_session": 50}, c) == "refocus_late"
    assert bucket_of({"state": "drowsy", "hour": 22.9}, c) == "bedtime_early"
    assert bucket_of({"state": "very_drowsy", "hour": 23.1}, c) == "bedtime_late"
    assert bucket_of({"state": "drowsy", "hour": 0.5}, c) == "bedtime_late"            # after midnight
    assert bucket_of({"state": "drowsy", "hour": None}, c) == "bedtime_early"
    assert bucket_of({"state": "focused", "ambiguous": True}, c) == "ambiguous"
    assert set(BUCKETS) == {"refocus_early", "refocus_late", "bedtime_early", "bedtime_late", "ambiguous"}


def test_an_update_lands_in_the_bucket_of_the_decision_time():
    b = bandit()
    b.observe(reward_event(1.0, ladder="bedtime", level=3, state="drowsy", hour=23.4))
    assert list(b.data["evidence"]) == ["bedtime_late"] and "bedtime:3" in b.data["evidence"]["bedtime_late"]


def test_the_prior_is_weak_and_gentle_first():
    b = bandit()
    means = [b.summary("refocus_early", f"refocus:{lv}")["mean"] for lv in (1, 2, 3, 4)]
    assert means == sorted(means, reverse=True) and means[0] == pytest.approx(0.6)
    a, bb, _ = b.posterior("refocus_early", "refocus:1")
    assert a + bb == pytest.approx(CFG.prior_strength)
    assert b.summary("bedtime_early", "bedtime:5")["mean"] == pytest.approx(-0.2)        # goodnight: cautious
    for _ in range(12):                                            # a few real outcomes outweigh the prior
        b.observe(reward_event(-0.1, level=1))
        b.observe(reward_event(1.0, level=3))
    assert b.summary("refocus_early", "refocus:3")["mean"] > b.summary("refocus_early", "refocus:1")["mean"]


def test_the_config_is_validated():
    raw = yaml.safe_load(open("config/bandit.yaml"))
    for bad in ({"model": "neural"}, {"arms": "everything"}, {"wait": {"value": -0.1}},
                {"prior": {"strength": 0}}, {"prior": {"mean": {"7x": 0.1}}}, {"discount": 0.1}, {"seed": "x"},
                {"surprise": 1}):
        with pytest.raises(BanditConfigError):
            parse({**raw, **bad})


# --- choosing ---------------------------------------------------------------------------------


def test_the_bandit_only_ever_picks_inside_the_allowed_set():
    b = bandit()
    contexts = [ctx(), ctx(level=1, ignored_levels=(1,)), ctx(level=2, ignored_levels=(1, 2), session_seconds=2000),
                ctx("drowsy", "bedtime"), ctx("very_drowsy", "bedtime", level=3, ignored_levels=(3,)),
                ctx("focused", "refocus", ambiguous=True, start_level=1), ctx(calibrated=False)]
    for c in contexts:
        for _ in range(50):
            a = b.decide(c)
            assert a.kind == "wait" or contains(c.allowed, a), (a, [x.level for x in c.allowed])
            assert 0 < a.propensity <= 1


def test_the_tone_follows_the_rule_and_the_choice_is_logged():
    b = bandit()
    a = b.decide(ctx(level=1, last_outcome="no_reaction", ignored_levels=(1,)))
    assert a.tone == "direct"                                       # calibrated, sure, after no reaction
    detail = a.extra["bandit"]
    assert detail["bucket"] == "refocus_early" and detail["chosen"] == arm_of(a.ladder, a.level, None, CFG)
    assert {r["arm"] for r in detail["arms"]} == {"refocus:2", "refocus:3", WAIT}
    assert all({"mean", "sd", "n", "sample", "words"} <= set(r) for r in detail["arms"])
    assert b.decide(ctx(calibrated=False)).tone == "hedged"


def test_a_fixed_seed_gives_the_same_choices():
    def run(seed):
        b = bandit(seed=seed)
        return [(b.decide(ctx(level=1, ignored_levels=(1,))).level, b.decide(ctx()).level) for _ in range(30)]
    assert run(7) == run(7)
    assert run(7) != run(8)


def test_an_untrained_bandit_resembles_the_fixed_escalation():
    fixed = FixedEscalation(ESC)
    agree = total = 0
    for seed in range(300):                                         # measured: 71 % over 2000 bandits
        b = bandit(seed=seed)
        level, ignored = None, ()
        for _ in range(4):                                          # a whole climb without reactions
            c = ctx(level=level, ignored_levels=ignored, session_seconds=2000,
                    last_outcome="no_reaction" if level else None)
            mine, theirs = b.decide(c), fixed.decide(c)
            if theirs.kind != "play":
                break
            total += 1
            agree += mine.kind == "play" and mine.level == theirs.level
            if mine.kind != "play":
                break
            level, ignored = mine.level, tuple(sorted(set(ignored) | {mine.level}))
    assert total > 900 and agree / total >= 0.65, agree / total


def test_the_bandit_waits_once_it_has_learned_that_nothing_helps():
    b = bandit()
    for _ in range(100):
        for lv in (1, 2):
            b.observe(reward_event(-1.0, level=lv))                 # interrupted focus, again and again
    waits = sum(b.decide(ctx()).kind == "wait" for _ in range(50))
    assert waits >= 45
    a = b.decide(ctx())
    assert a.extra["hold"] == CFG.wait_hold and a.extra["bandit"]["chosen"] == WAIT


# --- remembering ------------------------------------------------------------------------------


def test_posteriors_are_saved_per_participant_and_reset(tmp_path):
    store = Store(tmp_path / "bandit")
    b = bandit(store=store, participant="P07")
    b.observe(reward_event(1.0, level=2))
    data = json.loads((tmp_path / "bandit" / "P07.json").read_text())
    assert data["format"] == "eepy.bandit/1" and data["evidence"]["refocus_early"]["refocus:2"] == [1.0, 0.0, 1.0]
    again = bandit(store=store, participant="P07")
    assert again.posterior("refocus_early", "refocus:2") == b.posterior("refocus_early", "refocus:2")
    assert bandit(store=store, participant="P08").data["evidence"] == {}         # someone else: the prior
    assert store.participants() == ["P07"]
    assert store.reset("P07") and not (tmp_path / "bandit" / "P07.json").exists() and not store.reset("P07")
    assert store.path("../../etc/x").parent == tmp_path / "bandit"              # no escaping the folder


def test_discounting_lets_old_evidence_fade():
    raw = yaml.safe_load(open("config/bandit.yaml"))
    b = bandit(config=parse({**raw, "discount": 0.5}))
    b.observe(reward_event(1.0, level=1))
    b.observe(reward_event(1.0, level=2))                           # same bucket: level 1's evidence halves
    assert b.data["evidence"]["refocus_early"]["refocus:1"] == [0.5, 0.0, 0.5]
    assert bandit().config.discount == 1.0                         # off by default


def test_the_population_prior_starts_from_everyone_else_at_the_same_strength():
    store = MemoryStore()
    for who in ("P01", "P02"):
        b = bandit(store=store, participant=who)
        for _ in range(20):
            b.observe(reward_event(1.0, level=3))
    raw = yaml.safe_load(open("config/bandit.yaml"))
    raw["prior"] = {**raw["prior"], "population": True}
    new = bandit(config=parse(raw), store=store, participant="P03")
    a, bb, n = new.posterior("refocus_early", "refocus:3")
    assert n == 0 and a + bb == pytest.approx(CFG.prior_strength)  # still weak ...
    assert new.summary("refocus_early", "refocus:3")["mean"] > 0.8  # ... but where the others ended up
    plain = bandit(store=store, participant="P03")
    assert plain.summary("refocus_early", "refocus:3")["mean"] == pytest.approx(0.0)


# --- the switch, the engine, the panel ------------------------------------------------------


def test_the_switch_and_the_modes():
    assert isinstance(make_policy(ESC, "hmm_bandit", store=MemoryStore()), BanditPolicy)
    for mode in ("rules", "hmm", "wizard", None):
        assert isinstance(make_policy(ESC, mode, store=MemoryStore()), FixedEscalation)
    raw = copy.deepcopy(RAW)
    raw["policy"] = "fixed"
    assert isinstance(make_policy(parse_escalation(raw), "hmm_bandit"), FixedEscalation)


def _sim(policy, learner=None, seed=1, persona="average"):
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.bandit.simuser import run_responsive

    return run_responsive("focused 16m, distracted 6m, focused 3m, drowsy 12m", seed=seed, persona=persona,
                          policy=policy, learner=learner, start_hour=22.5)[0]


def test_in_the_engine_the_bandit_logs_its_choice_and_learns_online():
    b = bandit()
    rec = _sim(b, learner=b)
    decisions = [f for k, _, f in rec.events if k == "nudge_decision"]
    assert decisions and all(f["policy"] == "bandit" and f["chosen_in_allowed"] for f in decisions)
    assert all(f["bandit"]["bucket"] in BUCKETS and 0 < f["propensity"] <= 1 for f in decisions)
    assert b.data["updates"] > 0                                    # learned during the evening


def test_a_crashing_bandit_falls_back_to_the_fixed_escalation():
    class Broken(BanditPolicy):
        def decide(self, context):
            raise RuntimeError("bandit down")

    rec = _sim(Broken(ESC, CFG, store=MemoryStore()))
    decisions = [f for k, _, f in rec.events if k == "nudge_decision"]
    assert decisions and all(f["policy"] == "bandit" and not f.get("bandit") for f in decisions)
    assert any(k == "escalation" and f.get("what") == "policy_failed" for k, _, f in rec.events)


def test_the_bandit_learns_a_personas_preference():
    from eepy.bandit.simuser import load_personas

    b = bandit()
    persona = load_personas()["break_taker"]
    for i in range(25):
        _sim(b, learner=b, seed=i, persona=persona)
    means = {lv: b.summary("refocus_early", f"refocus:{lv}")["mean"] for lv in (1, 2, 3, 4)}
    assert max(means, key=means.get) == 4, means


def test_the_optional_linear_model_chooses_and_learns():
    pytest.importorskip("numpy")
    raw = yaml.safe_load(open("config/bandit.yaml"))
    b = bandit(config=parse({**raw, "model": "linear"}))
    c = ctx(level=1, ignored_levels=(1,))
    assert b.decide(c).level in (2, 3)
    before = b.linear.summary("refocus:3", {"probs": {"distracted": 0.9}, "confidence": 0.9})["mean"]
    for _ in range(30):
        b.observe(reward_event(1.0, level=3, probs={"distracted": 0.9}, confidence=0.9))
    after = b.linear.summary("refocus:3", {"probs": {"distracted": 0.9}, "confidence": 0.9})["mean"]
    assert after > before + 0.3


def test_the_words_for_the_panel():
    b = bandit()
    assert b.words("refocus:1") == "the wordless glance" and b.words(WAIT) == "waiting"
    assert b.sentence("refocus_early") is None                      # nothing tried yet
    for _ in range(9):
        b.observe(reward_event(1.0, level=1))
    assert b.sentence("refocus_early").startswith("EEPY has learned that the wordless glance works best")


def test_the_smoke_simulation_reports_both_policies():
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the library needs the IK solver")
    from eepy.bandit.smoke import run

    rep = run(sessions=3, only="needs_words")
    (p,) = rep["personas"]
    assert {"fixed", "bandit", "learned"} <= set(p) and p["bandit"]["nudges"] > 0
    assert set(p["learned"]) == set(BUCKETS)


def test_the_launcher_builds_the_bandit_commands():
    from eepy.launcher import bandit as tab

    assert tab.command({"kind": "show", "participant": "P03"}) == ["eepy.bandit", "show", "P03", "--json"]
    assert tab.command({"kind": "reset", "participant": "P03"})[-1] == "--yes"
    assert tab.command({"kind": "smoke", "sessions": 10})[:3] == ["eepy.bandit.smoke", "--sessions", "10"]
    for bad in ({"kind": "show", "participant": "../x"}, {"kind": "smoke", "sessions": 0}, {"kind": "x"}):
        with pytest.raises(ValueError):
            tab.command(bad)
