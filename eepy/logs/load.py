"""Load sessions into pandas, for analysis.

    from eepy.logs.load import load_session, sessions_table, all_annotations

    s = load_session("2026-10-06_P03_wizard")   # or a unique part: "P03", or "latest"
    s.events        # every event, all processes merged, ordered by time
    s.annotations   # the operator's observations -- the HMM labels
    s.behaviors     # one row per behaviour run: start, end, outcome, who triggered it
    s.notes         # free-text notes made during the session
    s.features      # perception features, or None
    s.aus           # action units (one row per AU result), or None
    s.meta          # meta.json, as a dict
    s.check         # the integrity check's verdict and any problems

Every table has the same time columns:

    t        seconds since the session started -- what you usually want to plot
    t_mono   the shared monotonic clock -- for lining up with other streams
    t_wall   local date and time, timezone-aware

The only module in eepy.logs that needs pandas (pip install -r
requirements-robot.txt).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise ImportError("Loading sessions needs pandas. Activate .venv-robot, or run:\n"
                      "    pip install -r requirements-robot.txt") from exc

from .check import CheckResult, check_session, find_sessions, resolve
from .session import DATA_DIR

TIME_COLUMNS = ["t", "t_mono", "t_wall"]


@dataclass
class Session:
    name: str
    folder: Path
    meta: dict
    events: "pd.DataFrame"
    annotations: "pd.DataFrame"
    behaviors: "pd.DataFrame"
    notes: "pd.DataFrame"
    features: "pd.DataFrame | None"
    check: CheckResult
    feature_spec: dict = field(default_factory=dict)
    aus: "pd.DataFrame | None" = None   # action units, one row per AU result (optional)

    @property
    def participant(self) -> str | None:
        return self.meta.get("participant")

    @property
    def mode(self) -> str | None:
        return self.meta.get("mode")

    def __repr__(self) -> str:
        features = "none" if self.features is None else f"{len(self.features)} rows"
        return (f"<Session {self.name}: {self.check.status}, {len(self.events)} events, "
                f"{len(self.annotations)} annotations, {len(self.behaviors)} behaviours, "
                f"features {features}>")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue           # a line cut off by a crash; check.py reports it
        if isinstance(record, dict):
            out.append(record)
    return out


def _start(meta: dict, events: list[dict]) -> float | None:
    started = (meta.get("started") or {}).get("t_mono")
    if started is not None:
        return float(started)
    return min((e["t_mono"] for e in events if "t_mono" in e), default=None)


def _wall(t_mono: "pd.Series", anchor: dict) -> "pd.Series":
    """Wall-clock time from the shared clock, through a writer's anchor."""
    base = pd.Timestamp(anchor["t_wall"])
    return base + pd.to_timedelta(t_mono - float(anchor["t_mono"]), unit="s")


def _events_frame(folder: Path, meta: dict, start: float | None) -> "pd.DataFrame":
    rows: list[dict] = []
    for path in sorted(folder.glob("events*.jsonl")):
        process = "main" if path.name == "events.jsonl" else path.name.split(".")[1]
        for record in _read_jsonl(path):
            record["process"] = process
            rows.append(record)
    if not rows:
        return pd.DataFrame(columns=TIME_COLUMNS + ["type", "source", "process", "seq"])
    frame = pd.DataFrame(rows)
    frame["t_wall"] = pd.to_datetime(frame["t_wall"], format="ISO8601")
    frame["t"] = frame["t_mono"] - (start if start is not None else frame["t_mono"].min())
    frame = frame.sort_values(["t_mono", "process", "seq"], kind="stable").reset_index(drop=True)
    first = TIME_COLUMNS + ["type", "source", "process", "seq"]
    rest = [c for c in frame.columns if c not in first and c != "v"]
    return frame[first + rest]


def _subset(events: "pd.DataFrame", event_type: str, columns: list[str]) -> "pd.DataFrame":
    picked = events[events["type"] == event_type] if "type" in events else events.iloc[0:0]
    for column in columns:
        if column not in picked:
            picked = picked.assign(**{column: pd.NA})
    return picked[TIME_COLUMNS + columns].reset_index(drop=True)


