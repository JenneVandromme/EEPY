"""Logging from the vision process: high-rate features, and its own events.

The vision process is a separate program, in the other virtualenv (Python 3.9,
no pydantic, no pyyaml). It does not start or end sessions -- the panel does.
It finds the running session through data/sessions/CURRENT, and follows it:

    from eepy.logs.features import FeatureStream, ProcessEventLog

    features = FeatureStream(["face_present", "gaze_x", "gaze_y", "eye_openness"],
                             rate_hz=15)
    events = ProcessEventLog("vision")

    while True:                                   # the perception loop
        ...
        features.row([1, 0.12, -0.3, 0.81], frame=frame_number)
        if face_was_lost:
            events.event("error", source="perception", message="face lost")

Rows and events arrive at any rate and are written about once a second. When a
session starts, they go into its folder; when it ends, they stop; when the next
one starts, a new file begins there. Outside a session they are DROPPED --
nothing is recorded about someone who is not a participant in a session.

Each process writes only its own files (features.csv / features.json, and
events.<process>.jsonl), so two programs never append to the same file.

Standard library only, Python 3.9 compatible.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union

from .clock import NAME as CLOCK_NAME
from .clock import monotonic
from .schema import SCHEMA_VERSION, SchemaError, check_event
from .session import CURRENT_FILE, DATA_DIR, pid_alive
from .writer import Clock, LineWriter, clean, write_json_atomic

POLL_SECONDS = 0.5   # how often to look at CURRENT for a new or ended session


def _alive(pid: Any) -> bool:
    """Is the process that started the session still running?

    If the panel crashes, CURRENT is left pointing at its session. Without this
    check the vision process would carry on writing features into that dead
    session for as long as it ran -- hours, possibly, into the wrong
    participant's folder. With it, recording stops within half a second.
    """
    return pid_alive(pid)       # (never os.kill(pid, 0): on Windows that sends Ctrl+C)


class _SessionFollower:
    """Keeps track of which session (if any) is running, cheaply."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._checked = -1e9
        self._session: Optional[Dict[str, Any]] = None

    def current(self) -> Optional[Dict[str, Any]]:
        now = monotonic()
        if now - self._checked >= POLL_SECONDS:
            self._checked = now
            try:
                pointer = json.loads((self.data_dir / CURRENT_FILE).read_text())
                folder = Path(pointer["folder"])
                self._session = (pointer if folder.is_dir() and _alive(pointer.get("pid"))
                                 else None)
            except (OSError, ValueError, KeyError):
                self._session = None
        return self._session


def current_session(data_dir: Path = DATA_DIR) -> Optional[Dict[str, Any]]:
    """The session the panel is running right now, with its participant code, or
    None. For the vision process, e.g. to load that participant's calibration:
    {"session": "2026-10-06_P03_wizard", "folder": ..., "participant": "P03", ...}"""
    pointer = _SessionFollower(Path(data_dir)).current()
    if pointer is None:
        return None
    info = dict(pointer)
    try:
        meta = json.loads((Path(pointer["folder"]) / "meta.json").read_text())
        info["participant"] = meta.get("participant")
    except (OSError, ValueError):
        info["participant"] = None
    return info


