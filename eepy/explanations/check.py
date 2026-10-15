"""Are the explanations faithful to the model? A diagnostic over a session (A20).

    python -m eepy.explanations.check latest                     # a recorded hmm session
    python -m eepy.explanations.check P03
    python -m eepy.explanations.check --synthetic "work 3m, sleepy 8m"

For every nudge decision with an HMM explanation:

  sum       the items + the dynamics add up to the change in log-odds (decided vs runner-up)
            over the window: |sum_error| below 1e-3 (the ledger is rounded to 6 decimals)
  deletion  the HMM re-run over the same features with the top item's evidence type left out
            during the decision window (and only then): the decided state's probability must
            drop by at least 10 points. (Not "by its whole contribution": once the belief is
            settled, evidence mostly holds it against the model's drift, and that drift itself
            depends on the belief -- see docs/A20_EXPLANATIONS.md.)

Recorded sessions use the explanations logged with each nudge_decision (the same estimates:
replay is deterministic). Sessions from before A20 have none: then the escalation is re-run
in virtual time to make them. Needs numpy (.venv-vision or .venv-robot).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SUM_TOLERANCE = 1e-3
DELETION_MIN_DROP = 0.10      # probability points


def _decisions_from_log(folder: Path) -> List[Tuple[float, Dict[str, Any]]]:
    from eepy.state.replay import _records

    out = []
    for path in sorted(folder.glob("events*.jsonl")):
        for r in _records(path):
            if r.get("type") == "nudge_decision" and isinstance(r.get("explanation"), dict):
                out.append((float(r["t_mono"]), r["explanation"]))
    return out


def _decisions_by_engine(estimates: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
    from eepy.interaction.simulate import run_estimates

    recorder, _, _ = run_estimates(estimates, [])
    return [(t, f["explanation"]) for kind, t, f in recorder.events
            if kind == "nudge_decision" and isinstance(f.get("explanation"), dict)]


def _probability(estimate: Any, explanation: Dict[str, Any]) -> Tuple[float, float]:
    """P(decided family) and log(b_decided / b_runner_up) for the hidden states the
    explanation compared, from an estimate's ledger."""
    led = estimate.ledger
    states, logb = led["states"], led["logb"]
    d, r = (states.index(s) for s in explanation["checks"]["compared"])
    family = ("drowsy", "very_drowsy") if explanation["ladder"] == "bedtime" else ("distracted",)
    p = sum(math.exp(v) for s, v in zip(states, logb) if s in family)
    return p, logb[d] - logb[r]


def _at(estimates: Sequence[Any], t: float) -> Any:
    best = None
    for e in estimates:
        if e.t <= t + 1e-6:
            best = e
        else:
            break
    return best


