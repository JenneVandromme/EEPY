"""The four easing curves, and how fast each one moves at its fastest.

The curves are copied verbatim from the SDK's own
reachy_mini/utils/interpolation.py (`time_trajectory`), so a behaviour eases
exactly the way the robot's built-in goto_target would. They are copied rather
than imported because this module also runs in CI, where the full SDK is not
installed. tests/test_easing.py compares the two side by side whenever the SDK
is present, so a change upstream would be caught.

PEAK_SPEED is the other half of the story. A curve's peak speed is higher than
its average: moving 20 degrees in 1 second with minjerk does not move at
20 deg/s, it peaks at 1.875x that. The speed limits are about the peak, so both
the validator and the player multiply by these factors.

`cartoon` is special: it overshoots. It goes slightly past its target and comes
back, so the most extreme pose of a cartoon move is NOT the keyframe value. That
is why the validator samples whole trajectories rather than only checking
keyframes.
"""

from __future__ import annotations

from typing import Callable

_C1 = 1.70158
_C2 = _C1 * 1.525


def linear(t: float) -> float:
    return t


def minjerk(t: float) -> float:
    return 10 * t**3 - 15 * t**4 + 6 * t**5


def ease_in_out(t: float) -> float:
    if t < 0.5:
        return 2 * t * t
    return 1 - ((-2 * t + 2) ** 2) / 2


def cartoon(t: float) -> float:
    if t < 0.5:
        return ((2 * t) ** 2 * ((_C2 + 1) * 2 * t - _C2)) / 2
    return (((2 * t - 2) ** 2 * ((_C2 + 1) * (2 * t - 2) + _C2)) + 2) / 2


CURVES: dict[str, Callable[[float], float]] = {
    "linear": linear,
    "minjerk": minjerk,
    "ease_in_out": ease_in_out,
    "cartoon": cartoon,
}


def ease(method: str, t: float) -> float:
    """Progress (0 at start, 1 at end) at normalised time t in [0, 1]."""
    return CURVES[method](min(1.0, max(0.0, t)))


def _peak_speed(curve: Callable[[float], float], samples: int = 20000) -> float:
    """max |d curve / dt| over [0, 1], numerically. Computed once at import."""
    step = 1.0 / samples
    return max(
        abs(curve((i + 1) * step) - curve(i * step)) / step for i in range(samples)
    )


def _range(curve: Callable[[float], float], samples: int = 20000) -> tuple[float, float]:
    values = [curve(i / samples) for i in range(samples + 1)]
    return min(values), max(values)


# Peak speed as a multiple of the average (distance / duration).
#   linear 1.0, minjerk 1.875, ease_in_out 2.0, cartoon about 5.6 -- cartoon
# is snappy because it winds up, overshoots by 10% and settles back.
PEAK_SPEED: dict[str, float] = {name: _peak_speed(fn) for name, fn in CURVES.items()}

# Lowest and highest progress each curve reaches. Only cartoon leaves [0, 1].
RANGE: dict[str, tuple[float, float]] = {name: _range(fn) for name, fn in CURVES.items()}
