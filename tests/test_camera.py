"""The camera stream (eepy/camera/), tested with FakeCamera: no webcam, no robot.

Runs in both virtualenvs and on every OS in CI (.github/workflows/camera.yml):
only numpy is required; the few OpenCV-dependent tests skip without it.
"""

from __future__ import annotations

import builtins
import io
import math
import socket
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from eepy.camera import CameraConfig, HeadPose, choose_source, open_camera
from eepy.camera.config import CameraConfigError, load, parse
from eepy.camera.errors import CameraError
from eepy.camera.fake import FakeCamera
from eepy.camera.laptop import LaptopCamera
from eepy.camera.region import Box, box_size, crop, focal_px, gaze_point, robot_box
from eepy.camera.relay import CameraRelay, RelayClient, RelayPortBusy, RelayUnavailable
from eepy.logs.schema import check_event

REPO = Path(__file__).resolve().parents[1]

try:
    import cv2
except ImportError:  # the robot virtualenv has no OpenCV
    cv2 = None
needs_cv2 = pytest.mark.skipif(cv2 is None, reason="needs OpenCV (the vision virtualenv)")


def fast(**changes) -> CameraConfig:
    """A config for tests: quick retries, frequent statistics."""
    base = replace(CameraConfig(), reconnect_seconds=0.1, stats_seconds=0.5)
    return replace(base, **changes)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Events:
    """Collects camera events, checked against the real logging schema."""

    def __init__(self) -> None:
        self.seen: list = []

    def event(self, event_type: str, source: str = "perception", **data) -> bool:
        check_event(event_type, source, data)
        for value in data.values():   # statistics only, never pixels
            assert isinstance(value, (int, float, str, type(None))), (event_type, data)
        self.seen.append((event_type, data))
        return True

    def types(self) -> list:
        return [t for t, _ in self.seen]


def wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


# --- the blue box: same maths as BURTRAM's eye_gaze_follow.py --sim ------------------------


def test_box_size_matches_burtram_and_has_the_robot_aspect():
    # BURTRAM: rect_w = frame_w * reachy_fov / wide_fov, with 50 and 78 by default.
    w, h = box_size(1280, 78, 50, 1280 / 720)
    assert abs(w - 1280 * 50 / 78) <= 0.5
    assert abs(w / h - 16 / 9) < 0.01          # the robot camera's shape, not the webcam's
    w4, h4 = box_size(640, 78, 50, 16 / 9)      # a 4:3 webcam still gets a 16:9 box
    assert abs(w4 - 640 * 50 / 78) <= 0.5 and abs(w4 / h4 - 16 / 9) < 0.02


def test_box_is_centred_without_a_head_pose_and_for_a_straight_head():
    for pose in (None, HeadPose()):
        box = robot_box(1280, 720, 78, 50, 16 / 9, pose)
        assert box.center == pytest.approx((640, 360), abs=1)
        assert box.inside(1280, 720)


def test_box_follows_the_head():
    left = robot_box(1280, 720, 78, 50, 16 / 9, HeadPose(yaw=10))
    right = robot_box(1280, 720, 78, 50, 16 / 9, HeadPose(yaw=-10))
    down = robot_box(1280, 720, 78, 50, 16 / 9, HeadPose(pitch=10))
    up = robot_box(1280, 720, 78, 50, 16 / 9, HeadPose(pitch=-10))
    assert left.center[0] < 640 < right.center[0]    # yaw + is LEFT
    assert up.center[1] < 360 < down.center[1]        # pitch + is chin DOWN
    # Pinhole: a 10-degree turn lands f*tan(10) pixels from the centre.
    assert left.center[0] == pytest.approx(640 - focal_px(1280, 78) * math.tan(math.radians(10)), abs=1)
    # Roll spins around the line of sight: the box does not move.
    assert robot_box(1280, 720, 78, 50, 16 / 9, HeadPose(roll=20)).center == pytest.approx((640, 360), abs=1)


