"""Face features (eepy/perception/) on synthetic signals: no camera, no MediaPipe.

The signals are generated as functions of TIME and then sampled at different
frame rates, which is how frame-rate independence is tested: the same blink
seen at 10 and at 30 fps must give the same answer.
"""

from __future__ import annotations

import math
from typing import Callable, List

import numpy as np
import pytest

from eepy.camera.config import CameraConfigError
from eepy.camera.frame import Frame
from eepy.logs.schema import check_event
from eepy.perception import landmarks as lm
from eepy.perception.baseline import Baseline, BaselineProvider
from eepy.perception.config import PerceptionConfig, parse
from eepy.perception.extractors import (EyeExtractor, HeadExtractor, MouthExtractor,
                                        PresenceExtractor)
from eepy.perception.observation import FaceObservation, HeadAngles, absent
from eepy.perception.pipeline import FacePipeline
from eepy.perception.windows import WindowAggregator

BASE = Baseline()


def sample(fps: float, seconds: float, offset: float = 0.0) -> List[float]:
    """Frame times at `fps`, starting `offset` seconds in (a different phase)."""
    return [offset + i / fps for i in range(int(seconds * fps))]


def trapezoid(t: float, start: float, ramp: float, hold: float, high: float = 1.0) -> float:
    """0 -> `high` over `ramp`, held for `hold`, back to 0 over `ramp`."""
    x = t - start
    if x < 0 or x > 2 * ramp + hold:
        return 0.0
    if x < ramp:
        return high * x / ramp
    if x < ramp + hold:
        return high
    return high * (2 * ramp + hold - x) / ramp


def blinks(fps: float, signal: Callable[[float], float], seconds: float = 3.0, offset: float = 0.0,
           config: PerceptionConfig = None):
    eye = EyeExtractor(config)
    events = []
    for t in sample(fps, seconds, offset):
        events += eye.track(t, signal(t))
    return events


# --- blinks ------------------------------------------------------------------------------------


def test_a_blink_is_detected_with_its_duration():
    # Closure rises over 50 ms, stays shut 150 ms, reopens over 50 ms. At the
    # 0.5 level that is 50/2 + 150 + 50/2 = 200 ms.
    found = blinks(30, lambda t: trapezoid(t, 1.0, 0.05, 0.15))
    assert len(found) == 1 and found[0].type == "blink"
    assert found[0].fields["duration_ms"] == pytest.approx(200, abs=35)
    assert found[0].t == pytest.approx(1.025, abs=0.035) and not found[0].fields["long"]


def test_blink_duration_is_frame_rate_independent():
    signal = lambda t: trapezoid(t, 1.0, 0.08, 0.25)     # 330 ms at the 0.5 level
    for offset in (0.0, 0.013, 0.031):                    # different frame phases
        slow = blinks(10, signal, offset=offset)
        fast = blinks(30, signal, offset=offset)
        assert len(slow) == len(fast) == 1
        assert slow[0].fields["duration_ms"] == pytest.approx(fast[0].fields["duration_ms"], abs=60)
        assert fast[0].fields["duration_ms"] == pytest.approx(330, abs=35)


def test_noise_is_not_a_blink_and_a_long_closure_is_a_long_blink():
    config = PerceptionConfig().replace(blink__min_ms=50)
    glitch = blinks(60, lambda t: trapezoid(t, 1.0, 0.005, 0.01), config=config)   # ~15 ms
    assert glitch == []
    long_one = blinks(30, lambda t: trapezoid(t, 1.0, 0.05, 0.8))
    assert len(long_one) == 1 and long_one[0].fields["long"]


def test_hysteresis_keeps_a_flickering_eye_one_blink():
    # Dips to 0.45 (below close=0.5, above open=0.35) mid-blink: still one blink.
    def flicker(t):
        base = trapezoid(t, 1.0, 0.05, 0.3)
        return 0.45 if 1.15 < t < 1.2 else base
    assert len(blinks(30, flicker)) == 1


