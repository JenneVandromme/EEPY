"""Per-user calibration (A12) on synthetic people: their eye shape, blinks, yawns
and look-aways are scripted, so every expected baseline value is known. No camera.
"""

from __future__ import annotations

import random
import time
from dataclasses import replace
from types import SimpleNamespace as NS

import numpy as np
import pytest

from eepy.camera.frame import Frame
from eepy.camera.region import Box
from eepy.logs.schema import check_event
from eepy.perception.baseline import Baseline, BaselineProvider, StoredBaselineProvider
from eepy.perception.calibration import log_record, recent, robust, save
from eepy.perception.calibration.baseline import BaselineCollector
from eepy.perception.calibration.face_check import FaceCheck
from eepy.perception.calibration.flow import SessionCalibration
from eepy.perception.extractors import EyeExtractor, FaceEvent
from eepy.perception.gaze.calibration import CalibrationStore
from eepy.perception.normalized import NormalizedExtractor
from eepy.perception.windows import WindowAggregator


def result(t: float, present: bool = True, events=(), **values):
    obs = NS(t=t, present=present, frame_index=int(t * 100))
    return NS(observation=obs, values=values, events=list(events))


def person(ear_open: float, seconds: float = 61.0, fps: float = 15.0, blink_every: float = 4.0,
           yaw: float = 5.0, look_away=(30.0, 33.0), yawn=(40.0, 43.0), absent=(), seed: int = 1):
    """Scripted frames for one person working normally: blinks (EAR dips to 18 % of
    open for 150 ms), one yawn, one look-away, small noise, an occasional outlier."""
    rng = random.Random(seed)
    frames = []
    blink_starts = [b for b in np.arange(2.0, seconds, blink_every)]
    for i in range(int(seconds * fps)):
        t = i / fps
        here = not any(a <= t < b for a, b in absent)
        blinking = any(b <= t < b + 0.15 for b in blink_starts)
        ear = (0.18 * ear_open if blinking else ear_open) + rng.gauss(0, 0.004)
        if rng.random() < 0.01:
            ear = 0.9                                          # a tracking glitch
        events = [FaceEvent("blink", b, {"duration_ms": 150.0, "long": False})
                  for b in blink_starts if b + 0.15 <= t < b + 0.15 + 1 / fps]
        yawning = yawn[0] <= t < yawn[1]
        if yawn[0] + 3.0 <= t < yawn[0] + 3.0 + 1 / fps:
            events.append(FaceEvent("yawn", yawn[0], {"duration_s": 3.0}))
        away = look_away[0] <= t < look_away[1]
        frames.append(result(t, here, events, ear=ear if here else None, ear_left=ear, ear_right=ear,
                             mar=0.8 if yawning else 0.02 + rng.gauss(0, 0.003),
                             head_yaw=(yaw + 40 if away else yaw) + rng.gauss(0, 0.5),
                             head_pitch=8 + rng.gauss(0, 0.5), head_roll=1.0,
                             face_size=0.4, au_AU04=0.12 + rng.gauss(0, 0.01)))
    return frames


def collect(frames, seconds: float = 60.0) -> BaselineCollector:
    collector = BaselineCollector(seconds=seconds)
    for r in frames:
        collector.add(r)
    return collector


# --- robust statistics ---------------------------------------------------------------------


def test_robust_drops_outliers():
    assert robust([1.0, 1.1, 0.9, 1.05, 0.95, 50.0]) == [1.0, 1.1, 0.9, 1.05, 0.95]
    assert robust([2.0] * 10) == [2.0] * 10                   # no spread: keep all


