"""The bandit's saved posteriors (A24): list, show, reset.

    python -m eepy.bandit list                 # every participant with saved posteriors
    python -m eepy.bandit show P03             # what EEPY has learned for P03, per situation
    python -m eepy.bandit reset P03            # forget it (asks first; --yes to skip)
    python -m eepy.bandit show P03 --json      # for the launcher's Bandit tab

The posteriors live in data/bandit/<participant>.json: numbers only, never in git.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

NAMES = {"refocus_early": "distracted, early in the work", "refocus_late": "distracted, after long work",
         "bedtime_early": "drowsy, before 23:00", "bedtime_late": "drowsy, late", "ambiguous": "distracted or drowsy"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.bandit", description="The bandit's saved posteriors.")
    parser.add_argument("command", choices=("list", "show", "reset"))
    parser.add_argument("participant", nargs="?")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--yes", action="store_true", help="reset without asking")
    args = parser.parse_args(argv)

    from eepy.interaction.config import load as load_escalation

    from .policy import BanditPolicy, Store, load_config

    store = Store()
    if args.command == "list":
        people = []
        for name in store.participants():
            data = store.load(name)
            people.append({"participant": name, "updates": data.get("updates", 0), "updated": data.get("updated")})
        if args.json:
            print(json.dumps({"participants": people, "folder": str(store.folder)}))
        else:
            print(f"\n  Saved bandit posteriors in {store.folder}:\n")
            for p in people or [{"participant": "(none yet)", "updates": 0, "updated": ""}]:
                print(f"    {p['participant']:12} {p['updates']:4d} updates   {p['updated'] or ''}")
            print()
        return 0
    if not args.participant:
        print("\n  Which participant? e.g. python -m eepy.bandit show P03\n", file=sys.stderr)
        return 2
    if args.command == "reset":
        if not args.yes and not args.json:
            if input(f"  Forget everything the bandit learned for {args.participant}? [y/N] ").strip().lower() != "y":
                print("  Kept.")
                return 0
        done = store.reset(args.participant)
        message = f"Reset {args.participant}." if done else f"{args.participant} had nothing saved."
        print(json.dumps({"ok": True, "message": message}) if args.json else f"  {message}")
        return 0
    policy = BanditPolicy(load_escalation(), load_config(), store=store, participant=args.participant)
    view = policy.view()
    if args.json:
        print(json.dumps(view))
        return 0
    print(f"\n  Bandit: {args.participant} ({view['updates']} updates, model {view['model']})\n")
    for bucket, arms in view["buckets"].items():
        print(f"  {NAMES.get(bucket, bucket)}")
        for a in arms:
            bar_at = int(round((a["mean"] + 1) * 10))
            bar = "".join("|" if i == 10 else ("#" if i == bar_at else ".") for i in range(21))
            print(f"    {a['words']:28} {a['mean']:+.2f} ± {a['sd']:.2f}  {bar}  {a['n']:.0f} tries")
        if view["sentences"].get(bucket):
            print(f"    -> {view['sentences'][bucket]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
