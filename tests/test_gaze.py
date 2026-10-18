"""Gaze on the work area (eepy/perception/gaze/) on synthetic faces: the head
angles and the iris position are set exactly, so every expected value is known.
No camera, no MediaPipe.
"""

from __future__ import annotations

import json
import math
import time
from typing import List

import numpy as np
import pytest

from eepy.camera.frame import Frame
from eepy.logs.schema import check_event
from eepy.perception.baseline import Baseline
from eepy.perception.gaze import calibration as calibration_module
from eepy.perception.gaze.calibration import STEPS, CalibrationStore, GazeCalibration, zone_model_from
from eepy.perception.gaze.config import GazeConfig, parse
from eepy.perception.gaze.estimate import LEFT, MODE_HEAD, MODE_IRIS, RIGHT, GazeEstimate, GazeEstimator, iris_offsets
from eepy.perception.gaze.extractor import GazeExtractor
from eepy.perception.gaze.zones import Rect, ZoneModel, ZoneSet
from eepy.perception.observation import FaceObservation, HeadAngles
from eepy.camera.config import CameraConfigError

BASE = Baseline()
CFG = GazeConfig()


def face_landmarks(h: float = 0.0, v: float = 0.0, eye_width: float = 40.0, h_left: float = None,
                   size: int = 800) -> np.ndarray:
    """478 normalised landmarks in a size x size picture: two eyes, each with its
    corners, lids and iris placed so that iris_offsets() returns exactly (h, v)."""
    pts = np.full((478, 3), 0.5, dtype=np.float32)

    def eye(spec, cx, offset_h):
        left, right, top, bottom, iris = spec
        half = eye_width / 2
        cy = 300.0
        px = {left: (cx - half, cy), right: (cx + half, cy),
              top: (cx, cy - eye_width * 0.15), bottom: (cx, cy + eye_width * 0.15),
              iris: (cx + offset_h * half, cy + v * eye_width * 0.15)}
        for index, (x, y) in px.items():
            pts[index, :2] = (x / size, y / size)

    eye(RIGHT, 340.0, h)                          # the person's right eye: image left
    eye(LEFT, 460.0, h if h_left is None else h_left)
    # A9's eye-openness points (EAR): an open eye, so eye_closure stays low.
    from eepy.perception import landmarks as lm

    for spec, cx in ((lm.RIGHT_EYE, 340.0), (lm.LEFT_EYE, 460.0)):
        p1, p2, p3, p4, p5, p6 = spec
        half, lid = eye_width / 2, eye_width * 0.15
        # (p1 and p4 are the eye corners, already placed above.)
        for index, (x, y) in {p2: (cx - half / 3, 300 - lid), p3: (cx + half / 3, 300 - lid),
                              p6: (cx - half / 3, 300 + lid), p5: (cx + half / 3, 300 + lid)}.items():
            pts[index, :2] = (x / size, y / size)
    return pts


def obs(t: float, head_yaw: float = 0.0, head_pitch: float = 0.0, present: bool = True,
        source: str = "laptop", **iris) -> FaceObservation:
    frame = Frame(image=np.zeros((4, 4, 3), np.uint8), t=t, index=int(t * 1000) + 1, source=source)
    if not present:
        return FaceObservation(t=t, frame_index=frame.index, source=source, frame=frame, present=False,
                               width=800, height=800)
    return FaceObservation(t=t, frame_index=frame.index, source=source, frame=frame, present=True,
                           landmarks=face_landmarks(**iris), head=HeadAngles(head_yaw, head_pitch, 0.0),
                           width=800, height=800)


def sample(fps: float, seconds: float, start: float = 0.0) -> List[float]:
    return [start + i / fps for i in range(int(seconds * fps))]


# --- the estimate ---------------------------------------------------------------------------


def test_iris_offsets_and_their_signs():
    points = face_landmarks(h=0.4, v=-0.5)[:, :2] * 800
    h, v, width = iris_offsets(points, RIGHT)
    assert h == pytest.approx(0.4, abs=1e-3) and v == pytest.approx(-0.5, abs=1e-3) and width == pytest.approx(40)
    # + = towards the image's right = the PERSON's left, the same as head yaw +.
    assert iris_offsets(face_landmarks(h=0.5)[:, :2] * 800, LEFT)[0] > 0


