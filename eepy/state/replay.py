"""Replay: run a state estimator over a recorded session -- no camera needed.

    python -m eepy.state.replay latest
    python -m eepy.state.replay P03 --rules my_rules.yaml      # try other thresholds
    python -m eepy.state.replay P03 --csv states.csv           # every second, for analysis

It reads the window summaries the vision process logged (events.vision.jsonl:
face_window, gaze_window, au_window), feeds them to the estimator exactly as
live, and prints the state timeline. With the operator's markers in the session
("phone picked up", "yawn", "back on task", ...) it also shows how often the
states agreed with them. With a state.csv from the live run, it checks that the
replay gives the same states (it should, with the same rules).

Standard library + pyyaml: runs in either environment.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .estimate import StateEstimate, StateEstimator, make_estimator
from .features import WINDOWS, Features, assemble
from .runner import StateTracker

# What each operator marker should line up with: (states, from, to) seconds
# relative to the marker. The operator presses a bit after seeing it; the
# features need a few seconds; drowsy signs may come before the yawn marker.
EXPECT: Dict[str, Tuple[Tuple[str, ...], float, float]] = {
    "phone_picked_up": (("distracted",), 0, 15),
    "looked_away": (("distracted",), 0, 15),
    "back_on_task": (("focused",), 0, 20),
    "yawn": (("drowsy", "very_drowsy"), -20, 30),
    "eyes_closing": (("drowsy", "very_drowsy"), -20, 30),
    "user_went_to_bed": (("absent",), 0, 60),
}
WINDOW_TYPES = {"face_window": "face", "gaze_window": "gaze", "au_window": "aus"}
META = {"v", "seq", "t_mono", "t_wall", "type", "source"}


def _records(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record


def session_features(folder: Path, windows: Sequence[float] = WINDOWS) -> List[Features]:
    """The features of every logged window summary, in time order -- what the live
    estimator was fed."""
    moments: Dict[float, Dict[str, Dict[str, Any]]] = {}
    for record in _records(folder / "events.vision.jsonl"):
        kind = WINDOW_TYPES.get(record.get("type", ""))
        if kind is None or record.get("t_mono") is None:
            continue
        fields = {k: v for k, v in record.items() if k not in META}
        moments.setdefault(float(record["t_mono"]), {})[kind] = fields
    return [assemble(t, parts.get("face"), parts.get("gaze"), parts.get("aus"), windows)
            for t, parts in sorted(moments.items()) if "face" in parts]


def session_start(folder: Path) -> Optional[float]:
    for record in _records(folder / "events.jsonl"):
        if record.get("type") == "session_start":
            return float(record["t_mono"])
    return None


def annotations(folder: Path) -> List[Tuple[float, str]]:
    return [(float(r["t_mono"]), str(r["marker"])) for r in _records(folder / "events.jsonl")
            if r.get("type") == "annotation" and r.get("marker") and r.get("t_mono") is not None]


def run(features: Sequence[Features], estimator: StateEstimator, gap_seconds: float = 3.0,
        until: Optional[float] = None) -> List[StateEstimate]:
    """Every estimate, with the same gap filling as live."""
    out: List[StateEstimate] = []
    tracker = StateTracker(estimator, gap_seconds, listeners=[out.append])
    for item in features:
        tracker.feed(item)
    if until is not None:
        tracker.idle(until, slack=0.0)
    return out


@dataclass
class Segment:
    state: str
    start: float
    end: float
    reason: str
    calibrated: bool


def timeline(estimates: Sequence[StateEstimate]) -> List[Segment]:
    segments: List[Segment] = []
    for e in estimates:
        if segments and segments[-1].state == e.state:
            segments[-1].end = e.t
            continue
        if segments:
            segments[-1].end = e.t
        segments.append(Segment(e.state, e.t, e.t, e.reason_text, e.calibrated))
    return segments


def state_at(estimates: Sequence[StateEstimate], t: float) -> Optional[str]:
    """The state shown at time t (the newest estimate at or before t)."""
    found = None
    for e in estimates:
        if e.t > t:
            break
        found = e.state
    return found


@dataclass
class Agreement:
    marker: str
    expected: Tuple[str, ...]
    count: int = 0
    agreed: int = 0
    delays: List[float] = field(default_factory=list)


def agreement(estimates: Sequence[StateEstimate], markers: Sequence[Tuple[float, str]]) -> Dict[str, Agreement]:
    """Per marker type: in how many cases the expected state was shown within the
    marker's time range, and how long after the marker it first was."""
    out: Dict[str, Agreement] = {}
    for t, marker in markers:
        if marker not in EXPECT:
            continue
        states, before, after = EXPECT[marker]
        item = out.setdefault(marker, Agreement(marker, states))
        item.count += 1
        hits = [e.t for e in estimates if t + before <= e.t <= t + after and e.state in states]
        if hits:
            item.agreed += 1
            later = [h for h in hits if h >= t]
            item.delays.append(round((later[0] if later else hits[0]) - t, 1))
    return out


