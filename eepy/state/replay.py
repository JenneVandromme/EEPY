"""Replay: run a state estimator over a recorded session -- no camera needed.

    python -m eepy.state.replay latest
    python -m eepy.state.replay P03 --rules my_rules.yaml      # try other thresholds
    python -m eepy.state.replay P03 --csv states.csv           # every second, for analysis
    python -m eepy.state.replay latest --estimator both        # rules and HMM side by side (A16)
    python -m eepy.state.replay P03 --estimator hmm --params my_hmm.yaml --prior none

It reads the window summaries the vision process logged (events.vision.jsonl:
face_window, gaze_window, au_window), feeds them to the estimator exactly as
live, and prints the state timeline. With the operator's markers in the session
("phone picked up", "yawn", "back on task", ...) it also shows how often the
states agreed with them. With a state.csv from the live run, it checks that the
replay gives the same states (it should, with the same rules).

Standard library + pyyaml: runs in either environment (the HMM also needs numpy).
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


def run(features: Sequence[Features], estimator: StateEstimator, gap_seconds: Optional[float] = None,
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


def live_rows(folder: Path) -> List[Dict[str, str]]:
    path = folder / "state.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("state")]


def live_states(folder: Path) -> List[Tuple[float, str]]:
    return [(float(row["t_mono"]), row["state"]) for row in live_rows(folder)]


def live_estimator(folder: Path) -> str:
    """Which estimator ran live (state.csv's estimator column; older files: the rules)."""
    rows = live_rows(folder)
    return (rows[-1].get("estimator") or "rules") if rows else "rules"


def compare_live(estimates: Sequence[StateEstimate], live: Sequence[Tuple[float, str]]) -> Tuple[int, int]:
    """(same, compared): replayed vs. live states at the live rows' times."""
    replayed = {round(e.t, 3): e.state for e in estimates}
    pairs = [(replayed.get(round(t, 3)), state) for t, state in live]
    compared = [(a, b) for a, b in pairs if a is not None]
    return sum(1 for a, b in compared if a == b), len(compared)


def belief_difference(estimates: Sequence[StateEstimate], rows: Sequence[Dict[str, str]]) -> Optional[float]:
    """The largest difference between the replayed and the live probabilities (HMM)."""
    replayed = {round(e.t, 3): e for e in estimates}
    worst: Optional[float] = None
    for row in rows:
        e = replayed.get(round(float(row["t_mono"]), 3))
        if e is None:
            continue
        for s in HIDDEN4:
            if row.get(f"p_{s}"):
                d = abs(float(row[f"p_{s}"]) - e.scores.get(s, 0.0))
                worst = d if worst is None else max(worst, d)
    return worst


HIDDEN4 = ("focused", "distracted", "drowsy", "very_drowsy")


@dataclass
class Pair:
    start: float
    end: float
    a: StateEstimate                # the first estimate of the stretch (rules)
    b: StateEstimate                # (hmm)


def _paired(a: Sequence[StateEstimate], b: Sequence[StateEstimate]) -> List[Tuple[StateEstimate, StateEstimate]]:
    other = {round(e.t, 3): e for e in b}
    return [(x, other[round(x.t, 3)]) for x in a if round(x.t, 3) in other]


def side_by_side(a: Sequence[StateEstimate], b: Sequence[StateEstimate]) -> List[Pair]:
    """Stretches where both estimators keep their state."""
    out: List[Pair] = []
    for x, y in _paired(a, b):
        if out and out[-1].a.state == x.state and out[-1].b.state == y.state:
            out[-1].end = x.t
            continue
        if out:
            out[-1].end = x.t
        out.append(Pair(x.t, x.t, x, y))
    return out


def disagreements(a: Sequence[StateEstimate], b: Sequence[StateEstimate], top: int = 10) -> List[Pair]:
    """The stretches where they disagree, longest first."""
    pairs = [p for p in side_by_side(a, b) if p.a.state != p.b.state]
    return sorted(pairs, key=lambda p: -(p.end - p.start))[:top]


def same_seconds(a: Sequence[StateEstimate], b: Sequence[StateEstimate]) -> Tuple[int, int]:
    paired = _paired(a, b)
    return sum(1 for x, y in paired if x.state == y.state), len(paired)


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _sure(e: StateEstimate) -> str:
    return f"{e.confidence:4.0%}" if e.confidence is not None and e.state not in ("unknown", "absent") else "    "


def _totals(estimates: Sequence[StateEstimate]) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for a, b in zip(estimates, estimates[1:]):
        totals[a.state] = totals.get(a.state, 0.0) + (b.t - a.t)
    whole = sum(totals.values()) or 1.0
    return {k: v / whole for k, v in totals.items()}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.state.replay",
                                     description="Run the state estimator over a recorded session.")
    parser.add_argument("session", help='a session folder name, a unique part of it (e.g. "P03"), or "latest"')
    parser.add_argument("--estimator", choices=("rules", "hmm", "both"), default="rules",
                        help="which estimator: rules (default), hmm, or both side by side")
    parser.add_argument("--rules", help="another rules file to try (default config/state_rules.yaml)")
    parser.add_argument("--params", help="another HMM parameter file to try (default config/state_hmm.yaml)")
    parser.add_argument("--prior", help="the HMM's session prior: a JSON file, or none "
                                        "(default: the session's prior.json, if any)")
    parser.add_argument("--csv", help="also write every second's state to this CSV file")
    parser.add_argument("--data", help="the data folder (default data/)")
    args = parser.parse_args(argv)
    use_rules, use_hmm = args.estimator in ("rules", "both"), args.estimator in ("hmm", "both")
    for flag, ok, what in (("--params", use_hmm, "the HMM"), ("--prior", use_hmm, "the HMM"),
                           ("--rules", use_rules, "the rules")):
        if getattr(args, flag[2:]) and not ok:
            print(f"\n  {flag} is for {what}: add --estimator {'hmm' if what == 'the HMM' else 'rules'} "
                  "or both", file=sys.stderr)
            return 2

    from eepy.logs.check import resolve
    from eepy.logs.features import read_prior
    from eepy.logs.session import DATA_DIR

    from .config import RulesConfigError, load
    from .hmm_params import HMMConfigError

    rules_est: Optional[StateEstimator] = None
    hmm_est: Any = None
    prior_text = ""
    try:
        folder = resolve(args.session, Path(args.data) / "sessions" if args.data else DATA_DIR)
        if use_rules:
            from .rules import RulesEstimator

            rules_est = RulesEstimator(load(Path(args.rules)) if args.rules else None)
        if use_hmm:
            from .hmm_params import load as load_hmm
            from .hmm_prior import resolve as resolve_prior

            params = load_hmm(Path(args.params) if args.params else None)
            hmm_est = make_estimator("hmm", params=params)
            if args.prior and args.prior.lower() == "none":
                raw_prior = None
            elif args.prior:
                raw_prior = read_prior_file(Path(args.prior))
            else:
                raw_prior = read_prior(folder)
            applied = resolve_prior(raw_prior, params)
            hmm_est.set_session_prior(applied)
            prior_text = applied.source + (f" ({applied.reason})" if applied.fell_back else "")
    except (FileNotFoundError, RulesConfigError, HMMConfigError, ValueError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    features = session_features(folder)
    if not features:
        print(f"\n  {folder.name} has no face features (events.vision.jsonl with face_window).\n"
              "  Was the vision program (python -m eepy.perception) running during the session?",
              file=sys.stderr)
        return 1
    from .runner import estimator_gap

    rules_out = run(features, rules_est, estimator_gap(rules_est)) if rules_est is not None else []
    hmm_out = run(features, hmm_est, estimator_gap(hmm_est)) if hmm_est is not None else []
    start = session_start(folder) or features[0]["t"]

    if args.estimator == "both":
        print(f"\n  {folder.name}: {len(features)} seconds of features")
        print(f"  rules: {args.rules or 'config/state_rules.yaml'}   hmm: {hmm_est.params.path} "
              f"(v{hmm_est.params.version}, {hmm_est.params.source}; prior: {prior_text})\n")
        print("  from   to     rules         hmm               hmm: why")
        for pair in side_by_side(rules_out, hmm_out):
            print(f"  {_clock(pair.start - start)}  {_clock(pair.end - start)}  {pair.a.state:12}  "
                  f"{pair.b.state:12} {_sure(pair.b)}   {pair.b.reason_text}")
        tr, th = _totals(rules_out), _totals(hmm_out)
        print("\n  Time per state       rules    hmm")
        for state in ("focused", "distracted", "drowsy", "very_drowsy", "absent", "unknown"):
            if state in tr or state in th:
                print(f"    {state:16} {tr.get(state, 0):6.0%}  {th.get(state, 0):6.0%}")
        same, compared = same_seconds(rules_out, hmm_out)
        if compared:
            print(f"  Rules and HMM agree: {same}/{compared} s ({same / compared:.1%}).")
        worst = disagreements(rules_out, hmm_out)
        if worst:
            print("  Where they disagree (longest first, at most 10):")
            for p in worst:
                print(f"    {_clock(p.start - start)}-{_clock(p.end - start)}  {p.end - p.start:3.0f} s  "
                      f"rules {p.a.state}, hmm {p.b.state} ({_sure(p.b).strip()})   "
                      f"hmm: {p.b.reason_text}   rules: {p.a.reason_text}")
    else:
        estimates = rules_out or hmm_out
        name = "rules" if rules_out else "hmm"
        extra = f" ({args.rules})" if args.rules else (
            f" ({hmm_est.params.path} v{hmm_est.params.version}; prior: {prior_text})" if hmm_out else "")
        print(f"\n  {folder.name}: {len(features)} seconds of features, estimator {name}{extra}\n")
        print("  from   to     state              why")
        for seg in timeline(estimates):
            flag = "" if seg.calibrated else "  [uncalibrated]"
            first = next(e for e in estimates if e.t >= seg.start)
            print(f"  {_clock(seg.start - start)}  {_clock(seg.end - start)}  {seg.state:12} {_sure(first)}  "
                  f"{seg.reason}{flag}")
        totals = _totals(estimates)
        print("\n  Time per state: " + "   ".join(f"{s} {v:.0%}" for s, v in
                                                 sorted(totals.items(), key=lambda kv: -kv[1])))

    markers = annotations(folder)
    if markers:
        print("\n  Agreement with the operator's markers:")
        results = [(n, agreement(o, markers)) for n, o in (("rules", rules_out), ("hmm", hmm_out)) if o]
        if not results[0][1]:
            print("    (none of the markers is about the state)")
        for marker, item in results[0][1].items():
            states, before, after = EXPECT[marker]
            within = f"{'/'.join(states)} within {before:+.0f}..{after:+.0f} s"
            if len(results) == 1:
                delays = sorted(item.delays)
                delay = f", median {delays[len(delays) // 2]:+.0f} s after the marker" if delays else ""
                print(f"    {marker:18} {item.agreed}/{item.count} agreed ({within}){delay}")
                continue
            cols = []
            for n, result in results:
                it = result[marker]
                delays = sorted(it.delays)
                delay = f", median {delays[len(delays) // 2]:+.0f} s" if delays else ""
                cols.append(f"{n} {it.agreed}/{it.count}{delay}")
            print(f"    {marker:18} {'   '.join(cols)}   ({within})")
        other = sorted({m for _, m in markers if m not in EXPECT})
        if other:
            print(f"    (not about the state: {', '.join(other)})")
    rows = live_rows(folder)
    ran = live_estimator(folder)
    replayed = rules_out if ran == "rules" else hmm_out
    changed_file = args.rules if ran == "rules" else (args.params or args.prior)
    if rows and replayed and not changed_file:
        same, compared = compare_live(replayed, [(float(r["t_mono"]), r["state"]) for r in rows])
        if compared:
            line = f"\n  Same as the live run ({ran}): {same}/{compared} seconds ({same / compared:.1%})."
            if ran == "hmm":
                diff = belief_difference(replayed, rows)
                line += f" Largest belief difference: {diff:.3f}." if diff is not None else ""
            print(line + f" Lower means the {ran} file changed since.")
    if args.csv:
        write_csv(Path(args.csv), rules_out, hmm_out, start)
        print(f"\n  Every second: {args.csv}")
    print()
    return 0


def read_prior_file(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"_error": f"cannot read {path}: {exc}"}
    return data if isinstance(data, dict) else {"_error": f"{path} is not a JSON object"}


def write_csv(path: Path, rules_out: Sequence[StateEstimate], hmm_out: Sequence[StateEstimate],
              start: float) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if rules_out and hmm_out:
            writer.writerow(["t", "t_mono", "rules_state", "rules_reason", "hmm_state", "hmm_most_likely",
                             "hmm_confidence", "hmm_pending"] + [f"p_{s}" for s in HIDDEN4]
                            + ["hmm_uncertainty", "hmm_evidence"])
            from .runner import evidence_summary

            for x, y in _paired(rules_out, hmm_out):
                writer.writerow([round(x.t - start, 3), x.t, x.state, x.reason_text, y.state, y.most_likely,
                                 y.confidence, y.pending or ""] + [y.scores.get(s, 0.0) for s in HIDDEN4]
                                + [y.uncertainty, evidence_summary(y)])
            return
        estimates = rules_out or hmm_out
        hmm = bool(hmm_out)
        writer.writerow(["t", "t_mono", "state", "pending", "calibrated", "rules", "reason"]
                        + (["confidence", "most_likely"] + [f"p_{s}" for s in HIDDEN4] if hmm else []))
        for e in estimates:
            writer.writerow([round(e.t - start, 3), e.t, e.state, e.pending or "", int(e.calibrated),
                             ",".join(r.rule for r in e.reasons), e.reason_text]
                            + ([e.confidence, e.most_likely] + [e.scores.get(s, 0.0) for s in HIDDEN4]
                               if hmm else []))


if __name__ == "__main__":
    raise SystemExit(main())
