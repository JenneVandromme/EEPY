"""Every recorded session, one line each.

    python -m eepy.logs.list
    python -m eepy.logs.list --participant P03 --mode wizard

Standard library only (no pandas), so it works in any environment.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .check import check_session, find_sessions, stale_pointer
from .session import DATA_DIR


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.logs.list",
                                     description="List every recorded session.")
    parser.add_argument("--participant", help="only this participant")
    parser.add_argument("--mode", help="only this mode")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    folders = find_sessions(args.data_dir)
    if not folders:
        print(f"No sessions yet in {args.data_dir}.")
        return 0
    rows = []
    for folder in folders:
        result = check_session(folder)
        meta = result.meta
        if args.participant and meta.get("participant") != args.participant:
            continue
        if args.mode and meta.get("mode") != args.mode:
            continue
        rows.append((result.name, meta.get("participant") or "?", meta.get("mode") or "?",
                     (meta.get("started") or {}).get("t_wall", "?")[11:16],
                     _duration(result.duration_s), result.events, result.annotations,
                     result.feature_rows, result.status))
    header = ("session", "who", "mode", "start", "length", "events", "marks", "features", "status")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    line = lambda r: "  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths))  # noqa: E731
    print(line(header))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print(line(row))
    bad = [r for r in rows if r[-1] != "OK"]
    print(f"\n  {len(rows)} session(s)" + (f", {len(bad)} need a look -- run "
                                         "python -m eepy.logs.check" if bad else ", all OK"))
    stale = stale_pointer(args.data_dir)
    if stale:
        print(f"  Note: {stale} was still recording when its program stopped (a crash).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
