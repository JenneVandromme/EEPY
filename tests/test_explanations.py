"""Tests for the explanations (A20): the evidence catalog, the Explanation of a decision in
both modes, its faithfulness (the sum and the deletion test), the words, and the on/off
condition through the engine, the speaker and the panel's settings.
"""

from __future__ import annotations

import math
import re

import pytest

from eepy.explanations import catalog
from eepy.explanations.build import Explainer
from eepy.explanations.catalog import BY_ID, default_sentence, placeholder_values

STATES = ["focused", "distracted", "tired", "drowsy", "very_drowsy"]


def ledger_estimate(t, probs, ev, drift=None, state="drowsy", epoch=1, values=None):
    """A hand-made HMM estimate with a consistent ledger (probabilities over STATES)."""
    total = sum(probs)
    return {"t": t, "state": state, "estimator": "hmm", "confidence": 0.8, "calibrated": True,
            "values": values or {},
            "ledger": {"epoch": epoch, "states": STATES, "logb": [math.log(p / total) for p in probs],
                       "drift": drift or [0.0] * 5, "ev": ev}}


def consistent(start_probs, end_probs, ev):
    """The drift that makes evidence + drift equal the belief's change exactly."""
    lb0 = [math.log(p / sum(start_probs)) for p in start_probs]
    lb1 = [math.log(p / sum(end_probs)) for p in end_probs]
    return [b1 - b0 - sum(v[i] for v in ev.values()) for i, (b0, b1) in enumerate(zip(lb0, lb1))]


# --- the catalog -------------------------------------------------------------------------


def test_every_hmm_feature_has_exactly_one_evidence_type():
    np = pytest.importorskip("numpy")  # noqa: F841
    from eepy.state.hmm_params import load

    params = load()
    for spec in params.features:
        assert spec.id in catalog.TYPE_OF_FEATURE, f"{spec.id} is in no evidence type"
    owners = [f for t in catalog.CATALOG for f in t.features]
    assert len(owners) == len(set(owners))


def test_correlated_hmm_features_share_one_type():
    """A16's correlation groups are the catalog's grouping: one closing eye is one reason."""
    pytest.importorskip("numpy")
    from eepy.state.hmm_params import load

    by_group = {}
    for spec in load().features:
        if spec.group:
            by_group.setdefault(spec.group, set()).add(catalog.type_of_feature(spec.id))
    assert all(len(types) == 1 for types in by_group.values()), by_group


def test_every_rule_feature_maps_to_a_type_or_is_not_evidence():
    from eepy.state.config import load

    for rule in load().rules:
        feature = rule.when.feature
        if feature.endswith("_points") or rule.state in ("absent", "unknown"):
            continue
        assert catalog.type_of_input(feature), f"rule {rule.id}: {feature} has no evidence type"


def test_placeholders_fill_in_human_units_and_skip_meaningless_ones():
    v = placeholder_values("eye_closure", {"perclos_60s": 0.342, "blink_mean_ms_rel_60s": 1.04,
                                           "long_blinks_60s": 1, "blink_max_ms_60s": 1640}, 185)
    assert v["closed_pct"] == "34" and v["longest_s"] == "1.6" and v["minutes"] == "3"
    assert v["long_blinks_text"] == "one long blink"
    assert "times_normal" not in v                 # 1.04x is not worth saying
    assert placeholder_values("yawning", {"yawns_60s": 2})["yawns_times"] == "twice"
    away = placeholder_values("looking_away", {"gaze_off_ratio_60s": 0.5, "away_current_s": 136,
                                               "away_episodes_60s": 1})
    assert away["away_for"] == "about 2 minutes" and away["on_work_pct"] == "50"
    assert "away_count" not in away                # one episode is not "looking away again and again"
    glance = placeholder_values("looking_away", {"gaze_off_ratio_60s": 0.69, "away_current_s": 5})
    assert "away_for" not in glance                # live: "looking away for 5 seconds" after 69 % of the minute
    assert default_sentence("looking_away", "direct", glance) == "You were looking away 69% of the last minute."


def test_the_default_sentence_falls_back_to_one_without_numbers():
    assert default_sentence("eye_closure", "direct", {"closed_pct": "34", "minutes": "1"}) == \
        "Your eyes were closed 34% of the last minute."
    assert default_sentence("eye_closure", "direct", {"minutes": "1"}) == "Your eyes keep closing."
    # the lead input's sentence comes first
    both = {"closed_pct": "20", "times_normal": "2.5", "minutes": "1"}
    assert "2.5 times" in default_sentence("eye_closure", "hedged", both, lead="blink_mean_ms_rel_60s")
    for t in catalog.CATALOG:                     # every type can always say SOMETHING
        for tone in catalog.TONES:
            assert default_sentence(t.id, tone, {"minutes": "1"})