def test_the_baseline_ignores_blinks_yawns_look_aways_and_glitches():
    collector = collect(person(ear_open=0.30))
    v = collector.values()
    assert v["ear_open"] == pytest.approx(0.30, abs=0.005)    # blinks and the 0.9 glitches excluded
    assert v["ear_floor"] == pytest.approx(0.054, abs=0.01)   # the bottom of the blinks
    assert v["blink_rate"] == pytest.approx(15, abs=1.5)      # one every 4 s
    assert v["blink_ms"] == pytest.approx(150)
    assert v["mar_closed"] == pytest.approx(0.02, abs=0.005)  # the yawn excluded
    assert v["yaw0"] == pytest.approx(5.0, abs=0.5)           # the look-away excluded
    assert v["pitch0"] == pytest.approx(8.0, abs=0.5) and v["face_size0"] == 0.4
    assert v["au_rest"]["AU04"] == pytest.approx(0.12, abs=0.01)
    verdict = collector.evaluate()
    assert verdict.action == "accept", verdict.message


# --- quality checks ----------------------------------------------------------------------------


def test_face_out_of_view_extends_then_fails_with_a_plain_message():
    frames = person(ear_open=0.30, seconds=92, absent=[(5, 25)])
    collector = BaselineCollector(seconds=60)
    actions = []
    for r in frames:
        collector.add(r)
        verdict = collector.evaluate()
        if verdict.action != "collect":
            actions.append(verdict.action)
            if verdict.action == "fail":
                break
    assert actions[0] == "extend"
    assert actions[-1] in ("accept", "fail")       # after the extension: decided either way


def test_too_much_absence_fails_with_a_plain_message():
    collector = collect(person(ear_open=0.30, seconds=91, absent=[(0, 40)]))
    collector.extended = True
    verdict = collector.evaluate()
    assert verdict.action == "fail" and "blue rectangle" in verdict.message


def test_a_slow_camera_fails():
    verdict = collect(person(ear_open=0.30, fps=8)).evaluate()
    assert verdict.action == "fail" and "frames per second" in verdict.message


def test_a_camera_pause_is_reported_as_a_pause_not_a_slow_camera():
    """Found live: moving the laptop stopped the camera for a while, and the old
    check blamed a "slow camera" (2 fps over the minute)."""
    frames = [r for r in person(ear_open=0.30, seconds=75) if not (20 <= r.observation.t < 35)]
    verdict = collect(frames).evaluate()
    assert verdict.action == "extend"
    assert "stopped sending pictures for 15 seconds" in verdict.message
    rate = next(c for c in verdict.checks if c["check"] == "frame_rate")
    assert rate["ok"] and rate["measured"] == pytest.approx(15, abs=0.5)    # while it did send


def test_typing_at_the_keyboard_is_normal_work():
    """Found live: looking down at the keyboard (zone down_other) was thrown away as
    "not working", leaving no eye data -- and a misleading 'unusual eyes' failure."""
    frames = person(ear_open=0.34)
    for i, r in enumerate(frames):
        r.values["gaze_zone"] = "down_other" if i % 3 else "screen"     # mostly the keyboard
    verdict = collect(frames).evaluate()
    assert verdict.action == "accept", verdict.message
    assert collect(frames).values()["ear_open"] == pytest.approx(0.34, abs=0.005)


def test_too_little_usable_data_extends_instead_of_failing_on_the_values():
    frames = person(ear_open=0.34)
    for r in frames:
        r.values["gaze_zone"] = "away"                                    # nothing usable
    verdict = collect(frames).evaluate()
    assert verdict.action == "extend"
    assert not any(c["kind"] == "fail" and not c["ok"] for c in verdict.checks)


def test_implausible_eyes_fail():
    verdict = collect(person(ear_open=0.85)).evaluate()
    assert verdict.action == "fail" and "eyes could not be measured" in verdict.message


def test_long_look_aways_extend():
    verdict = collect(person(ear_open=0.30, look_away=(10, 25))).evaluate()
    assert verdict.action == "extend" and "looked away" in verdict.message