def test_gaze_is_head_plus_eyes_in_iris_mode():
    estimate = GazeEstimator(CFG).estimate(obs(0.0, head_yaw=10, head_pitch=5, h=0.4, v=0.2))
    assert estimate.mode == MODE_IRIS and estimate.why_head_only == ""
    assert estimate.yaw == pytest.approx(10 + 0.4 * 35, abs=0.1)
    assert estimate.pitch == pytest.approx(5 + 0.2 * 25, abs=0.1)


@pytest.mark.parametrize("kwargs, closure, reason", [
    (dict(eye_width=10), None, "too small"),
    (dict(head_yaw=55), None, "turned too far"),
    (dict(h=0.5, h_left=-0.4), None, "disagree"),
    ({}, 0.8, "half closed"),
])
def test_head_only_fallback(kwargs, closure, reason):
    head = {k: kwargs.pop(k) for k in ("head_yaw",) if k in kwargs}
    estimate = GazeEstimator(CFG).estimate(obs(0.0, **head, **kwargs), eye_closure=closure)
    assert estimate.mode == MODE_HEAD and reason in estimate.why_head_only
    assert estimate.yaw == pytest.approx(head.get("head_yaw", 0.0))        # the head alone


def test_a_jittery_iris_falls_back_to_the_head():
    estimator = GazeEstimator(CFG)
    modes = [estimator.estimate(obs(i / 30, h=0.6 if i % 2 else -0.6)).mode for i in range(12)]
    assert modes[-1] == MODE_HEAD
    assert "jittery" in estimator.estimate(obs(0.4, h=0.6)).why_head_only


# --- zones -------------------------------------------------------------------------------------


def test_default_zones():
    zones = ZoneModel.default(CFG)
    assert not zones.calibrated
    assert zones.classify(0, 0, True).zone == "screen"
    assert zones.classify(0, 30, True).zone == "notes"
    assert zones.classify(0, 60, True).zone == "down_other"      # the lap: below, not the notes
    assert zones.classify(45, 0, True).zone == "away"
    assert zones.classify(0, -40, True).zone == "away"           # up
    assert zones.classify(20, 0, True).on_work == pytest.approx(0.5)   # exactly on the edge
    assert zones.classify(0, 0, True).on_work > 0.9 and zones.classify(60, 0, True).on_work < 0.01


def test_rect_signed_distance():
    rect = Rect(-10, 10, -5, 5)
    assert rect.signed_distance(0, 0) == -5 and rect.signed_distance(13, 0) == 3
    assert rect.signed_distance(13, 9) == pytest.approx(5)


def test_iris_and_head_modes_use_their_own_zones():
    gaze = ZoneSet(Rect(-20, 20, -10, 10), None)
    head = ZoneSet(Rect(-8, 8, -5, 5), None)                     # head turns less than the eyes
    model = ZoneModel(gaze, head, calibrated=True)
    assert model.classify(15, 0, iris_mode=True).zone == "screen"
    assert model.classify(15, 0, iris_mode=False).zone == "away"


# --- calibration -------------------------------------------------------------------------------


TARGETS = {"screen_center": (0, 10), "screen_top_left": (-15, 0), "screen_top_right": (15, 0),
           "screen_bottom_right": (15, 20), "screen_bottom_left": (-15, 20),
           "notes_center": (0, 45), "notes_far": (0, 38), "away": (50, 5)}


def run_calibration(targets=TARGETS, face_every: int = 1) -> GazeCalibration:
    cal = GazeCalibration(CFG)
    while not cal.done:
        yaw, pitch = targets[cal.step.key]
        for i in range(30):
            present = i % face_every == 0
            cal.add(GazeEstimate(yaw, pitch, MODE_IRIS, yaw * 0.6, pitch * 0.6, None, None)
                    if present else None)
        cal.next()
    return cal


def test_calibration_builds_zones_around_the_points():
    section = run_calibration().finish()
    model = zone_model_from(section, CFG)
    assert model.calibrated
    for key, (yaw, pitch) in TARGETS.items():
        expected = "away" if key == "away" else ("notes" if key.startswith("notes") else "screen")
        assert model.classify(yaw, pitch, iris_mode=True).zone == expected, key
    # The head-only zones are learned from the head angles (smaller here).
    assert model.classify(0.6 * -15, 0.6 * 0, iris_mode=False).zone == "screen"
    assert model.classify(0, 0.6 * 42, iris_mode=False).zone == "notes"
    assert section["warnings"] == []