def test_every_template_uses_only_its_types_placeholders():
    for t in catalog.CATALOG:
        for tone in catalog.TONES:
            for template in t.templates[tone]:
                assert set(re.findall(r"\{([a-z_]+)\}", template.text)) <= set(t.placeholder_names), template


def test_the_catalog_doc_is_generated_and_up_to_date():
    assert catalog.DOC.exists(), "run python -m eepy.explanations.catalog --write"
    assert catalog.DOC.read_text(encoding="utf-8") == catalog.render_markdown(), \
        "docs/EXPLANATION_CATALOG.md is out of date: python -m eepy.explanations.catalog --write"
    text = catalog.render_markdown()
    for t in catalog.CATALOG:
        assert f"## {t.id}" in text and t.line_id in text
        for p in t.placeholder_names:
            assert f"{{{p}}}" in text


# --- the Explanation (hand-made ledgers) ------------------------------------------------


def two_point_explainer(start_probs, end_probs, ev, mid=None):
    x = Explainer()
    x.feed(ledger_estimate(0.0, start_probs, {}), 0.0)
    if mid is not None:
        x.feed(mid, 60.0)
    x.feed(ledger_estimate(120.0, end_probs, ev, consistent(start_probs, end_probs, ev),
                           values={"perclos_60s": 0.3}), 120.0)
    return x


def test_the_items_and_the_dynamics_add_up_to_the_change_in_log_odds():
    start, end = [0.6, 0.2, 0.1, 0.05, 0.05], [0.05, 0.3, 0.05, 0.55, 0.05]
    ev = {"eye_closure": [0.0, 0.0, 0.0, 3.0, 1.0], "looking_away": [1.0, -1.0, 1.0, 1.0, 1.0]}
    x = two_point_explainer(start, end, ev).explain("bedtime", 3, "direct", 120.0, 0.0)
    c = x["checks"]
    assert abs(c["sum_error"]) < 1e-9
    expected = (math.log(0.55) - math.log(0.3)) - (math.log(0.05) - math.log(0.2))
    assert c["log_odds_change"] == pytest.approx(expected, abs=1e-4)


def test_the_contrast_picks_the_runner_up_and_says_why():
    start, end = [0.6, 0.2, 0.1, 0.05, 0.05], [0.05, 0.3, 0.05, 0.55, 0.05]
    ev = {"eye_closure": [0.0, 0.0, 0.0, 3.0, 1.0], "looking_away": [1.0, -1.0, 1.0, 1.0, 1.0]}
    x = two_point_explainer(start, end, ev).explain("bedtime", 3, "direct", 120.0, 0.0)
    assert x["state"] == "drowsy" and x["runner_up"] == "distracted"      # 0.30 > focused 0.10
    assert x["items"][0]["type"] == "eye_closure"
    away = next(i for i in x["items"] if i["type"] == "looking_away")
    assert away["direction"] == "against"            # looking at the work: against distracted
    assert x["contrast"]["text"] == ("drowsy rather than distracted, because your eyes were closing more "
                                     "and you kept looking at your work")
    # with focus the runner-up instead
    end2 = [0.35, 0.05, 0.05, 0.5, 0.05]
    x2 = two_point_explainer(start, end2, ev).explain("bedtime", 3, "direct", 120.0, 0.0)
    assert x2["runner_up"] == "focused"


def test_evidence_is_summed_over_the_window_not_the_last_step():
    start, end = [0.6, 0.2, 0.1, 0.05, 0.05], [0.05, 0.1, 0.05, 0.75, 0.05]
    mid_ev = {"eye_closure": [0.0, 0.0, 0.0, 2.0, 0.5]}
    ev = {"eye_closure": [0.0, 0.0, 0.0, 3.0, 1.0]}
    mid = ledger_estimate(60.0, [0.3, 0.2, 0.1, 0.35, 0.05], mid_ev,
                          consistent(start, [0.3, 0.2, 0.1, 0.35, 0.05], mid_ev))
    x = two_point_explainer(start, end, ev, mid=mid)
    whole = x.explain("bedtime", 3, "direct", 120.0, 0.0)["items"][0]["contribution"]
    last = Explainer(window_min=10)
    for t, e in x.history:
        last.feed(e, t)
    recent = last.explain("bedtime", 3, "direct", 120.0, 60.0)["items"][0]["contribution"]
    assert whole == pytest.approx(3.0 - 0.0) and recent == pytest.approx(3.0 - 2.0)
    assert whole > recent


