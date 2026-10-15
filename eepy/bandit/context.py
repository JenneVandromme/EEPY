"""What the bandit may know at decision time (A22): nothing from after the decision.

FocusTrend keeps the recent P(focused) of the HMM's estimates; `trend()` is the change over the
last TREND_SECONDS, computed at the moment of the decision and stored with it (a user already
drifting back to focus should not make a nudge look successful). snapshot() picks the context
fields the bandit trains on from a decision's logged context. Standard library only.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, Mapping, Optional, Tuple

TREND_SECONDS = 20.0
BELIEF_STATES = ("focused", "distracted", "drowsy", "very_drowsy")


class FocusTrend:
    def __init__(self, seconds: float = 120.0) -> None:
        self.seconds = seconds
        self.points: Deque[Tuple[float, float]] = deque()

    def reset(self) -> None:
        self.points.clear()

    def add(self, estimate: Mapping[str, Any], t: float) -> None:
        probs = estimate.get("probs")
        if not isinstance(probs, Mapping) or estimate.get("updated") is False \
                or estimate.get("state") in ("absent", "unknown"):
            return
        self.points.append((t, round(float(probs.get("focused", 0.0)), 3)))
        while self.points and t - self.points[0][0] > self.seconds:
            self.points.popleft()

    def trend(self, now: float, lookback: float = TREND_SECONDS) -> Optional[float]:
        """P(focused) now minus P(focused) `lookback` s ago (the last point at or before it);
        None without HMM estimates covering the lookback."""
        upto = [(t, p) for t, p in self.points if t <= now + 1e-9]      # never a point after `now`
        if not upto:
            return None
        before = [p for t, p in upto if t <= now - lookback]
        if not before:
            return None
        return round(upto[-1][1] - before[-1], 3)


def hour_of(t_wall: Optional[str]) -> Optional[float]:
    """The local hour of day (21.5 = 21:30) from a logged t_wall ("2026-10-15T21:30:12.345+02:00")."""
    if not t_wall or len(t_wall) < 16:
        return None
    try:
        return round(int(t_wall[11:13]) + int(t_wall[14:16]) / 60.0, 2)
    except ValueError:
        return None


def snapshot(context: Mapping[str, Any], *, level: int, t_wall: Optional[str] = None) -> Dict[str, Any]:
    """The bandit's context, from a decision's logged context: only what was known then."""
    probs = context.get("probs") if isinstance(context.get("probs"), Mapping) else None

    def minutes(key: str) -> Optional[float]:
        v = context.get(key)
        return None if v is None else round(float(v) / 60.0, 2)

    return {
        "state": context.get("state"),
        "most_likely": context.get("most_likely"),
        "confidence": context.get("confidence"),
        "uncertainty": context.get("uncertainty"),
        **{f"p_{s}": (None if probs is None else probs.get(s)) for s in BELIEF_STATES},
        "focused_trend_20s": context.get("focused_trend_20s"),
        "pending": context.get("pending"),
        "estimator": context.get("estimator"),
        "calibrated": context.get("calibrated"),
        "ladder": context.get("ladder"),
        "level": level,
        "previous_level": context.get("level"),
        "last_outcome": context.get("last_outcome"),
        "hour": hour_of(t_wall),
        "minutes_in_session": minutes("session_seconds"),
        "minutes_since_break": minutes("since_break"),
        "minutes_since_last_nudge": minutes("since_last_nudge"),
        "nudges_session": context.get("nudges_session"),
        "nudges_last_hour": context.get("nudges_last_hour"),
    }