def test_calibration_warns_when_looking_away_lands_in_the_work_area():
    targets = dict(TARGETS, away=(5, 10))
    assert any("inside the work area" in w for w in run_calibration(targets).finish()["warnings"])


def test_a_step_without_a_face_is_repeated():
    cal = GazeCalibration(CFG)
    for _ in range(30):
        cal.add(None)
    ok, why = cal.step_quality()
    assert not ok and "no face" in why


def test_the_store_keeps_other_sections(tmp_path):
    store = CalibrationStore(tmp_path)
    store.save_section("P03", "laptop", "baseline", {"ear_open": 0.33})     # A12, some day
    store.save_section("P03", "laptop", "gaze", {"zones": {}})
    data = json.loads((tmp_path / "P03" / "laptop.json").read_text())
    assert data["baseline"] == {"ear_open": 0.33} and data["gaze"] == {"zones": {}}
    assert store.section("P03", "robot", "gaze") is None                  # per camera setup
    assert store.path("../../etc", "x").parent.parent == tmp_path          # no escaping the folder


# --- the extractor: zones per frame, hysteresis, events, windows -----------------------------------


def make(tmp_path, **changes) -> GazeExtractor:
    return GazeExtractor(GazeConfig().replace(**changes), store=CalibrationStore(tmp_path))


def run(extractor: GazeExtractor, fps: float, script):
    """script: list of (seconds, head_yaw, head_pitch, present). Returns (values, events)."""
    values, events, t = [], [], 0.0
    for seconds, yaw, pitch, present in script:
        for tt in sample(fps, seconds, t):
            out = extractor.update(obs(tt, head_yaw=yaw, head_pitch=pitch, present=present), BASE)
            values.append(out.values)
            events += out.events
        t += seconds
    return values, events


GLANCES = [(3.0, 0, 5, True),       # on the screen
           (0.5, 45, 5, True),      # a quick glance away: does NOT count
           (2.0, 0, 5, True),
           (2.5, 45, 5, True),      # really away
           (0.3, 0, 5, True),       # a blip back: does not count either
           (1.5, 45, 5, True),
           (3.0, 0, 5, True)]       # back on work


@pytest.mark.parametrize("fps", [10, 30])
def test_hysteresis_short_glances_do_not_count_long_ones_do(tmp_path, fps):
    values, events = run(make(tmp_path), fps, GLANCES)
    kinds = [(e.type, round(e.t, 1)) for e in events]
    assert [k for k, _ in kinds] == ["looked_away", "back_on_work"]
    assert kinds[0][1] == pytest.approx(5.5, abs=0.15)              # when it really began
    back = events[1]
    assert back.t == pytest.approx(9.8, abs=0.15)
    assert back.fields["away_seconds"] == pytest.approx(4.3, abs=0.2)     # ends at the real return
    assert back.fields["zone_was"] == "away"
    # away_s counts until "back" is CONFIRMED (0.5 s after the eyes return):
    assert max(v["away_s"] for v in values) == pytest.approx(4.3 + 0.5, abs=0.2)
    assert values[-1]["away_s"] == 0.0 and values[-1]["gaze_zone"] == "screen"


def test_minimum_durations_are_configurable(tmp_path):
    _, events = run(make(tmp_path, min_away_seconds=0.3), 30, GLANCES)
    assert [e.type for e in events].count("looked_away") >= 2      # now the quick glance counts


def test_no_face_is_off_work_but_looking_down_is_work(tmp_path):
    """Base version: looking down (notes, keyboard, lap) counts as work -- reading notes
    was mistaken for the lap half the time. The zone itself is still told apart."""
    values, events = run(make(tmp_path), 30, [(2, 0, 5, True), (2, 0, 0, False), (2, 0, 5, True),
                                              (2, 0, 60, True)])
    zones = [v["gaze_zone"] for v in values]
    assert "no_face" in zones and "down_other" in zones
    assert [e.fields.get("zone") for e in events if e.type == "looked_away"] == ["no_face"]


