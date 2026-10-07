"""The scripted state scenario of the self-test (python -m eepy.perception.selftest
--states): work, a phone, back to work, act sleepy, leave -- and which state the
rules showed in each part, and how long each switch took.

The phases and the report live here (no camera, no windows) so the tests can
check the report on made-up estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .estimate import StateEstimate


@dataclass(frozen=True)
class Phase:
    title: str
    instruction: str
    seconds: float
    expected: Tuple[str, ...]
    note: str = ""


SCENARIO: Tuple[Phase, ...] = (
    Phase("Work normally", "Work as usual: read or type, look at the screen and your notes.", 40,
          ("focused",), "starts as unknown: ~5 s of data + 5 s dwell"),
    Phase("Phone", "Look down at an imaginary PHONE in your lap, and keep looking at it.", 20,
          ("distracted",), "5 s looking away + 5 s dwell"),
    Phase("Back to work", "Back to work: look at the screen as usual.", 20, ("focused",), "5 s dwell"),
    Phase("Act sleepy", "Act sleepy: close your eyes slowly for about a second, every few "
          "seconds, and yawn once or twice.", 45, ("drowsy", "very_drowsy"),
          "builds up over the 60 s window, then 10 s dwell (3 s for very drowsy)"),
    Phase("Leave", "Leave the camera's view (stand up or move aside) for 30 seconds -- "
          "count to 30, then come back.", 30, ("absent",),
          "8 of 10 s without a face, + 5 s dwell; leaving drowsy first takes 20 s"),
)


@dataclass
class PhaseResult:
    phase: Phase
    start: float
    end: float
    seconds_per_state: Dict[str, float]
    reached_after: Optional[float]          # seconds from the phase start to an expected state

    @property
    def passed(self) -> bool:
        return self.reached_after is not None


def judge(phases: Sequence[Tuple[Phase, float, float]], estimates: Sequence[StateEstimate]) -> List[PhaseResult]:
    """phases: (phase, start, end) on the estimates' clock."""
    out: List[PhaseResult] = []
    for phase, start, end in phases:
        inside = [e for e in estimates if start <= e.t <= end]
        per: Dict[str, float] = {}
        for a, b in zip(inside, inside[1:]):
            per[a.state] = per.get(a.state, 0.0) + (b.t - a.t)
        hit = next((e.t for e in inside if e.state in phase.expected), None)
        out.append(PhaseResult(phase, start, end, per, None if hit is None else round(hit - start, 1)))
    return out


def report(results: Sequence[PhaseResult], estimates: Sequence[StateEstimate], t0: float) -> List[str]:
    lines = ["", "  User state (A13 rules): scenario results", "  " + "-" * 78]
    for n, r in enumerate(results, start=1):
        shown = ", ".join(f"{s.replace('_', ' ')} {v:.0f} s" for s, v in
                          sorted(r.seconds_per_state.items(), key=lambda kv: -kv[1])) or "nothing"
        expected = "/".join(s.replace("_", " ") for s in r.phase.expected)
        verdict = (f"PASS  {expected} after {r.reached_after:.0f} s" if r.passed
                   else f"CHECK {expected} never shown")
        lines.append(f"  {n}. {r.phase.title:14} expected {expected:22} {verdict}")
        lines.append(f"     shown: {shown}   ({r.phase.note})")
    lines += ["  " + "-" * 78, "  State changes (seconds from the start):"]
    last = None
    for e in estimates:
        if e.state != last:
            flag = "" if e.calibrated else "  [uncalibrated]"
            lines.append(f"    {e.t - t0:6.1f} s  {e.state:12} {e.reason_text}{flag}")
            last = e.state
    return lines
