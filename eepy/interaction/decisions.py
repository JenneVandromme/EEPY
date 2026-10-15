"""The decision log (A23): every nudge decision with what the policy could choose from.

    python -m eepy.interaction.decisions latest                       # a recorded session
    python -m eepy.interaction.decisions --scenario takes_the_break    # a built-in or written scenario
    python -m eepy.interaction.decisions --responsive "focused 15m, distracted 6m, drowsy 15m"
    python -m eepy.interaction.decisions --synthetic "work 3m, phone 5m, sleepy 8m"

Each row: when, the state and how sure, the action chosen, whether it was inside the allowed set,
the allowed set itself (the most intrusive options marked), and what came of it. --json for the
launcher's Decisions tab.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def rows_from_events(events: Sequence[Tuple[str, float, Dict[str, Any]]], t0: float) -> List[Dict[str, Any]]:
    """(event type, t, fields) -> one row per nudge_decision, with its outcome."""
    outcomes = {str(f.get("decision_id")): f for kind, _, f in events if kind == "nudge_outcome"}
    rows = []
    for kind, t, f in events:
        if kind != "nudge_decision":
            continue
        ctx = f.get("context") or {}
        out = outcomes.get(str(f.get("decision_id"))) or {}
        rows.append({
            "t": round(t - t0, 1), "decision_id": f.get("decision_id"), "policy": f.get("policy"),
            "state": ctx.get("state"), "confidence": ctx.get("confidence"), "calibrated": ctx.get("calibrated"),
            "chosen": f"{f.get('ladder')} {f.get('level')} {f.get('tone')}", "reason": f.get("reason"),
            "allowed": f.get("allowed"), "most_intrusive": f.get("most_intrusive") or [],
            "inside": f.get("chosen_in_allowed"), "ignored": ctx.get("ignored_levels") or [],
            "outcome": out.get("outcome"), "detail": out.get("detail")})
    return rows


def session_rows(folder: Path) -> List[Dict[str, Any]]:
    events, t0 = [], None
    for line in (folder / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") == "session_start":
            t0 = r["t_mono"]
        if r.get("t_mono") is not None:
            events.append((r["type"], float(r["t_mono"]), r))
    return rows_from_events(events, t0 if t0 is not None else (events[0][1] if events else 0.0))


def print_rows(rows: List[Dict[str, Any]], title: str) -> None:
    print(f"\n  Decisions: {title}\n")
    if not rows:
        print("  No nudge decisions.\n")
        return
    for r in rows:
        m, s = divmod(int(r["t"]), 60)
        conf = "" if r["confidence"] is None else f" {r['confidence']:.0%}"
        inside = "the wind-down timer, not a policy choice" if r["policy"] == "wind_down_timer" else \
            {True: "inside", False: "OUTSIDE", None: "(not logged)"}[r["inside"]]
        print(f"  {m:02d}:{s:02d}  {r['state']}{conf}  ->  {r['chosen']}  [{inside}]   "
              f"{(r['outcome'] or 'open').replace('_', ' ')}")
        if r["allowed"]:
            marked = [f"{a}{'!' if a in r['most_intrusive'] else ''}" for a in r["allowed"]]
            print(f"         allowed: {', '.join(marked)}" + (f"   (ignored: {r['ignored']})" if r["ignored"] else ""))
    outside = sum(1 for r in rows if r["inside"] is False)
    print(f"\n  {len(rows)} decisions, {outside} outside the allowed set.  (! = most intrusive)\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.interaction.decisions",
                                     description="Every nudge decision with the allowed set next to it.")
    parser.add_argument("session", nargs="?", help='a recorded session (name, part of it, or "latest")')
    parser.add_argument("--scenario", help='a built-in scenario or a timeline, e.g. "focused 15m, distracted 5m"')
    parser.add_argument("--responsive", help="a simulated user who reacts to nudges (eepy/bandit/simuser.py)")
    parser.add_argument("--synthetic", help='simulated faces through the real HMM, e.g. "work 3m, phone 5m"')
    parser.add_argument("--seed", type=int, default=1, help="--responsive: the run")
    parser.add_argument("--data", help="the data folder (default data/)")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON (for the launcher)")
    args = parser.parse_args(argv)
    try:
        if args.session:
            from eepy.logs.check import resolve
            from eepy.logs.session import DATA_DIR

            folder = resolve(args.session, Path(args.data) / "sessions" if args.data else DATA_DIR)
            rows, title = session_rows(folder), folder.name
        elif args.scenario:
            from .simulate import SCENARIOS, parse_spec, run_scenario

            recorder, _, _ = run_scenario(SCENARIOS.get(args.scenario) or parse_spec(args.scenario))
            rows, title = rows_from_events(recorder.events, 0.0), f"scenario: {args.scenario}"
        elif args.responsive:
            from eepy.bandit.simuser import run_responsive

            recorder, t0 = run_responsive(args.responsive, seed=args.seed)
            rows, title = rows_from_events(recorder.events, t0), f"reactive user: {args.responsive}"
        elif args.synthetic:
            from .simulate import run_estimates, synthetic_estimates

            estimates, t0 = synthetic_estimates(args.synthetic)
            recorder, _, _ = run_estimates(estimates, [])
            rows, title = rows_from_events(recorder.events, t0), f"synthetic: {args.synthetic}"
        else:
            parser.print_help()
            return 2
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"title": title, "rows": rows}, default=str))
    else:
        print_rows(rows, title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