def test_a_reset_starts_the_window_at_the_reset():
    x = Explainer()
    x.feed(ledger_estimate(0.0, [0.25] * 4 + [0.0001], {"eye_closure": [0, 0, 0, 9, 0]}, epoch=1), 0.0)
    x.feed(ledger_estimate(100.0, [0.6, 0.2, 0.1, 0.05, 0.05], {}, epoch=2), 100.0)
    ev = {"eye_closure": [0.0, 0.0, 0.0, 1.0, 0.0]}
    x.feed(ledger_estimate(150.0, [0.3, 0.2, 0.1, 0.35, 0.05], ev,
                           consistent([0.6, 0.2, 0.1, 0.05, 0.05], [0.3, 0.2, 0.1, 0.35, 0.05], ev), epoch=2), 150.0)
    out = x.explain("bedtime", 1, "hedged", 150.0, 0.0)
    assert out["items"][0]["contribution"] == pytest.approx(1.0)        # not the old session's 9
    assert abs(out["checks"]["sum_error"]) < 1e-9


# --- the Explanation (the real HMM, simulated faces) ------------------------------------


def _sim(spec, estimator="hmm"):
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the escalation's library needs the IK solver")
    from eepy.interaction.simulate import run_estimates, synthetic_estimates

    estimates, _ = synthetic_estimates(spec, estimator)
    recorder, _, _ = run_estimates(estimates, [])
    return [f["explanation"] for kind, _, f in recorder.events if kind == "nudge_decision"], estimates


def test_real_hmm_explanations_are_exact_grouped_and_worded():
    explanations, estimates = _sim("work 3m, sleepy 8m")
    assert explanations
    keys = set().union(*(e.get("ledger", {}).get("ev", {}).keys() for e in estimates))
    assert keys <= set(BY_ID), keys              # the ledger is kept per TYPE, never per feature
    for x in explanations:
        assert x["mode"] == "hmm" and x["state"] in ("drowsy", "very_drowsy")
        assert abs(x["checks"]["sum_error"]) < 1e-3
        types = [i["type"] for i in x["items"]]
        assert len(types) == len(set(types))      # never the same thing twice
        assert x["items"][0]["type"] == "eye_closure" and x["sentence"]
        assert x["window"]["seconds"] >= 60


def test_the_deletion_test_lowers_the_decided_state():
    pytest.importorskip("numpy")
    from eepy.explanations.check import run_check
    from eepy.state.synthetic import simulate

    explanations, estimates = _sim("work 3m, phone 4m")
    features = simulate([("work", 180), ("phone", 240)], seed=1, aus=True, t0=1000.0)
    decisions = [(x["window"]["start"] + x["window"]["seconds"], x) for x in explanations]
    rows = run_check(features, decisions)
    assert rows and all(r["sum_ok"] and r["deletion_ok"] for r in rows), rows
    assert all(r["top"] == "looking_away" and r["p_without_top"] < r["p"] - 0.1 for r in rows)


def test_rules_mode_explanations_list_the_rules_that_fired():
    explanations, _ = _sim("work 3m, sleepy 8m", estimator="rules")
    assert explanations
    x = explanations[0]
    assert x["mode"] == "rules" and x["dynamics"] is None and x["checks"] == {}
    top = x["items"][0]
    assert top["type"] == "eye_closure" and top["contribution"] is None and top["seconds"] > 0
    assert top["rules"] and top["rule_value"]["threshold"] is not None
    assert x["sentence"] and x["contrast"]["text"].startswith(("drowsy rather", "very drowsy rather"))


# --- the condition: engine, speaker, player, settings -----------------------------------


def _engine(explanations):
    pytest.importorskip("numpy")
    from eepy.behaviors import reach

    if not reach.available():
        pytest.skip("the escalation's library needs the IK solver")
    from eepy.behaviors.loader import load_library
    from eepy.interaction.actuator import SimActuator
    from eepy.interaction.config import load
    from eepy.interaction.engine import EscalationEngine
    from eepy.interaction.policy import FixedEscalation
    from eepy.interaction.simulate import Recorder, VirtualClock, synthetic_estimates

    class Capturing(SimActuator):
        def __init__(self, *a):
            super().__init__(*a)
            self.contexts = []

        def play_level(self, ladder, level, intensity, tone, context):
            self.contexts.append((level, dict(context)))
            return super().play_level(ladder, level, intensity, tone, context)

    config = load()
    estimates, t0 = synthetic_estimates("work 3m, sleepy 8m")
    clock = VirtualClock(t0)
    actuator = Capturing(load_library(), clock)
    recorder = Recorder()
    engine = EscalationEngine(config, FixedEscalation(config), actuator, recorder, explanations=explanations)
    engine.session_started(t0)
    for e in estimates:
        clock.now = e["t"]
        engine.on_state(e, e["t"])
    return engine, actuator, [f for k, _, f in recorder.events if k == "nudge_decision"]