def test_eye_closure_is_relative_to_the_baseline():
    assert BASE.eye_closure(0.28) == 0.0 and BASE.eye_closure(0.05) == 1.0
    wide_eyed = Baseline(ear_open=0.36)
    assert wide_eyed.eye_closure(0.28) > 0.2      # half-closed for THIS person
    assert BASE.eye_closure(0.5) == 0.0 and BASE.eye_closure(0.0) == 1.0


# --- PERCLOS and windows ------------------------------------------------------------------------------


def run_windows(fps: float, values_at: Callable[[float], dict], seconds: float,
                config: PerceptionConfig = None, events_at=None) -> dict:
    agg = WindowAggregator(config)
    summary = None
    for t in sample(fps, seconds):
        out = agg.add(t, values_at(t), events_at(t) if events_at else [], BASE)
        summary = out or summary
    return summary


def eyes_closed_30_percent(t: float) -> dict:
    closed = (t % 2.0) < 0.6                    # shut 0.6 s of every 2 s
    return {"face_present": 1.0, "eye_closure": 0.95 if closed else 0.05,
            "head_yaw": 0.0, "head_pitch": 0.0}


def test_perclos_is_time_weighted_and_frame_rate_independent():
    slow = run_windows(10, eyes_closed_30_percent, 20)
    fast = run_windows(30, eyes_closed_30_percent, 20)
    assert slow["perclos_10s"] == pytest.approx(0.30, abs=0.04)
    assert fast["perclos_10s"] == pytest.approx(0.30, abs=0.04)
    assert slow["face_absent_ratio_10s"] == 0.0


def test_perclos_uses_the_p80_threshold():
    nearly_shut = lambda t: {"face_present": 1.0, "eye_closure": 0.75, "head_yaw": 0, "head_pitch": 0}
    assert run_windows(30, nearly_shut, 12)["perclos_10s"] == 0.0      # 75 % shut is not "closed"
    shut = lambda t: {"face_present": 1.0, "eye_closure": 0.85, "head_yaw": 0, "head_pitch": 0}
    assert run_windows(30, shut, 12)["perclos_10s"] == 1.0


def test_low_frame_rate_flags_blink_figures():
    assert run_windows(8, eyes_closed_30_percent, 12)["blink_confidence_10s"] == "low"
    assert run_windows(14.9, eyes_closed_30_percent, 12)["blink_confidence_10s"] == "ok"
    assert run_windows(30, eyes_closed_30_percent, 12)["blink_confidence_10s"] == "ok"


def test_window_counts_rates_and_head_figures():
    from eepy.perception.extractors import FaceEvent

    def values(t):
        away = 10 <= t < 13                                  # 3 s of 10 turned away
        return {"face_present": 1.0, "eye_closure": 0.0, "head_yaw": 45.0 if away else 0.0,
                "head_pitch": 10.0}

    def events(t):
        return [FaceEvent("blink", t, {"duration_ms": 150.0, "long": False})] if abs(t % 2) < 1e-6 else []

    out = run_windows(10, values, 20.05, events_at=events)
    assert out["blink_rate_10s"] == pytest.approx(30, abs=4)      # one every 2 s
    assert out["blink_mean_ms_10s"] == 150.0 and out["long_blinks_10s"] == 0
    assert out["look_away_ratio_10s"] == pytest.approx(0.3, abs=0.03)
    assert out["pitch_mean_10s"] == pytest.approx(10.0) and out["pitch_var_10s"] == pytest.approx(0.0)
    assert set(k.rsplit("_", 1)[1] for k in out) == {"10s", "60s"}


def test_windows_are_emitted_about_once_a_second():
    agg = WindowAggregator()
    emitted = [t for t in sample(30, 10)
               if agg.add(t, {"face_present": 1.0}, [], BASE) is not None]
    assert 8 <= len(emitted) <= 10
    assert all(0.9 < b - a < 1.1 for a, b in zip(emitted, emitted[1:]))


# --- yawns ----------------------------------------------------------------------------------------


def yawns(fps: float, mar_at: Callable[[float], float], jaw_at: Callable[[float], float],
          seconds: float = 8.0):
    mouth = MouthExtractor()
    out = []
    for t in sample(fps, seconds):
        out += mouth.track(t, mar_at(t), jaw_at(t), BASE)
    return out + mouth._finish()


