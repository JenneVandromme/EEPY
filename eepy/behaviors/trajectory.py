"""A resolved behaviour as motion over time: "where is the head at t = 1.37 s?"

The loader turns YAML into a list of Steps -- absolute poses with durations.
This module turns those Steps into a Trajectory you can sample at any instant,
using the SDK's own easing curves. The player samples it 50 times a second to
drive the robot; the validator samples it to check the motion BETWEEN
keyframes, where cartoon easing overshoots.

Timing is relative to the trajectory's own start, in seconds. Nothing here
knows about clocks, threads or robots.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import easing, safety
from .loader import Step
from .pose import Pose


@dataclass(frozen=True)
class Segment:
    """Move from `start` to `end` over `duration` seconds, then hold."""

    start_s: float
    duration: float
    hold: float
    easing: str
    start: Pose
    end: Pose
    step: Step
    index: int                       # 0-based position in the behaviour
    action: str | None = None        # a hand-over to the SDK, e.g. goto_sleep
    stretched_from: float | None = None  # set when the blend-in was lengthened

    @property
    def arrive_s(self) -> float:
        return self.start_s + self.duration

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration + self.hold

    def sample(self, t: float) -> Pose:
        """Pose at absolute trajectory time t (clamped to this segment)."""
        if self.action is not None or self.duration <= 0:
            return self.end
        progress = easing.ease(self.easing, (t - self.start_s) / self.duration)
        return self.start.lerp(self.end, progress)


@dataclass(frozen=True)
class Cue:
    """Something to do at a moment in the trajectory.

    kind is "sound", "say" or "keyframe". Sounds and lines fire when their
    keyframe STARTS moving; "keyframe" fires when it ARRIVES.
    """

    t: float
    kind: str
    value: str
    segment: int


@dataclass
class Trajectory:
    segments: list[Segment]
    cues: list[Cue] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.segments[-1].end_s if self.segments else 0.0

    @property
    def end_pose(self) -> Pose:
        return self.segments[-1].end if self.segments else Pose()

    def segment_at(self, t: float) -> Segment:
        for segment in self.segments:
            if t < segment.end_s:
                return segment
        return self.segments[-1]

    def sample(self, t: float) -> Pose:
        return self.segment_at(t).sample(t)

    @classmethod
    def build(cls, steps: list[Step], start: Pose, stretch: str = "first") -> "Trajectory":
        """Lay the steps out end to end, starting from `start`.

        `stretch` says which moves may be lengthened when they would be too
        fast. Only the timing ever changes, never a target.

          "first"  only the blend-in from `start` to the first keyframe. This
                   is what the VALIDATOR uses: it starts every behaviour from
                   neutral, so anything after the blend-in is exactly as
                   authored, and a too-fast authored move is an error.
          "all"    any move. This is what the PLAYER uses, because a live start
                   pose changes moves the author never wrote. Example:
                   approving_nod ends with antenna_perk, a 0.4 s cartoon flick
                   back to neutral antennas. From neutral that is no movement
                   at all. Played right after bedtime_subtle, with the antennas
                   still drooped, it is a 140 degree snap peaking near
                   2000 deg/s. Refusing it would make the robot unable to
                   praise someone right after nudging them toward bed;
                   stretching it just makes the perk take a little longer.
          "none"   nothing. For tests.
        """
        if stretch not in ("first", "all", "none"):
            raise ValueError(f"stretch must be first, all or none, not {stretch!r}")
        segments: list[Segment] = []
        cues: list[Cue] = []
        clock = 0.0
        previous = start
        for index, step in enumerate(steps):
            if step.action is not None:
                end = previous  # the SDK decides where an action ends up
            else:
                end = Pose(
                    yaw=step.head_yaw, pitch=step.head_pitch, roll=step.head_roll,
                    x=0.0, y=0.0, z=step.head_z,
                    antenna_right=step.antenna_right, antenna_left=step.antenna_left,
                    body_yaw=step.body_yaw,
                )
            duration = step.duration
            stretched_from = None
            may_stretch = stretch == "all" or (stretch == "first" and index == 0)
            if may_stretch and step.action is None:
                safe = safety.blend_duration(previous, end, duration, step.easing)
                if safe > duration + 1e-9:
                    stretched_from, duration = duration, safe
            segment = Segment(
                start_s=clock, duration=duration, hold=step.hold, easing=step.easing,
                start=previous, end=end, step=step, index=index,
                action=step.action, stretched_from=stretched_from,
            )
            segments.append(segment)
            if step.sound:
                cues.append(Cue(clock, "sound", step.sound, index))
            if step.say:
                cues.append(Cue(clock, "say", step.say, index))
            cues.append(Cue(segment.arrive_s, "keyframe", step.source, index))
            clock = segment.end_s
            previous = end
        return cls(segments, cues)

    # --- checking ------------------------------------------------------------

    def violations(self, rate_hz: float = 50.0, check_reach: bool = True
                   ) -> list[tuple[Segment, safety.Violation]]:
        """Every safety rule this trajectory breaks, anywhere along it.

        Checks, for each move:
          * its peak speed, exactly, from the easing curve;
          * its two endpoints and, for cartoon, the two overshoot extremes,
            exactly -- so a 10% overshoot past a limit cannot slip between
            samples;
          * reachability every 1/rate_hz seconds in between, because the
            reachable region is not a box and two reachable poses can have an
            unreachable one between them.
        Returns the first problem per segment, to keep reports readable.
        """
        found: list[tuple[Segment, safety.Violation]] = []
        for segment in self.segments:
            if segment.action is not None:
                continue
            problems = safety.check_move_speed(
                segment.start, segment.end, segment.duration, segment.easing
            )
            low, high = easing.RANGE[segment.easing]
            probes = [segment.start, segment.end]
            if low < 0.0:
                probes.append(segment.start.lerp(segment.end, low))
            if high > 1.0:
                probes.append(segment.start.lerp(segment.end, high))
            for pose in probes:
                if problems:
                    break
                problems = safety.check_pose(pose, check_reach=check_reach)
            if not problems and check_reach and segment.duration > 0:
                n = max(2, int(segment.duration * rate_hz))
                for i in range(1, n):
                    problems = safety.check_pose(
                        segment.sample(segment.start_s + segment.duration * i / n),
                        check_reach=True,
                    )
                    if problems:
                        break
            if problems:
                found.append((segment, problems[0]))
        return found
