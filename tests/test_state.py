"""The user-state estimator (A13): threshold rules on synthetic feature sequences.

No camera, no MediaPipe: the features are the window summaries the vision process
logs, made up here. Covers each rule on its own, the drowsy points, the priority
order, the dwell times (no flicker), uncalibrated and low-confidence handling,
the YAML checks, the panel's status bar, and replay giving the same states as live.
"""

from __future__ import annotations

import copy
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
import yaml

from eepy.state import STATES, make_estimator
from eepy.state.config import DEFAULT_PATH, RulesConfigError, load, parse
from eepy.state.features import assemble, catalog, empty
from eepy.state.replay import agreement, compare_live, live_states, main as replay_main, run, \
    session_features, timeline
from eepy.state.rules import RulesEstimator
from eepy.state.runner import StateFromPipeline, StateRecorder, StateTracker

REPO = Path(__file__).resolve().parents[1]
RAW = yaml.safe_load(DEFAULT_PATH.read_text())

# An alert person at the desk, calibrated: every rule quiet.
FACE = {"coverage_s_10s": 10.0, "coverage_s_60s": 60.0, "fps_10s": 15.0, "fps_60s": 15.0,
        "face_absent_ratio_10s": 0.0, "face_absent_ratio_60s": 0.0,
        "perclos_10s": 0.02, "perclos_60s": 0.02, "blink_confidence_10s": "ok", "blink_confidence_60s": "ok",
        "blink_rate_60s": 15.0, "blink_mean_ms_60s": 150.0, "blink_mean_ms_rel_60s": 1.0,
        "blink_max_ms_60s": 220.0, "long_blinks_60s": 0, "yawns_60s": 0, "nods_60s": 0,
        "eyes_closed_now_s": 0.0, "baseline_calibrated": True}
GAZE = {"calibrated": True, "off_work_ratio_10s": 0.0, "off_work_ratio_60s": 0.05,
        "no_face_ratio_10s": 0.0, "no_face_ratio_60s": 0.0, "down_other_ratio_10s": 0.0,
        "away_current_s": 0.0}