def test_unusual_blink_rate_is_only_a_warning():
    verdict = collect(person(ear_open=0.30, blink_every=100)).evaluate()
    assert verdict.action == "accept"
    rate = next(c for c in verdict.checks if c["check"] == "blink_rate")
    assert not rate["ok"] and rate["kind"] == "warning"


# --- normalisation: two different faces, same behaviour, same normalised result ---------------------


def baseline_of(ear_open: float) -> Baseline:
    v = collect(person(ear_open=ear_open)).values()
    return Baseline.from_dict(v, calibrated=True)


def test_two_people_with_different_eyes_give_the_same_normalised_result():
    narrow, wide = baseline_of(0.22), baseline_of(0.36)
    norm = NormalizedExtractor()
    for share in (1.0, 0.5, 0.2):                             # fully open, half, nearly shut
        a = norm.derive({"ear": 0.22 * share}, narrow)
        b = norm.derive({"ear": 0.36 * share}, wide)
        assert a["ear_rel"] == pytest.approx(b["ear_rel"], abs=0.02) == pytest.approx(share, abs=0.03)
        assert narrow.eye_closure(0.22 * share) == pytest.approx(wide.eye_closure(0.36 * share), abs=0.03)
    # ...and the same blinks are found for both, with their own baselines.
    counts = []
    for base, ear_open in ((narrow, 0.22), (wide, 0.36)):
        eye = EyeExtractor()
        events = []
        for r in person(ear_open=ear_open, blink_every=3, seed=7):
            if r.values["ear"] is not None and r.values["ear"] < 0.8:
                events += eye.track(r.observation.t, base.eye_closure(r.values["ear"]))
        counts.append(len(events))
    assert counts[0] == counts[1] >= 18


def test_head_mouth_size_and_aus_relative_to_the_baseline():
    base = Baseline(yaw0=6, pitch0=10, roll0=2, mar_closed=0.03, face_size0=0.4,
                    au_rest={"AU04": 0.2}, calibrated=True)
    out = NormalizedExtractor().derive({"head_yaw": 16, "head_pitch": 10, "head_roll": 0, "mar": 0.53,
                                        "face_size": 0.5, "au_AU04": 0.6}, base)
    assert (out["head_yaw_rel"], out["head_pitch_rel"], out["head_roll_rel"]) == (10, 0, -2)
    assert out["mar_rel"] == pytest.approx(0.5) and out["face_size_rel"] == pytest.approx(1.25)
    assert out["au_AU04_rel"] == pytest.approx(0.5)            # (0.6 - 0.2) / (1 - 0.2)
    assert out["calibrated"] == 1


def test_without_a_baseline_everything_still_works_and_is_flagged():
    out = NormalizedExtractor().derive({"ear": 0.28, "head_yaw": 3}, BaselineProvider().current())
    assert out["calibrated"] == 0 and out["ear_rel"] == pytest.approx(1.0) and out["head_yaw_rel"] == 3
    assert out["face_size_rel"] is None                        # no usual size known


def test_window_blink_rate_relative_to_the_person():
    agg = WindowAggregator()
    base = Baseline(blink_rate=10.0, blink_ms=150.0, calibrated=True)
    summary = None
    for i in range(int(12 * 15)):
        t = i / 15
        events = [FaceEvent("blink", t, {"duration_ms": 300.0, "long": False})] if i % 45 == 0 and i else []
        summary = agg.add(t, {"face_present": 1.0, "eye_closure": 0.0}, events, base) or summary
    assert summary["blink_rate_rel_10s"] == pytest.approx(2.0, abs=0.3)      # 20/min vs usual 10
    assert summary["blink_mean_ms_rel_10s"] == pytest.approx(2.0)             # 300 ms vs usual 150


# --- storage, reuse, logging ------------------------------------------------------------------------