def test_explanations_on_are_spoken_after_spoken_levels_only():
    engine, actuator, decisions = _engine(True)
    for d, (level, ctx) in zip(decisions, actuator.contexts):
        x = d["explanation"]
        assert x["condition"] == "on"
        speaks = actuator.speaks(d["behavior"])
        assert x["spoken"] == speaks
        assert ("explanation" in ctx) == speaks
        if speaks:
            assert ctx["explanation"]["sentence"] == x["sentence"]
            assert ctx["explanation"]["line"] == "explain_" + x["items"][0]["type"]
        else:
            assert x["not_spoken"] == "this level does not speak"
    assert engine.status(engine.estimate_t)["explanation"]["decision_id"] == decisions[-1]["decision_id"]


def test_explanations_off_are_logged_but_never_spoken():
    engine, actuator, decisions = _engine(False)
    assert decisions and all(d["explanation"]["condition"] == "off" for d in decisions)
    assert not any(d["explanation"]["spoken"] for d in decisions)
    assert all(d["explanation"]["not_spoken"] == "explanations are off" for d in decisions)
    assert not any("explanation" in ctx for _, ctx in actuator.contexts)
    assert engine.status(engine.estimate_t)["explanations"] == "off"
    none, actuator, decisions = _engine(None)
    assert all(d["explanation"] is None for d in decisions)


def test_the_speaker_says_the_explanation_in_the_same_clip():
    from eepy.behaviors.loader import load_library
    from eepy.speech import Speaker

    shown = []
    speaker = Speaker(None, lines=lambda: load_library().lines, emit=lambda *a, **k: None,
                      on_text=lambda line, text: shown.append((line, text)))
    speaker.start()
    try:
        assert speaker.speak_line("refocus_check", "direct", then="  You were looking away.  ").wait(2)
    finally:
        speaker.close()
    line, text = shown[0]
    assert line == "refocus_check" and text.endswith("? You were looking away.")


def test_explanations_are_an_experimental_condition_in_the_settings(tmp_path):
    pytest.importorskip("pydantic")
    from eepy.settings import load_settings

    assert load_settings().explanations.enabled is True
    assert load_settings(explanations=False).explanations.enabled is False
    path = tmp_path / "eepy.yaml"
    path.write_text("explanations:\n  enabled: false\n  top_k: 2\n")
    s = load_settings(path)
    assert s.explanations.enabled is False and s.explanations.top_k == 2
    assert load_settings(path, explanations=True).explanations.top_k == 2


# --- the validator: explain_<type> lines -------------------------------------------------

_ANY_BEHAVIOUR = """
behaviors:
  - name: anything
    description: Placeholder.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""


def _lines(behaviors_dir, body):
    (behaviors_dir / "lines.yaml").write_text("lines:\n" + body)


def test_an_explanation_line_may_use_its_types_placeholders(behaviors_dir, write_behaviors, run_validator):
    _lines(behaviors_dir, """
  explain_eye_closure:
    description: Why, when the eyes were closing.
    variants:
      hedged:
        - "Your eyes were closed {closed_pct}% of the last {minutes} minutes."
      direct:
        - "Your eyes keep closing."
""")
    write_behaviors(_ANY_BEHAVIOUR)
    assert not run_validator().errors


def test_an_explanation_line_rejects_another_types_placeholder(behaviors_dir, write_behaviors, run_validator):
    _lines(behaviors_dir, """
  explain_eye_closure:
    description: Wrong placeholder.
    variants:
      direct:
        - "You yawned {yawns_times}."
""")
    write_behaviors(_ANY_BEHAVIOUR)
    report = " ".join(e.message + " " + (e.hint or "") for e in run_validator().errors)
    assert "{yawns_times}" in report and "for this evidence type" in report and "{closed_pct}" in report


def test_an_explanation_line_for_an_unknown_type_is_an_error(behaviors_dir, write_behaviors, run_validator):
    _lines(behaviors_dir, """
  explain_eye_closing:
    description: Typo in the type.
    variants:
      direct:
        - "Your eyes keep closing."
""")
    write_behaviors(_ANY_BEHAVIOUR)
    report = " ".join(e.message + " " + (e.hint or "") for e in run_validator().errors)
    assert "not an evidence type" in report and "explain_eye_closure" in report