def test_values_and_modes_per_frame(tmp_path):
    extractor = make(tmp_path)
    out = extractor.update(obs(0.0, head_yaw=5, head_pitch=2, h=0.2), BASE)
    v = out.values
    assert v["gaze_mode"] == MODE_IRIS and v["gaze_calibrated"] == 0
    assert v["gaze_yaw"] == pytest.approx(5 + 0.2 * 35, abs=0.1)
    assert 0 < v["gaze_confidence"] <= 0.7                          # uncalibrated: never full
    head_only = extractor.update(obs(0.05, head_yaw=5, head_pitch=2, eye_width=8), BASE).values
    assert head_only["gaze_mode"] == MODE_HEAD


def test_windows(tmp_path):
    extractor = make(tmp_path)
    run(extractor, 15, [(4.0, 0, 5, True), (3.0, 45, 5, True), (2.0, 0, 5, True), (1.5, 45, 5, True)])
    window = dict(extractor.summary(10.5))["gaze_window"]
    assert window["away_episodes_10s"] == 2
    assert window["away_longest_s_10s"] == pytest.approx(3.0, abs=0.2)
    assert window["away_current_s"] == pytest.approx(1.5, abs=0.2)
    assert window["off_work_ratio_10s"] == pytest.approx(0.45, abs=0.06)
    assert window["screen_ratio_10s"] + window["away_ratio_10s"] == pytest.approx(1.0, abs=0.02)
    check_event("gaze_window", "perception", window)


def test_the_calibration_for_the_person_and_camera_is_used(tmp_path):
    store = CalibrationStore(tmp_path)
    store.save_section("P07", "laptop", "gaze", run_calibration().finish())
    extractor = GazeExtractor(store=store, user="P07")
    extractor.update(obs(0.0, source="laptop"), BASE)
    assert extractor.zones.calibrated
    status = dict(extractor.drain())["gaze_status"]
    assert status == {"calibrated": True, "user": "P07", "setup": "laptop"}
    check_event("gaze_status", "perception", status)
    extractor.update(obs(0.1, source="robot"), BASE)              # another camera: not calibrated
    assert not extractor.zones.calibrated
    assert "calibrate_gaze" in dict(extractor.drain())["gaze_status"]["note"]


def test_events_fit_the_logging_schema(tmp_path):
    _, events = run(make(tmp_path), 15, GLANCES)
    for event in events:
        check_event(event.type, "perception", event.fields)


def test_resolve_user_follows_the_session(monkeypatch):
    import eepy.logs.features as features
    from eepy.perception.gaze import resolve_user

    monkeypatch.setattr(features, "current_session", lambda: {"participant": "P12"})
    assert resolve_user(None) == "P12" and resolve_user("P01") == "P01"
    monkeypatch.setattr(features, "current_session", lambda: None)
    assert resolve_user(None) == "default"


def test_config_errors_are_explained():
    with pytest.raises(CameraConfigError, match="not a gaze setting"):
        parse({"min_away": 1})
    with pytest.raises(CameraConfigError, match="below pitch_max"):
        parse({"default_notes": {"pitch_min": 50, "pitch_max": 40}})


def test_settings_py_validates_the_gaze_section():
    pytest.importorskip("pydantic")
    from eepy.settings import load_settings

    assert load_settings().gaze is not None


def test_calibration_steps_cover_screen_notes_and_away():
    groups = [s.group for s in STEPS]
    assert groups.count("screen") == 5 and groups.count("notes") >= 1 and groups[-1] == "away"
    assert calibration_module.CALIBRATION_DIR.parts[-2:] == ("data", "calibration")


# --- the bonus: real "look at user" -----------------------------------------------------------------


def test_face_offset_points_the_robot_at_the_eyes():
    from eepy.perception.gaze.look_at import face_offset

    centred = face_offset(obs(0.0), fov_deg=90)              # eyes at x 340/460 -> centre 400 of 800
    assert centred["yaw"] == pytest.approx(0, abs=0.01) and centred["pitch"] < 0   # eyes above the middle
    shifted = FaceObservation(**{**obs(0.0).__dict__, "landmarks": face_landmarks() + [0.1, 0, 0]})
    right = face_offset(shifted, fov_deg=90)                  # face further RIGHT in the picture...
    assert right["yaw"] < 0                                   # ...the robot turns to ITS right (yaw -)
    # 80 px right of centre with a 90-degree lens on 800 px: atan(80 / 400) = 11.3 degrees.
    assert right["yaw"] == pytest.approx(-math.degrees(math.atan(80 / 400)), abs=0.05)
    assert face_offset(obs(0.0, present=False), 90) is None


