"""The HMM state estimator (A16) on synthetic feature sequences.

No camera: a small simulator turns per-second behaviour (working, on the phone,
acting sleepy, away from the desk) into the window features the vision process
logs, through the real assemble(). Covers the parameter file, the maths
(expm, rate independence, recovery of known states), behaviour, missing data,
absent/unknown, the double-counting correction, the reported state and its
reasons, and the runner/replay/logging integration.

Numbers in [brackets] are what the real implementation measured when the
thresholds were set (A16); the thresholds keep a margin.
"""

from __future__ import annotations

import copy
import math
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pytest
import yaml

from eepy.state import hmm_model as model
from eepy.state.estimate import for_mode, make_estimator
from eepy.state.features import assemble, empty
from eepy.state.hmm import HMMEstimator
from eepy.state.hmm_params import DEFAULT_PATH, HIDDEN, HMMConfigError, dump, load, loads, parse
from eepy.state.synthetic import SCENARIO, closures, simulate

REPO = Path(__file__).resolve().parents[1]
RAW = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
PARAMS = load()
EYE_GROUP = ("perclos", "blink_duration", "long_blinks", "microsleep", "eyes_closed_now", "au_eyes_closed")


def params_with(change: Callable[[Dict[str, Any]], Any]) -> Any:
    raw = copy.deepcopy(RAW)
    change(raw)
    return parse(raw)




def run(features: List[Dict[str, Any]], est: Optional[HMMEstimator] = None) -> List[Any]:
    est = est or HMMEstimator(PARAMS)
    return [est.update(dict(f)) for f in features]


def switches(estimates: List[Any], t0: float = 1000.0) -> List[Tuple[float, str]]:
    out, last = [], None
    for e in estimates:
        if e.state != last:
            out.append((round(e.t - t0, 3), e.state))
            last = e.state
    return out


def first_at(estimates: List[Any], states: Tuple[str, ...], after: float, t0: float = 1000.0) -> Optional[float]:
    return next((e.t - t0 - after for e in estimates if e.t - t0 > after and e.state in states), None)


def p_sleepy(e: Any) -> float:
    return e.scores["drowsy"] + e.scores["very_drowsy"]


# A calm, calibrated, alert person (as test_state.py's FACE/GAZE).
FACE = {"coverage_s_10s": 10.0, "coverage_s_60s": 60.0, "fps_10s": 15.0, "fps_60s": 15.0,
        "face_absent_ratio_10s": 0.0, "face_absent_ratio_60s": 0.0,
        "perclos_10s": 0.02, "perclos_60s": 0.02, "blink_confidence_10s": "ok", "blink_confidence_60s": "ok",
        "blink_rate_60s": 15.0, "blink_mean_ms_60s": 150.0, "blink_mean_ms_rel_60s": 1.0,
        "blink_max_ms_60s": 220.0, "long_blinks_60s": 0, "yawns_60s": 0, "nods_60s": 0,
        "eyes_closed_now_s": 0.0, "eyes_measured_s_10s": 10.0, "eyes_measured_s_60s": 60.0,
        "baseline_calibrated": True}
GAZE = {"calibrated": True, "off_work_ratio_10s": 0.0, "off_work_ratio_60s": 0.05,
        "no_face_ratio_10s": 0.0, "no_face_ratio_60s": 0.0, "away_current_s": 0.0}
AUS = {"status": "running", "rate": 10.0, "results_60s": 600, "results_eyes_60s": 480,
       "AU43_active_60s": 0.08, "AU26_active_60s": 0.02}


