"""Tests for the BehaviorPlayer, on the DryRunBackend.

These run the real player -- real threads, the real 50 Hz loop, the real
monotonic clock -- against short behaviours (a few tenths of a second), so
timing is genuinely exercised without the suite taking minutes. Timing
assertions allow a few frames of slack: they check that things happen at the
right moment, not that the operating system schedules threads perfectly.
"""

from __future__ import annotations

import struct
import time
import wave
from pathlib import Path

import pytest

from eepy.behaviors import reach
from eepy.behaviors.backends import SLEEP_POSE, DryRunBackend
from eepy.behaviors.player import BehaviorPlayer, PlayerError
from eepy.behaviors.pose import NEUTRAL, Pose
from eepy.behaviors.schema import HeadPose
from eepy.behaviors.targets import StaticTargetProvider

pytestmark = pytest.mark.skipif(
    not reach.available(), reason="the player safety-checks with the IK solver"
)

FRAME = 1 / 50       # one control-loop frame
SLACK = 4 * FRAME    # allowed timing error

BLOCKS = """
gaze_targets:
  user:
    pitch: -12
  notes:
    pitch: 15
blocks:
  nod:
    description: A tiny relative nod.
    relative: true
    keyframes:
      - head: { pitch: 6 }
        duration: 0.12
      - head: { pitch: -6 }
        duration: 0.12
"""

LINES = """
lines:
  greet:
    description: A greeting.
    variants:
      hedged:
        - "Hi?"
      direct:
        - "Hello."
  wait_for_it:
    description: Has a placeholder.
    variants:
      hedged:
        - "Just {minutes} more?"
        - "A little longer?"
"""