def test_looking_far_away_puts_the_box_off_the_picture():
    assert gaze_point(HeadPose(yaw=95), 1280, 720, 78) is None
    box = robot_box(1280, 720, 78, 50, 16 / 9, HeadPose(yaw=95))
    assert box.x + box.w <= 0


def test_crop_is_always_the_box_size_and_black_outside_the_picture():
    image = np.full((720, 1280, 3), 200, dtype=np.uint8)
    inside = crop(image, Box(100, 100, 400, 225))
    assert inside.shape == (225, 400, 3) and inside.min() == 200
    half_out = crop(image, Box(-200, 0, 400, 225))
    assert half_out.shape == (225, 400, 3)
    assert half_out[:, :200].max() == 0 and half_out[:, 200:].min() == 200
    assert crop(image, Box(5000, 0, 400, 225)).max() == 0


# --- configuration -------------------------------------------------------------------------


def test_the_repo_config_loads_and_matches_the_defaults():
    config = load(REPO / "config" / "eepy.yaml") if _has_yaml() else parse(None)
    assert config.robot.resolution == (1280, 720) and config.fps == 15
    assert config.laptop.fov_deg == 78 and config.robot.fov_deg == 50


def test_settings_py_accepts_the_same_camera_section():
    pytest.importorskip("pydantic")
    from eepy.settings import SettingsError, load_settings

    assert load_settings().camera_config == load(REPO / "config" / "eepy.yaml")
    bad = REPO / "config" / "eepy.yaml"
    text = bad.read_text()
    tmp = Path(pytest.importorskip("tempfile").mkdtemp()) / "eepy.yaml"
    tmp.write_text(text.replace("fps: 15", "fps: fast"))
    with pytest.raises(SettingsError, match="fps must be a number"):
        load_settings(tmp)


@pytest.mark.parametrize("raw, message", [
    ({"source": "webcam"}, "is not a camera"),
    ({"fps": 0}, "between"),
    ({"laptop": {"resolution": [1280]}}, r"\[width, height\]"),
    ({"laptop": {"index": "first"}}, "auto"),
    ({"robot": {"fov": 50}}, "not a robot setting"),
])
def test_bad_settings_are_explained(raw, message):
    with pytest.raises(CameraConfigError, match=message):
        parse(raw)


def _has_yaml() -> bool:
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


# --- which camera ----------------------------------------------------------------------------


def test_source_follows_the_backend_unless_overridden():
    auto = CameraConfig()
    assert choose_source(auto, backend="real") == "robot"
    assert choose_source(auto, backend="sim") == "laptop"
    assert choose_source(auto, backend="dry") == "laptop"
    assert choose_source(auto, "laptop", backend="real") == "laptop"          # --camera wins
    assert choose_source(replace(auto, source="fake"), backend="real") == "fake"
    with pytest.raises(CameraConfigError):
        choose_source(auto, "webcam")


def test_open_camera_builds_the_chosen_class():
    assert isinstance(open_camera("fake", config=fast()), FakeCamera)
    assert isinstance(open_camera("laptop", config=fast(), pose_fn=lambda: None), LaptopCamera)
    assert open_camera("robot", config=fast()).name == "robot"


# --- real-time behaviour ---------------------------------------------------------------------


def test_slow_consumer_gets_the_newest_frame_not_a_backlog():
    camera = FakeCamera(fast(fps=30), native_fps=120).start()
    try:
        first = camera.wait_for_frame(timeout=3)
        assert first is not None
        seen = []
        for _ in range(5):
            time.sleep(0.25)                     # a consumer far slower than 30 fps
            frame = camera.get_latest()
            seen.append(frame.index)
            assert time.monotonic() - frame.t < 0.15   # fresh, not queued up
        gaps = [b - a for a, b in zip(seen, seen[1:])]
        assert min(gaps) >= 3                    # skipped ahead every time
        assert wait_until(lambda: camera.stats().get("dropped_total", 0) > 0)
    finally:
        camera.stop()


