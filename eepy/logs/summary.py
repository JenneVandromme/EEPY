"""A readable report on one session.

    python -m eepy.logs.summary 2026-10-06_P03_wizard
    python -m eepy.logs.summary P03          # a unique part of the name
    python -m eepy.logs.summary latest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.logs.summary",
                                     description="Summarise one recorded session.")
    parser.add_argument("session", help='folder name, a unique part of it, or "latest"')
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        from .load import load_session
        from .session import DATA_DIR
    except ImportError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        s = load_session(args.session, args.data_dir or DATA_DIR)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    meta, code = s.meta, s.meta.get("code") or {}
    started = (meta.get("started") or {}).get("t_wall", "?")
    ended = meta.get("ended") or {}
    print(f"\n  {s.name}   [{s.check.status}]")
    print(f"  participant {meta.get('participant')}   mode {meta.get('mode')}   "
          f"backend {meta.get('backend')}")
    print(f"  {started[:19].replace('T', ' ')}  ->  "
          f"{ended.get('t_wall', 'never ended cleanly')[:19].replace('T', ' ')}"
          f"   ({s.check.duration_s} s, ended by: {ended.get('reason', '-')})")
    dirty = "  (with uncommitted changes)" if code.get("dirty") else ""
    print(f"  code {str(code.get('commit'))[:10]} on {code.get('branch')}{dirty}")
    for note in meta.get("notes") or []:
        print(f"  note: {note}")

    events = s.events
    print(f"\n  EVENTS ({len(events)})")
    if not events.empty:
        counts = events.groupby(["type", "source"]).size().sort_values(ascending=False)
        for (kind, source), n in counts.items():
            print(f"    {n:5d}  {kind:22s} {source}")

    b = s.behaviors
    print(f"\n  BEHAVIOURS ({len(b)} runs)")
    if not b.empty:
        table = (b.groupby(["source", "outcome"]).size().unstack(fill_value=0))
        for line in table.to_string().splitlines():
            print("    " + line)

    a = s.annotations
    print(f"\n  ANNOTATIONS ({len(a)}) -- the HMM labels")
    if not a.empty:
        for marker, group in a.groupby("marker"):
            times = ", ".join(f"{t:.1f}s" for t in group["t"].head(8))
            more = " ..." if len(group) > 8 else ""
            print(f"    {len(group):4d}  {marker:18s} at {times}{more}")

    if len(s.notes):
        print(f"\n  NOTES ({len(s.notes)})")
        for row in s.notes.itertuples():
            print(f"    {row.t:7.1f}s  {row.text}")

    if s.features is not None:
        f = s.features
        rate = len(f) / (f["t"].iloc[-1] - f["t"].iloc[0]) if len(f) > 1 else float("nan")
        columns = [c for c in f.columns if c not in ("t", "t_mono", "t_wall", "frame")]
        print(f"\n  FEATURES  {len(f)} rows, about {rate:.1f} per second, columns: "
              + ", ".join(columns))

    if s.check.problems:
        print("\n  PROBLEMS")
        for p in s.check.problems:
            print(f"    {p.severity:10s} {p.file}: {p.message}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