def test_a_yawn_is_detected():
    wide = lambda t: 0.8 if 2.0 <= t < 5.0 else 0.05
    jaw = lambda t: 0.7 if 2.0 <= t < 5.0 else 0.05
    found = yawns(15, wide, jaw)
    assert len(found) == 1 and found[0].fields["duration_s"] == pytest.approx(3.0, abs=0.15)


def test_talking_is_not_a_yawn():
    # Normal speech: the mouth opens to MAR 0.2-0.35 several times a second.
    speech_mar = lambda t: 0.2 + 0.15 * abs(math.sin(2 * math.pi * 3 * t))
    speech_jaw = lambda t: 0.25 + 0.2 * abs(math.sin(2 * math.pi * 3 * t))
    assert yawns(30, speech_mar, speech_jaw) == []
    # Even wide syllables, open 0.25 s with 0.4 s closed between them.
    syllables = lambda t: 0.7 if (t % 0.65) < 0.25 else 0.05
    assert yawns(30, syllables, syllables) == []


def test_yawn_is_frame_rate_independent():
    wide = lambda t: 0.8 if 2.0 <= t < 4.0 else 0.05
    slow, fast = yawns(8, wide, wide), yawns(30, wide, wide)
    assert len(slow) == len(fast) == 1
    assert slow[0].fields["duration_s"] == pytest.approx(fast[0].fields["duration_s"], abs=0.15)


# --- nods ---------------------------------------------------------------------------------------------


def nods(fps: float, pitch_at: Callable[[float], float], seconds: float = 12.0):
    head = HeadExtractor()
    out = []
    for t in sample(fps, seconds):
        out += head.track(t, pitch_at(t))
    return out


def nodding_off(t: float) -> float:
    # Steady, then the chin drops 20 deg in 0.8 s, hangs 0.3 s, jerks back up in 0.4 s.
    if t < 6.0:
        return 0.0
    if t < 6.8:
        return 20 * (t - 6.0) / 0.8
    if t < 7.1:
        return 20.0
    if t < 7.5:
        return 20 * (1 - (t - 7.1) / 0.4)
    return 0.0


def test_nodding_off_is_detected_at_any_frame_rate():
    for fps in (8, 15, 30):
        found = nods(fps, nodding_off)
        assert len(found) == 1, fps
        assert found[0].fields["depth_deg"] == pytest.approx(20, abs=3)
        assert found[0].t == pytest.approx(6.0, abs=0.2)


def test_slowly_lowering_the_head_or_keeping_it_down_is_not_a_nod():
    assert nods(15, lambda t: min(25.0, 2.0 * t)) == []                     # 2 deg/s: reading, not nodding
    assert nods(15, lambda t: 0.0 if t < 6 else min(20.0, 30 * (t - 6))) == []   # drops, stays down


def test_a_small_nod_of_agreement_is_below_the_threshold():
    assert nods(30, lambda t: 6 * math.sin(math.pi * (t - 6) / 0.4) if 6 <= t < 6.4 else 0.0) == []


# --- presence ------------------------------------------------------------------------------------------


def obs(t: float, present: bool = True, box_h: int = 200, height: int = 462) -> FaceObservation:
    from eepy.camera.region import Box

    return FaceObservation(t=t, frame_index=int(t * 100), source="fake", frame=None, present=present,
                           face_box=Box(300, 100, 180, box_h) if present else None,
                           width=821, height=height)


def test_face_lost_and_back_with_timing():
    presence = PresenceExtractor()
    events = []
    for t in sample(15, 10):
        here = not (3.0 <= t < 6.0)
        events += presence.update(obs(t, here), BASE).events
    kinds = [(e.type, round(e.t, 1)) for e in events]
    assert kinds == [("face_lost", 2.9), ("face_back", 6.0)]
    assert events[1].fields["away_seconds"] == pytest.approx(3.1, abs=0.1)