def feats(t: float = 100.0, face: Optional[Dict[str, Any]] = None, gaze: Optional[Dict[str, Any]] = None,
          aus: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return assemble(t, {**FACE, **(face or {})}, {**GAZE, **(gaze or {})}, aus)


def estimator(raw: Optional[dict] = None) -> RulesEstimator:
    return RulesEstimator(parse(raw if raw is not None else RAW))


def drive(est: Any, timeline_fn: Any, seconds: int, start: int = 0) -> List[Any]:
    """One estimate per second; timeline_fn(t) -> (face, gaze, aus) overrides."""
    out = []
    for t in range(start, start + seconds):
        face, gaze, aus = timeline_fn(t)
        out.append(est.update(feats(float(t), face, gaze, aus)))
    return out


def first(estimates: List[Any], state: str) -> Optional[float]:
    return next((e.t for e in estimates if e.state == state), None)


# --- the config -------------------------------------------------------------------------------


def test_the_shipped_rules_load_and_cover_every_state():
    config = load()
    assert {r.state for r in config.rules} == {"very_drowsy", "drowsy", "absent", "distracted"}
    assert config.priority == ("very_drowsy", "drowsy", "absent", "distracted", "focused")
    assert all(r.says for r in config.rules + config.unknown)


def test_every_shipped_rule_uses_a_known_feature():
    names = catalog(point_states=["drowsy"])
    for rule in load().rules:
        for condition in (rule.when, *rule.needs, *([rule.uncalibrated] if rule.uncalibrated else [])):
            assert condition.feature in names or condition.feature.startswith("AU"), condition


# --- each rule on its own ---------------------------------------------------------------------

DIRECT = [   # (rule, face, gaze, aus, the state it makes)
    ("perclos_very_high", {"perclos_60s": 0.30}, {}, None, "very_drowsy"),
    ("microsleep", {"blink_max_ms_60s": 1800.0}, {}, None, "very_drowsy"),
    ("eyes_closed_now", {"eyes_closed_now_s": 2.0}, {}, None, "very_drowsy"),
    ("repeated_nodding", {"nods_60s": 2}, {}, None, "very_drowsy"),
    ("many_drowsy_signs", {"perclos_60s": 0.15, "long_blinks_60s": 3, "yawns_60s": 1}, {}, None, "very_drowsy"),
    ("perclos_high", {"perclos_60s": 0.15}, {}, None, "drowsy"),
    ("no_face", {"face_absent_ratio_10s": 0.9}, {}, None, "absent"),
    ("looking_away_now", {}, {"away_current_s": 6.0}, None, "distracted"),
    ("often_away", {}, {"off_work_ratio_60s": 0.5}, None, "distracted"),
    ("looking_down_at_lap", {}, {"down_other_ratio_10s": 0.6}, None, "distracted"),
]


@pytest.mark.parametrize("rule, face, gaze, aus, state", DIRECT, ids=[d[0] for d in DIRECT])
def test_each_rule_fires_on_its_own(rule, face, gaze, aus, state):
    raw, fired, _, _ = estimator().evaluate(feats(face=face, gaze=gaze, aus=aus))
    assert raw == state
    assert rule in [r.rule for r in fired[state]]


POINTS = [   # drowsy signs: each adds its points, none is drowsy alone (except PERCLOS)
    ("slow_blinks", {"blink_mean_ms_rel_60s": 1.7}, None, 0.6),
    ("long_blinks", {"long_blinks_60s": 3}, None, 0.6),
    ("yawning", {"yawns_60s": 1}, None, 0.5),
    ("nodding", {"nods_60s": 1}, None, 0.7),
    ("au_eyes_closing", {}, {"status": "running", "AU43_active_60s": 0.2}, 0.5),
]


@pytest.mark.parametrize("rule, face, aus, points", POINTS, ids=[p[0] for p in POINTS])
def test_each_drowsy_sign_adds_its_points_but_alone_is_not_drowsy(rule, face, aus, points):
    raw, fired, _, values = estimator().evaluate(feats(face=face, aus=aus))
    assert raw == "focused"
    assert values["drowsy_points"] == pytest.approx(points)
    assert "drowsy" not in fired


def test_two_weak_signs_together_are_drowsy():
    raw, fired, _, values = estimator().evaluate(feats(face={"yawns_60s": 1, "long_blinks_60s": 4}))
    assert raw == "drowsy" and values["drowsy_points"] == pytest.approx(1.1)
    assert {r.rule for r in fired["drowsy"]} == {"yawning", "long_blinks"}
    assert "yawned in the last minute" in [r.text for r in fired["drowsy"]]


def test_the_reason_carries_the_measured_value_and_threshold():
    _, fired, _, _ = estimator().evaluate(feats(face={"perclos_60s": 0.15}))
    (reason,) = fired["drowsy"]
    assert (reason.feature, reason.value, reason.threshold) == ("perclos_60s", 0.15, 0.12)
    assert reason.text == "eyes closed 15% of the last minute"


def test_nothing_fires_is_focused_with_a_reason():
    est = estimator()
    raw, fired, skipped, _ = est.evaluate(feats())
    assert raw == "focused" and fired["focused"][0].rule == "no_signs"
    assert skipped == ["au_eyes_closing: needs au_status == running (is missing)"]   # AUs not installed


# --- priority ---------------------------------------------------------------------------------


@pytest.mark.parametrize("face, gaze, state", [
    ({"perclos_60s": 0.15}, {"away_current_s": 8.0}, "drowsy"),           # drowsy > distracted
    ({"perclos_60s": 0.30}, {"down_other_ratio_10s": 0.9}, "very_drowsy"),
    ({"face_absent_ratio_10s": 0.9}, {"away_current_s": 9.0}, "absent"),  # absent > distracted
])
def test_priority_order(face, gaze, state):
    assert estimator().evaluate(feats(face=face, gaze=gaze))[0] == state


def test_perclos_waits_for_30_s_of_data():
    raw, _, skipped, _ = estimator().evaluate(feats(face={"perclos_60s": 0.4, "coverage_s_60s": 19.0}))
    assert raw == "focused"
    assert any(x.startswith("perclos_very_high: needs coverage_s_60s >= 30") for x in skipped)


def test_an_empty_desk_is_absent_not_drowsy():
    """The 60 s window still holds the PERCLOS from before the person left: the
    drowsiness rules need the face visible, so it is absent (and listed as skipped)."""
    raw, _, skipped, _ = estimator().evaluate(
        feats(face={"face_absent_ratio_10s": 1.0, "perclos_60s": 0.30}))
    assert raw == "absent"
    assert any(s.startswith("perclos_very_high: needs face_present_ratio_10s") for s in skipped)


def test_back_at_the_desk_the_time_away_does_not_count_as_distraction():
    """off_work_ratio counts the empty desk as off work; gaze_off_ratio only counts
    looking away while there."""
    f = feats(gaze={"off_work_ratio_60s": 0.6, "no_face_ratio_60s": 0.55})
    assert f["gaze_off_ratio_60s"] == pytest.approx(0.05 / 0.45, abs=1e-3)
    assert estimator().evaluate(f)[0] == "focused"


# --- dwell: no flicker -------------------------------------------------------------------------


def calm(t):
    return {}, {}, None


def test_start_up_is_unknown_then_focused_after_its_dwell():
    est = estimator()
    out = drive(est, lambda t: ({"coverage_s_10s": float(min(t, 10))}, {}, None), 20)
    assert out[0].state == "unknown" and "camera data" in out[0].reason_text
    assert first(out, "focused") == 10.0          # data from 5 s, then 5 s of focused


def test_a_short_look_away_does_not_flicker():
    est = estimator()
    drive(est, calm, 20)
    out = drive(est, lambda t: ({}, {"away_current_s": float(t - 20) if t < 28 else 0.0}, None), 20, start=20)
    # away 0..7 s: the rule fires from 5 s (t=25) for 3 s -- shorter than the 5 s dwell.
    assert all(e.state == "focused" for e in out)
    assert any(e.pending == "distracted" for e in out)


def test_a_long_look_away_is_distracted_5_s_after_the_rule_fires():
    est = estimator()
    drive(est, calm, 20)
    out = drive(est, lambda t: ({}, {"away_current_s": float(t - 20)}, None), 20, start=20)
    assert first(out, "distracted") == 30.0       # looked away at 20, rule at 25, dwell 5


def test_one_yawn_alone_changes_nothing():
    est = estimator()
    drive(est, calm, 20)
    out = drive(est, lambda t: ({"yawns_60s": 1}, {}, None), 60, start=20)
    assert {e.state for e in out} == {"focused"}


def test_drowsy_needs_10_s_and_then_holds_20_s_without_signs():
    est = estimator()
    drive(est, calm, 20)
    out = drive(est, lambda t: ({"perclos_60s": 0.15 if t < 60 else 0.05}, {}, None), 60, start=20)
    assert first(out, "drowsy") == 30.0
    after = [e for e in out if e.t >= 60]
    back = first(after, "focused")
    assert back == 79.0                           # last sign at 59 s + 20 s (focused's own 5 s overlaps)
    held = next(e for e in after if e.t == 70.0)
    assert held.pending == "focused"
    assert held.state == "drowsy" and held.reasons[0].rule == "hold"
    assert "until 20 s without signs" in held.reason_text


def test_very_drowsy_comes_fast_even_from_drowsy():
    est = estimator()
    drive(est, calm, 20)
    drive(est, lambda t: ({"perclos_60s": 0.15}, {}, None), 15, start=20)
    assert est.state == "drowsy"
    out = drive(est, lambda t: ({"perclos_60s": 0.15, "nods_60s": 2}, {}, None), 10, start=35)
    assert first(out, "very_drowsy") == 38.0      # 3 s: not held back by drowsy's leave time


def test_scores_are_one_hot_over_all_states():
    est = estimator()
    e = drive(est, calm, 12)[-1]
    assert set(e.scores) == set(STATES) and sum(e.scores.values()) == 1.0 and e.scores["focused"] == 1.0


# --- uncalibrated, low confidence, unknown ------------------------------------------------------


def test_uncalibrated_falls_back_to_the_raw_feature_and_is_flagged():
    est = estimator()
    f = feats(face={"baseline_calibrated": False, "blink_mean_ms_rel_60s": None,
                    "blink_mean_ms_60s": 340.0, "yawns_60s": 1}, gaze={"calibrated": False})
    raw, fired, skipped, _ = est.evaluate(f)
    assert raw == "drowsy"                        # 0.6 (slow blinks, 300 ms rule) + 0.5 (yawn)
    slow = next(r for r in fired["drowsy"] if r.rule == "slow_blinks")
    assert slow.feature == "blink_mean_ms_60s" and slow.text == "slow blinks (340 ms on average)"
    e = est.update(f)
    assert e.calibrated is False
    assert any("not calibrated" in s for s in e.skipped) and any("gaze not calibrated" in s for s in e.skipped)


def test_calibrated_without_a_value_skips_instead_of_falling_back():
    raw, _, skipped, values = estimator().evaluate(
        feats(face={"blink_mean_ms_rel_60s": None, "blink_mean_ms_60s": 900.0}))
    assert raw == "focused" and values["drowsy_points"] == 0
    assert "slow_blinks: no value for blink_mean_ms_rel_60s" in skipped


def test_blink_rules_are_skipped_when_the_camera_is_too_slow_for_blinks():
    raw, _, skipped, values = estimator().evaluate(
        feats(face={"blink_confidence_60s": "low", "long_blinks_60s": 6, "yawns_60s": 1}))
    assert raw == "focused" and values["drowsy_points"] == pytest.approx(0.5)   # only the yawn
    assert any(s.startswith("long_blinks: needs blink_confidence_60s == ok (is low)") for s in skipped)


def test_au_rules_only_with_action_units_running():
    for aus in (None, {"status": "paused", "AU43_active_60s": 0.5}):
        _, _, skipped, values = estimator().evaluate(feats(aus=aus))
        assert values["drowsy_points"] == 0 and any(s.startswith("au_eyes_closing") for s in skipped)


@pytest.mark.parametrize("face, words", [
    ({"coverage_s_10s": 3.0}, "only 3 s of camera data"),
    ({"fps_10s": 3.0}, "camera too slow (3 pictures/s)"),
])
def test_not_enough_data_is_unknown_whatever_else_fires(face, words):
    raw, fired, _, _ = estimator().evaluate(feats(face={**face, "perclos_60s": 0.4}))
    assert raw == "unknown" and words in fired["unknown"][0].text


def test_no_pictures_for_a_while_becomes_unknown():
    """The camera stops: no window summaries at all. The tracker feeds "no data"
    after gap_seconds, so the state does not freeze on the last one."""
    est = estimator()
    out: List[Any] = []
    tracker = StateTracker(est, gap_seconds=3, listeners=[out.append])
    for t in range(0, 20):
        tracker.feed(feats(float(t)))
    assert tracker.latest.state == "focused"
    tracker.idle(30.0, slack=0.0)                 # nothing since t=19
    gap = [e for e in out if e.t > 19]
    assert gap[0].t == 22.0 and gap[0].pending == "unknown"   # still shows focused (dwell)
    assert gap[0].reason_text == "unknown in 2 s if it lasts"
    became = next(e for e in gap if e.state == "unknown")
    assert became.t == 24.0 and became.reason_text == "no pictures from the camera"   # 3 s gap + 2 s
    tracker.feed(feats(31.0))                     # pictures again
    assert out[-1].t == 31.0


# --- the YAML checks ----------------------------------------------------------------------------


def broken(change):
    raw = copy.deepcopy(RAW)
    change(raw)
    return raw


def rule(raw, rule_id):
    return next(r for r in raw["rules"] if r["id"] == rule_id)


@pytest.mark.parametrize("change, words", [
    (lambda r: rule(r, "perclos_high").update(when="perclos_6s >= 0.12"),
     'unknown feature "perclos_6s" (did you mean "perclos_60s"?)'),
    (lambda r: rule(r, "perclos_high").update(when="perclos_60s => 0.12"), "is not a condition"),
    (lambda r: rule(r, "perclos_high").update(state="sleepy"), 'state must be one of'),
    (lambda r: rule(r, "perclos_high").pop("says"), 'rule "perclos_high" needs `says`'),
    (lambda r: rule(r, "perclos_high").pop("points"), "give this rule `points`"),
    (lambda r: rule(r, "no_face").update(points=1), "only ['drowsy'] rules take `points`"),
    (lambda r: rule(r, "perclos_high").update(says="closed {valu}"), "cannot be filled in"),
    (lambda r: rule(r, "perclos_high").update(treshold=3), "unknown key(s) ['treshold']"),
    (lambda r: rule(r, "yawning").update(id="perclos_high"), "rule ids must be unique"),
    (lambda r: rule(r, "slow_blinks").update(needs="blink_confidence_60s == ok"), "`needs` is a list"),
    (lambda r: rule(r, "slow_blinks").update(when="blink_confidence_60s >= ok"), "compares with a word"),
    (lambda r: r.update(priority=["drowsy", "very_drowsy", "absent", "focused"]), "priority must list each"),
    (lambda r: r["dwell"]["enter"].update(sleepy=3), '"sleepy" is not a state here'),
    (lambda r: r["dwell"]["leave"].update(drowsy=-1), "must be a number >= 0"),
    (lambda r: r.update(gap_seconds=0), "gap_seconds must be a positive number"),
    (lambda r: r.update(thresholds={}), "unknown setting(s) ['thresholds']"),
])
def test_mistakes_in_the_yaml_are_explained(change, words):
    with pytest.raises(RulesConfigError) as error:
        parse(broken(change))
    assert words in str(error.value)
    assert str(error.value).startswith("config/state_rules.yaml:")


def test_invalid_yaml_says_so(tmp_path):
    bad = tmp_path / "rules.yaml"
    bad.write_text("priority: [very_drowsy\nrules:\n  - id: x\n")
    with pytest.raises(RulesConfigError, match="not valid YAML"):
        load(bad)


def test_a_threshold_change_in_the_yaml_changes_the_state():
    looser = broken(lambda r: rule(r, "perclos_high").update(when="perclos_60s >= 0.08"))
    f = feats(face={"perclos_60s": 0.10})
    assert estimator().evaluate(f)[0] == "focused"
    assert estimator(looser).evaluate(f)[0] == "drowsy"


# --- live = replay -------------------------------------------------------------------------------


def scenario(t: float) -> Dict[str, Any]:
    """A made-up session (seconds): working, a phone, drowsy, away, a camera pause."""
    face, gaze = dict(FACE), dict(GAZE)
    if 30 <= t < 55:
        gaze.update(away_current_s=float(t - 30), down_other_ratio_10s=0.8)
    if 70 <= t < 110:
        face.update(perclos_60s=0.14 + (t - 70) * 0.004, yawns_60s=1)
    if 130 <= t < 150:
        face.update(face_absent_ratio_10s=1.0)
        gaze.update(no_face_ratio_10s=1.0)
    return {"face": face, "gaze": gaze}


def test_replay_gives_the_same_states_as_live(tmp_path):
    from eepy.logs.features import ProcessEventLog
    from eepy.logs.session import SessionLog

    data = tmp_path / "sessions"
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    log.start("P09", mode="wizard")
    events = ProcessEventLog("vision", data_dir=data, flush_seconds=0.05)
    stream = StateRecorder.make_stream(data_dir=data, flush_seconds=0.05)
    live: List[Any] = []
    tracker = StateTracker(make_estimator("rules"), gap_seconds=3,
                           listeners=[live.append, StateRecorder(events, stream)])
    subscriber = StateFromPipeline(tracker, session=lambda: "P09-session")
    t0 = 1000.0
    for step in range(0, 400):                     # 0.5 s steps, like the vision main loop
        t = t0 + step * 0.5
        second = step * 0.5
        if 160 <= second < 170:                    # camera pause: no summaries, only idle ticks
            tracker.idle(t)
            continue
        if step % 2 == 0:                          # one window summary a second
            parts = scenario(second)
            result = SimpleNamespace(t=t + 0.0371, window=parts["face"],
                                     records=[("gaze_window", dict(parts["gaze"]))])
            # what FacePipeline._record writes, then what the subscriber sees:
            events.event("face_window", t_mono=result.t, **result.window)
            events.event("gaze_window", t_mono=result.t, **parts["gaze"])
            subscriber(result)
        tracker.idle(t)
    for annotation_t, marker in ((t0 + 31, "phone_picked_up"), (t0 + 80, "yawn"), (t0 + 56, "back_on_task")):
        log.event("annotation", source="wizard", t_mono=annotation_t, marker=marker)
    events.close()
    stream.close()
    log.end()
    (folder,) = [p for p in data.iterdir() if p.is_dir()]

    replayed = run(session_features(folder), make_estimator("rules"), gap_seconds=3)
    assert [(round(e.t, 3), e.state, e.pending) for e in replayed] == \
           [(round(e.t, 3), e.state, e.pending) for e in live]
    states = [s.state for s in timeline(replayed)]
    # (Every drowsy sign stops at once at 110 s: very drowsy holds 20 s, then straight to focused.)
    assert states == ["unknown", "focused", "distracted", "focused", "drowsy", "very_drowsy",
                      "focused", "absent", "focused", "unknown", "focused"], states
    same, compared = compare_live(replayed, live_states(folder))
    assert compared > 150 and same == compared
    changes = [json.loads(line) for line in (folder / "events.vision.jsonl").read_text().splitlines()
               if '"state_changed"' in line]
    assert [c["to"] for c in changes] == states
    assert changes[2]["rules"] == ["looking_away_now", "looking_down_at_lap"]   # both fired
    assert changes[2]["values"]["away_current_s"] >= 5 and changes[2]["from"] == "focused"

    result = agreement(replayed, [(t0 + 31, "phone_picked_up"), (t0 + 80, "yawn")])
    assert result["phone_picked_up"].agreed == 1 and result["yawn"].agreed == 1


def test_the_replay_command(tmp_path, capsys):
    from eepy.logs.features import ProcessEventLog
    from eepy.logs.session import SessionLog

    data = tmp_path / "sessions"
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    log.start("P10", mode="wizard")
    events = ProcessEventLog("vision", data_dir=data, flush_seconds=0.05)
    from eepy.logs.clock import monotonic

    t0 = monotonic()
    for second in range(0, 120):
        parts = scenario(second)
        events.event("face_window", t_mono=t0 + second, **parts["face"])
        events.event("gaze_window", t_mono=t0 + second, **parts["gaze"])
    log.event("annotation", source="wizard", t_mono=t0 + 31, marker="phone_picked_up")
    log.event("annotation", source="wizard", t_mono=t0 + 40, marker="user_replied")
    events.close()
    log.end()
    out_csv = tmp_path / "states.csv"
    assert replay_main(["P10", "--data", str(tmp_path), "--csv", str(out_csv)]) == 0
    text = capsys.readouterr().out
    assert "120 seconds of features" in text and "distracted" in text and "looking away for" in text
    assert "phone_picked_up    1/1 agreed" in text and "(not about the state: user_replied)" in text
    assert out_csv.read_text().splitlines()[0].startswith("t,t_mono,state")

    looser = tmp_path / "looser.yaml"
    looser.write_text(DEFAULT_PATH.read_text().replace("when: away_current_s >= 5", "when: away_current_s >= 50"))
    assert replay_main(["P10", "--data", str(tmp_path), "--rules", str(looser)]) == 0
    assert "looking away for" not in capsys.readouterr().out


def test_replay_explains_a_session_without_face_features(tmp_path, capsys):
    from eepy.logs.session import SessionLog

    data = tmp_path / "sessions"
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    log.start("P11", mode="wizard")
    log.end()
    assert replay_main(["P11", "--data", str(tmp_path)]) == 1
    assert "no face features" in capsys.readouterr().err


def test_the_estimator_is_chosen_by_name_and_mode():
    from eepy.state.estimate import for_mode

    assert make_estimator("rules").name == "rules"
    assert for_mode("hmm") == "rules"             # until A16 registers the HMM
    with pytest.raises(ValueError, match="No state estimator called"):
        make_estimator("crystal_ball")


def test_empty_features_are_unknown():
    assert estimator().evaluate(empty(5.0))[0] == "unknown"


# --- the panel ----------------------------------------------------------------------------------


def test_the_relay_carries_the_state_to_the_panel():
    from eepy.camera.relay import CameraRelay, RelayClient
    from eepy.state.runner import StatePublisher

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    got: List[dict] = []
    relay = CameraRelay(port, state_fn=got.append).start()
    publisher = StatePublisher(port, client=RelayClient(port))
    try:
        est = estimator()
        drive(est, calm, 12)
        publisher(est.update(feats(12.0)))
        assert got[-1]["state"] == "focused" and got[-1]["estimator"] == "rules"
        assert got[-1]["reason"] == "face on the work; no signs of drowsiness or distraction"
    finally:
        publisher.close()
        relay.stop()


# --- the self-test scenario's report --------------------------------------------------------------


def test_the_scenario_report_gives_states_and_switch_times():
    from eepy.state.scenario import SCENARIO, judge, report

    est = estimator()
    estimates, phases, t = [], [], 0.0

    def run_phase(phase, face, gaze, seconds):
        nonlocal t
        start = t
        for _ in range(int(seconds)):
            estimates.append(est.update(feats(t, face, gaze)))
            t += 1.0
        phases.append((phase, start, t - 1.0))

    work, phone, back, sleepy, leave = SCENARIO
    run_phase(work, {}, {}, 40)
    run_phase(phone, {}, {"away_current_s": 0.0}, 20)      # (never looked away: CHECK)
    run_phase(back, {}, {}, 20)
    run_phase(sleepy, {"perclos_60s": 0.2}, {}, 45)
    run_phase(leave, {"face_absent_ratio_10s": 1.0}, {}, 30)
    results = judge(phases, estimates)
    assert [r.passed for r in results] == [True, False, True, True, True]
    assert results[0].reached_after == 5.0                 # data from the start + 5 s dwell
    assert results[3].reached_after == 10.0                # drowsy's 10 s dwell
    assert results[4].reached_after == 19.0                # 20 s after the last drowsy sign (1 s before)
    text = "\n".join(report(results, estimates, 0.0))
    assert "2. Phone          expected distracted             CHECK distracted never shown" in text
    assert "4. Act sleepy     expected drowsy/very drowsy     PASS  drowsy/very drowsy after 10 s" in text
    assert "     90.0 s  drowsy       eyes closed 20% of the last minute" in text