class FeatureStream:
    """A CSV of numeric features at a high rate, for the running session.

    `columns` are fixed for the life of the stream and recorded in
    features.json, which the loader and the check compare against the CSV.
    """

    def __init__(self, columns: Sequence[str], rate_hz: Optional[float] = None,
                 data_dir: Path = DATA_DIR, flush_seconds: float = 1.0,
                 name: str = "features") -> None:
        columns = [str(c) for c in columns]
        bad = [c for c in columns if not c.replace("_", "").isalnum()]
        if bad or len(set(columns)) != len(columns):
            raise SchemaError("feature names must be unique, made of letters, digits and "
                              f"underscores: {bad or columns}")
        if {"t_mono", "frame"} & set(columns):
            raise SchemaError('"t_mono" and "frame" are added automatically; '
                              "do not list them as features.")
        self.columns = columns
        self.rate_hz = rate_hz
        self.name = name
        self.flush_seconds = flush_seconds
        self.clock = Clock()
        self._follower = _SessionFollower(data_dir)
        self._lock = threading.Lock()
        self._writer: Optional[LineWriter] = None
        self._session: Optional[str] = None
        self._folder: Optional[Path] = None
        self._rows = 0
        self.dropped = 0           # rows that arrived with no session running
        self._frame = 0

    def row(self, values: Union[Sequence[Any], Mapping[str, Any]],
            t_mono: Optional[float] = None, frame: Optional[int] = None) -> bool:
        """Add one row. Returns True if it was recorded (a session is running).

        `values` is either a list in column order or a dict by column name
        (missing names become empty cells). Numbers, booleans and short text
        only -- never an image.
        """
        t = monotonic() if t_mono is None else t_mono
        if isinstance(values, Mapping):
            unknown = set(values) - set(self.columns)
            if unknown:
                raise SchemaError(f"unknown feature(s): {sorted(unknown)}")
            values = [values.get(c) for c in self.columns]
        values = list(values)
        if len(values) != len(self.columns):
            raise SchemaError(f"expected {len(self.columns)} feature values, got {len(values)}")
        cells = [_cell(v, c) for v, c in zip(values, self.columns)]
        with self._lock:
            self._frame = self._frame + 1 if frame is None else frame
            self._roll_over()
            if self._writer is None:
                self.dropped += 1
                return False
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="").writerow([f"{t:.6f}", self._frame, *cells])
            self._writer.write(buffer.getvalue())
            self._rows += 1
            return True

    @property
    def session(self) -> Optional[str]:
        """The session rows are going into right now, or None (rows are dropped)."""
        return self._session

    def close(self) -> None:
        with self._lock:
            self._stop()

    def _roll_over(self) -> None:
        session = self._follower.current()
        name = session["session"] if session else None
        if name == self._session:
            return
        self._stop()
        if session is None:
            return
        folder = Path(session["folder"])
        self._session, self._folder, self._rows = name, folder, 0
        t = monotonic()
        write_json_atomic(folder / f"{self.name}.json", {
            "schema_version": SCHEMA_VERSION, "columns": ["t_mono", "frame", *self.columns],
            "rate_hz": self.rate_hz, "pid": os.getpid(), "clock": CLOCK_NAME,
            "clock_anchor": self.clock.anchor(),
            "started": {"t_mono": round(t, 6), "t_wall": self.clock.wall(t)}, "stopped": None})
        self._writer = LineWriter(folder / f"{self.name}.csv", self.flush_seconds,
                                  header=",".join(["t_mono", "frame", *self.columns]))

    def _stop(self) -> None:
        if self._writer is None:
            return
        self._writer.close()
        self._writer = None
        path = self._folder / f"{self.name}.json"
        try:
            meta = json.loads(path.read_text())
            t = monotonic()
            meta["stopped"] = {"t_mono": round(t, 6), "t_wall": self.clock.wall(t)}
            meta["rows"] = self._rows
            write_json_atomic(path, meta)
        except (OSError, ValueError):
            pass
        self._session = self._folder = None


def _cell(value: Any, column: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "" if not math.isfinite(value) else repr(value)
    if hasattr(value, "item") and not hasattr(value, "__len__"):   # numpy scalar
        return _cell(value.item(), column)
    if isinstance(value, str) and len(value) <= 64:
        return value
    raise SchemaError(f'feature "{column}" must be a number, a boolean or short text, '
                      f"not {type(value).__name__}. Never put an image in a feature.")


class ProcessEventLog:
    """Discrete events from a process other than the panel's, e.g. "vision".

    Same schema and checks as eepy.logs.log.event(); written to
    events.<process>.jsonl in the running session's folder. The loader merges
    every events*.jsonl file into one table, ordered by t_mono.
    """

    def __init__(self, process: str, data_dir: Path = DATA_DIR,
                 flush_seconds: float = 1.0) -> None:
        if not process.isidentifier():
            raise SchemaError(f'process name must be a simple word, like "vision", not {process!r}')
        self.process = process
        self.flush_seconds = flush_seconds
        self.clock = Clock()
        self._follower = _SessionFollower(data_dir)
        self._lock = threading.Lock()
        self._writer: Optional[LineWriter] = None
        self._session: Optional[str] = None
        self._seq = 0

    def event(self, event_type: str, source: str = "perception",
              t_mono: Optional[float] = None, **data: Any) -> bool:
        check_event(event_type, source, data)
        data = clean(data, event_type)
        t = monotonic() if t_mono is None else t_mono
        with self._lock:
            session = self._follower.current()
            name = session["session"] if session else None
            if name != self._session:
                if self._writer is not None:
                    self._writer.close()
                self._writer, self._session, self._seq = None, name, 0
                if session is not None:
                    self._writer = LineWriter(Path(session["folder"]) /
                                              f"events.{self.process}.jsonl", self.flush_seconds)
            if self._writer is None:
                return False
            self._seq += 1
            record = {"v": SCHEMA_VERSION, "seq": self._seq, "t_mono": round(t, 6),
                      "t_wall": self.clock.wall(t), "type": event_type, "source": source,
                      **{k: v for k, v in data.items() if v is not None}}
            self._writer.write(json.dumps(record, ensure_ascii=False))
            return True

    def close(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.close()
                self._writer = None