def test_a_short_glance_away_is_not_face_lost():
    presence = PresenceExtractor()
    events = []
    for t in sample(15, 6):
        events += presence.update(obs(t, not (2.0 <= t < 2.6)), BASE).events
    assert events == []


def test_face_size_is_relative_to_the_picture():
    out = PresenceExtractor().update(obs(0.0, box_h=231, height=462), BASE)
    assert out.values == {"face_present": 1.0, "face_size": 0.5}


# --- geometry ----------------------------------------------------------------------------------------


def synthetic_landmarks(ear: float = 0.28, mar: float = 0.05, size: int = 100) -> np.ndarray:
    """478 normalised landmarks with exactly this EAR and MAR (in a square picture)."""
    pts = np.full((478, 3), 0.5, dtype=np.float32)
    s = size / 1000.0

    def eye(idx, cx):
        p1, p2, p3, p4, p5, p6 = idx
        h = ear * 2 * s / 2
        pts[p1, :2] = (cx - s, 0.4)
        pts[p4, :2] = (cx + s, 0.4)
        pts[p2, :2] = pts[p3, :2] = (cx, 0.4 - h)
        pts[p6, :2] = pts[p5, :2] = (cx, 0.4 + h)
        pts[p2, 0] -= s / 3
        pts[p6, 0] -= s / 3
        pts[p3, 0] += s / 3
        pts[p5, 0] += s / 3

    eye(lm.RIGHT_EYE, 0.35)
    eye(lm.LEFT_EYE, 0.65)
    a, b = lm.MOUTH_CORNERS
    pts[a, :2], pts[b, :2] = (0.4, 0.7), (0.6, 0.7)
    for top, bottom in lm.MOUTH_VERTICAL:
        pts[top, :2] = (0.5, 0.7 - mar * 0.2 / 2)
        pts[bottom, :2] = (0.5, 0.7 + mar * 0.2 / 2)
    return pts


def test_ear_and_mar_geometry():
    points = lm.to_pixels(synthetic_landmarks(ear=0.3, mar=0.6), 1000, 1000)
    assert lm.eye_aspect_ratio(points, lm.RIGHT_EYE) == pytest.approx(0.3, abs=0.01)
    assert lm.eye_aspect_ratio(points, lm.LEFT_EYE) == pytest.approx(0.3, abs=0.01)
    assert lm.mouth_aspect_ratio(points) == pytest.approx(0.6, abs=0.01)


def test_distances_are_measured_in_pixels_not_normalised_units():
    marks = synthetic_landmarks(ear=0.3)
    square = lm.eye_aspect_ratio(lm.to_pixels(marks, 1000, 1000), lm.RIGHT_EYE)
    wide = lm.eye_aspect_ratio(lm.to_pixels(marks, 1600, 900), lm.RIGHT_EYE)
    assert square != pytest.approx(wide, abs=0.01)   # the picture's shape matters...
    assert wide == pytest.approx(0.3 * 900 / 1600, abs=0.01)   # ...exactly as geometry says


@pytest.mark.parametrize("yaw, pitch, roll", [(0, 0, 0), (25, 0, 0), (0, -15, 0), (0, 0, 12), (-30, 20, -8)])
def test_head_angles_round_trip(yaw, pitch, roll):
    assert lm.head_angles(lm.matrix_from_angles(yaw, pitch, roll)) == pytest.approx((yaw, pitch, roll), abs=1e-6)


def test_head_angle_signs_follow_the_robot_convention():
    # Turned to the person's left: their face points to the camera's right (+x).
    assert lm.matrix_from_angles(20, 0, 0)[0, 2] > 0
    # Chin down: the face points below the camera (-y).
    assert lm.matrix_from_angles(0, 15, 0)[1, 2] < 0
    # Tipped toward the left ear: the top of the head moves to the camera's right (+x).
    assert lm.matrix_from_angles(0, 0, 10)[0, 1] > 0


def test_square_box_for_face_models():
    from eepy.camera.region import Box

    square = lm.square_box(Box(100, 50, 80, 100), expand=1.2)
    assert square.w == square.h == 120 and square.center == pytest.approx((140, 100), abs=1)


