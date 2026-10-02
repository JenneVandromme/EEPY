"""Session logs: one file per participant session, one JSON object per line.

    logs/sessions/P03_2026-10-06_14-03-12.jsonl

Every line has the same core fields:

    t        seconds on time.monotonic() -- the SAME clock the player stamps its
             events with, so robot behaviours and the operator's observations
             can be lined up to the millisecond. Only meaningful within one run
             of the panel.
    wall     the same moment as a local ISO date and time, for humans.
    session  "P03_2026-10-06_14-03-12"
    kind     what happened: behavior_started, annotation, wizard_command, ...
    source   who caused it: wizard (the operator), idle, system, auto (later)

plus whatever that kind of record carries. The first line of every file
(session_start) records `t` and `wall` together, which is what lets the two be
converted into each other afterwards.

Each line is flushed to disk the moment it is written, so a crash loses nothing
that was already logged.

The writing sits behind SessionWriter, so the proper logging module (task A7)
can replace JsonlSessionWriter without touching the panel.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "logs" / "sessions"

PARTICIPANT = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class SessionError(Exception):
    """A session request that cannot be honoured. The message is for humans."""


class SessionWriter(Protocol):
    """Where session records go. JsonlSessionWriter is the only one, for now."""

    def open(self, session_id: str) -> str:
        """Start a session. Returns where it is being written, for display."""
        ...

    def write(self, record: dict[str, Any]) -> None:
        """Append one record. Must be safe to call from several threads."""
        ...

    def close(self) -> None:
        ...


class JsonlSessionWriter:
    """One .jsonl file per session, flushed after every line."""

    def __init__(self, directory: Path = LOGS_DIR) -> None:
        self.directory = Path(directory)
        self._file = None
        self._lock = threading.Lock()

    def open(self, session_id: str) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{session_id}.jsonl"
        # "x": never overwrite an existing session's file.
        self._file = path.open("x", encoding="utf-8")
        return str(path)

    def write(self, record: dict[str, Any]) -> None:
        with self._lock:
            if self._file is None:
                return
            self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


class Clock:
    """Converts the shared monotonic clock to wall-clock time.

    One anchor, taken once: the monotonic and wall clocks read at the same
    instant. Every record's `wall` is derived from its `t` through this anchor,
    so `t` and `wall` can never disagree about the order of events.
    """

    def __init__(self) -> None:
        self.mono0 = time.monotonic()
        self.wall0 = datetime.now().astimezone()

    def wall(self, t: float) -> str:
        return (self.wall0 + timedelta(seconds=t - self.mono0)).isoformat(timespec="milliseconds")


class Sessions:
    """Starts and ends sessions, and stamps every record consistently."""

    def __init__(self, writer: SessionWriter | None = None, clock: Clock | None = None) -> None:
        self.writer = writer or JsonlSessionWriter()
        self.clock = clock or Clock()
        self.session_id: str | None = None
        self.participant: str | None = None
        self.started_t: float | None = None
        self.location: str | None = None
        self.counts: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self.session_id is not None

    def record(self, kind: str, source: str, t: float | None = None, **data: Any) -> dict:
        """Build a record. Written to the log only if a session is running."""
        t = time.monotonic() if t is None else t
        record = {"t": round(t, 6), "wall": self.clock.wall(t), "session": self.session_id,
                  "kind": kind, "source": source}
        record.update({k: v for k, v in data.items() if v is not None})
        with self._lock:
            if self.session_id is not None:
                self.writer.write(record)
                self.counts[kind] = self.counts.get(kind, 0) + 1
        return record

    def start(self, participant: str, **meta: Any) -> dict:
        participant = (participant or "").strip()
        if not PARTICIPANT.match(participant):
            raise SessionError(
                "The participant ID can use letters, numbers, - and _ only "
                "(no spaces), up to 32 characters. For example: P03")
        with self._lock:
            if self.session_id is not None:
                raise SessionError(
                    f"A session for {self.participant} is still running. End it first.")
            t = time.monotonic()
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
            session_id = f"{participant}_{stamp}"
            try:
                self.location = self.writer.open(session_id)
            except FileExistsError:
                raise SessionError("A session with that name already exists. "
                                   "Wait a second and try again.") from None
            self.session_id, self.participant, self.started_t = session_id, participant, t
            self.counts = {}
        return self.record("session_start", "wizard", t=t, participant=participant,
                           monotonic_at_start=round(t, 6),
                           wall_at_start=self.clock.wall(t), **meta)

    def end(self, reason: str = "operator") -> dict:
        with self._lock:
            if self.session_id is None:
                raise SessionError("No session is running.")
        t = time.monotonic()
        record = self.record("session_end", "wizard", t=t, reason=reason,
                             duration_s=round(t - (self.started_t or t), 3),
                             counts=dict(self.counts))
        with self._lock:
            self.writer.close()
            self.session_id = self.participant = self.started_t = None
        return record

    def status(self) -> dict:
        with self._lock:
            if self.session_id is None:
                return {"active": False}
            return {"active": True, "participant": self.participant,
                    "session": self.session_id, "file": self.location,
                    "elapsed_s": round(time.monotonic() - (self.started_t or 0), 1),
                    "annotations": self.counts.get("annotation", 0)
                    + self.counts.get("note", 0)}