def live_states(folder: Path) -> List[Tuple[float, str]]:
    path = folder / "state.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [(float(row["t_mono"]), row["state"]) for row in csv.DictReader(f) if row.get("state")]


def compare_live(estimates: Sequence[StateEstimate], live: Sequence[Tuple[float, str]]) -> Tuple[int, int]:
    """(same, compared): replayed vs. live states at the live rows' times."""
    replayed = {round(e.t, 3): e.state for e in estimates}
    pairs = [(replayed.get(round(t, 3)), state) for t, state in live]
    compared = [(a, b) for a, b in pairs if a is not None]
    return sum(1 for a, b in compared if a == b), len(compared)


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.state.replay",
                                     description="Run the state estimator over a recorded session.")
    parser.add_argument("session", help='a session folder name, a unique part of it (e.g. "P03"), or "latest"')
    parser.add_argument("--estimator", default="rules", help="which estimator (rules; the HMM from A16)")
    parser.add_argument("--rules", help="another rules file to try (default config/state_rules.yaml)")
    parser.add_argument("--csv", help="also write every second's state to this CSV file")
    parser.add_argument("--data", help="the data folder (default data/)")
    args = parser.parse_args(argv)

    from eepy.logs.check import resolve
    from eepy.logs.session import DATA_DIR

    from .config import RulesConfigError, load

    try:
        folder = resolve(args.session, Path(args.data) / "sessions" if args.data else DATA_DIR)
        if args.rules:
            from .rules import RulesEstimator

            estimator: StateEstimator = RulesEstimator(load(Path(args.rules)))
        else:
            estimator = make_estimator(args.estimator)
    except (FileNotFoundError, RulesConfigError, ValueError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    features = session_features(folder)
    if not features:
        print(f"\n  {folder.name} has no face features (events.vision.jsonl with face_window).\n"
              "  Was the vision program (python -m eepy.perception) running during the session?",
              file=sys.stderr)
        return 1
    gap = getattr(getattr(estimator, "config", None), "gap_seconds", 3.0)
    estimates = run(features, estimator, gap)
    start = session_start(folder) or features[0]["t"]

    print(f"\n  {folder.name}: {len(features)} seconds of features, estimator {estimator.name}"
          + (f" ({args.rules})" if args.rules else "") + "\n")
    print("  from   to     state         why")
    for seg in timeline(estimates):
        flag = "" if seg.calibrated else "  [uncalibrated]"
        print(f"  {_clock(seg.start - start)}  {_clock(seg.end - start)}  {seg.state:12}  {seg.reason}{flag}")

    totals: Dict[str, float] = {}
    for a, b in zip(estimates, estimates[1:]):
        totals[a.state] = totals.get(a.state, 0.0) + (b.t - a.t)
    whole = sum(totals.values()) or 1.0
    print("\n  Time per state: " + "   ".join(f"{s} {v / whole:.0%}" for s, v in
                                             sorted(totals.items(), key=lambda kv: -kv[1])))

    markers = annotations(folder)
    if markers:
        result = agreement(estimates, markers)
        print("\n  Agreement with the operator's markers:")
        if not result:
            print("    (none of the markers is about the state)")
        for item in result.values():
            states, before, after = EXPECT[item.marker]
            delays = sorted(item.delays)
            delay = f", median {delays[len(delays) // 2]:+.0f} s after the marker" if delays else ""
            print(f"    {item.marker:18} {item.agreed}/{item.count} agreed ({'/'.join(states)} "
                  f"within {before:+.0f}..{after:+.0f} s){delay}")
        other = sorted({m for _, m in markers if m not in EXPECT})
        if other:
            print(f"    (not about the state: {', '.join(other)})")
    live = live_states(folder)
    if live and not args.rules and args.estimator == "rules":
        same, compared = compare_live(estimates, live)
        if compared:
            print(f"\n  Same as the live run: {same}/{compared} seconds ({same / compared:.1%}). "
                  "Lower means the rules file changed since.")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["t", "t_mono", "state", "pending", "calibrated", "rules", "reason"])
            for e in estimates:
                writer.writerow([round(e.t - start, 3), e.t, e.state, e.pending or "", int(e.calibrated),
                                 ",".join(r.rule for r in e.reasons), e.reason_text])
        print(f"\n  Every second: {args.csv}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