def test_frames_are_delivered_at_the_configured_rate():
    camera = FakeCamera(fast(fps=10), native_fps=60).start()
    try:
        first = camera.wait_for_frame(timeout=3)
        time.sleep(1.5)
        last = camera.get_latest()
        rate = (last.index - first.index) / (last.t - first.t)
        assert 7 <= rate <= 11
    finally:
        camera.stop()


def test_frames_carry_timestamp_counter_source_and_size():
    camera = FakeCamera(fast(), resolution=(320, 180)).start()
    try:
        a = camera.wait_for_frame(timeout=3)
        b = camera.wait_for_frame(newer_than=a, timeout=3)
        assert b.index > a.index and b.t > a.t
        assert (a.source, a.resolution) == ("fake", (320, 180))
        assert "320x180" in repr(a) and "array" not in repr(a)   # no pixels in logs/tracebacks
    finally:
        camera.stop()


def test_subscribers_get_frames_and_a_failing_one_does_not_stop_the_camera():
    got = []
    camera = FakeCamera(fast())
    camera.subscribe(lambda f: 1 / 0)
    unsubscribe = camera.subscribe(got.append)
    camera.start()
    try:
        assert wait_until(lambda: len(got) >= 3)
        unsubscribe()
        count = len(got)
        time.sleep(0.3)
        assert len(got) <= count + 1
        assert camera.status == "running"
    finally:
        camera.stop()


def test_reconnects_after_the_camera_drops_out_and_logs_it():
    events = Events()
    camera = FakeCamera(fast(), events=events, fail_opens=1, fail_after=5).start()
    try:
        assert wait_until(lambda: "camera_reconnected" in events.types())
        assert events.types()[:2] == ["camera_error", "camera_started"]   # absent, then found
        assert "camera_error" in events.types()[2:]                       # the drop-out
        assert camera.status == "running" and camera.error is None
        assert wait_until(lambda: "camera_stats" in events.types())
        stats = dict(events.seen)["camera_stats"]
        assert stats["fps"] > 0 and stats["camera"] == "fake"
    finally:
        camera.stop()
    assert camera.status == "stopped"


def test_wait_for_frame_times_out_when_nothing_comes():
    camera = FakeCamera(fast(), fail_opens=1000).start()
    try:
        assert camera.wait_for_frame(timeout=0.2) is None
        assert wait_until(lambda: camera.status == "reconnecting")
        assert "not plugged in" in camera.error
    finally:
        camera.stop()


# --- the laptop camera's crop (no webcam needed) ----------------------------------------------


def test_laptop_frames_hold_only_the_robot_view():
    picture = np.zeros((720, 1280, 3), dtype=np.uint8)
    picture[:, 400:] = 255                       # white from x=400 rightwards
    camera = LaptopCamera(fast(), pose_fn=lambda: HeadPose(yaw=-20))   # head turned right
    frame = camera._make_frame(picture, 1.0, 1)
    assert frame.full is None                    # perception never gets the whole picture
    assert frame.box.x > 400                     # the whole view is in the white part...
    visible = 1280 - frame.box.x
    assert frame.image[:, :visible].min() == 255
    assert frame.image[:, visible:].max() == 0   # ...and past the picture's edge it is black
    assert frame.resolution == (frame.box.w, frame.box.h)
    assert camera.head_known

    preview = LaptopCamera(fast(), keep_full=True, pose_fn=lambda: None)._make_frame(picture, 1.0, 1)
    assert preview.full is picture and preview.box.center == pytest.approx((640, 360), abs=1)


# --- the relay ---------------------------------------------------------------------------------


def test_relay_serves_frames_and_pose_on_localhost_only():
    port = free_port()
    jpeg = b"\xff\xd8not-really-a-jpeg\xff\xd9"
    relay = CameraRelay(port, frame_fn=lambda: jpeg, pose_fn=lambda: {"yaw": 5.0, "pitch": -2.0,
                                                                      "roll": 0.0, "body_yaw": 0.0},
                        fps=30, label="real").start()
    client = RelayClient(port)
    try:
        assert relay._server.server_address[0] == "127.0.0.1"
        assert client.status()["camera"] == "real"
        assert wait_until(lambda: client.frame()[0]["index"] > 0)
        header, payload = client.frame()
        assert payload == jpeg and header["len"] == len(jpeg)
        assert client.pose()["yaw"] == 5.0
        with pytest.raises(RelayPortBusy, match="already in use"):
            CameraRelay(port).start()
    finally:
        client.close()
        relay.stop()
    with pytest.raises(RelayUnavailable, match="python -m eepy.camera.relay"):
        RelayClient(port).status()