def feats(t: float = 100.0, face: Optional[Dict[str, Any]] = None, gaze: Optional[Dict[str, Any]] = None,
          aus: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return assemble(t, {**FACE, **(face or {})}, {**GAZE, **(gaze or {})}, aus)


# --- 1-5: the parameter file -----------------------------------------------------------------------


def _numbers_have_why(node: Any, path: str, parent: Optional[Dict[str, Any]] = None, key: str = "") -> List[str]:
    """Every number in the file sits in a mapping with a non-empty why, or next to a <key>_why."""
    bad: List[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            bad += _numbers_have_why(v, f"{path}.{k}", node, str(k))
    elif isinstance(node, list):
        for v in node:
            bad += _numbers_have_why(v, path, parent, key)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        ok = parent is not None and (str(parent.get("why", "")).strip() or str(parent.get(f"{key}_why", "")).strip())
        if not ok and key != "version":
            bad.append(path)
    return bad


def test_shipped_params_load():
    assert len(PARAMS.features) == 11 and all(f.enabled for f in PARAMS.features)
    assert set(PARAMS.groups) == {"eye_closure", "yawning", "gaze"}
    assert PARAMS.source == "hand_set" and PARAMS.raw["fit"] is None
    assert _numbers_have_why(RAW, "") == []
    # the agreed choices (docs/A16_HMM_DECISIONS.md B1-B3)
    assert PARAMS.groups["eye_closure"] == 0.5
    assert PARAMS.feature("au_jaw_drop").tau == 60 and PARAMS.feature("gaze_off").tau == 20


def _set(path: str, value: Any) -> Callable[[Dict[str, Any]], None]:
    def change(raw: Dict[str, Any]) -> None:
        node = raw
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        if value is _DELETE:
            del node[keys[-1]]
        else:
            node[keys[-1]] = value
    return change


_DELETE = object()


@pytest.mark.parametrize("change, message", [
    (_set("format", "eepy.hmm/2"),
     'format must be "eepy.state_hmm/1", not "eepy.hmm/2" (is this a parameter file from another version?).'),
    (_set("transitions_per_hour.focused.distracted.value", -6),
     "transitions_per_hour.focused.distracted must be a number >= 0 (times per hour), not -6."),
    (_set("transitions_per_hour.drowsy.focused", _DELETE),
     'transitions_per_hour.drowsy: missing "focused" (every pair of different states needs a rate).'),
    (lambda raw: [raw["transitions_per_hour"][s]["very_drowsy"].update(value=0)
                  for s in raw["states"] if s != "very_drowsy"],
     "transitions_per_hour: very_drowsy can never be reached (all rates into it are 0)."),
    (lambda raw: raw["initial"]["focused"].update(value=raw["initial"]["focused"]["value"] - 0.05),
     "initial: the probabilities add up to 0.95; they must add up to 1."),
    (_set("features.perclos.input", "perclos_6s"),
     'features.perclos: unknown input "perclos_6s" (did you mean "perclos_60s"?). The features are listed '
     "in eepy/state/README.md."),
    (_set("features.perclos.params.drowsy.mean", 1.2),
     "features.perclos.params.drowsy: mean must be between 0 and 1 (exclusive), not 1.2."),
    (_set("features.away_now.params.focused.probs", [0.8, 0.17, 0.025, 0.004, 0.001]),
     "features.away_now.params.focused: probs has 5 values; bins [0, 5, 10, 20, 60] need 6 (one more than the edges)."),
    (_set("features.away_now.bins", [0, 10, 5, 20, 60]), "features.away_now.bins must increase: [0, 10, 5, 20, 60]."),
    (_set("features.yawns.family", "poison"),
     'features.yawns: family "poison" is not one of beta, student_t, gaussian, poisson, bernoulli, categorical.'),
    (_set("features.nods.params.focused.why", _DELETE),
     'features.nods.params.focused needs a "why" (one line: where the number comes from).'),
    (_set("features.perclos.clip_why", _DELETE), "features.perclos: clip needs a clip_why."),
    (_set("features.perclos.group", "eyes"),
     'features.perclos.group "eyes" is not in groups (have: eye_closure, gaze, yawning).'),
    (_set("groups.gaze.correlation.value", 1.5), "groups.gaze.correlation must be between 0 and 1, not 1.5."),
    (_set("modulation.enabled", True), "modulation is not implemented yet (A16 keeps the slot; see eepy/state/README.md)."),
    (_set("source", "fitted"),
     'source is "fitted", so a fit: block is needed (method, fitted_at, sessions, n_sessions, n_seconds, log_likelihood).'),
    (lambda raw: raw.update(source="fitted", fit={"method": "map", "fitted_at": "2026-11-02", "sessions": ["a", "b"],
                                                  "n_sessions": 3, "n_seconds": 10, "log_likelihood": -1.0}),
     "fit.n_sessions is 3 but fit.sessions lists 2."),
    (_set("features.perclos.needs", ["eyes_measured_s_60s >> 20"]),
     'features.perclos.needs: "eyes_measured_s_60s >> 20" is not a condition like "perclos_60s >= 0.12" '
     "(compare with one of >= > <= < == !=)."),
    (_set("modulation.applies_to", ["focused->sleepy"]),
     'modulation.applies_to: "focused->sleepy" is not a pair of two different states.'),
    (_set("features.microsleep.missing_value_why", _DELETE), "features.microsleep: missing_value needs a missing_value_why."),
    (_set("fit", {"method": "x"}), 'source is "hand_set", so fit must be null'),
    (_set("transitions_per_hour.focused.drowsy", 1),
     'transitions_per_hour.focused.drowsy needs a "why" (one line: where the number comes from)'),
])
def test_param_mistakes_are_explained(change, message):
    raw = copy.deepcopy(RAW)
    change(raw)
    with pytest.raises(HMMConfigError) as err:
        parse(raw)
    assert str(err.value).startswith("config/state_hmm.yaml: ")
    assert message in str(err.value)


def test_dump_load_round_trip(tmp_path):
    text = dump(PARAMS)
    again = loads(text)
    assert again == PARAMS
    assert dump(again) == text
    from eepy.state.hmm_params import save
    save(PARAMS, tmp_path / "p.yaml")
    assert load(tmp_path / "p.yaml") == PARAMS


def test_a17_fitted_file_contract():
    raw = copy.deepcopy(RAW)
    raw.update(source="fitted", version=2, fit={
        "method": "map", "fitted_at": "2026-11-02T14:00:00+01:00", "code_commit": "abc1234",
        "parent": {"version": 1, "sha256": "0" * 64}, "sessions": ["2026-10-20_P03_rules", "2026-10-21_P05_rules"],
        "n_sessions": 2, "n_seconds": 7200, "labels": "wizard_annotations", "log_likelihood": -12345.6,
        "log_likelihood_per_second": -1.71, "fixed": ["states"], "notes": ""})
    raw["transitions_per_hour"]["focused"]["drowsy"]["prior_strength"] = 3600
    raw["features"]["perclos"]["params"]["drowsy"]["prior_strength"] = 20
    raw["features"]["perclos"]["params"]["drowsy"]["why"] = "fitted: 812 s labelled drowsy (2 sessions)"
    fitted = parse(raw)
    text = dump(fitted)
    assert loads(text) == fitted and dump(loads(text)) == text
    assert "prior_strength: 3600" in text and "prior_strength: 20" in text
    assert fitted.raw["fit"]["sessions"] == ["2026-10-20_P03_rules", "2026-10-21_P05_rules"]
    raw["transitions_per_hour"]["focused"]["drowsy"]["prior_strength"] = -1
    with pytest.raises(HMMConfigError, match="prior_strength must be a number >= 0"):
        parse(raw)


def test_conditions_share_the_rules_syntax():
    with pytest.raises(HMMConfigError, match='unknown feature "eyes_measured_s_6s" \\(did you mean "eyes_measured_s_60s"\\?\\)'):
        params_with(_set("features.perclos.needs", ["eyes_measured_s_6s >= 20"]))
    with pytest.raises(HMMConfigError, match="did you mean"):
        params_with(_set("absent.enter_when", "face_absent_ratio_10 >= 0.8"))


# --- 6-10: the maths ----------------------------------------------------------------------------------

Q = model.generator(PARAMS.rates_per_hour)


@pytest.mark.parametrize("dt", [0, 1e-3, 1, 7.3, 600, 86400, 1e7])
def test_expm_is_a_proper_transition_matrix(dt):
    p = model.transition(Q, dt)
    assert (p >= 0).all()
    assert np.allclose(p.sum(axis=1), 1, atol=1e-12)
    if dt == 0:
        assert np.array_equal(p, np.eye(len(PARAMS.states)))
    a, b = dt * 0.3, dt * 0.7
    assert np.allclose(model.transition(Q, a) @ model.transition(Q, b), p, atol=1e-12)
    if dt == 1e7:
        assert np.allclose(p, model.stationary(Q)[None, :], atol=1e-6)


def test_the_stationary_distribution_and_dwell_times():
    pi = model.stationary(Q)              # focused, distracted, tired, drowsy, very_drowsy
    assert np.allclose(pi, [0.635, 0.080, 0.153, 0.091, 0.042], atol=0.002)
    assert abs(pi @ Q).max() < 1e-12 and abs(pi.sum() - 1) < 1e-12
    assert (np.diag(model.transition(Q, 1.0)) >= 0.983).all()


@pytest.mark.parametrize("dt", [1e-3, 1, 7.3, 600, 86400, 1e7])
def test_expm_matches_scipy(dt):
    linalg = pytest.importorskip("scipy.linalg")
    tol = 1e-9 if dt >= 1e7 else 1e-10
    assert np.abs(model.transition(Q, dt) - linalg.expm(Q * dt)).max() <= tol


def test_hmm_runs_without_scipy():
    code = ("import sys; sys.modules['scipy'] = None\n"
            "from eepy.state.estimate import make_estimator\n"
            "from eepy.state.features import empty\n"
            "e = make_estimator('hmm'); print(e.update(empty(1.0)).state)\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "unknown"


def test_one_step_is_fast():
    est = HMMEstimator(PARAMS)
    fs = [feats(float(t), aus=AUS) for t in range(3600)]
    start = time.perf_counter()
    for f in fs:
        est.update(f)
    # an hour of data, with explanations: ~0.5 s on a laptop; shared CI machines are up to ~4x slower
    assert time.perf_counter() - start < 5.0


def test_predict_only_is_exactly_rate_independent():
    def belief(times: List[float]) -> np.ndarray:
        est = HMMEstimator(PARAMS)
        est.log_b = np.log(np.array([0.3, 0.2, 0.15, 0.25, 0.1]))
        est.t_last = 0.0
        for t in times:
            est.update(empty(t))
        return np.exp(est.log_b)

    rng = random.Random(3)
    cuts = sorted(rng.uniform(0, 600) for _ in range(50)) + [600.0]
    ref = belief([float(t) for t in range(1, 601)])
    for times in ([t * 0.5 for t in range(1, 1201)], [600.0], cuts):
        assert np.abs(belief(times) - ref).max() < 1e-9


# --- 11-12: rate independence of the whole estimator -----------------------------------------------------


def test_constant_signal_same_belief_at_any_rate():
    base = simulate([("work", 90)], seed=3)[-1]
    finals = []
    for rate in (0.5, 1.0, 2.0, 5.0):
        est, t = HMMEstimator(PARAMS), 0.0
        while t <= 120 + 1e-9:
            est.update(dict(base, t=t))
            t += 1.0 / rate
        finals.append(np.exp(est.log_b))
    assert np.ptp(np.array(finals), axis=0).max() < 0.01        # [0.0014]


def _by_second(estimates: List[Any]) -> Dict[int, Any]:
    return {round(e.t - 1000.0): e for e in estimates}


@pytest.mark.parametrize("label, kwargs, mean_limit", [
    ("2 Hz", dict(rate=2.0), 0.02),
    ("0.5 Hz", dict(rate=0.5), 0.05),
    ("1 Hz jitter", dict(rate=1.0, jitter=0.2), 0.02),
])
def test_scenario_same_states_at_different_rates(label, kwargs, mean_limit):
    base = _by_second(run(simulate(SCENARIO, seed=1)))
    fs = simulate(SCENARIO, seed=1, rate=kwargs["rate"])
    if kwargs.get("jitter"):
        rng = random.Random(7)
        for f in fs:
            f["t"] += rng.uniform(-kwargs["jitter"], kwargs["jitter"])
    other = _by_second(run(fs))
    common = [t for t in base if t in other and t >= 1]
    agree = sum(base[t].state == other[t].state for t in common) / len(common)
    diffs = [max(abs(base[t].scores[s] - other[t].scores[s]) for s in HIDDEN) for t in common]
    print(f"{label}: agreement {agree:.3f}, mean {np.mean(diffs):.3f}, max {max(diffs):.3f}")
    assert agree >= (0.96 if kwargs["rate"] < 1 else 0.97)     # [0.5 Hz: 0.962, one state change a second later]
    assert np.mean(diffs) <= mean_limit
    sw_base = [(t, s) for t, s in switches(list(base.values()))]
    sw_other = switches([other[t] for t in sorted(other)])
    assert [s for _, s in sw_base] == [s for _, s in sw_other]
    slack = max(2.0, 1.0 / kwargs["rate"] + 1.0)               # 0.5 Hz: a sample every 2 s [drowsy 3 s sooner]
    assert all(abs(a - b) <= slack for (a, _), (b, _) in zip(sw_base, sw_other))


# --- 13: recovery (exact Bayes on data sampled from the model) --------------------------------------------

REPRESENTATIVE_AWAY = (0.0, 2.5, 7.5, 15.0, 40.0, 90.0)


def _sample(spec: Any, state: int, rng: np.random.Generator) -> Any:
    p = spec.params[state]
    if spec.family == "beta":
        return float(rng.beta(p["mean"] * p["concentration"], (1 - p["mean"]) * p["concentration"]))
    if spec.family == "student_t":
        return float(p["mean"] + p["sd"] * rng.standard_t(spec.df))
    if spec.family == "poisson":
        return (int(rng.poisson(p["rate_per_minute"])), 1.0)
    if spec.family == "bernoulli":
        return int(rng.random() < p["p"])
    return REPRESENTATIVE_AWAY[int(rng.choice(len(p["probs"]), p=p["probs"]))]


def test_filter_recovers_sampled_states():
    """Scored on the reported state: "tired" looks exactly like focused by design (a step on the
    ladder), so only the transitions tell them apart."""
    n = len(PARAMS.states)
    shown = [HIDDEN.index(r) for r in PARAMS.reports]
    q = model.generator(PARAMS.rates_per_hour) * 10           # dwells ten times shorter
    p1 = model.transition(q, 1.0)
    rng = np.random.default_rng(0)
    accuracies, p_true = [], []
    for _ in range(20):
        state = int(rng.choice(n, p=list(PARAMS.initial)))
        log_b = np.log(np.array(PARAMS.initial))
        hits = 0
        for t in range(600):
            if t:
                state = int(rng.choice(n, p=p1[state]))
            ll = sum(model.loglik(spec, _sample(spec, state, rng)) for spec in PARAMS.features)
            log_b = model.forward_step(log_b, p1 if t else np.eye(n), ll, 1e-300)
            probs = np.zeros(len(HIDDEN))
            np.add.at(probs, shown, np.exp(log_b))
            hits += int(np.argmax(probs)) == shown[state]
            p_true.append(probs[shown[state]])
        accuracies.append(hits / 600)
    print(f"recovery: min {min(accuracies):.3f}, mean {np.mean(accuracies):.3f}, P(true) {np.mean(p_true):.3f}")
    assert min(accuracies) >= 0.93 and np.mean(accuracies) >= 0.95 and np.mean(p_true) >= 0.90


# --- 14-17: behaviour on simulated sessions ------------------------------------------------------------


def _max_after(estimates: List[Any], after: float = 30.0) -> Tuple[float, float]:
    late = [e for e in estimates if e.t - 1000 > after]
    return max(p_sleepy(e) for e in late), max(e.scores["distracted"] for e in late)


@pytest.mark.parametrize("aus", [False, True])
@pytest.mark.parametrize("label, extra", [
    ("one yawn", {70: {"yawn": True}}),
    ("one nod", {70: {"nod": True}}),
    ("a yawn and a nod", {70: {"yawn": True}, 75: {"nod": True}}),
    ("a 4-s glance", {k: {"away": True, "judged": False} for k in range(70, 74)}),
    ("an 8-s look away", {k: {"away": True, "judged": False} for k in range(70, 78)}),
])
def test_isolated_signs_do_not_flip(label, extra, aus):
    out = run(simulate([("work", 150)], seed=2, extra=extra, aus=aus))
    assert {e.state for e in out if e.t - 1000 > 10} == {"focused"}, label
    sleepy, distracted = _max_after(out)
    assert sleepy < 0.3 and distracted < 0.2, (label, sleepy, distracted)       # [0.03 (yawn + nod 0.23), 0.06]


@pytest.mark.parametrize("aus", [False, True])
def test_sustained_signs_flip(aus):
    out = run(simulate([("work", 60), ("sleepy", 90)], seed=2, aus=aus))
    assert first_at(out, ("drowsy", "very_drowsy"), 60) <= 50                     # [40 / 39 with AUs]
    out = run(simulate([("work", 150)], seed=2, extra=closures(60, 150), aus=aus))
    assert first_at(out, ("drowsy", "very_drowsy"), 60) <= 45                     # [33 / 34]
    assert first_at(out, ("very_drowsy",), 60) <= 95                              # [73 / 82]
    out = run(simulate(SCENARIO, seed=1, aus=aus))
    assert first_at(out, ("distracted",), 40) <= 15                               # [12]
    assert first_at(out, ("focused",), 60) <= 25                                  # [13]
    assert first_at(out, ("absent",), 125) <= 18                                  # [13]
    out = run(simulate([("work", 40), ("phone", 30), ("work", 40)], seed=2, aus=aus))
    assert first_at(out, ("focused",), 70) <= 25                                  # [16]


@pytest.mark.parametrize("aus", [False, True])
def test_divergences_from_a13_are_pinned(aus):
    """With alert eyes, yawns or nods only make the HMM drowsy when they keep coming (version 3:
    counted twice a minute; the rules: a yawn and a nod are drowsy at once, a nod every 25 s
    very drowsy), never very drowsy; a long blink-length ratio on its own does nothing."""
    for extra in ({k: {"yawn": True} for k in range(60, 150, 20)},
                  {k: {"nod": True} for k in range(60, 150, 25)}):
        out = run(simulate([("work", 150)], seed=2, extra=extra, aus=aus))
        assert "very_drowsy" not in {e.state for e in out}
        first = first_at(out, ("drowsy",), 60)
        assert first is None or first >= 50                                        # [70 / -, 85 / 82]
    # yawning every 20 s for over two minutes: drowsy in the end
    out = run(simulate([("work", 200)], seed=1, extra={k: {"yawn": True} for k in range(60, 200, 20)}, aus=aus))
    assert 50 <= first_at(out, ("drowsy",), 60) <= 130                             # [66 / 117]
    # a focused minute with the blink-length ratio forced to 3.5x (seconds 60-119, >= 5 blinks)
    fs = simulate([("work", 150)], seed=2, aus=aus)
    for f in fs[60:120]:
        f["blink_mean_ms_rel_60s"] = 3.5
    out = run(fs)
    assert all((f.get("blink_count_60s") or 0) >= 5 for f in fs[60:120])
    assert _max_after(out)[0] < 0.2                                                # [0.02]


@pytest.mark.parametrize("levels", [(0.04, False), (0.06, False)])
def test_lowered_eyelids_while_reading_do_not_flip_quickly(levels):
    """Judged seconds that read as fully shut (lowered eyelids looking at notes), no blink events:
    no drowsy at low levels (the rules turn drowsy after about a minute even at 4 %)."""
    share, _ = levels
    rng = random.Random(5)
    extra = {k: {"closed": 1.0, "judged": True, "away": False} for k in range(60, 240) if rng.random() < share}
    out = run(simulate([("work", 240)], seed=2, extra=extra))
    assert {e.state for e in out if e.t - 1000 > 10} == {"focused"}


def test_gaze_off_orders_distracted_first():
    spec = PARAMS.feature("gaze_off")
    for x in (0.35, 0.45, 0.6):
        ll = model.loglik(spec, x)
        assert int(np.argmax(ll)) == 1
    for x in (0.1, 0.2, 0.3):
        ll = model.loglik(spec, x)
        assert ll[2] <= ll[0] + 1e-12 and ll[3] <= ll[0] + 1e-12


def test_log_ratio_tables():
    """The tables in eepy/state/README.md (per full window, vs focused)."""
    iR, iV = PARAMS.states.index("drowsy"), PARAMS.states.index("very_drowsy")
    t = PARAMS.feature("blink_duration")
    for ratio, r, v in ((1.0, -1.06, -1.88), (2.0, 1.43, 1.31), (3.5, 1.72, 2.65)):
        ll = model.loglik(t, math.log(ratio))
        assert abs((ll[iR] - ll[0]) - r) < 0.02 and abs((ll[iV] - ll[0]) - v) < 0.02
    pc = PARAMS.feature("perclos")
    for x, r, v in ((0.12, 1.70, 1.12), (0.25, 5.17, 5.76)):
        ll = model.loglik(pc, x)
        assert abs((ll[iR] - ll[0]) - r) < 0.02 and abs((ll[iV] - ll[0]) - v) < 0.02


# --- 18-25: missing data, absent, unknown, double counting -------------------------------------------------


def _belief_after(est: HMMEstimator, f: Dict[str, Any], n: int = 20) -> np.ndarray:
    for k in range(n):
        est.update(dict(f, t=100.0 + k))
    return np.exp(est.log_b)


def test_missing_features_are_skipped_not_filled():
    f = feats(face={"eyes_measured_s_60s": 12.0, "eyes_measured_s_10s": 0.0, "perclos_60s": 0.3,
                    "long_blinks_60s": 5})
    est = HMMEstimator(PARAMS)
    e = None
    for k in range(10):
        e = est.update(dict(f, t=100.0 + k))
    assert not {d["feature"] for d in e.evidence} & set(EYE_GROUP)
    assert "perclos: needs eyes_measured_s_60s >= 20 (is 12.0)" in e.skipped
    no_eyes = params_with(lambda raw: [raw["features"][k].update(enabled=False) for k in EYE_GROUP])
    assert np.abs(_belief_after(HMMEstimator(PARAMS), f) - _belief_after(HMMEstimator(no_eyes), f)).max() < 1e-12
    # blink ratio missing: the eye group has n = 4 -> n_eff 4 / 1.9 ... with rho 0.5: 4 / 2.5 = 1.6, 0.4 each
    obs, _ = HMMEstimator(PARAMS).extract(feats(face={"blink_mean_ms_rel_60s": None}))
    eye = [o.spec for o in obs if o.spec.id in EYE_GROUP]
    assert len(eye) == 4
    w = model.group_weights(eye, PARAMS.groups)
    assert all(abs(v - 0.4) < 1e-12 for v in w.values())


def test_low_blink_confidence_and_aus():
    est = HMMEstimator(PARAMS)
    obs, skipped = est.extract(feats(face={"blink_confidence_60s": "low"}))
    ids = {o.spec.id for o in obs}
    assert "blink_duration" not in ids and "long_blinks" not in ids
    obs, skipped = est.extract(feats())
    assert not {"au_eyes_closed", "au_jaw_drop"} & {o.spec.id for o in obs}
    assert not any(s.startswith("au_") for s in skipped)                       # AUs off: not listed
    obs, skipped = est.extract(feats(aus=dict(AUS, status="paused")))
    assert any(s.startswith("au_eyes_closed: needs au_status == running (is paused)") for s in skipped)
    obs, _ = est.extract(feats(aus=AUS))
    assert {"au_eyes_closed", "au_jaw_drop"} <= {o.spec.id for o in obs}


def test_absent_is_a_flag_and_only_predicts():
    est = HMMEstimator(PARAMS)
    for t in range(100, 130):
        est.update(feats(float(t)))
    gone = feats(face={"face_absent_ratio_10s": 1.0, "face_absent_ratio_60s": 0.5})
    states = []
    for t in range(130, 141):
        e = est.update(dict(gone, t=float(t)))
        states.append(e.state)
        if t == 135:
            before, t_before = np.exp(est.log_b), float(t)
    assert states[:5] != ["absent"] * 5 and states[5] == "absent"            # 5 s after the condition holds
    assert e.updated is False and e.state == "absent"
    expected = before @ model.transition(Q, 140.0 - t_before)
    assert np.abs(np.exp(est.log_b) - expected).max() < 1e-9
    assert np.abs(np.array([e.scores[s] for s in HIDDEN]) - est.reported(expected)).max() < 1e-9
    e = est.update(feats(141.0, face={"face_absent_ratio_10s": 0.4}))
    assert e.state in HIDDEN and e.updated


def test_unknown_startup_slow_camera_no_data():
    est = HMMEstimator(PARAMS)
    for t in range(1, 30):
        est.update(feats(float(t)))
    assert est.update(feats(30.0, face={"coverage_s_10s": None})).state == "unknown"
    e = est.update(feats(31.0, face={"fps_10s": 3.0}))
    assert e.state == "unknown" and "camera too slow (3 pictures/s)" in e.reason_text
    e = est.update(empty(32.0))
    assert e.state == "unknown" and not e.updated and e.reason_text == "no pictures from the camera"
    # cold start: coverage grows from 1 s
    out = run(simulate([("work", 30)], seed=1))
    assert first_at(out, ("focused",), 0) in (9.0, 10.0)                          # [9]
    # mid-run reset with full windows: 5 s of warm-up (the 5th update, each counting 1 s)
    est = HMMEstimator(PARAMS)
    first = next(t for t in range(100, 120) if est.update(feats(float(t))).state != "unknown")
    assert first - 100 == 4


def test_gap_restarts_evidence_timing():
    est = HMMEstimator(PARAMS)
    for t in range(100, 130):
        est.update(feats(float(t)))
    before = np.exp(est.log_b)
    for t in range(133, 150):
        est.update(empty(float(t)))
    expected = before @ model.transition(Q, 149.0 - 129.0)
    assert np.abs(np.exp(est.log_b) - expected).max() < 1e-12
    # the first update after the gap counts one second of evidence (k_f = 1/tau), not a full tau
    sleepy = feats(150.0, face={"perclos_60s": 0.3})
    log_pred = model.predict(est.log_b, model.transition(est.q, 1.0))
    obs, _ = est.extract(sleepy)
    w = model.group_weights([o.spec for o in obs], PARAMS.groups)
    ev = sum(w[o.spec.id] * min(1.0, o.spec.tau) / o.spec.tau
             * model.bounded(model.loglik(o.spec, o.x), PARAMS.max_log_ratio) for o in obs)
    est.update(sleepy)
    assert np.abs(est.log_b - model.normalise(log_pred + ev, PARAMS.min_prob)).max() < 1e-12


def _pair_params(rho: float) -> Any:
    def change(raw: Dict[str, Any]) -> None:
        for k in raw["features"]:
            raw["features"][k]["enabled"] = False
        raw["features"]["perclos"].update(enabled=True, group="test_pair")
        raw["features"]["perclos_copy"] = copy.deepcopy(raw["features"]["perclos"])
        raw["groups"]["test_pair"] = {"correlation": {"value": rho, "why": "test"}}
    return params_with(change)


def test_double_counting_correction():
    def log_odds_change(params: Any) -> float:
        est = HMMEstimator(params)
        i = params.states.index("drowsy")
        before = est.log_b[i] - est.log_b[0]
        est.update(feats(100.0, face={"perclos_60s": 0.2}))
        return float(est.log_b[i] - est.log_b[0] - before)

    single = log_odds_change(params_with(lambda raw: [raw["features"][k].update(enabled=(k == "perclos"))
                                                      for k in raw["features"]]))
    for rho, ratio in ((1.0, 1.0), (0.5, 4 / 3), (0.0, 2.0)):
        assert abs(log_odds_change(_pair_params(rho)) / single - ratio) < 1e-9
    # the shipped eye group plus a copy: n 5 -> 6 (AUs off) gives 5/3 -> 6/3.5, not 5/3 + 1
    spec = PARAMS.feature("perclos")
    copy_spec = type(spec)(**{**spec.__dict__, "id": "perclos_copy"})
    five = [PARAMS.feature(k) for k in EYE_GROUP[:5]]
    assert abs(sum(model.group_weights(five, PARAMS.groups).values()) - 5 / 3) < 1e-12
    assert abs(sum(model.group_weights(five + [copy_spec], PARAMS.groups).values()) - 6 / 3.5) < 1e-12


def test_group_weights():
    specs = [PARAMS.feature(k) for k in EYE_GROUP[:5]]
    assert all(abs(w - 1 / 3) < 1e-12 for w in model.group_weights(specs, PARAMS.groups).values())
    assert model.group_weights(specs[:1], PARAMS.groups) == {"perclos": 1.0}
    heavy = type(specs[0])(**{**specs[0].__dict__, "weight": 2.0})
    w = model.group_weights([heavy, specs[1]], PARAMS.groups)
    assert abs(w["perclos"] - 2 * w["blink_duration"]) < 1e-12
    assert abs(sum(w.values()) - 2 / 1.5) < 1e-12


def test_uncalibrated():
    est = HMMEstimator(PARAMS)
    obs, skipped = est.extract(feats(face={"baseline_calibrated": False, "blink_mean_ms_rel_60s": None,
                                           "blink_mean_ms_60s": 300.0}))
    half = {o.spec.id for o in obs if o.uncalibrated}
    assert half == {"perclos", "blink_duration", "long_blinks", "microsleep", "eyes_closed_now"}
    blink = next(o for o in obs if o.spec.id == "blink_duration")
    assert abs(blink.x - math.log(1.5)) < 1e-12 and blink.show == "blinks 300 ms on average"
    assert skipped.count("not calibrated: eye signs at half weight") == 1
    # calibrated, but no blink-length ratio: skipped (no raw fallback)
    obs, skipped = est.extract(feats(face={"blink_mean_ms_rel_60s": None}))
    assert "blink_duration" not in {o.spec.id for o in obs}
    assert "blink_duration: no value for blink_mean_ms_rel_60s" in skipped


# --- 26-28: the output ----------------------------------------------------------------------------------


def _reporter() -> HMMEstimator:
    return HMMEstimator(PARAMS)


def _p(f: float, d: float, r: float, v: float) -> np.ndarray:
    return np.array([f, d, r, v])


def test_reported_state_hysteresis():
    est = _reporter()
    assert est._report(_p(0.97, 0.01, 0.01, 0.01))[0] == "focused"
    states = [est._report(_p(0.55, 0.45, 0, 0) if k % 2 else _p(0.45, 0.55, 0, 0)) for k in range(10)]
    assert {s for s, _ in states} == {"focused"} and any(p == "distracted" for _, p in states)
    est = _reporter()
    est._report(_p(0.05, 0.05, 0.7, 0.2))
    split = [est._report(_p(0.1, 0, 0.45, 0.45))[0] for _ in range(5)]
    assert len(set(split)) == 1
    est = _reporter()
    assert est._report(_p(0.30, 0.0, 0.35, 0.35))[0] in ("drowsy", "very_drowsy")
    est = _reporter()
    est._report(_p(0.1, 0.1, 0.7, 0.1))
    assert est._report(_p(0.45, 0.35, 0.15, 0.05))[0] == "focused"            # sleepy fell below 40 %
    est = HMMEstimator(PARAMS)
    for t in range(100, 120):
        e = est.update(feats(float(t)))
        assert e.most_likely == HIDDEN[int(np.argmax([e.scores[s] for s in HIDDEN]))]
    assert abs(e.confidence - e.scores["focused"]) < 1e-6
    assert model.entropy(np.ones(4) / 4) == pytest.approx(1.0) and model.entropy(np.array([1.0, 0, 0, 0])) == 0.0


def _drive(f: Dict[str, Any], seconds: int = 90, est: Optional[HMMEstimator] = None) -> List[Any]:
    est = est or HMMEstimator(PARAMS)
    return [est.update(dict(f, t=100.0 + k)) for k in range(seconds)]


def test_evidence_and_reasons_are_truthful():
    # focused at ~99 %: explained by contrasts, never "hold", never "no difference"
    out = _drive(feats(), 60)
    e = out[-1]
    assert e.state == "focused" and e.confidence > 0.95
    assert e.reasons[0].rule != "hold"
    assert all("speaks against" in r.text for r in e.reasons)
    assert not any("no difference" in d["text"] for d in e.evidence if d["impact"] > 0)
    # distracted, eyes alert: low PERCLOS never "suggests distraction"
    away = feats(gaze={"off_work_ratio_60s": 0.6, "off_work_ratio_10s": 1.0, "away_current_s": 30.0})
    out = _drive(away, 60)
    e = out[-1]
    assert e.state == "distracted"
    perclos = next(d for d in e.evidence if d["feature"] == "perclos")
    assert perclos["supports"] is None
    assert not any("eye closure" in r.text for r in e.reasons)
    # drowsy from eye closure while looking at the work: away_now is never the reason
    sleepy = feats(face={"perclos_60s": 0.25, "long_blinks_60s": 5})
    out = _drive(sleepy, 90)
    e = out[-1]
    assert e.state in ("drowsy", "very_drowsy")
    assert all(r.rule != "away_now" for o in out if o.state in ("drowsy", "very_drowsy") for r in o.reasons)
    assert e.reasons[0].rule in ("perclos", "long_blinks")
    perclos_reason = next(r for r in e.reasons if r.rule == "perclos")
    assert perclos_reason.text.startswith("eye closure (eyes closed 25% of the last minute) suggests ")
    impacts = [d["impact"] for d in e.evidence]
    assert impacts == sorted(impacts, reverse=True)
    perclos_ev = next(d for d in e.evidence if d["feature"] == "perclos")
    assert perclos_ev["text"].startswith("eye closure (eyes closed 25% of the last minute) clearly suggests ")


def test_the_spoken_evidence_is_short_or_empty():
    e = _drive(feats(face={"perclos_60s": 0.25, "long_blinks_60s": 5}), 90)[-1]
    assert e.say in ("eyes closed 25% of the last minute", "5 long blinks in the last minute")
    est = HMMEstimator(PARAMS)
    est.update(feats(100.0))
    hold = est._reasons([], {}, 2, np.array([0.1, 0.1, 0.8]), np.array([0.1, 0.1, 0.6, 0.2]), 0.8, "drowsy")
    assert hold[0].rule == "hold" and est._say(hold, []) == ""


def test_scores_are_probabilities():
    for e in run(simulate(SCENARIO, seed=1)):
        assert abs(sum(e.scores[s] for s in HIDDEN) - 1) < 1e-9
        assert e.scores["absent"] == 0.0 and e.scores["unknown"] == 0.0
        assert 0 <= e.uncertainty <= 1


# --- 29: the registry --------------------------------------------------------------------------------------


def test_registry_and_mode():
    est = make_estimator("hmm", params=PARAMS)
    assert est.name == "hmm" and est.params is PARAMS
    assert for_mode("hmm") == "hmm" and for_mode("hmm_bandit") == "hmm" and for_mode("rules") == "rules"
    assert for_mode(None) == "rules"
    with pytest.raises(ValueError, match="crystal_ball"):
        make_estimator("crystal_ball")


# --- 30-35: runner, recording, replay, the per-session estimator --------------------------------------

from types import SimpleNamespace  # noqa: E402

from eepy.state.replay import belief_difference, compare_live, disagreements, live_rows, live_states, \
    main as replay_main, run as replay_run, session_features, side_by_side  # noqa: E402
from eepy.state.runner import STATE_COLUMNS, SessionEstimators, StateFromPipeline, StateRecorder, \
    StateTracker, describe_estimator, panel_view  # noqa: E402


def _parts(features: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """Split assembled features back into the logged window summaries (what FacePipeline logs)."""
    gaze_keys = {"off_work_ratio_10s", "off_work_ratio_60s", "no_face_ratio_10s", "no_face_ratio_60s", "away_current_s"}
    derived = {"t", "face_present_ratio_10s", "face_present_ratio_60s", "gaze_off_ratio_10s", "gaze_off_ratio_60s",
               "blink_count_10s", "blink_count_60s", "face_seen_s_10s", "face_seen_s_60s", "gaze_calibrated"}
    face = {k: v for k, v in features.items() if k not in gaze_keys | derived and not k.startswith(("AU", "au_"))
            and v is not None}
    gaze = {k: features[k] for k in gaze_keys if features.get(k) is not None}
    gaze["calibrated"] = bool(features.get("gaze_calibrated"))
    aus = None
    if features.get("au_status"):
        aus = {"status": features["au_status"], "rate": features.get("au_rate")}
        aus.update({k[3:]: v for k, v in features.items() if k.startswith("au_results")})
        aus.update({k: v for k, v in features.items() if k.startswith("AU") and v is not None})
    return face, gaze, aus


def _record_live_session(tmp_path: Path, mode: str = "hmm", prior: Optional[Dict[str, Any]] = None,
                         participant: str = "P50", params: Any = None, seconds: int = 150,
                         data: Optional[Path] = None) -> Tuple[Path, List[Any], Any]:
    from eepy.logs.features import ProcessEventLog
    from eepy.logs.session import SessionLog

    data = data or tmp_path / "sessions"
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    log.start(participant, mode=mode, prior=prior)
    events = ProcessEventLog("vision", data_dir=data, flush_seconds=0.05)
    stream = StateRecorder.make_stream(data_dir=data, flush_seconds=0.05)
    live: List[Any] = []
    tracker = StateTracker(make_estimator("rules"), listeners=[live.append])
    recorder = StateRecorder(events, stream, describe=lambda: describe_estimator(tracker.estimator))
    tracker.listeners.append(recorder)
    choose = SessionEstimators(tracker, hmm_params=params or PARAMS, data_dir=data, say=lambda text: None)
    subscriber = StateFromPipeline(tracker, session=lambda: log.session, on_session=choose)
    t0 = 1000.0
    feats_all = simulate(SCENARIO, seed=1, aus=True, t0=t0)[:seconds]
    for f in feats_all:
        if 100 <= f["t"] - t0 < 106:               # a camera pause: no summaries, only idle ticks
            tracker.idle(f["t"], slack=0.0)
            continue
        face, gaze, aus = _parts(f)
        result = SimpleNamespace(t=f["t"], window=face,
                                 records=[("gaze_window", dict(gaze))] + ([("au_window", dict(aus))] if aus else []))
        events.event("face_window", t_mono=result.t, **face)
        events.event("gaze_window", t_mono=result.t, **gaze)
        if aus:
            events.event("au_window", t_mono=result.t, **aus)
        subscriber(result)
    events.close()
    stream.close()
    log.end()
    folder = next(p for p in sorted(data.iterdir()) if p.is_dir() and p.name.endswith(f"{participant}_{mode}"))
    return folder, live, tracker


def test_hmm_replay_gives_the_same_beliefs_as_live(tmp_path):
    folder, live, tracker = _record_live_session(tmp_path, seconds=110)
    assert tracker.estimator.name == "hmm"                  # auto: the session's mode
    replayed = replay_run(session_features(folder), HMMEstimator(PARAMS))
    assert [(round(e.t, 3), e.state) for e in replayed] == [(round(e.t, 3), e.state) for e in live]
    assert max(abs(a.scores[s] - b.scores[s]) for a, b in zip(replayed, live) for s in HIDDEN) <= 1e-12
    same, compared = compare_live(replayed, live_states(folder))
    assert compared > 100 and same == compared
    assert belief_difference(replayed, live_rows(folder)) <= 1e-12


def test_replay_both(tmp_path, capsys):
    folder, _, _ = _record_live_session(tmp_path, seconds=150)
    out_csv = tmp_path / "both.csv"
    assert replay_main([folder.name, "--data", str(tmp_path), "--estimator", "both", "--csv", str(out_csv)]) == 0
    text = capsys.readouterr().out
    assert "Rules and HMM agree:" in text and "hmm: why" in text and "Same as the live run (hmm)" in text
    assert "Largest belief difference: 0.000" in text
    header = out_csv.read_text().splitlines()[0].split(",")
    assert header[:5] == ["t", "t_mono", "rules_state", "rules_reason", "hmm_state"] and "p_drowsy" in header
    assert replay_main([folder.name, "--data", str(tmp_path), "--params", "x.yaml"]) == 2
    assert "--params is for the HMM" in capsys.readouterr().err
    assert replay_main([folder.name, "--data", str(tmp_path), "--estimator", "hmm", "--prior", "none"]) == 0
    # the pure functions on hand-made estimates
    from eepy.state.estimate import StateEstimate

    def est(t: float, state: str, conf: Optional[float] = None) -> StateEstimate:
        return StateEstimate(t=t, state=state, scores={}, reasons=[], calibrated=True, confidence=conf)

    a = [est(t, s) for t, s in ((1, "focused"), (2, "focused"), (3, "drowsy"), (4, "drowsy"), (5, "focused"))]
    b = [est(t, s, 0.8) for t, s in ((1, "focused"), (2, "drowsy"), (3, "drowsy"), (4, "drowsy"), (5, "drowsy"))]
    assert [(p.a.state, p.b.state) for p in side_by_side(a, b)] == \
           [("focused", "focused"), ("focused", "drowsy"), ("drowsy", "drowsy"), ("focused", "drowsy")]
    assert [(p.start, p.end) for p in disagreements(a, b)] == [(2, 3), (5, 5)]


class _FlakyEvents:
    """An event log whose first call fails (it has not noticed the session yet)."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def event(self, event_type: str, t_mono: Optional[float] = None, **fields: Any) -> bool:
        from eepy.logs.schema import check_event

        check_event(event_type, "perception", fields)
        self.calls.append((event_type, fields))
        return len(self.calls) > 1


class _Rows:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def row(self, values: Dict[str, Any], t_mono: float) -> None:
        assert set(values) <= set(STATE_COLUMNS)
        self.rows.append(values)


def test_recorder_hmm_columns_and_state_model_retry():
    events, rows = _FlakyEvents(), _Rows()
    est = HMMEstimator(PARAMS)
    recorder = StateRecorder(events, rows, describe=lambda: describe_estimator(est))
    tracker = StateTracker(est, listeners=[recorder])
    for f in simulate([("work", 12)], seed=1):
        tracker.feed(f)
    kinds = [k for k, _ in events.calls]
    assert kinds[0] == "state_model" and kinds.count("state_model") == 2      # failed once, then logged once
    model_event = next(f for k, f in events.calls[1:] if k == "state_model")
    assert model_event["estimator"] == "hmm" and model_event["version"] == PARAMS.version
    assert len(model_event["sha256"]) == 64 and model_event["states"] == list(PARAMS.states)
    assert model_event["prior"]["source"] == "default" and "perclos" in model_event["features"]
    change = next(f for k, f in events.calls if k == "state_changed" and f["to"] == "focused")
    assert set(change["probs"]) == set(HIDDEN) and "hmm_evidence" in change and change["most_likely"] == "focused"
    last = rows.rows[-1]
    assert last["estimator"] == "hmm" and last["most_likely"] == "focused" and last["confidence"] > 0.5
    assert len(last["evidence"]) <= 64 and last["evidence"].isascii()
    tracker.reset()                                     # a new session: state_model again
    tracker.feed(simulate([("work", 2)], seed=1, t0=2000.0)[0])
    assert [k for k, _ in events.calls].count("state_model") == 3


def test_auto_estimator_follows_the_session_mode(tmp_path):
    from eepy.logs.features import current_session_mode
    from eepy.logs.session import SessionLog

    data = tmp_path / "sessions"
    tracker = StateTracker(make_estimator("rules"))
    choose = SessionEstimators(tracker, hmm_params=PARAMS, data_dir=data, say=lambda text: None)
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    for mode, want in (("rules", "rules"), ("hmm", "hmm"), ("wizard", "rules")):
        log.start("P51", mode=mode)
        assert current_session_mode(data) == mode
        tracker.feed(feats(100.0))
        choose(log.session)
        assert tracker.estimator.name == want and tracker.latest is None      # swapped and reset
        assert tracker.gap == (PARAMS.gap_seconds if want == "hmm" else 3.0)
        log.end()
    choose(None)
    assert tracker.estimator.name == "rules" and current_session_mode(data) is None
    fixed = SessionEstimators(tracker, fixed="hmm", hmm_params=PARAMS, data_dir=data, say=lambda text: None)
    fixed(None)
    assert tracker.estimator.name == "hmm"


def test_a_failing_hmm_keeps_the_rules(tmp_path, monkeypatch):
    from eepy.logs.session import SessionLog

    said: List[str] = []
    data = tmp_path / "sessions"
    tracker = StateTracker(make_estimator("rules"))
    choose = SessionEstimators(tracker, hmm_params=PARAMS, data_dir=data, say=said.append)

    def broken(name: str) -> Any:
        raise ValueError('The "hmm" estimator cannot load: No module named numpy.')

    monkeypatch.setattr(choose, "build", broken)
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    log.start("P52", mode="hmm")
    choose(log.session)
    choose(log.session)
    log.end()
    assert tracker.estimator.name == "rules" and len(said) == 1 and "Running the rules instead" in said[0]


def test_tracker_is_thread_safe():
    import threading

    est = HMMEstimator(PARAMS)
    seen: List[float] = []
    original = est.update

    def watched(f: Dict[str, Any]) -> Any:
        before = est.t_last_obs
        out = original(f)
        if before is not None and est.t_last_obs is not None:
            seen.append(est.t_last_obs - before)
        return out

    est.update = watched                                   # type: ignore[assignment]
    tracker = StateTracker(est)
    errors: List[BaseException] = []
    stop = time.perf_counter() + 2.0
    clock = {"t": 0.0}

    def pipeline() -> None:
        try:
            n = 0
            while time.perf_counter() < stop:
                clock["t"] += 1.0
                tracker.feed(feats(clock["t"]))
                n += 1
                if n % 50 == 0:                            # a session change from the pipeline thread
                    tracker.use(est)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def main_loop() -> None:
        try:
            while time.perf_counter() < stop:
                tracker.idle(clock["t"] + 5.0)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=pipeline), threading.Thread(target=main_loop)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert not errors and not any(th.is_alive() for th in threads)
    assert all(d >= 0 for d in seen)


def test_scenario_report_both():
    from eepy.state.estimate import StateEstimate
    from eepy.state.scenario import SCENARIO as PHASES, judge, report_both

    def est(t: float, state: str, probs: Optional[Dict[str, float]] = None) -> StateEstimate:
        scores = probs or {s: float(s == state) for s in HIDDEN}
        return StateEstimate(t=t, state=state, scores=scores, reasons=[], calibrated=True,
                             confidence=None if probs is None else max(scores.values()))

    timed, start = [], 0.0
    for phase in PHASES:
        timed.append((phase, start, start + phase.seconds))
        start += phase.seconds
    expected = [p.expected[0] for p in PHASES]
    rules = [est(float(t), expected[min(i, 4)]) for i, (_, a, b) in enumerate(timed) for t in range(int(a) + 3, int(b))]
    hmm = []
    for i, (phase, a, b) in enumerate(timed):
        for t in range(int(a), int(b)):
            state = phase.expected[0] if (i != 3 or t - a > 50) else "focused"   # never drowsy in "Act sleepy"
            probs = {s: 0.9 if s == state else 0.1 / 3 for s in HIDDEN} if state in HIDDEN else {s: 0.25 for s in HIDDEN}
            hmm.append(est(float(t), state, probs))
    lines = report_both(judge(timed, rules), judge(timed, hmm), rules, hmm, 0.0)
    text = "\n".join(lines)
    assert "Act sleepy" in text and "CHECK never drowsy/very drowsy" in text and "known difference" in text
    assert "PASS" in text and "Belief timeline" in text
    assert all(line.isascii() for line in lines)


# --- 46: what the panel gets ---------------------------------------------------------------------------


def test_panel_view_has_the_belief_only_for_the_hmm():
    import json

    est = HMMEstimator(PARAMS)
    for f in simulate([("work", 30)], seed=1):
        e = est.update(f)
    view = panel_view(e)
    assert set(view["probs"]) == set(HIDDEN) and view["confidence"] > 0.5 and view["most_likely"] == "focused"
    assert "say" in view and "updated" in view and len(view["evidence"]) <= 3
    assert len(json.dumps(view)) < 2048          # once a second over the relay; ~1.1 kB with the top 3 signs (A19)
    rules = make_estimator("rules")
    r = None
    for f in simulate([("work", 30)], seed=1):
        r = rules.update(f)
    assert not {"probs", "confidence", "say"} & set(panel_view(r))


# --- 47-49: the per-session prior ---------------------------------------------------------------------

from eepy.state.hmm_prior import resolve, water_fill  # noqa: E402

ENABLED = params_with(lambda raw: raw["session_prior"].update(enabled=True))


def test_session_prior_is_clipped_and_shrunk():
    given = {"focused": 0.2, "distracted": 0.1, "drowsy": 0.5, "very_drowsy": 0.2}
    applied = resolve({"source": "kss", "initial": given, "drowsiness_multiplier": 10, "inputs": {"kss": 7}}, ENABLED)
    assert applied.source == "kss" and applied.trust == 0.5 and not applied.fell_back
    assert applied.drowsiness_multiplier == pytest.approx(2.0)                # 10 -> 4 (clipped), then sqrt
    assert any("drowsiness_multiplier" in c for c in applied.clipped)
    from eepy.state.hmm_prior import reported_initial

    base = reported_initial(PARAMS)                   # tired counts as focused
    for i, s in enumerate(HIDDEN):
        assert applied.initial[s] == pytest.approx(0.5 * base[i] + 0.5 * given[s])
    assert water_fill([1, 0, 0, 0], 0.01, 0.95) == pytest.approx([0.95, 0.05 / 3, 0.05 / 3, 0.05 / 3])
    assert water_fill([0.5, 0.5, 0, 0], 0.01, 0.95) == pytest.approx([0.49, 0.49, 0.01, 0.01])
    est = HMMEstimator(ENABLED, prior=applied)
    assert np.allclose(est.reported(np.exp(est.log_b)), [applied.initial[s] for s in HIDDEN])
    base = model.generator(ENABLED.rates_per_hour)
    n = len(ENABLED.states)
    for i in range(n):
        for j in range(n):
            if i != j:
                factor = 2.0 if (i, j) in ENABLED.drowsiness_pairs else 1.0
                assert est.q[i, j] == pytest.approx(base[i, j] * factor)


@pytest.mark.parametrize("raw, words", [
    ({"_error": "cannot read prior.json: Expecting value"}, "could not be used"),
    ({"source": "oracle"}, 'source must be one of default, heuristic, kss, llm, not "oracle"'),
    ({"source": "kss", "drowsiness_multiplier": float("nan")}, "drowsiness_multiplier must be a number > 0"),
    ({"source": "kss", "initial": {"focused": 1.0}}, "initial must give exactly"),
    ({"source": "kss", "emissions": {}}, "it also has emissions"),
    ({"source": "llm", "inputs": {"note": "I slept badly"}}, 'input "note" is not allowed'),
    ({"source": "llm", "inputs": {"kss": "seven"}}, 'input "kss" must be a number'),
])
def test_bad_prior_falls_back(raw, words):
    applied = resolve(raw, ENABLED)
    assert applied.source == "default" and applied.fell_back and words in applied.reason
    est = HMMEstimator(ENABLED, prior=applied)
    assert est.update(feats(100.0)).state == "unknown"            # it runs


def test_a_valid_prior_is_ignored_while_switched_off():
    applied = resolve({"source": "kss", "drowsiness_multiplier": 2.0}, PARAMS)
    assert applied.source == "default" and applied.fell_back and "switched off" in applied.reason
    assert resolve(None, PARAMS).reason == "no prior given" and not resolve(None, PARAMS).fell_back


def test_prior_is_logged_per_session_and_replayed(tmp_path, capsys):
    import json

    data = tmp_path / "sessions"
    prior = {"source": "kss", "initial": {"focused": 0.4, "distracted": 0.1, "drowsy": 0.4, "very_drowsy": 0.1},
             "drowsiness_multiplier": 3.0, "inputs": {"kss": 8}, "model_id": "test-1"}
    first, live1, _ = _record_live_session(tmp_path, prior=prior, participant="P53", params=ENABLED,
                                           seconds=40, data=data)
    second, live2, _ = _record_live_session(tmp_path, participant="P54", params=ENABLED, seconds=40, data=data)
    models = [json.loads(line) for line in (first / "events.vision.jsonl").read_text().splitlines()
              if '"state_model"' in line]
    assert len(models) == 1 and models[0]["prior"]["source"] == "kss" and models[0]["prior"]["model_id"] == "test-1"
    models2 = [json.loads(line) for line in (second / "events.vision.jsonl").read_text().splitlines()
               if '"state_model"' in line]
    assert models2[0]["prior"]["source"] == "default"                          # no leak into the next session
    # replay with the session's prior.json reproduces the beliefs
    applied = resolve(json.loads((first / "prior.json").read_text()), ENABLED)
    replayed = replay_run(session_features(first), HMMEstimator(ENABLED, prior=applied))
    assert max(abs(a.scores[s] - b.scores[s]) for a, b in zip(replayed, live1) for s in HIDDEN) <= 1e-12
    default = replay_run(session_features(first), HMMEstimator(ENABLED))
    assert max(abs(a.scores[s] - b.scores[s]) for a, b in zip(default, live1) for s in HIDDEN) > 1e-6


# --- the explanation viewer (eepy/state/explain.py) ---------------------------------------------------

import re  # noqa: E402

from eepy.state import explain  # noqa: E402


def _embedded(page: str) -> Dict[str, Any]:
    import json

    blob = page.split("let DATA = ", 1)[1].split(";\nconst LIVE", 1)[0]
    return json.loads(blob.replace("<\\/", "</"))


def test_explain_demo_is_a_self_contained_page():
    data = explain.demo(PARAMS)
    assert len(data["steps"]) == sum(n for _, n in explain.DEMO)
    step = data["steps"][120]
    assert set(step) >= {"t", "p", "s", "ml", "c", "u", "up", "r", "ev", "sk", "rules"}
    assert abs(sum(step["p"]) - 1) < 1e-3
    for item in step["ev"]:                          # [feature, supported family, push(3), step(3), text, no-news]
        assert 0 <= item[0] < len(data["features"]) and item[1] in (-1, 0, 1, 2)
        assert len(item[2]) == 3 and len(item[3]) == 3 and isinstance(item[4], str)
    assert [p["text"] for p in data["meta"]["parts"]] == explain.DEMO_PARTS
    robot = any(e["kind"] == "nudge" for e in data["events"])
    assert robot or any("No robot actions" in n for n in data["meta"]["notes"])   # .venv-vision: no library
    page = explain.render(data)
    assert not re.search(r"""(src|href)\s*=\s*["']?https?:""", page)            # nothing from the internet
    assert "/*__DATA__*/" not in page and "__TITLE__" not in page
    assert _embedded(page)["steps"][120] == step


def test_explain_a_recorded_session(tmp_path, capsys):
    folder, live, _ = _record_live_session(tmp_path, seconds=110)
    data = explain.from_session(folder, PARAMS)
    assert [s["s"] for s in data["steps"]] == [e.state for e in live]
    assert data["meta"]["mode"] == "hmm" and data["meta"]["notes"] == []
    assert data["meta"]["thresholds"]["act"] == {"refocus": 0.7, "bedtime": 0.7}
    assert all(s["rules"] for s in data["steps"])                                 # the rules alongside
    out = tmp_path / "page.html"
    assert explain.main([folder.name, "--data", str(tmp_path), "--out", str(out)]) == 0
    assert out.exists() and "Written:" in capsys.readouterr().out
    assert explain.main(["--data", str(tmp_path)]) == 2
    assert "Give a session" in capsys.readouterr().err


def test_explain_reads_the_robots_decisions(tmp_path):
    import json

    lines = [
        {"type": "annotation", "t_mono": 105.0, "marker": "phone_picked_up"},
        {"type": "escalation", "t_mono": 110.0, "what": "waiting_for_confidence", "ladder": "refocus",
         "state": "distracted", "confidence": 0.62, "needed": 0.7},
        {"type": "escalation", "t_mono": 114.0, "what": "confidence_wait_ended", "ladder": "refocus",
         "reason": "sure enough"},
        {"type": "role_changed", "t_mono": 114.0, "to": "refocus"},
        {"type": "nudge_decision", "t_mono": 114.0, "decision_id": 1, "ladder": "refocus", "level": 1,
         "tone": "hedged", "behavior": "refocus_joint_attention", "context": {"confidence": 0.75}},
        {"type": "nudge_outcome", "t_mono": 140.0, "decision_id": 1, "outcome": "success"},
        {"type": "note", "t_mono": 150.0, "text": "free text stays out"},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(r) for r in lines))
    events = explain._events(tmp_path, 100.0)
    assert [e["kind"] for e in events] == ["marker", "gate_wait", "gate_end", "role", "nudge", "outcome"]
    assert events[1]["text"] == "held back: distracted 62% < 70%" and events[1]["t"] == 10.0
    assert events[4]["confidence"] == 0.75 and events[5]["outcome"] == "success"
    assert explain._thresholds(tmp_path)["source"] == "today"                     # no snapshot: today's file


# --- the synthetic dataset (eepy/state/dataset.py) and the live page's server ---------------------------

from eepy.state import dataset as ds_mod  # noqa: E402


def test_synthetic_dataset_build_train_eval(tmp_path, capsys):
    folder = tmp_path / "synthetic" / "t"
    manifest = ds_mod.build(folder, users=6, minutes=(4, 5), seed=3, hard_users=2, say=None)
    again = ds_mod.build(tmp_path / "synthetic" / "t2", users=6, minutes=(4, 5), seed=3, hard_users=2, say=None)
    assert [u["profile"] for u in manifest["users"]] == [u["profile"] for u in again["users"]]   # reproducible
    assert {u["split"] for u in manifest["users"]} == {"train", "test", "test_hard"}
    first = folder / manifest["users"][0]["folder"]
    truth = ds_mod.truth_of(first)
    assert truth[0][0] == 0 and all(s in ds_mod.STATES5 for _, _, s in truth)
    assert session_features(first)                                   # a normal session folder
    folders = [folder / u["folder"] for u in ds_mod.users_of(folder, ["train"])]
    data, trans = ds_mod.collect(folders, PARAMS)
    fitted = ds_mod.fit(PARAMS, data, trans, "t")
    assert fitted.source == "fitted" and fitted.version == PARAMS.version + 1
    assert loads(dump(fitted)) == fitted                             # a valid, loadable parameter file
    scores = ds_mod.run_model("hmm", [folder / u["folder"] for u in ds_mod.users_of(folder, ["test"])])
    assert 0 <= scores["accuracy"] <= 1 and "false_alarm_s_per_hour" in scores
    assert ds_mod.main(["eval", "--name", "t", "--data", str(tmp_path), "--split", "all", "--model", "rules"]) == 0
    assert "accuracy (5 states)" in capsys.readouterr().out


def test_evaluate_scores_against_the_truth():
    from eepy.state.estimate import StateEstimate

    truth = [(0, 100, "focused"), (100, 160, "distracted"), (160, 300, "focused")]

    def est(t: float, s: str) -> StateEstimate:
        return StateEstimate(t=1000.0 + t, state=s, scores={}, reasons=[], calibrated=True)

    perfect = [est(t, "distracted" if 100 <= t < 160 else "focused") for t in range(300)]
    r = ds_mod.combine([ds_mod.evaluate(perfect, truth)])
    assert r["accuracy"] == 1.0 and r["false_alarm_s_per_hour"] == 0 and r["detected"]["distracted"] == 1.0
    late = [est(t, "distracted" if 110 <= t < 190 else "focused") for t in range(300)]   # 10 s past the grace
    r = ds_mod.combine([ds_mod.evaluate(late, truth)])
    assert r["delay_median_s"]["distracted"] == 10 and r["false_alarm_s_per_hour"] > 0


def test_the_live_page_serves_data_and_the_page():
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from eepy.perception.live import LiveState, _handler

    state = LiveState(PARAMS)
    est = HMMEstimator(PARAMS)
    for f in simulate([("work", 12)], seed=1):
        state.add(est.update(f), "focused", {"perclos_60s": f.get("perclos_60s")})
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state, "<html>live</html>"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        data = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/data?since=10", timeout=5).read())
        assert page == "<html>live</html>" and data["total"] == 12 and len(data["steps"]) == 2
        assert data["steps"][-1]["raw"] and data["features"]
        assert data["meta"]["thresholds"]["act"] == {"refocus": 0.7, "bedtime": 0.7}   # step 4: not "no gate"
    finally:
        server.shutdown()


def test_the_live_page_inside_the_vision_program_follows_the_session(tmp_path):
    """python -m eepy.perception --live: the page shows the tracker's own HMM (rules steps are
    left out), starts over at a new session, and reads the robot's decisions from the logs."""
    import json

    from eepy.perception.live import SessionFeed
    from eepy.state.estimate import make_estimator
    from eepy.state.runner import StateTracker

    class Baseline:
        def current(self):
            return type("B", (), {"calibrated": True})()

    feed = SessionFeed(PARAMS, type("C", (), {"windows": (10, 60)})(), Baseline())
    tracker = StateTracker(make_estimator("rules"), listeners=[feed])
    work = simulate([("work", 30)], seed=1)
    for f in work[:5]:
        tracker.feed(f)
    assert feed.state.snapshot(0)["total"] == 0                   # the rules have no belief to show
    tracker.use(make_estimator("hmm", params=PARAMS))             # an hmm session starts: a fresh run
    for f in work[5:]:
        tracker.feed(f)
    data = feed.state.snapshot(0)
    assert data["total"] == 25 and data["run"] == 1
    assert len(feed.state.snapshot(20, run=0)["steps"]) == 25     # an older run on the page: everything again
    assert len(feed.state.snapshot(20, run=1)["steps"]) == 5

    t0 = feed.state.t0
    (tmp_path / "events.jsonl").write_text(json.dumps(
        {"type": "policy_decision", "t_mono": t0 + 12, "decision": "wait", "ladder": "refocus",
         "reason": "not sure yet", "evidence": [{"text": "looking away"}]}) + "\n")
    feed.refresh("2026-10-15_P99_hmm", tmp_path, "hmm")
    data = feed.state.snapshot(0)
    assert data["events"][0]["decision"] == "wait" and data["events"][0]["t"] == 12
    assert "(hmm)" in data["live"]["banner"]["part"]
    feed.refresh("2026-10-15_P99_rules", None, "rules")
    assert "rules" in feed.state.snapshot(0)["live"]["banner"]["part"]
    tracker.reset()
    assert feed.state.snapshot(0)["total"] == 0 and feed.state.run == 2


# --- the drowsiness ladder (A16 option A: docs/A16_HMM_DECISIONS.md, Part H) ----------------------------


def test_the_ladder_shows_tired_as_focused():
    assert PARAMS.states == ("focused", "distracted", "tired", "drowsy", "very_drowsy")
    assert PARAMS.reports == ("focused", "distracted", "focused", "drowsy", "very_drowsy")
    est = HMMEstimator(PARAMS)
    for t in range(100, 110):                                    # past the warm-up
        est.update(feats(float(t)))
    est.log_b = np.log(np.array([0.2, 0.05, 0.6, 0.1, 0.05]))     # mostly tired
    e = est.update(feats(110.0))
    assert e.state == "focused" and set(e.scores) >= set(HIDDEN) and "tired" not in e.scores
    assert e.values["hidden"]["tired"] > 0.4 and 0.2 < e.values["drowsiness_level"] < 0.6
    assert abs(sum(e.scores[s] for s in HIDDEN) - 1) < 1e-9


def test_waking_up_is_faster_with_the_ladder():
    """A 2-minute drowsy spell, then work: focused again within 85 s of looking alert again
    [70 s; the one-step model of v1: 97 s], and drowsiness still noticed within 45 s [37]."""
    out = run(simulate([("work", 60), ("sleepy", 120), ("work", 240)], seed=2))
    assert first_at(out, ("drowsy", "very_drowsy"), 60) <= 45
    assert first_at(out, ("focused",), 180) <= 85


@pytest.mark.parametrize("change, message", [
    (lambda raw: raw.update(reported_as={"tired": "sleepy"}), 'reported_as.tired: "sleepy" is not one of'),
    (lambda raw: raw.update(reported_as={"drowsy": "focused"}), 'reported_as: "drowsy" is not an extra hidden state'),
    (lambda raw: raw.pop("reported_as"), 'states: "tired" is not one of focused, distracted, drowsy, very_drowsy, so reported_as must say'),
    (lambda raw: raw.pop("reported_as_why"), "reported_as: reported_as needs a reported_as_why."),
])
def test_ladder_mistakes_are_explained(change, message):
    raw = copy.deepcopy(RAW)
    change(raw)
    with pytest.raises(HMMConfigError) as err:
        parse(raw)
    assert message in str(err.value)


def test_a_four_state_file_still_works():
    raw = copy.deepcopy(RAW)
    raw["states"] = ["focused", "distracted", "drowsy", "very_drowsy"]
    for key in ("reported_as", "reported_as_why"):
        raw.pop(key)
    raw["initial"].pop("tired")
    raw["initial"]["focused"]["value"] += 0.08
    raw["transitions_per_hour"].pop("tired")
    for row in raw["transitions_per_hour"].values():
        row.pop("tired")
    for entry in raw["features"].values():
        entry["params"].pop("tired")
    raw["modulation"]["applies_to"] = ["focused->drowsy", "drowsy->very_drowsy"]
    four = parse(raw)
    assert four.states == HIDDEN
    e = run(simulate([("work", 30)], seed=1), HMMEstimator(four))[-1]
    assert e.state == "focused" and "hidden" not in e.values