# --- the pipeline, without MediaPipe -----------------------------------------------------------------


class Recorder:
    def __init__(self):
        self.rows, self.events = [], []

    def row(self, values, t_mono=None, frame=None):
        assert all(v is None or isinstance(v, (int, float)) for v in values.values())
        self.rows.append(values)

    def event(self, event_type, source="perception", t_mono=None, **data):
        check_event(event_type, source, data)          # the real schema
        self.events.append((event_type, data))


def face_obs(t: float, ear: float, index: int) -> FaceObservation:
    marks = synthetic_landmarks(ear=ear)
    frame = Frame(image=np.zeros((10, 10, 3), np.uint8), t=t, index=index, source="fake")
    return FaceObservation(t=t, frame_index=index, source="fake", frame=frame, present=True,
                           landmarks=marks, face_box=lm.face_box(lm.to_pixels(marks, 1000, 1000)),
                           head=HeadAngles(0, 0, 0), blendshapes={"eyeBlinkLeft": 0.1, "jawOpen": 0.0},
                           process_ms=4.0, width=1000, height=1000)


def test_pipeline_records_features_events_and_windows_in_the_schema():
    features, events = Recorder(), Recorder()
    pipeline = FacePipeline(camera=None, features=features, events=events,
                            landmarker_factory=lambda: None)
    for i, t in enumerate(sample(30, 4), start=1):
        ear = 0.04 if 2.0 <= t < 2.2 else 0.28                  # one 200 ms blink
        pipeline._deliver(pipeline.process(face_obs(t, ear, i)))
    blank = Frame(image=np.zeros((10, 10, 3), np.uint8), t=4.1, index=999, source="fake")
    pipeline._deliver(pipeline.process(absent(blank)))
    assert len(features.rows) == 121 and set(features.rows[0]) == set(pipeline.columns)
    assert features.rows[-1]["face_present"] == 0.0 and features.rows[-1]["ear"] is None
    kinds = [kind for kind, _ in events.events]
    assert kinds.count("blink") == 1 and "face_window" in kinds
    assert all(r["process_ms"] >= 4.0 for r in features.rows[:-1])


def test_the_baseline_provider_is_asked_every_frame():
    class Calibrated(BaselineProvider):
        calls = 0

        def current(self):
            Calibrated.calls += 1
            return Baseline(ear_open=0.36, calibrated=True)

    pipeline = FacePipeline(camera=None, baseline=Calibrated(), landmarker_factory=lambda: None)
    result = pipeline.process(face_obs(0.0, 0.28, 1))
    assert Calibrated.calls == 1
    assert result.values["eye_closure"] > 0.2     # 0.28 is a half-shut eye for this person


# --- configuration --------------------------------------------------------------------------------


def test_config_defaults_and_errors():
    config = parse(None)
    assert config.get("blink", "close") == 0.5 and config.windows == (10, 60)
    with pytest.raises(CameraConfigError, match="not a perception.blink setting"):
        parse({"blink": {"closed": 0.5}})
    with pytest.raises(CameraConfigError, match="must be lower"):
        parse({"blink": {"close": 0.3, "open": 0.4}})
    with pytest.raises(CameraConfigError, match="between"):
        parse({"yawn": {"min_seconds": 100}})


def test_settings_py_and_the_vision_reader_agree():
    pytest.importorskip("pydantic")
    from eepy.perception.config import load
    from eepy.settings import load_settings

    assert load_settings().perception_config == load()


# --- the real model (only where MediaPipe is installed) ---------------------------------------------


def test_mediapipe_runs_and_finds_no_face_in_a_blank_picture():
    pytest.importorskip("mediapipe")
    from eepy.perception.model import ModelError
    from eepy.perception.pipeline import Landmarker

    try:
        landmarker = Landmarker()
    except ModelError as exc:
        pytest.skip(f"model not available offline: {exc}")
    try:
        result = landmarker.detect(np.full((360, 640, 3), 128, np.uint8), 1.0)
        assert result.face_landmarks == []
    finally:
        landmarker.close()