def run_check(features: List[Dict[str, Any]], decisions: List[Tuple[float, Dict[str, Any]]],
              params: Any = None, prior: Any = None) -> List[Dict[str, Any]]:
    from eepy.state import make_estimator
    from eepy.state.replay import run
    from eepy.state.runner import estimator_gap

    def replay(muted: Optional[str] = None, window: Tuple[float, float] = (0.0, -1.0)) -> List[Any]:
        est = make_estimator("hmm", params=params) if params is not None else make_estimator("hmm")
        if prior is not None:
            est.set_session_prior(prior)
        if muted is None:
            return run(features, est, estimator_gap(est))
        original = est.update

        def update(f: Dict[str, Any]) -> Any:
            est.muted = frozenset([muted]) if window[0] <= float(f["t"]) <= window[1] else frozenset()
            return original(f)
        est.update = update                         # type: ignore[assignment]
        return run(features, est, estimator_gap(est))

    full = replay()
    rows = []
    for t, x in decisions:
        if x.get("mode") != "hmm" or not x.get("items"):
            continue
        top = x["items"][0]
        start = float(x["window"]["start"])
        base = _at(full, t)
        cut = _at(replay(top["type"], (start, t)), t)
        if base is None or cut is None or base.ledger is None:
            continue
        p0, lo0 = _probability(base, x)
        p1, lo1 = _probability(cut, x)
        drop = lo0 - lo1
        rows.append({
            "t": t, "ladder": x["ladder"], "level": x["level"], "state": x["state"],
            "runner_up": x["runner_up"], "top": top["type"], "contribution": top["contribution"],
            "share": top.get("share"), "sum_error": x["checks"]["sum_error"],
            "p": round(p0, 4), "p_without_top": round(p1, 4), "log_odds_drop": round(drop, 3),
            "sum_ok": abs(x["checks"]["sum_error"]) < SUM_TOLERANCE,
            "drop_per_contribution": round(drop / top["contribution"], 3) if top["contribution"] else None,
            "deletion_ok": p0 - p1 >= DELETION_MIN_DROP,
        })
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.explanations.check",
                                     description="Are the explanations faithful? Sum and deletion test.")
    parser.add_argument("session", nargs="?", help='a session (folder name, part of it, or "latest")')
    parser.add_argument("--synthetic", help='simulated faces instead, e.g. "work 3m, sleepy 8m"')
    parser.add_argument("--data", help="the data folder (default data/)")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    args = parser.parse_args(argv)
    if bool(args.session) == bool(args.synthetic):
        print("\n  Give a session (e.g. latest) or --synthetic.", file=sys.stderr)
        return 2
    prior = None
    try:
        if args.synthetic:
            from eepy.interaction.simulate import synthetic_estimates
            from eepy.state.synthetic import simulate

            estimates, t0 = synthetic_estimates(args.synthetic)
            from eepy.interaction.simulate import _seconds
            phases = []
            for part in [p.strip() for p in args.synthetic.split(",") if p.strip()]:
                kind, _, length = part.partition(" ")
                phases.append((kind, int(_seconds(length.strip()) or 0)))
            features = simulate(phases, seed=1, aus=True, t0=t0)
            decisions = _decisions_by_engine(estimates)
            title = f"synthetic: {args.synthetic}"
        else:
            from eepy.logs.check import resolve
            from eepy.logs.features import read_prior
            from eepy.logs.session import DATA_DIR
            from eepy.state.hmm_params import load
            from eepy.state.hmm_prior import resolve as resolve_prior
            from eepy.state.replay import session_features
            from eepy.state.runner import panel_view

            folder = resolve(args.session, Path(args.data) / "sessions" if args.data else DATA_DIR)
            features = session_features(folder)
            if not features:
                raise ValueError(f"{folder.name} has no face features")
            prior = resolve_prior(read_prior(folder), load())
            decisions = _decisions_from_log(folder)
            if not decisions:
                from eepy.state import make_estimator
                from eepy.state.replay import run
                from eepy.state.runner import estimator_gap

                est = make_estimator("hmm")
                est.set_session_prior(prior)
                decisions = _decisions_by_engine([dict(panel_view(e)) for e in run(features, est, estimator_gap(est))])
                title = f"{folder.name} (no logged explanations: the escalation re-run in virtual time)"
            else:
                title = f"{folder.name} ({len(decisions)} logged explanations)"
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    rows = run_check(features, decisions, prior=prior)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0 if all(r["sum_ok"] and r["deletion_ok"] for r in rows) else 1
    print(f"\n  Faithfulness: {title}\n")
    if not rows:
        print("  No hmm nudge decisions with evidence to check.\n")
        return 0
    print(f"  {'decision':22} {'top evidence':14} {'share':>6} {'sum err':>9} {'P':>6} {'P w/o top':>9} "
          f"{'drop':>6}  ok")
    for r in rows:
        print(f"  {r['ladder']} {r['level']} {r['state']:11} {r['top']:14} {r['share'] or 0:6.0%} "
              f"{r['sum_error']:+9.1e} {r['p']:6.0%} {r['p_without_top']:9.0%} {r['log_odds_drop']:6.2f}  "
              f"{'yes' if r['sum_ok'] and r['deletion_ok'] else 'NO'}")
    bad = [r for r in rows if not (r["sum_ok"] and r["deletion_ok"])]
    print(f"\n  {len(rows) - len(bad)} of {len(rows)} decisions pass (sum within {SUM_TOLERANCE:g}; removing the "
          f"top evidence lowers P(decided) by >= {DELETION_MIN_DROP:.0%} points).\n")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
