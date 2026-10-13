"""Where "look_at: user" actually points.

Behaviour files name gaze targets -- user, notes, screen, away -- and never
contain angles for them. Something has to turn a name into a direction at play
time, and that something is a TargetProvider.

For now that is StaticTargetProvider, which reads the fixed angles from
behaviors/_blocks.yaml. Later, face tracking can supply a provider whose "user"
follows the real person. The player only ever calls `get(name)`, so swapping
providers needs no change to the player or to any behaviour file.

Targets are looked up once, when a behaviour starts -- not continuously while it
plays. That keeps every behaviour fully safety-checked before the first frame
is sent. A tracking provider just has to be current at that moment.
"""

from __future__ import annotations

from typing import Callable, Protocol

from .schema import HeadPose


class TargetProvider(Protocol):
    """Anything that can say where a named gaze target is."""

    def get(self, name: str) -> HeadPose:
        """The head pose that looks at `name`. Raise KeyError if unknown."""
        ...


class StaticTargetProvider:
    """Fixed directions, from _blocks.yaml, with optional per-desk overrides.

    `library` is a callable rather than a Library so that the provider always
    sees the CURRENT library after the player reloads the YAML -- edit a gaze
    target in _blocks.yaml and the next behaviour uses it.

        StaticTargetProvider(lambda: player.library,
                             overrides={"user": HeadPose(yaw=15, pitch=-14)})
    """

    def __init__(
        self,
        library: Callable[[], object],
        overrides: dict[str, HeadPose] | None = None,
    ) -> None:
        self._library = library
        self._overrides = dict(overrides or {})
        self._initial = dict(self._overrides)

    def get(self, name: str) -> HeadPose:
        if name in self._overrides:
            return self._overrides[name]
        targets = getattr(self._library(), "gaze_targets", {})
        if name not in targets:
            known = ", ".join(sorted(set(targets) | set(self._overrides)))
            raise KeyError(f'no gaze target called "{name}" (known: {known})')
        return targets[name]

    def set(self, name: str, pose: HeadPose) -> None:
        """Override one target at runtime, e.g. once the user is located."""
        self._overrides[name] = pose

    def clear(self, name: str) -> None:
        """Back to the target from _blocks.yaml (or the override given at the start)."""
        self._overrides.pop(name, None)
        if name in self._initial:
            self._overrides[name] = self._initial[name]


class TrackingTargetProvider:
    """ "user" follows the real student; every other target (and "user" when no
    face has been seen for `max_age` seconds) comes from `fallback`.

    The vision process reports where the student's face is as head angles
    RELATIVE to where the robot is looking now (via the camera relay, USER
    message). This turns that into an absolute target -- current head pose +
    offset -- and smooths it, so the robot does not jitter: an exponential step
    (time constant `smoothing`) capped at `max_speed` degrees per second (the
    idea of BURTRAM's smooth_towards, made time-based). The result is kept
    within `yaw_limit` / `pitch_limits`, well inside what the neck can do.

    Behaviours look targets up when they START (see the module docstring), so
    "look_at: user" points at the student as they were at that moment.
    """

    def __init__(self, fallback: TargetProvider, pose: Callable[[], object],
                 max_age: float = 1.0, smoothing: float = 0.3, max_speed: float = 90.0,
                 yaw_limit: float = 60.0, pitch_limits: tuple = (-30.0, 25.0),
                 clock: Callable[[], float] = None) -> None:
        import time

        self.fallback = fallback
        self.pose = pose                       # -> the robot's current Pose (yaw, pitch in degrees)
        self.max_age, self.smoothing, self.max_speed = max_age, smoothing, max_speed
        self.yaw_limit, self.pitch_limits = yaw_limit, pitch_limits
        self._clock = clock or time.perf_counter   # precise on every OS (Windows monotonic: 15.6 ms steps)
        self._target: tuple | None = None      # smoothed (yaw, pitch)
        self._seen = -1e9                      # when a face was last reported

    def observe(self, offset: dict | None) -> None:
        """A report from the vision process: {"yaw", "pitch"} or None (no face)."""
        import math

        now = self._clock()
        if offset is None:
            return                             # keep the last target until it goes stale
        pose = self.pose()
        yaw = max(-self.yaw_limit, min(self.yaw_limit, float(getattr(pose, "yaw", 0.0)) + offset["yaw"]))
        pitch = max(self.pitch_limits[0], min(self.pitch_limits[1],
                                              float(getattr(pose, "pitch", 0.0)) + offset["pitch"]))
        if self._target is None or now - self._seen > self.max_age:
            self._target = (yaw, pitch)        # first sighting (or after a gap): no slow approach
        else:
            dt = max(0.0, now - self._seen)
            alpha = 1.0 - math.exp(-dt / self.smoothing) if self.smoothing > 0 else 1.0
            step_cap = self.max_speed * dt
            new = []
            for old, want in zip(self._target, (yaw, pitch)):
                step = max(-step_cap, min(step_cap, alpha * (want - old)))
                new.append(old + step)
            self._target = (new[0], new[1])
        self._seen = now

    def current(self) -> tuple | None:
        """Where the student's face is now, as (yaw, pitch) head angles; None while
        no face has been reported for `max_age` seconds."""
        return self._target if self.tracking else None

    @property
    def tracking(self) -> bool:
        """Is "user" currently the real student (not the default)?"""
        return self._target is not None and self._clock() - self._seen <= self.max_age

    def get(self, name: str) -> HeadPose:
        if name == "user" and self.tracking:
            yaw, pitch = self._target
            return HeadPose(yaw=round(yaw, 1), pitch=round(pitch, 1))
        return self.fallback.get(name)
