"""The scripted state scenario of the self-test (python -m eepy.perception.selftest
--states): work, a phone, back to work, act sleepy, leave -- and which state the
rules (and the HMM, A16) showed in each part, and how long each switch took.

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
    Phase("Phone", "Look at an imaginary PHONE beside you (turn your head to the side), and keep looking.", 20,
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
    belief_after: Optional[float] = None    # HMM: first P(expected states) >= 0.5, seconds after the start

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
        belief = next((e.t for e in inside if e.confidence is not None
                       and sum(e.scores.get(s, 0.0) for s in phase.expected) >= 0.5), None)
        out.append(PhaseResult(phase, start, end, per, None if hit is None else round(hit - start, 1),
                               None if belief is None else round(belief - start, 1)))
    return out


def report(results: Sequence[PhaseResult], estimates: Sequence[StateEstimate], t0: float,
           title: str = "User state (A13 rules)") -> List[str]:
    lines = ["", f"  {title}: scenario results", "  " + "-" * 78]
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


def _verdict(r: PhaseResult) -> str:
    expected = "/".join(x.replace("_", " ") for x in r.phase.expected)
    return f"PASS {r.reached_after:3.0f} s" if r.passed else f"CHECK never {expected}"


def report_both(rules: Sequence[PhaseResult], hmm: Sequence[PhaseResult], rules_est: Sequence[StateEstimate],
                hmm_est: Sequence[StateEstimate], t0: float, every: float = 5.0) -> List[str]:
    """The rules and the HMM side by side: a verdict per phase, then the belief timeline
    every `every` seconds and at every change of either estimator. ASCII only."""
    lines = ["", "  User state: rules (A13) and HMM (A16) on the same pictures", "  " + "-" * 92,
             f"  {'part':16} {'expected':22} {'rules':18} {'HMM':18} HMM: P >= 50 %"]
    for n, (r, h) in enumerate(zip(rules, hmm), start=1):
        expected = "/".join(x.replace("_", " ") for x in r.phase.expected)
        belief = f"after {h.belief_after:.0f} s" if h.belief_after is not None else "never"
        lines.append(f"  {n}. {r.phase.title:13} {expected:22} {_verdict(r):18} {_verdict(h):18} {belief}")
    sleepy = next((h for h in hmm if "drowsy" in h.phase.expected and not h.passed), None)
    if sleepy is not None:
        lines.append(f"  Note: the HMM needs more eye signs than the rules before it says drowsy (on "
                     f"simulated data ~40 s of the {sleepy.phase.seconds:.0f} s); a CHECK in "
                     f'"{sleepy.phase.title}" is this known difference (docs/A16_HMM_DECISIONS.md, A3).')
    lines += ["  " + "-" * 92, "  Belief timeline (seconds from the start):",
              f"  {'t':>7}  {'rules':13} {'hmm':13} {'focused':>7} {'distr.':>7} {'drowsy':>7} {'v.drowsy':>8}"
              "  top reason (hmm)"]
    rules_at = {round(e.t, 3): e for e in rules_est}
    next_t, last = None, (None, None)
    for e in hmm_est:
        r = rules_at.get(round(e.t, 3))
        pair = (r.state if r else None, e.state)
        due = next_t is None or e.t - t0 >= next_t
        if not due and pair == last:
            continue
        if due:
            next_t = (int((e.t - t0) // every) + 1) * every
        last = pair
        reason = e.reasons[0].text if e.reasons else ""
        reason = reason.encode("ascii", "replace").decode("ascii")
        lines.append(f"  {e.t - t0:7.1f}  {(pair[0] or '-'):13} {e.state:13} "
                     + " ".join(f"{e.scores.get(s, 0.0):7.0%}" for s in ("focused", "distracted", "drowsy"))
                     + f" {e.scores.get('very_drowsy', 0.0):8.0%}  {reason[:60]}")
    return lines
