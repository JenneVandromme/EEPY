"""Aiming the robot's resting direction at the participant's face.

The loader's `base` (absolute angles become offsets from it), the player's set_base /
check_base, and the panel's aim flow: centred within a tolerance, never further than
every behaviour can still play safely from, saved with the calibration and restored by
Reuse. Without a base, everything is exactly as before.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from eepy.behaviors import reach
from eepy.behaviors.backends import DryRunBackend
from eepy.behaviors.loader import load_library
from eepy.behaviors.player import BehaviorPlayer
from eepy.behaviors.pose import NEUTRAL
from eepy.behaviors.schema import HeadPose

REPO = Path(__file__).resolve().parents[1]
LIBRARY = load_library()
needs_ik = pytest.mark.skipif(not reach.available(), reason="the safety checks use the IK solver")


def _poses(steps):
    return [(s.head_yaw, s.head_pitch, s.head_roll, s.head_z, s.antenna_right, s.antenna_left, s.body_yaw)
            for s in steps]


# --- the loader ------------------------------------------------------------------------------------


@pytest.mark.parametrize("intensity", [0.3, 0.7, 1.0])
def test_no_base_or_a_zero_base_changes_nothing(intensity):
    for behavior in LIBRARY.behaviors.values():
        plain = LIBRARY.resolve(behavior, intensity_scale=intensity)
        assert _poses(LIBRARY.resolve(behavior, intensity_scale=intensity, base=None)) == _poses(plain)
        assert _poses(LIBRARY.resolve(behavior, intensity_scale=intensity,
                                      base=HeadPose(yaw=0, pitch=0))) == _poses(plain)


def test_written_angles_are_offsets_from_the_base_and_targets_stay_put():
    base = HeadPose(yaw=10, pitch=-10)
    sag = LIBRARY.behaviors["bedtime_subtle"]
    from eepy.behaviors.pose import Pose
    rest = Pose(yaw=10, pitch=-10)                    # the player plans from where the robot rests
    plain, based = LIBRARY.resolve(sag), LIBRARY.resolve(sag, start=rest, base=base)
    for a, b in zip(plain, based):
        assert b.head_pitch == pytest.approx(a.head_pitch - 10) and b.head_yaw == pytest.approx(a.head_yaw + 10)
        assert (b.head_roll, b.antenna_right, b.antenna_left) == (a.head_roll, a.antenna_right, a.antenna_left)
    glance = LIBRARY.behaviors["refocus_joint_attention"]           # look_at user, then notes
    notes = LIBRARY.gaze_targets["notes"].pitch
    steps = LIBRARY.resolve(glance, start=rest, base=base, intensity_scale=1.0)
    assert any(s.head_pitch == pytest.approx(notes) for s in steps)  # a place in the room: not shifted
    half = LIBRARY.resolve(glance, start=rest, base=base, intensity_scale=0.5)
    assert any(s.head_pitch == pytest.approx(-10 + (notes + 10) * 0.5) for s in half)   # scaled toward the base


# --- the player --------------------------------------------------------------------------------------


@pytest.fixture
def player():
    p = BehaviorPlayer(DryRunBackend()).start()
    yield p
    p.close()


def _wait(check, seconds=4.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


@needs_ik
def test_check_base_knows_the_safe_range(player):
    assert player.check_base(HeadPose(yaw=0, pitch=0)) == []
    assert player.check_base(HeadPose(yaw=0, pitch=-12)) == []
    assert player.check_base(HeadPose(yaw=20, pitch=10)) == []
    problems = player.check_base(HeadPose(yaw=0, pitch=-25))
    assert problems and any("bedtime_attentive" in p for p in problems)


@needs_ik
def test_set_base_moves_neutral_and_close_returns_to_true_neutral(player):
    player.set_base(HeadPose(yaw=8, pitch=-10))
    assert _wait(lambda: abs(player.pose.pitch + 10) < 0.3 and abs(player.pose.yaw - 8) < 0.3)
    assert (player.base.yaw, player.base.pitch) == (8.0, -10.0)
    player.play("refocus_curious", 1.0, wait=True)
    assert _wait(lambda: abs(player.pose.pitch + 10) < 0.3)          # back to the base, not to 0
    player.stop()
    assert _wait(lambda: abs(player.pose.pitch + 10) < 0.3)          # Esc too
    player.set_base(None)
    assert _wait(lambda: player.pose.is_close(NEUTRAL, tolerance=0.3))
    player.set_base(HeadPose(yaw=0, pitch=-8))
    assert _wait(lambda: abs(player.pose.pitch + 8) < 0.3)
    player.close()
    assert player.pose.is_close(NEUTRAL, tolerance=0.3) and player.base is None


# --- the panel ----------------------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")

from eepy.logs import SessionLog  # noqa: E402
from eepy.wizard.server import Wizard, WizardError  # noqa: E402


@pytest.fixture
def files(tmp_path):
    shutil.copytree(REPO / "behaviors", tmp_path / "behaviors")
    shutil.copytree(REPO / "sounds", tmp_path / "sounds")
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "wizard_panel.yaml", tmp_path / "config" / "wizard_panel.yaml")
    return {"behaviors": tmp_path / "behaviors", "sounds": tmp_path / "sounds",
            "config": tmp_path / "config" / "wizard_panel.yaml", "data": tmp_path / "data",
            "calibration": tmp_path / "calibration"}


class Face:
    """A participant sitting still: reports their face (in room angles) as the vision
    process would, relative to where the robot looks now, ten times a second."""

    def __init__(self, wizard, yaw, pitch, via_relay=False):
        self.wizard, self.yaw, self.pitch = wizard, yaw, pitch
        self.report = wizard._on_user if via_relay else wizard.user_target.observe   # _on_user: as the relay does
        self.running = True
        self.report(self._offset())
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _offset(self):
        pose = self.wizard.player.pose
        return {"yaw": self.yaw - pose.yaw, "pitch": self.pitch - pose.pitch}

    def _run(self):
        while self.running:
            self.report(self._offset())
            time.sleep(0.1)

    def stop(self):
        self.running = False
        self.thread.join(1)


@pytest.fixture
def wizard(files):
    from eepy.perception.gaze.calibration import CalibrationStore

    w = Wizard(DryRunBackend, backend_name="dry", behaviors_dir=files["behaviors"], sounds_dir=files["sounds"],
               config_path=files["config"], speech_callback=lambda line, text: None,
               session_log=SessionLog(data_dir=files["data"], repo_root=REPO),
               calibration_store=CalibrationStore(files["calibration"]))
    w.config = w.config.model_copy(update={"aim": w.config.aim.model_copy(
        update={"enabled": True, "settle_s": 0.6, "auto": False})})      # shipped switched off (docs/TODO.md)
    yield w
    w.shutdown()


def _aim(w, then=None):
    state = w.aim(then=then)
    assert _wait(lambda: w.aim_state["state"] not in ("aiming",), 15)
    return w.aim_state if state else state


def _events(w, kind):
    return [r for r in w.log_since(0)[1] if r.get("type") == kind]


@needs_ik
def test_a_face_slightly_off_centre_needs_no_aim(wizard):
    face = Face(wizard, 4, -5)
    try:
        state = _aim(wizard)
    finally:
        face.stop()
    assert state["state"] == "aimed" and wizard.base is None           # within 8 degrees: as it is


@needs_ik
def test_aims_at_a_face_to_the_side_and_saves_it(wizard):
    wizard.start_session("P50")
    face = Face(wizard, 20, -6)
    try:
        state = _aim(wizard)
    finally:
        face.stop()
    assert state["state"] == "aimed", state
    assert (wizard.base.yaw, wizard.base.pitch) == (20.0, -6.0)
    assert wizard.player.base.yaw == 20.0
    assert wizard.player.targets.get("user").yaw == pytest.approx(20.0, abs=1.0)
    logged = _events(wizard, "robot_base")
    assert logged and logged[0]["reason"] == "aim" and logged[0]["yaw"] == 20.0
    saved = wizard._calibration_file().section("P50", "laptop", "robot_base")
    assert (saved["yaw"], saved["pitch"]) == (20.0, -6.0)
    wizard.end_session()


@needs_ik
def test_a_face_far_above_aims_only_as_far_as_is_safe(wizard):
    face = Face(wizard, 0, -24)                       # robot at chest height
    try:
        state = _aim(wizard)
    finally:
        face.stop()
    assert state["state"] == "limited", state
    assert -14 <= wizard.base.pitch <= -10              # [-13 with the shipped behaviours]
    assert wizard.player.check_base(wizard.base) == []
    assert "raise the robot" in state["message"]


def test_no_face_no_aim(wizard):
    state = _aim(wizard)
    assert state["state"] == "no_face" and wizard.base is None


@needs_ik
def test_reset_and_reconnect(wizard):
    face = Face(wizard, -18, 0)
    try:
        _aim(wizard)
    finally:
        face.stop()
    assert wizard.base.yaw == -18.0
    wizard.connect()                                  # a reconnect keeps the aim
    assert _wait(lambda: wizard.player is not None and wizard.player.base is not None
                 and wizard.player.base.yaw == -18.0)
    wizard.reset_aim()
    assert wizard.base is None and _wait(lambda: wizard.player.base is None)
    assert _wait(lambda: not wizard.user_target.tracking)            # the face report goes stale ...
    assert wizard.player.targets.get("user").pitch == LIBRARY.gaze_targets["user"].pitch   # ... default again


@needs_ik
def test_calibrate_aims_first_then_asks_the_vision_process(wizard):
    wizard.start_session("P51")
    face = Face(wizard, 15, 0)
    try:
        cal = wizard.calibration_command("calibrate")
        assert cal["message"] == "aiming at the participant first"
        assert _wait(lambda: len(wizard._calibration_commands) == 1, 15)
    finally:
        face.stop()
    assert wizard.base.yaw == 15.0
    assert wizard._next_calibration_command()["command"] == "calibrate"
    wizard.end_session()


def test_calibrate_without_a_face_is_as_before(wizard):
    wizard.start_session("P52")
    wizard.calibration_command("calibrate")
    assert wizard._next_calibration_command()["command"] == "calibrate"   # straight away
    assert wizard.base is None
    wizard.end_session()


@needs_ik
def test_reuse_restores_the_saved_aim(wizard):
    wizard._calibration_file().save_section("P53", "laptop", "robot_base", {"yaw": 12.0, "pitch": -8.0})
    wizard.start_session("P53")
    wizard.calibration = dict(wizard.calibration, reuse={"taken_at": "x", "age_days": 1})
    wizard.calibration_command("reuse")
    assert (wizard.base.yaw, wizard.base.pitch) == (12.0, -8.0)
    assert _events(wizard, "robot_base")[0]["reason"] == "reuse"
    wizard.end_session()


def test_shipped_switched_off():
    from eepy.wizard.config import load_config
    assert load_config(REPO / "config" / "wizard_panel.yaml").aim.enabled is False


def test_aiming_switched_off(wizard):
    wizard.config = wizard.config.model_copy(update={"aim": wizard.config.aim.model_copy(update={"enabled": False})})
    assert wizard.aim()["state"] == "off" and wizard.base is None


def test_aim_needs_a_connected_robot(wizard):
    wizard.player.close()
    wizard.player = None
    with pytest.raises(WizardError, match="not connected"):
        wizard.aim()


# --- aiming by itself --------------------------------------------------------------------------------


def _auto(w, **extra):
    w.config = w.config.model_copy(update={"aim": w.config.aim.model_copy(
        update=dict({"auto": True, "auto_after_s": 0.3, "auto_min_gap_s": 0.0}, **extra))})


@needs_ik
def test_aims_by_itself_when_it_sees_the_face(wizard):
    _auto(wizard)
    wizard.player.set_idle("study_buddy_idle")             # the idle may run meanwhile
    face = Face(wizard, -15, -8, via_relay=True)
    try:
        assert _wait(lambda: wizard.base is not None and wizard.aim_state["state"] == "aimed", 15)
    finally:
        face.stop()
    assert (wizard.base.yaw, wizard.base.pitch) == (-15.0, -8.0)


@needs_ik
def test_no_automatic_aim_while_a_behaviour_plays_or_a_calibration_runs(wizard):
    _auto(wizard, auto_after_s=0.3)
    wizard.calibration = {"state": "running", "user": "P60"}
    face = Face(wizard, 20, 0, via_relay=True)
    try:
        time.sleep(1.5)
        assert wizard.base is None                           # calibrating: hands off
        wizard.calibration = {"state": "done", "user": "P60"}
        wizard.play("bedtime_verbal", 0.5)                   # a few seconds long
        time.sleep(1.0)
        assert wizard.base is None                           # a behaviour plays: wait
        assert _wait(lambda: wizard.base is not None, 20)    # it ends: aims
        assert _wait(lambda: wizard.aim_state["state"] != "aiming", 15)
    finally:
        face.stop()
    assert "calibrate again" in wizard.aim_state["message"]   # aimed after the calibration


@needs_ik
def test_at_the_safe_limit_it_does_not_keep_trying(wizard):
    _auto(wizard)
    face = Face(wizard, 0, -24, via_relay=True)
    try:
        assert _wait(lambda: wizard.aim_state["state"] == "limited", 15)
        events = len(_events(wizard, "robot_base"))
        time.sleep(2.0)
        assert len(_events(wizard, "robot_base")) == events    # no hunting at the limit
    finally:
        face.stop()


def test_automatic_aim_can_be_switched_off(wizard):
    face = Face(wizard, 20, 0, via_relay=True)                # the fixture has auto: false
    try:
        time.sleep(1.0)
    finally:
        face.stop()
    assert wizard.base is None