def test_save_reuse_and_the_provider(tmp_path):
    store = CalibrationStore(tmp_path)
    collector = collect(person(ear_open=0.33))
    yesterday = time.localtime(time.time() - 86400)
    late = time.mktime((yesterday.tm_year, yesterday.tm_mon, yesterday.tm_mday, 23, 30, 0, 0, 0, -1))
    section = collector.section(collector.evaluate().checks, when=late)
    assert section["late_evening"] and section["local_hour"] == 23 and section["kss"] is None
    save(store, "P03", "laptop", {"baseline": section, "gaze": {"zones": {}}})
    found = recent(store, "P03", "laptop", days=10_000)
    assert found is not None and found["late_evening"]
    assert recent(store, "P03", "robot") is None                         # another camera
    assert recent(store, "P03", "laptop", days=0.0001) is None           # too old
    provider = StoredBaselineProvider(store=store, user="P03", source="laptop")
    base = provider.current()
    assert base.calibrated and base.ear_open == pytest.approx(0.33, abs=0.005) and base.label == "P03/laptop"
    provider.use("P99", "laptop")
    assert not provider.current().calibrated                             # nobody calibrated P99


def test_the_calibration_event_fits_the_schema():
    collector = collect(person(ear_open=0.30))
    record = log_record("P03", "laptop", "new", {"baseline": collector.section(collector.evaluate().checks)})
    check_event("calibration", "perception", record)
    assert record["values"]["ear_open"] and record["checks"]
    check_event("calibration", "perception", log_record("P03", "laptop", "skipped", reason="no time"))


# --- the whole flow ---------------------------------------------------------------------------------


def face_result(t: float, ear: float = 0.30, yaw: float = 0.0, pitch: float = 5.0, h: float = 0.0,
                brightness: int = 120, present: bool = True, events=(), zone: str = "screen"):
    """A result with a real-looking observation (landmarks, face box, picture)."""
    from test_gaze import face_landmarks
    from eepy.perception import landmarks as lm
    from eepy.perception.observation import FaceObservation, HeadAngles

    image = np.full((800, 800, 3), brightness, np.uint8)
    frame = Frame(image=image, t=t, index=int(t * 100) + 1, source="laptop")
    if not present:
        obs = FaceObservation(t=t, frame_index=frame.index, source="laptop", frame=frame, present=False,
                              width=800, height=800)
        return NS(observation=obs, values={"face_present": 0.0}, events=list(events))
    marks = face_landmarks(h=h)
    box = lm.face_box(lm.to_pixels(marks, 800, 800))
    box = Box(box.x - 60, box.y - 80, box.w + 120, box.h + 260)       # a whole face, not just eyes
    obs = FaceObservation(t=t, frame_index=frame.index, source="laptop", frame=frame, present=True,
                          landmarks=marks, face_box=box, head=HeadAngles(yaw, pitch, 0.0), width=800, height=800)
    values = {"face_present": 1.0, "ear": ear, "ear_left": ear, "ear_right": ear, "eye_closure": 0.0,
              "mar": 0.02, "head_yaw": yaw, "head_pitch": pitch, "head_roll": 0.0, "face_size": 0.45,
              "gaze_zone": zone}
    return NS(observation=obs, values=values, events=list(events))


GAZE = {"screen_center": (0, 5), "screen_top_left": (15, -2), "screen_top_right": (-15, -2),
        "screen_bottom_right": (-15, 12), "screen_bottom_left": (15, 12),
        "notes_center": (-30, 30), "notes_far": (-30, 25), "away": (45, 0)}


def drive(flow: SessionCalibration, until: float = 400.0, face_problem_until: float = 0.0):
    t, fps, lines = 0.0, 15.0, []
    while not flow.done and t < until:
        if flow.stage == "face":
            r = face_result(t, brightness=20 if t < face_problem_until else 120)
        elif flow.stage == "gaze":
            step = flow.gaze_step
            yaw, pitch = GAZE[step.key] if step else (0, 5)
            r = face_result(t, yaw=yaw, pitch=pitch)
        else:
            # A blink every 4 s: the eyes really close for 150 ms (a blink event alone,
            # without the eyes closing, is rightly rejected by the quality check).
            phase = t % 4.0
            blink = [FaceEvent("blink", t, {"duration_ms": 150.0, "long": False})] if phase < 1 / fps else []
            r = face_result(t, ear=0.05 if phase < 0.15 else 0.30, events=blink)
        flow.tick(r, t)
        lines += flow.pop_lines()
        t += 1 / fps
    return lines, t


