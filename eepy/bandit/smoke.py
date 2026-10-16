"""The bandit's smoke simulation (A24): can it find each persona's best nudge?

    python -m eepy.bandit.smoke                      # 4 personas x 30 evenings, bandit vs fixed escalation
    python -m eepy.bandit.smoke --sessions 60 --persona needs_words
    python -m eepy.bandit.smoke --json               # for the launcher's Bandit tab

Each persona (eepy/bandit/personas.yaml, MADE-UP numbers) has hidden preferences. Every simulated
evening has a distracted spell and a drowsy spell; evenings alternate between an early and a late
start (both bedtime buckets) and a short and a long work stretch (both refocus buckets). The bandit
keeps learning across evenings (as with a real participant's saved posteriors); the fixed
escalation is the comparison, on the same users and seeds.

Reported per persona and policy: total and mean reward, nudges, false nudges (the user was really
focused, or the outcome was interrupted focus), and per bucket whether the bandit's favourite is the
persona's best level. A short check; the full harness and learning curves are A25/A26.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

EVENINGS = [  # (timeline, start hour): early/late bedtime x short/long work
    ("focused 16m, distracted 6m, focused 3m, drowsy 14m", 21.5),
    ("focused 48m, distracted 6m, focused 3m, drowsy 14m", 22.5),
    ("focused 16m, distracted 6m, focused 3m, drowsy 14m", 23.2),
    ("focused 48m, distracted 6m, focused 3m, drowsy 14m", 21.0),
]


class Collector:
    """Collects every RewardEvent; optionally passes it on (to the bandit)."""

    def __init__(self, then: Any = None) -> None:
        self.events: List[Any] = []
        self.then = then

    def observe(self, event: Any) -> None:
        self.events.append(event)
        if self.then is not None:
            self.then.observe(event)


def run_persona(name: str, persona: Dict[str, Any], sessions: int, seed: int = 1) -> Dict[str, Any]:
    from eepy.interaction.config import load

    from .policy import BanditPolicy, MemoryStore, load_config
    from .simuser import run_responsive

    esc, cfg = load(), load_config()
    bandit = BanditPolicy(esc, cfg, store=MemoryStore(), participant=name, seed=seed)
    out: Dict[str, Any] = {"persona": name, "description": persona.get("description", ""), "best": persona.get("best", {})}
    choices: Dict[str, List[int]] = {}
    for label in ("fixed", "bandit"):
        totals, false_nudges, nudges, per_session = 0.0, 0, 0, []
        for i in range(sessions):
            spec, hour = EVENINGS[i % len(EVENINGS)]
            col = Collector(bandit if label == "bandit" else None)
            rec, _ = run_responsive(spec, seed=seed * 1000 + i, persona=persona, start_hour=hour,
                                    policy=bandit if label == "bandit" else None, learner=col)
            rewards = [e.reward for e in col.events if e.included]
            totals += sum(rewards)
            per_session.append(round(sum(rewards), 3))
            nudges += sum(1 for k, _, _ in rec.events if k == "nudge_decision")
            false_nudges += sum(1 for _, _, actual in rec.truth if actual == "focused")
            false_nudges += sum(1 for e in col.events if e.outcome == "interrupted_focus")
            if label == "bandit" and i >= sessions * 2 // 3:            # the last third: what it chooses now
                for k, _, f in rec.events:
                    if k == "nudge_decision" and f.get("bandit"):
                        choices.setdefault(f["bandit"]["bucket"], []).append(int(f["level"]))
        out[label] = {"total": round(totals, 2), "per_evening": round(totals / sessions, 3), "nudges": nudges,
                      "mean_per_nudge": round(totals / nudges, 3) if nudges else None,
                      "false_nudges": false_nudges, "curve": per_session}
    learned: Dict[str, Any] = {}
    view = bandit.view()
    for bucket, arms in view["buckets"].items():
        tried = [a for a in arms if a["arm"] != "wait" and a["n"] >= 2]
        fav = max(tried, key=lambda a: a["mean"]) if tried else None
        best = out["best"].get(bucket)
        level = int(fav["arm"].split(":")[1]) if fav else None
        picks = choices.get(bucket, [])
        learned[bucket] = {"favourite": level, "favourite_mean": fav["mean"] if fav else None,
                           "favourite_tries": fav["n"] if fav else 0, "best": best,
                           "found": level == best if fav and best is not None else None,
                           "late_choices": {str(lv): picks.count(lv) for lv in sorted(set(picks))},
                           "tries": sum(a["n"] for a in arms if a["arm"] != "wait")}
    out["learned"] = learned
    return out


def run(sessions: int = 30, only: Optional[str] = None, seed: int = 1) -> Dict[str, Any]:
    from .simuser import load_personas

    personas = load_personas()
    if only:
        if only not in personas:
            raise ValueError(f"no persona {only!r}; there are: {', '.join(personas)}")
        personas = {only: personas[only]}
    results = [run_persona(name, p, sessions, seed) for name, p in personas.items()]
    return {"sessions": sessions, "personas": results}


def print_report(rep: Dict[str, Any]) -> None:
    print(f"\n  Bandit smoke simulation: {rep['sessions']} evenings per persona (made-up personas)\n")
    for r in rep["personas"]:
        f, b = r["fixed"], r["bandit"]
        print(f"  {r['persona']}: {r['description']}")
        print(f"    {'':8} {'total':>8} {'/evening':>9} {'nudges':>7} {'/nudge':>7} {'false':>6}")
        for name, x in (("fixed", f), ("bandit", b)):
            print(f"    {name:8} {x['total']:+8.2f} {x['per_evening']:+9.3f} {x['nudges']:7d} "
                  f"{(x['mean_per_nudge'] or 0):+7.3f} {x['false_nudges']:6d}")
        for bucket, l in r["learned"].items():
            if not l["tries"]:
                continue
            mark = {True: "found it", False: "NOT yet", None: "-"}[l["found"]]
            print(f"    {bucket:14} favourite level {l['favourite']} ({(l['favourite_mean'] or 0):+.2f}, "
                  f"{l['favourite_tries']:.0f} tries)  best {l['best']}  {mark}   last third: {l['late_choices']}")
        print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.bandit.smoke",
                                     description="Bandit vs fixed escalation on simulated personas.")
    parser.add_argument("--sessions", type=int, default=30, help="evenings per persona (default 30)")
    parser.add_argument("--persona", help="only this persona (eepy/bandit/personas.yaml)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        rep = run(max(1, args.sessions), args.persona, args.seed)
    except (ValueError, FileNotFoundError) as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep))
    else:
        print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