def _behaviors_frame(events: "pd.DataFrame") -> "pd.DataFrame":
    """Pair each behavior_started with the event that ended it."""
    columns = ["behavior", "source", "intensity", "t_start", "t_end", "duration",
               "outcome", "interrupted_by", "reason"]
    if events.empty or "type" not in events:
        return pd.DataFrame(columns=columns)
    runs: list[dict] = []
    open_runs: dict[str, dict] = {}
    for row in events.itertuples(index=False):
        kind = row.type
        name = getattr(row, "behavior", None)
        if kind == "behavior_started":
            open_runs[name] = {"behavior": name, "source": row.source,
                               "intensity": getattr(row, "intensity", None),
                               "t_start": row.t, "t_end": None, "outcome": "unfinished",
                               "interrupted_by": None, "reason": None}
            runs.append(open_runs[name])
        elif kind in ("behavior_finished", "behavior_interrupted") and name in open_runs:
            run = open_runs.pop(name)
            run["t_end"] = row.t
            run["outcome"] = "finished" if kind == "behavior_finished" else "interrupted"
            run["interrupted_by"] = getattr(row, "by", None) if kind != "behavior_finished" else None
        elif kind == "behavior_refused":
            runs.append({"behavior": name, "source": row.source,
                         "intensity": None, "t_start": row.t, "t_end": row.t,
                         "outcome": "refused", "interrupted_by": None,
                         "reason": getattr(row, "reason", None)})
    frame = pd.DataFrame(runs, columns=[c for c in columns if c != "duration"])
    frame["duration"] = frame["t_end"] - frame["t_start"]
    return frame[columns]


def _features_frame(folder: Path, start: float | None,
                    name: str = "features") -> tuple["pd.DataFrame | None", dict]:
    spec_path, csv_path = folder / f"{name}.json", folder / f"{name}.csv"
    if not csv_path.is_file():
        return None, {}
    spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
    frame = pd.read_csv(csv_path, on_bad_lines="skip")
    frame = frame.dropna(subset=["t_mono"])
    frame["t"] = frame["t_mono"] - (start if start is not None else frame["t_mono"].min())
    if spec.get("clock_anchor"):
        frame["t_wall"] = _wall(frame["t_mono"], spec["clock_anchor"])
    else:
        frame["t_wall"] = pd.NaT
    first = TIME_COLUMNS + ["frame"]
    return frame[first + [c for c in frame.columns if c not in first]], spec


def load_session(name: str | Path, data_dir: Path = DATA_DIR) -> Session:
    """Load one session. `name`: a folder name, a unique part of one, a path, or "latest"."""
    folder = resolve(str(name), Path(data_dir))
    result = check_session(folder)
    meta = result.meta
    raw_events = []
    for path in folder.glob("events*.jsonl"):
        raw_events.extend(_read_jsonl(path))
    start = _start(meta, raw_events)
    events = _events_frame(folder, meta, start)
    features, spec = _features_frame(folder, start)
    aus, _ = _features_frame(folder, start, "aus")
    return Session(
        name=folder.name, folder=folder, meta=meta, events=events,
        annotations=_subset(events, "annotation", ["marker", "label", "via", "source"]),
        behaviors=_behaviors_frame(events),
        notes=_subset(events, "note", ["text", "source"]),
        features=features, check=result, feature_spec=spec, aus=aus)


def sessions_table(data_dir: Path = DATA_DIR) -> "pd.DataFrame":
    """One row per session: who, when, which mode, how long, and its verdict."""
    rows = []
    for folder in find_sessions(data_dir):
        result = check_session(folder)
        meta = result.meta
        rows.append({"session": folder.name, "participant": meta.get("participant"),
                     "mode": meta.get("mode"), "backend": meta.get("backend"),
                     "started": (meta.get("started") or {}).get("t_wall"),
                     "duration_s": result.duration_s, "events": result.events,
                     "annotations": result.annotations, "feature_rows": result.feature_rows,
                     "status": result.status, "problems": len(result.problems)})
    frame = pd.DataFrame(rows, columns=["session", "participant", "mode", "backend", "started",
                                        "duration_s", "events", "annotations", "feature_rows",
                                        "status", "problems"])
    if not frame.empty:
        frame["started"] = pd.to_datetime(frame["started"], format="ISO8601")
    return frame


def all_annotations(data_dir: Path = DATA_DIR, include_broken: bool = False) -> "pd.DataFrame":
    """Every session's annotations in one table -- the training labels for the HMM.

    Broken sessions are left out unless include_broken=True. Incomplete ones
    (crashed, but intact up to the crash) are kept.
    """
    parts = []
    for folder in find_sessions(data_dir):
        session = load_session(folder, data_dir)
        if session.check.status == "BROKEN" and not include_broken:
            continue
        parts.append(session.annotations.assign(session=session.name,
                                                participant=session.participant,
                                                mode=session.mode))
    if not parts:
        return pd.DataFrame(columns=["session", "participant", "mode"] + TIME_COLUMNS
                            + ["marker", "label", "via", "source"])
    frame = pd.concat(parts, ignore_index=True)
    return frame[["session", "participant", "mode"] + [c for c in frame.columns
                                                         if c not in ("session", "participant", "mode")]]