BEHAVIORS = """
behaviors:
  - name: short
    description: Two keyframes, 0.4 s total.
    keyframes:
      - head: { pitch: 10 }
        duration: 0.2
      - head: { pitch: 0 }
        duration: 0.2

  - name: long
    description: About 1.5 s, slow and gentle.
    keyframes:
      - head: { yaw: 20 }
        duration: 0.5
      - head: { yaw: -20 }
        duration: 0.5
      - head: { yaw: 0 }
        duration: 0.5

  - name: idle_breath
    description: A looping idle.
    loop: forever
    keyframes:
      - head: { z: 3 }
        duration: 0.15
      - head: { z: 0 }
        duration: 0.15

  - name: relative_nod
    description: Just the relative nod block.
    keyframes:
      - use: nod

  - name: big_pitch
    description: Pitch 20 then back.
    keyframes:
      - head: { pitch: 20 }
        duration: 0.3
      - head: { pitch: 0 }
        duration: 0.3

  - name: relative_up
    description: Relative +30 pitch -- fine from neutral, too far from pitch 15.
    keyframes:
      - head: { pitch: 30 }
        relative: true
        duration: 0.4

  - name: talker
    description: Says something and plays a sound.
    keyframes:
      - head: { pitch: 4 }
        duration: 0.1
        say: greet
      - head: { pitch: 0 }
        duration: 0.2
        sound: beep.wav

  - name: counts
    description: Uses a placeholder.
    keyframes:
      - head: { pitch: 3 }
        duration: 0.1
        say: wait_for_it

  - name: looker
    description: Looks at the user.
    keyframes:
      - look_at: user
        duration: 0.3

  - name: sleeper
    description: Hands over to the SDK's sleep.
    keyframes:
      - head: { pitch: 5 }
        duration: 0.1
      - action: goto_sleep
        duration: 0.1
"""


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    behaviors = tmp_path / "behaviors"
    behaviors.mkdir()
    (behaviors / "_blocks.yaml").write_text(BLOCKS)
    (behaviors / "lines.yaml").write_text(LINES)
    (behaviors / "test.yaml").write_text(BEHAVIORS)
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    with wave.open(str(sounds / "beep.wav"), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(struct.pack("<10h", *([0] * 10)))
    return behaviors, sounds


class Harness:
    """A started player plus everything it emitted."""

    def __init__(self, dirs, start: Pose = NEUTRAL, **kwargs) -> None:
        self.backend = DryRunBackend(start=start, action_seconds=0.05)
        self.spoken: list[tuple[str, str]] = []
        self.player = BehaviorPlayer(
            self.backend, behaviors_dir=dirs[0], sounds_dir=dirs[1],
            speech_callback=lambda line, text: self.spoken.append((line, text)),
            **kwargs,
        )
        self.events: list = []
        self.player.events.subscribe(self.events.append)
        self.player.start()

    def kinds(self, behavior: str | None = None) -> list[str]:
        self.player.events.flush()
        return [e.kind for e in self.events if behavior is None or e.behavior == behavior]

    def first(self, kind: str, behavior: str | None = None):
        self.player.events.flush()
        return next(e for e in self.events
                    if e.kind == kind and (behavior is None or e.behavior == behavior))

    def wait_until(self, condition, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError("timed out waiting")


@pytest.fixture
def harness(dirs):
    made: list[Harness] = []

    def make(**kwargs) -> Harness:
        made.append(Harness(dirs, **kwargs))
        return made[-1]

    yield make
    for h in made:
        h.player.close(return_to_neutral=False)


# --- the API -----------------------------------------------------------------


def test_play_does_not_block(harness):
    h = harness()
    started = time.monotonic()
    handle = h.player.play("long")
    assert time.monotonic() - started < 0.05, "play() must return at once"
    assert h.player.is_playing()
    assert handle.wait(5)
    assert not h.player.is_playing()


def test_play_with_wait_blocks_until_done(harness):
    h = harness()
    started = time.monotonic()
    assert h.player.play("short", wait=True)
    assert time.monotonic() - started >= 0.4 - SLACK


def test_current_names_what_is_playing(harness):
    h = harness()
    handle = h.player.play("long")
    h.wait_until(lambda: h.player.current() == "long")
    handle.wait(5)
    assert h.player.current() is None


def test_play_level(harness, dirs):
    (dirs[0] / "ladder.yaml").write_text("""
behaviors:
  - name: rung0
    ladder: t
    level: 0
    description: Bottom rung.
    keyframes:
      - head: { pitch: 2 }
        duration: 0.1
""")
    h = harness()
    assert h.player.play_level("t", 0, wait=True)
    with pytest.raises(PlayerError, match="has no level 3"):
        h.player.play_level("t", 3)


def test_unknown_name_gets_a_suggestion(harness):
    h = harness()
    with pytest.raises(PlayerError, match='Did you mean "short"'):
        h.player.play("shrot")


def test_intensity_out_of_range_is_refused_not_clamped(harness):
    h = harness()
    with pytest.raises(PlayerError, match="between 0 and 1"):
        h.player.play("short", intensity=1.5)


def test_waiting_on_a_forever_loop_is_refused(harness):
    h = harness()
    with pytest.raises(PlayerError, match="loops forever"):
        h.player.play("idle_breath", wait=True)


# --- timing ------------------------------------------------------------------


def test_keyframes_arrive_on_schedule(harness):
    h = harness()
    h.player.play("long", wait=True)
    started = h.first("behavior_started", "long").t
    arrivals = [e.t - started for e in h.events
                if e.kind == "keyframe_reached" and e.behavior == "long"]
    assert len(arrivals) == 3
    for actual, planned in zip(arrivals, (0.5, 1.0, 1.5)):
        assert abs(actual - planned) < SLACK, f"{actual:.3f}s vs {planned}s"


def test_timing_does_not_drift_over_a_long_run(harness):
    """Deadline scheduling: lateness must not accumulate frame after frame."""
    h = harness()
    h.player.play("long", wait=True)
    started = h.first("behavior_started", "long").t
    finished = h.first("behavior_finished", "long").t
    assert abs((finished - started) - 1.5) < SLACK


def test_frame_timing_jitter(harness):
    """The quick jitter test: how late is each frame vs its schedule?

    This measures the player's own scheduling on this machine. Network jitter
    to a real robot adds to it, and can only be measured with the robot
    (python -m eepy.behaviors.play <name> --backend real --timing).
    """
    h = harness()
    h.player.play("long", wait=True)
    stats = h.player.timing_stats()
    print(f"\n  frame lateness: {stats}")
    assert stats["frames"] > 60
    assert stats["p95_ms"] < 1000 * FRAME, "95% of frames within one frame period"


def test_frames_are_sent_at_the_loop_rate(harness):
    h = harness()
    h.player.play("long", wait=True)
    times = [t for t, _ in h.backend.sent]
    gaps = [b - a for a, b in zip(times, times[1:])]
    median = sorted(gaps)[len(gaps) // 2]
    assert abs(median - FRAME) < 0.005


# --- preemption and idle -----------------------------------------------------


def test_a_new_play_interrupts_the_current_one(harness):
    h = harness()
    first = h.player.play("long")
    time.sleep(0.4)
    second = h.player.play("short")
    assert second.wait(5)
    assert first.outcome == "interrupted"
    assert first.interrupted_by == "short"
    event = h.first("behavior_interrupted", "long")
    assert event.data["by"] == "short"


def test_preemption_blends_without_snapping(harness):
    h = harness()
    h.player.play("long")
    time.sleep(0.35)   # mid-move, yaw around 14
    h.player.play("big_pitch", wait=True)
    poses = [p for _, p in h.backend.sent]
    worst = max(max(abs(a.yaw - b.yaw), abs(a.pitch - b.pitch)) for a, b in zip(poses, poses[1:]))
    assert worst < 4.0, f"a {worst:.1f} deg jump between frames is a snap"


def test_idle_resumes_after_a_behaviour(harness):
    h = harness()
    h.player.set_idle("idle_breath")
    h.wait_until(lambda: "behavior_started" in h.kinds("idle_breath"))
    h.player.play("short", wait=True)
    assert h.first("behavior_interrupted", "idle_breath").data["by"] == "short"
    h.wait_until(lambda: h.kinds("idle_breath").count("behavior_started") >= 2)


def test_idle_does_not_count_as_playing(harness):
    h = harness()
    h.player.set_idle("idle_breath")
    h.wait_until(lambda: "behavior_started" in h.kinds("idle_breath"))
    assert not h.player.is_playing()
    assert h.player.current() is None


# --- relative poses and intensity --------------------------------------------


def test_relative_poses_build_on_the_live_start_pose(harness):
    h = harness(start=Pose(pitch=10))
    h.player.play("relative_nod", wait=True)
    pitches = [p.pitch for _, p in h.backend.sent]
    assert max(pitches) == pytest.approx(16, abs=0.2)   # 10 + 6
    assert pitches[-1] == pytest.approx(10, abs=0.2)    # and back


def test_intensity_scales_the_movement(harness):
    h = harness()
    h.player.play("big_pitch", intensity=0.5, wait=True)
    assert max(p.pitch for _, p in h.backend.sent) == pytest.approx(10, abs=0.3)


def test_a_behaviour_unsafe_from_the_live_pose_is_refused(harness):
    """+30 from neutral is fine; +30 from pitch 15 would be 45, past 40."""
    h = harness(start=Pose(pitch=15))
    handle = h.player.play("relative_up")
    assert not handle.wait(2)
    assert handle.outcome == "refused"
    assert "head pitch" in handle.reason
    assert "behavior_refused" in h.kinds("relative_up")
    assert max(p.pitch for _, p in h.backend.sent) <= 15.5, "nothing unsafe was sent"


def test_a_refused_behaviour_does_not_interrupt_the_current_one(harness):
    h = harness(start=Pose(pitch=15))
    running = h.player.play("long")
    time.sleep(0.2)
    h.player.play("relative_up")
    assert running.wait(5), "the refused request must leave 'long' playing"


def test_too_fast_from_the_live_pose_is_slowed_not_refused(harness):
    """Drooped antennas snapping back to neutral would be far over the speed
    limit; the move is made longer, and the target is unchanged."""
    h = harness(start=Pose(antenna_right=-150, antenna_left=150))
    (h.player.behaviors_dir / "perk.yaml").write_text("""
behaviors:
  - name: perk
    description: Antennas straight back to neutral, quickly.
    keyframes:
      - antennas: { right: -10, left: 10 }
        duration: 0.2
""")
    assert h.player.reload().ok
    time.sleep(0.05)
    handle = h.player.play("perk")
    assert handle.wait(5), handle.reason
    assert h.first("behavior_started", "perk").data["stretched"]
    assert h.backend.sent[-1][1].antenna_right == pytest.approx(-10, abs=0.1)


# --- stop -------------------------------------------------------------------


def test_stop_interrupts_and_returns_to_neutral(harness):
    h = harness()
    handle = h.player.play("big_pitch")
    time.sleep(0.25)
    h.player.stop()
    h.wait_until(lambda: h.player.pose.is_close(NEUTRAL, tolerance=0.1))
    assert handle.outcome == "interrupted" and handle.interrupted_by == "stop"


def test_stop_keeps_the_robot_quiet_until_the_next_play(harness):
    h = harness()
    h.player.set_idle("idle_breath")
    h.wait_until(lambda: "behavior_started" in h.kinds("idle_breath"))
    h.player.stop()
    h.wait_until(lambda: h.player.pose.is_close(NEUTRAL, tolerance=0.1))
    count = h.kinds("idle_breath").count("behavior_started")
    time.sleep(0.5)
    assert h.kinds("idle_breath").count("behavior_started") == count, "idle stayed off"


def test_stop_without_return_holds_still(harness):
    h = harness()
    h.player.play("big_pitch")
    time.sleep(0.2)
    h.player.stop(return_to_neutral=False)
    time.sleep(0.1)
    held = h.player.pose
    time.sleep(0.3)
    assert h.player.pose == held and held.pitch > 1


def test_close_returns_to_neutral(dirs):
    h = Harness(dirs)
    h.player.play("big_pitch")
    time.sleep(0.2)
    h.player.close()
    assert h.backend.sent[-1][1].is_close(NEUTRAL, tolerance=0.1)
    assert not h.backend.connected


# --- sounds, lines and events ---------------------------------------------------


def test_sounds_and_lines_fire_at_their_keyframe(harness):
    h = harness()
    h.player.play("talker", tone="direct", wait=True)
    h.player.events.flush()
    started = h.first("behavior_started", "talker").t
    line = h.first("line_requested", "talker")
    sound = h.first("sound_played", "talker")
    assert abs(line.t - started) < SLACK                # keyframe 1 starts at 0
    assert abs(sound.t - started - 0.1) < SLACK         # keyframe 2 starts at 0.1
    h.wait_until(lambda: h.spoken and h.backend.sounds)
    assert h.spoken == [("greet", "Hello.")]             # direct tone
    assert h.backend.sounds[0][1] == "beep.wav"


def test_placeholders_are_filled_from_context(harness):
    h = harness()
    h.player.play("counts", context={"minutes": 5}, wait=True)
    h.wait_until(lambda: h.spoken)
    assert h.spoken[0][1] in ("Just 5 more?", "A little longer?")


def test_a_line_whose_slot_cannot_be_filled_is_passed_over(harness):
    h = harness()
    for _ in range(5):
        h.player.play("counts", wait=True)
    h.wait_until(lambda: len(h.spoken) == 5)
    assert {text for _, text in h.spoken} == {"A little longer?"}, "never '{minutes}'"


def test_events_carry_monotonic_time_and_the_behaviour(harness):
    h = harness()
    h.player.play("short", wait=True)
    h.player.events.flush()
    mine = [e for e in h.events if e.behavior == "short"]
    assert [e.kind for e in mine][0] == "behavior_started"
    assert [e.kind for e in mine][-1] == "behavior_finished"
    times = [e.t for e in mine]
    assert times == sorted(times)
    assert all(0 < t <= time.monotonic() for t in times)


# --- look-at targets ------------------------------------------------------------


def test_look_at_comes_from_the_target_provider(harness):
    h = harness()
    h.player.targets = StaticTargetProvider(lambda: h.player.library,
                                            overrides={"user": HeadPose(yaw=15, pitch=-5)})
    h.player.play("looker", wait=True)
    final = h.backend.sent[-1][1]
    assert final.yaw == pytest.approx(15) and final.pitch == pytest.approx(-5)


def test_an_unknown_target_is_refused_not_guessed(harness):
    h = harness()

    class Empty:
        def get(self, name):
            raise KeyError(f'no gaze target called "{name}"')

    h.player.targets = Empty()
    handle = h.player.play("looker")
    assert not handle.wait(2) and handle.outcome == "refused"


# --- sleep ----------------------------------------------------------------------


def test_after_goto_sleep_idle_does_not_wake_it(harness):
    h = harness()
    h.player.set_idle("idle_breath")
    h.player.play("sleeper", wait=True)
    assert [a for _, a in h.backend.actions] == ["goto_sleep"]
    count = h.kinds("idle_breath").count("behavior_started")
    time.sleep(0.4)
    assert h.kinds("idle_breath").count("behavior_started") == count


def test_the_next_play_wakes_it_first(harness):
    h = harness()
    h.player.play("sleeper", wait=True)
    h.player.play("short", wait=True)
    assert [a for _, a in h.backend.actions] == ["goto_sleep", "wake_up"]


def test_blending_out_of_the_sleep_pose_has_no_sideways_snap(harness):
    """The sleep pose has x = -21 mm, which no behaviour can set. Blend it out."""
    h = harness(start=SLEEP_POSE)
    h.wait_until(lambda: h.player.pose.is_close(NEUTRAL, tolerance=0.1), timeout=8)
    xs = [p.x for _, p in h.backend.sent]
    assert xs[0] < -15, "started from the sleep pose's sideways shift"
    assert max(abs(a - b) for a, b in zip(xs, xs[1:])) < 3, "and slid out of it"


# --- reload -----------------------------------------------------------------------


def test_reload_with_a_broken_file_keeps_the_last_good_version(harness, dirs):
    h = harness()
    good = (dirs[0] / "test.yaml").read_text()
    (dirs[0] / "test.yaml").write_text(good.replace("pitch: 20 }", "pitch: 200 }"))
    result = h.player.reload()
    assert not result.ok
    assert "head pitch" in result.message
    assert h.player.play("big_pitch", wait=True), "still plays the last valid version"
    assert max(p.pitch for _, p in h.backend.sent) == pytest.approx(20, abs=0.3)


def test_reload_does_not_disturb_what_is_playing(harness, dirs):
    h = harness()
    running = h.player.play("long")
    time.sleep(0.2)
    (dirs[0] / "test.yaml").write_text(
        (dirs[0] / "test.yaml").read_text().replace("pitch: 20 }", "pitch: 18 }"))
    assert h.player.reload().ok
    assert running.wait(5)


def test_reload_with_a_good_file_takes_effect_next_time(harness, dirs):
    h = harness()
    (dirs[0] / "test.yaml").write_text(
        (dirs[0] / "test.yaml").read_text().replace("pitch: 20 }", "pitch: 12 }"))
    assert h.player.reload().ok
    time.sleep(0.05)
    h.player.play("big_pitch", wait=True)
    assert max(p.pitch for _, p in h.backend.sent) == pytest.approx(12, abs=0.3)


def test_reload_refuses_to_lose_the_idle(harness, dirs):
    h = harness()
    h.player.set_idle("idle_breath")
    text = (dirs[0] / "test.yaml").read_text().replace("name: idle_breath", "name: renamed")
    (dirs[0] / "test.yaml").write_text(text)
    result = h.player.reload()
    assert not result.ok and "idle_breath" in result.message


# --- safety at runtime ------------------------------------------------------------


def test_every_frame_sent_obeys_every_limit(harness):
    """Defence in depth: check the actual frames, not the plan."""
    from eepy.behaviors import safety

    h = harness()
    for name in ("long", "big_pitch", "relative_nod", "talker", "looker"):
        h.player.play(name, wait=True)
    frames = h.backend.sent
    for (t0, a), (t1, b) in zip(frames, frames[1:]):
        assert not safety.check_pose(b), b
        assert not safety.check_tick_speed(a, b, t1 - t0)
