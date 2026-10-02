"""The safety rules, written once and used by both the validator and the player.

The validator checks a behaviour file before it is merged. The player checks
every pose again, live, before sending it -- because the live start pose, a
moving gaze target or a bug in our own code can produce something the
validator never saw. Both read these same functions, so the two can never
disagree about what is allowed.

Nothing is ever clamped. A pose that breaks a rule comes back as a Violation,
and the caller refuses it.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import easing, limits, reach
from .pose import Pose


@dataclass(frozen=True)
class Violation:
    """One broken rule.

    kind is "range", "neck", "reach" or "speed". message is plain English,
    in the same voice as the validator's.
    """

    kind: str
    message: str


_RANGES: tuple[tuple[str, str, float, float, str], ...] = (
    # (field, label, low, high, unit)
    ("pitch", "head pitch", -limits.HEAD_PITCH_LIMIT_DEG, limits.HEAD_PITCH_LIMIT_DEG, "degrees"),
    ("roll", "head roll", -limits.HEAD_ROLL_LIMIT_DEG, limits.HEAD_ROLL_LIMIT_DEG, "degrees"),
    ("yaw", "head yaw", -limits.HEAD_YAW_LIMIT_DEG, limits.HEAD_YAW_LIMIT_DEG, "degrees"),
    ("z", "head height", limits.HEAD_Z_MIN_MM, limits.HEAD_Z_MAX_MM, "mm"),
    ("body_yaw", "body yaw", -limits.BODY_YAW_LIMIT_DEG, limits.BODY_YAW_LIMIT_DEG, "degrees"),
    ("antenna_right", "right antenna", -limits.ANTENNA_LIMIT_DEG, limits.ANTENNA_LIMIT_DEG, "degrees"),
    ("antenna_left", "left antenna", -limits.ANTENNA_LIMIT_DEG, limits.ANTENNA_LIMIT_DEG, "degrees"),
)

_EPS = 1e-6


def check_pose(pose: Pose, check_reach: bool = True) -> list[Violation]:
    """Every hard limit a single pose breaks. Empty means it is safe."""
    out: list[Violation] = []
    for field, label, low, high, unit in _RANGES:
        value = getattr(pose, field)
        if not (low - _EPS <= value <= high + _EPS):
            out.append(Violation(
                "range",
                f"{label} would be {value:.1f} {unit}, outside {low:.0f} to {high:.0f}.",
            ))
    delta = abs(pose.yaw - pose.body_yaw)
    if delta > limits.HEAD_BODY_YAW_DELTA_LIMIT_DEG + _EPS:
        out.append(Violation(
            "neck",
            f"the head would be turned {delta:.1f} degrees away from the body, "
            f"more than the {limits.HEAD_BODY_YAW_DELTA_LIMIT_DEG:.0f} the neck allows.",
        ))
    # Reachability only means something once each value is individually legal.
    if check_reach and not out and reach.available():
        if not reach.is_reachable(
            yaw=pose.yaw, pitch=pose.pitch, roll=pose.roll, z_mm=pose.z,
            body_yaw=pose.body_yaw, x_mm=pose.x, y_mm=pose.y,
        ):
            out.append(Violation(
                "reach",
                f"the head cannot physically hold this pose (yaw {pose.yaw:.0f}, "
                f"pitch {pose.pitch:.0f}, roll {pose.roll:.0f}, height {pose.z:.0f} mm).",
            ))
    return out


def peak_speeds(start: Pose, end: Pose, duration: float, method: str) -> dict[str, float]:
    """Peak speed per unit kind for one eased move. Exact, not sampled."""
    if duration <= 0:
        return {kind: (0.0 if d == 0 else float("inf")) for kind, d in start.delta(end).items()}
    factor = easing.PEAK_SPEED[method]
    return {kind: d / duration * factor for kind, d in start.delta(end).items()}


_UNITS = {"head": "deg/s", "body": "deg/s", "antenna": "deg/s", "translation": "mm/s"}
_LABELS = {"head": "the head", "body": "the body", "antenna": "an antenna",
           "translation": "the head's position"}


def check_move_speed(start: Pose, end: Pose, duration: float, method: str) -> list[Violation]:
    """Speed violations for one eased move, judged on its PEAK speed."""
    out = []
    for kind, speed in peak_speeds(start, end, duration, method).items():
        cap = limits.HARD_SPEED[kind]
        if speed > cap + _EPS:
            out.append(Violation(
                "speed",
                f"{_LABELS[kind]} would peak at {speed:.0f} {_UNITS[kind]}, over the "
                f"{cap:.0f} {_UNITS[kind]} limit ({method} easing peaks at "
                f"{easing.PEAK_SPEED[method]:.1f}x its average speed).",
            ))
    return out


def check_tick_speed(previous: Pose, current: Pose, dt: float) -> list[Violation]:
    """Speed violations between two consecutive commands, for the player.

    Both poses are exact samples of a smooth curve, so their average speed can
    never exceed the curve's peak -- this only fires on a genuine bug. The 2%
    tolerance absorbs floating point, not slack in the rule.
    """
    if dt <= 0:
        return []
    out = []
    for kind, change in previous.delta(current).items():
        speed = change / dt
        cap = limits.HARD_SPEED[kind]
        if speed > cap * 1.02:
            out.append(Violation(
                "speed",
                f"{_LABELS[kind]} was about to move at {speed:.0f} {_UNITS[kind]}, "
                f"over the {cap:.0f} {_UNITS[kind]} limit.",
            ))
    return out


def blend_duration(start: Pose, end: Pose, wanted: float, method: str) -> float:
    """How long a blend from start to end must take to stay comfortably safe.

    Returns `wanted` if that is already slow enough, otherwise the shortest
    duration at which every axis peaks at BLEND_SPEED_FRACTION of its hard
    limit. The TARGET is never changed -- only how long it takes to get there.
    """
    needed = 0.0
    factor = easing.PEAK_SPEED[method]
    for kind, distance in start.delta(end).items():
        allowed = limits.HARD_SPEED[kind] * limits.BLEND_SPEED_FRACTION
        needed = max(needed, distance * factor / allowed)
    return max(wanted, needed)