def test_the_publisher_reports_through_the_relay_and_says_no_face_once():
    from eepy.camera.relay import CameraRelay
    from eepy.perception.gaze.look_at import UserTargetPublisher

    received = []
    port = _free_port()
    relay = CameraRelay(port, user_fn=received.append).start()
    publisher = UserTargetPublisher(port, robot_lens_fov=87, laptop_view_fov=50, rate=1000)
    try:
        result = type("R", (), {"observation": obs(0.0)})()
        gone = type("R", (), {"observation": obs(0.1, present=False)})()
        for r in (result, gone, gone, gone):
            publisher(r)
            time.sleep(0.002)
    finally:
        publisher.close()
        relay.stop()
    assert received[0]["yaw"] == pytest.approx(0, abs=0.01)
    assert received[1:] == [None]                             # "no face" said once


def test_the_publisher_is_silent_without_a_panel():
    from eepy.perception.gaze.look_at import UserTargetPublisher

    publisher = UserTargetPublisher(_free_port(), 87, 50, rate=1000)
    publisher(type("R", (), {"observation": obs(0.0)})())    # nobody listening: no error
    assert publisher.sent == 0
    publisher.close()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def provider(clock, pose=None):
    pytest.importorskip("pydantic")
    from eepy.behaviors.schema import HeadPose
    from eepy.behaviors.targets import TrackingTargetProvider

    class Static:
        def get(self, name):
            if name not in ("user", "notes"):
                raise KeyError(name)
            return HeadPose(pitch=-12) if name == "user" else HeadPose(pitch=20)

    pose = pose or type("P", (), {"yaw": 0.0, "pitch": 0.0})()
    return TrackingTargetProvider(Static(), pose=lambda: pose, clock=clock)


def test_look_at_user_follows_the_face_and_falls_back():
    clock = Clock()
    p = provider(clock)
    assert p.get("user").pitch == -12 and not p.tracking     # no face yet: the default
    p.observe({"yaw": 10.0, "pitch": -5.0})
    assert p.tracking and (p.get("user").yaw, p.get("user").pitch) == (10.0, -5.0)
    assert p.get("notes").pitch == 20                        # other targets untouched
    clock.t += 1.5                                           # face gone for longer than max_age
    assert not p.tracking and p.get("user").pitch == -12


def test_look_at_user_is_smoothed_and_speed_capped():
    clock = Clock()
    p = provider(clock)
    p.observe({"yaw": 0.0, "pitch": 0.0})
    clock.t += 0.1
    p.observe({"yaw": 40.0, "pitch": 0.0})                   # a sudden 40-degree jump...
    assert 0 < p.get("user").yaw <= 9.0 + 1e-6               # ...moves at most 90 deg/s x 0.1 s
    for _ in range(30):
        clock.t += 0.1
        p.observe({"yaw": 40.0, "pitch": 0.0})
    assert p.get("user").yaw == pytest.approx(40, abs=0.5)   # and gets there


def test_look_at_user_adds_the_head_pose_and_stays_within_limits():
    clock = Clock()
    pose = type("P", (), {"yaw": 20.0, "pitch": 5.0})()
    p = provider(clock, pose)
    p.observe({"yaw": 10.0, "pitch": -3.0})
    assert (p.get("user").yaw, p.get("user").pitch) == (30.0, 2.0)       # relative to the head
    clock.t += 2
    p.observe({"yaw": 80.0, "pitch": 60.0})                               # absurd: clamped
    assert (p.get("user").yaw, p.get("user").pitch) == (60.0, 25.0)


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_state_estimator_knows_the_same_zones():
    """eepy/state lists the zones itself (the panel's environment has no vision
    packages); this keeps the two lists equal."""
    from eepy.perception.gaze import ZONES
    from eepy.state.features import ZONES as STATE_ZONES

    assert tuple(STATE_ZONES) == tuple(ZONES)