def test_relay_without_robot_camera_still_serves_the_pose():
    port = free_port()
    relay = CameraRelay(port, frame_fn=lambda: None, pose_fn=lambda: None, label=lambda: None).start()
    client = RelayClient(port)
    try:
        assert client.status()["camera"] is None
        assert client.pose() is None
        assert client.frame()[0]["index"] == 0
    finally:
        client.close()
        relay.stop()


def test_robot_camera_explains_a_missing_relay():
    camera = open_camera("robot", config=fast(relay_port=free_port()))
    if cv2 is None:
        with pytest.raises(CameraError, match="OpenCV"):
            camera._open()
    else:
        with pytest.raises(CameraError, match="Wizard panel"):
            camera._open()


@needs_cv2
def test_robot_camera_decodes_relay_frames():
    port = free_port()
    picture = np.zeros((72, 128, 3), dtype=np.uint8)
    picture[:, :, 2] = 200
    ok, encoded = cv2.imencode(".jpg", picture)
    relay = CameraRelay(port, frame_fn=lambda: encoded.tobytes(), fps=30, label="real").start()
    camera = open_camera("robot", config=fast(relay_port=port)).start()
    try:
        frame = camera.wait_for_frame(timeout=3)
        assert frame is not None and frame.resolution == (128, 72) and frame.box is None
        assert abs(int(frame.image[..., 2].mean()) - 200) < 10
    finally:
        camera.stop()
        relay.stop()


def test_laptop_box_follows_the_pose_from_the_relay():
    from eepy.camera.laptop import RelayPose

    port = free_port()
    relay = CameraRelay(port, pose_fn=lambda: {"yaw": 12.0, "pitch": 3.0, "roll": 0.0,
                                               "body_yaw": 0.0}).start()
    try:
        pose = RelayPose(port)()
        assert pose == HeadPose(yaw=12.0, pitch=3.0, roll=0.0)
    finally:
        relay.stop()
    gone = RelayPose(port)
    assert gone() is None and not gone.known


# --- privacy ---------------------------------------------------------------------------------


def test_no_frame_is_ever_written_to_disk(tmp_path, monkeypatch):
    """Run the whole path -- fake camera, relay, crop -- with every file write trapped."""
    monkeypatch.chdir(tmp_path)
    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError(f"the camera tried to write {file!r}")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(Path, "write_bytes", lambda *a, **k: pytest.fail("write_bytes"))
    if cv2 is not None:
        monkeypatch.setattr(cv2, "imwrite", lambda *a, **k: pytest.fail("cv2.imwrite"))

    port = free_port()
    events = Events()
    camera = FakeCamera(fast(relay_port=port), events=events, fail_after=3).start()
    relay = CameraRelay(port, frame_fn=lambda: b"\xff\xd8x\xff\xd9", fps=30, label="real").start()
    client = RelayClient(port)
    try:
        assert wait_until(lambda: "camera_reconnected" in events.types())
        assert wait_until(lambda: client.frame()[0]["index"] > 0)
        LaptopCamera(fast(), pose_fn=lambda: HeadPose(yaw=5))._make_frame(
            np.zeros((720, 1280, 3), dtype=np.uint8), 0.0, 1)
    finally:
        client.close()
        relay.stop()
        camera.stop()
    assert list(tmp_path.iterdir()) == []


def test_test_clips_stay_out_of_git():
    import subprocess

    result = subprocess.run(["git", "check-ignore", "-q", ".cache/camera/clip.mp4"], cwd=REPO)
    assert result.returncode == 0
