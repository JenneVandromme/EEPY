"""Is a session's data complete and intact?

    python -m eepy.logs.check                     # every session
    python -m eepy.logs.check 2026-10-06_P03_wizard

Every session gets one of three verdicts:

    OK          complete and intact.
    INCOMPLETE  usable, but it did not end cleanly -- the program crashed or was
                killed, so there is no session_end and the last second or so
                may be missing. Everything up to that point loads normally.
    BROKEN      something is wrong that the loader cannot paper over: a missing
                or unreadable file, lines missing from the middle, a file
                written by a newer version. Look before using it.

Standard library only, so it runs anywhere -- including the vision virtualenv.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import EVENT_TYPES, SCHEMA_VERSION
from .session import CURRENT_FILE, DATA_DIR, _sha256

# Events are written in the order they ARRIVE, which can differ slightly from
# the order they HAPPENED (the player's events come through a separate thread;
# a command is written once accepted but stamped with the moment of the press).
# A few milliseconds back is normal. More than this suggests a clock problem.
BACKWARDS_TOLERANCE_S = 2.0


@dataclass
class Problem:
    severity: str          # "error", "incomplete" or "warning"
    file: str
    message: str


@dataclass
class CheckResult:
    name: str
    folder: Path
    status: str = "OK"
    problems: List[Problem] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    events: int = 0
    annotations: int = 0
    feature_rows: int = 0
    duration_s: Optional[float] = None

    def add(self, severity: str, file: str, message: str) -> None:
        self.problems.append(Problem(severity, file, message))


def _read_jsonl(path: Path, result: CheckResult, label: str) -> List[Dict[str, Any]]:
    """Every readable line. A bad LAST line is a crash; a bad middle line is not."""
    records: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        lines = handle.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()            # the file ends with a newline, as it should
        truncated_tail = False
    else:
        truncated_tail = bool(lines)
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("not an object")
        except ValueError:
            if number == len(lines):
                result.add("incomplete", label, f"line {number} was cut off part-way through "
                           "writing (the program stopped mid-write); it is skipped.")
            else:
                result.add("error", label, f"line {number} is not valid JSON.")
            continue
        if number == len(lines) and truncated_tail:
            result.add("warning", label, "the file does not end with a newline.")
        records.append(record)
    return records


def _check_events(records: List[Dict[str, Any]], result: CheckResult, label: str) -> None:
    seqs = [r.get("seq") for r in records]
    expected = list(range(1, len(records) + 1))
    if seqs != expected:
        missing = sorted(set(expected) - {s for s in seqs if isinstance(s, int)})
        result.add("error", label, "line numbers (seq) are not 1, 2, 3, ... -- lines are "
                   f"missing or out of place (e.g. {missing[:5] or seqs[:5]}).")
    unknown = sorted({r.get("type") for r in records if r.get("type") not in EVENT_TYPES})
    if unknown:
        result.add("warning", label, f"unknown event types {unknown} (written by a newer "
                   "version?); they load, but nothing here knows what they mean.")
    newer = [r for r in records if isinstance(r.get("v"), int) and r["v"] > SCHEMA_VERSION]
    if newer:
        result.add("error", label, f"{len(newer)} lines use schema version {newer[0]['v']}, "
                   f"newer than this code ({SCHEMA_VERSION}). Update the code first.")
    previous = None
    for record in records:
        t = record.get("t_mono")
        if not isinstance(t, (int, float)):
            result.add("error", label, f"seq {record.get('seq')} has no t_mono.")
            continue
        if previous is not None and t < previous - BACKWARDS_TOLERANCE_S:
            result.add("warning", label, f"time jumps back {previous - t:.1f} s at seq "
                       f"{record.get('seq')} -- a clock problem?")
        previous = t if previous is None else max(previous, t)


def check_session(folder: Path) -> CheckResult:
    folder = Path(folder)
    result = CheckResult(folder.name, folder)

    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        result.add("error", "meta.json", "missing.")
    else:
        try:
            result.meta = json.loads(meta_path.read_text())
        except ValueError:
            result.add("error", "meta.json", "is not valid JSON.")
    meta = result.meta
    if meta:
        if meta.get("schema_version", 0) > SCHEMA_VERSION:
            result.add("error", "meta.json", f"written by schema version "
                       f"{meta['schema_version']}, newer than this code ({SCHEMA_VERSION}).")
        if not meta.get("ended"):
            result.add("incomplete", "meta.json", "the session never ended cleanly (crash, "
                       "power cut, or the program was killed). Data up to that point is fine.")
        else:
            result.duration_s = round(meta["ended"]["t_mono"] - meta["started"]["t_mono"], 1)
        for relative, digest in (meta.get("snapshot") or {}).items():
            copy = folder / "snapshot" / relative
            if not copy.is_file():
                result.add("error", "snapshot", f"{relative} is missing.")
            elif _sha256(copy) != digest:
                result.add("error", "snapshot", f"{relative} was changed after the session.")

    events_path = folder / "events.jsonl"
    if not events_path.is_file():
        result.add("error", "events.jsonl", "missing.")
    else:
        records = _read_jsonl(events_path, result, "events.jsonl")
        _check_events(records, result, "events.jsonl")
        result.events = len(records)
        result.annotations = sum(1 for r in records if r.get("type") == "annotation")
        if records and records[0].get("type") != "session_start":
            result.add("error", "events.jsonl", "does not start with session_start.")
        if records and meta.get("ended") and records[-1].get("type") != "session_end":
            result.add("warning", "events.jsonl", "meta.json says the session ended, but "
                       "the last event is not session_end.")
        if records and meta and records[0].get("participant") not in (None, meta.get("participant")):
            result.add("error", "events.jsonl", "participant differs from meta.json.")
        if result.duration_s is None and records:
            result.duration_s = round(max(r.get("t_mono", 0) for r in records)
                                      - records[0].get("t_mono", 0), 1)

    for extra in sorted(folder.glob("events.*.jsonl")):
        records = _read_jsonl(extra, result, extra.name)
        _check_events(records, result, extra.name)
        result.events += len(records)

    for spec in sorted(folder.glob("*.json")):
        if spec.name == "meta.json":
            continue
        _check_features(folder, spec, result)

    severities = {p.severity for p in result.problems}
    result.status = ("BROKEN" if "error" in severities else
                     "INCOMPLETE" if "incomplete" in severities else "OK")
    return result


def _check_features(folder: Path, spec_path: Path, result: CheckResult) -> None:
    csv_path = spec_path.with_suffix(".csv")
    label = csv_path.name
    try:
        spec = json.loads(spec_path.read_text())
    except ValueError:
        result.add("error", spec_path.name, "is not valid JSON.")
        return
    if not csv_path.is_file():
        result.add("error", label, f"missing, though {spec_path.name} describes it.")
        return
    with csv_path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()
    complete = text.endswith("\n")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        result.add("error", label, "is empty -- not even a header.")
        return
    header, body = rows[0], rows[1:]
    if header != spec.get("columns"):
        result.add("error", label, f"its columns {header} do not match {spec_path.name} "
                   f"{spec.get('columns')}.")
        return
    bad = [n for n, row in enumerate(body, start=2) if len(row) != len(header)]
    if bad and not complete and bad == [len(body) + 1]:
        result.add("incomplete", label, f"the last row (line {bad[0]}) was cut off; it is skipped.")
        bad = []
    if bad:
        result.add("error", label, f"{len(bad)} rows have the wrong number of values "
                   f"(first at line {bad[0]}).")
    result.feature_rows += len(body) - (0 if complete else 1)
    if spec.get("stopped") is None:
        result.add("warning", spec_path.name, "the feature stream was never closed cleanly "
                   "(the vision process stopped or crashed).")


def find_sessions(data_dir: Path = DATA_DIR) -> List[Path]:
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return []
    return sorted(p for p in data_dir.iterdir() if p.is_dir() and not p.name.startswith("."))


def stale_pointer(data_dir: Path = DATA_DIR) -> Optional[str]:
    """CURRENT names a session whose program is gone: it crashed mid-session."""
    try:
        pointer = json.loads((Path(data_dir) / CURRENT_FILE).read_text())
    except (OSError, ValueError):
        return None
    try:
        os.kill(int(pointer.get("pid", -1)), 0)
        return None
    except (OSError, ValueError):
        return pointer.get("session", "?")


def clear_stale_pointer(data_dir: Path = DATA_DIR) -> None:
    if stale_pointer(data_dir):
        (Path(data_dir) / CURRENT_FILE).unlink(missing_ok=True)


def resolve(name: str, data_dir: Path = DATA_DIR) -> Path:
    """A session by folder name, a unique part of one, a path, or "latest"."""
    candidate = Path(name)
    if candidate.is_dir():
        return candidate
    sessions = find_sessions(data_dir)
    if name == "latest":
        if not sessions:
            raise FileNotFoundError(f"There are no sessions in {data_dir}.")
        return max(sessions, key=lambda p: p.stat().st_mtime)
    exact = [p for p in sessions if p.name == name]
    if exact:
        return exact[0]
    partial = [p for p in sessions if name in p.name]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise FileNotFoundError(f'"{name}" matches {len(partial)} sessions: '
                                + ", ".join(p.name for p in partial[:6]))
    raise FileNotFoundError(f'No session matches "{name}" in {data_dir}. '
                            "Run: python -m eepy.logs.list")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.logs.check",
                                     description="Check sessions are complete and intact.")
    parser.add_argument("sessions", nargs="*", help="session names (default: all)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)
    try:
        folders = [resolve(s, args.data_dir) for s in args.sessions] or find_sessions(args.data_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not folders:
        print(f"No sessions in {args.data_dir}.")
        return 0
    worst = 0
    for folder in folders:
        result = check_session(folder)
        mark = {"OK": "OK        ", "INCOMPLETE": "INCOMPLETE", "BROKEN": "BROKEN    "}[result.status]
        print(f"  {mark}  {result.name}")
        for problem in result.problems:
            print(f"              {problem.severity:10s} {problem.file}: {problem.message}")
        worst = max(worst, {"OK": 0, "INCOMPLETE": 0, "BROKEN": 1}[result.status])
    stale = stale_pointer(args.data_dir)
    if stale:
        print(f"\n  Note: data/sessions/CURRENT still points at {stale}, whose program is no "
              "longer running -- it crashed mid-session. The next session start replaces it.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