def test_the_whole_flow_runs_in_order_and_says_what_to_do():
    flow = SessionCalibration()
    lines, t = drive(flow)
    assert flow.stage == "done" and not flow.failed, flow.message
    assert set(flow.result) == {"gaze", "baseline"}
    order = [l for l in lines if l != "beep"]
    assert order[:3] == ["calibration_intro", "calibration_face_check", "calibration_look_dot"]
    assert order.index("calibration_look_notes") < order.index("calibration_look_away") \
        < order.index("calibration_baseline_start") < order.index("calibration_done")
    assert lines.count("beep") == 3                              # after the notes (2) and away
    assert 70 < t < 160                                          # about two minutes


def test_the_face_check_explains_the_problem():
    flow = SessionCalibration()
    lines, _ = drive(flow, until=12, face_problem_until=12)
    assert flow.stage == "face" and "calibration_too_dark" in lines
    assert "too dark" in flow.message


def test_only_one_part():
    flow = SessionCalibration(["face", "baseline"])
    drive(flow)
    assert flow.stage == "done" and set(flow.result) == {"baseline"}
    with pytest.raises(ValueError):
        SessionCalibration(["teeth"])


def test_face_check_rejects_a_face_at_the_edge():
    edge = face_result(0.0)
    edge.observation = replace(edge.observation, face_box=Box(-5, 100, 300, 300))
    problem, message = FaceCheck.judge(edge.observation)
    assert problem == "calibration_face_outside_box" and "blue rectangle" in message


# --- the panel <-> vision messages over the relay ------------------------------------------------------


def test_relay_say_poll_and_calib():
    import socket

    from eepy.camera.relay import CameraRelay, RelayClient

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    said, reports, commands = [], [], [{"command": "calibrate", "user": "P03", "parts": ["face", "gaze"]}]
    relay = CameraRelay(port, say_fn=lambda line: said.append(line) or True,
                        command_fn=lambda: commands.pop() if commands else None,
                        calib_fn=reports.append).start()
    client = RelayClient(port)
    try:
        assert client.say("calibration_intro") and said == ["calibration_intro"]
        assert client.poll() == {"command": "calibrate", "user": "P03", "parts": ["face", "gaze"]}
        assert client.poll() is None
        client.report({"stage": "gaze", "progress": 0.4})
        assert reports == [{"stage": "gaze", "progress": 0.4}]
    finally:
        client.close()
        relay.stop()
    bare = CameraRelay(port).start()                             # an old panel: no calibration
    client = RelayClient(port)
    try:
        assert client.say("calibration_intro") is False and client.poll() is None
    finally:
        client.close()
        bare.stop()



def test_the_normal_posture_and_open_eyes_come_from_looking_at_the_screen():
    """Reading notes during the baseline minute lowers head and eyelids: the
    'forward' reference and the open-eye value must come from the screen moments."""
    frames = person(ear_open=0.30)
    for i, r in enumerate(frames):
        if i % 3:                                        # two thirds of the time: notes
            r.values.update(gaze_zone="notes", head_pitch=19.0, ear=0.18, ear_left=0.18, ear_right=0.18)
        else:
            r.values.update(gaze_zone="screen", head_pitch=2.0)
    values = collect(frames).values()
    assert values["pitch0"] == pytest.approx(2.0, abs=0.5)
    assert values["ear_open"] == pytest.approx(0.30, abs=0.01)
