"""The central logger: one session at a time, one folder per session.

    from eepy.logs import log

    log.start("P03", mode="wizard", backend="real")
    log.event("annotation", source="wizard", marker="yawn", via="key")
    log.end()

Everything lands in data/sessions/<date>_<participant>_<mode>/:

    meta.json      who, when, which mode, which code, a snapshot index, notes
    events.jsonl   one JSON object per line, one line per event
    snapshot/      exact copies of the behaviour and config files in use

log.event() validates on the spot -- an unknown type or a missing field raises
immediately -- and then hands the line to a background thread, so logging never
waits for the disk.

Standard library only, Python 3.9 compatible -- see schema.py for why.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .clock import NAME as CLOCK_NAME
from .clock import monotonic
from .schema import EVENT_TYPES, MODES, SCHEMA_VERSION, check_event
from .writer import Clock, LineWriter, clean, write_json_atomic

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "sessions"
CURRENT_FILE = "CURRENT"
DEFAULT_PARTICIPANT_PATTERN = r"^P[0-9]{2,3}$"

# What gets copied into snapshot/ at the start of every session: everything a
# designer can change that alters how the robot behaves. (Sounds are recorded
# by name and hash only -- audio is never copied.)
SNAPSHOT_GLOBS = ("behaviors/*.yaml", "config/*.yaml")


class SessionError(Exception):
    """A session request that cannot be honoured. The message is for humans."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def git_info(repo: Path = REPO_ROOT) -> Dict[str, Any]:
    """Which code ran. 'dirty' means the commit alone does not describe it."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=5, check=True).stdout.strip()
    try:
        changed = [line[3:] for line in git("status", "--porcelain").splitlines()]
        return {"commit": git("rev-parse", "HEAD"),
                "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                "dirty": bool(changed), "modified": changed[:50]}
    except Exception as exc:  # noqa: BLE001 -- git missing, not a repo, ...
        return {"commit": None, "error": f"{type(exc).__name__}: {exc}"}


class SessionLog:
    """See the module docstring. Thread-safe: log from any thread."""

    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        participant_pattern: str = DEFAULT_PARTICIPANT_PATTERN,
        store_raw_speech: bool = False,
        flush_seconds: float = 1.0,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.participant_pattern = participant_pattern
        self.store_raw_speech = store_raw_speech
        self.flush_seconds = flush_seconds
        self.repo_root = Path(repo_root)
        self.clock = Clock()
        self._lock = threading.RLock()
        self._writer: Optional[LineWriter] = None
        self._seq = 0
        self._counts: Dict[str, int] = {}
        self.folder: Optional[Path] = None
        self.session: Optional[str] = None
        self.participant: Optional[str] = None
        self.mode: Optional[str] = None
        self.started_mono: Optional[float] = None
        self._meta: Dict[str, Any] = {}
        self._atexit_registered = False

    def configure(self, data_dir: Optional[Path] = None,
                  participant_pattern: Optional[str] = None,
                  store_raw_speech: Optional[bool] = None,
                  flush_seconds: Optional[float] = None) -> "SessionLog":
        """Change settings in place -- only while no session is running."""
        with self._lock:
            if self.active:
                raise SessionError("Cannot change logging settings during a session.")
            if data_dir is not None:
                self.data_dir = Path(data_dir)
            if participant_pattern is not None:
                self.participant_pattern = participant_pattern
            if store_raw_speech is not None:
                self.store_raw_speech = store_raw_speech
            if flush_seconds is not None:
                self.flush_seconds = flush_seconds
        return self

    # ------------------------------------------------------------------ state

    @property
    def active(self) -> bool:
        return self.session is not None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            if not self.active:
                return {"active": False}
            return {"active": True, "session": self.session, "participant": self.participant,
                    "mode": self.mode, "folder": str(self.folder),
                    "elapsed_s": round(monotonic() - (self.started_mono or 0), 1),
                    "annotations": self._counts.get("annotation", 0) + self._counts.get("note", 0),
                    "events": self._seq,
                    "write_error": self._writer.error if self._writer else None}

    # ------------------------------------------------------------------ events

    def event(self, event_type: str, source: str = "system",
              t_mono: Optional[float] = None, **data: Any) -> Dict[str, Any]:
        """Log one event. Returns the record, with "saved" saying whether it
        went to a session file (it is only shown, not saved, outside a session).

        Raises SchemaError straight away for an unknown type, a missing
        required field, or data that cannot or must not be logged.
        """
        check_event(event_type, source, data)
        data = clean(data, event_type)
        for name in EVENT_TYPES[event_type].personal:
            if name in data and not self.store_raw_speech:
                data[name] = None
                data[f"{name}_redacted"] = True
        t = monotonic() if t_mono is None else t_mono
        with self._lock:
            record: Dict[str, Any] = {"v": SCHEMA_VERSION, "seq": None,
                                      "t_mono": round(t, 6), "t_wall": self.clock.wall(t),
                                      "type": event_type, "source": source}
            record.update({k: v for k, v in data.items() if v is not None
                           or k.endswith("_redacted")})
            saved = self._writer is not None
            if saved:
                self._seq += 1
                record["seq"] = self._seq
                self._writer.write(json.dumps(record, ensure_ascii=False))
                self._counts[event_type] = self._counts.get(event_type, 0) + 1
        return {**record, "saved": saved, "session": self.session if saved else None}

    # ------------------------------------------------------------------ sessions

    def start(self, participant: str, mode: str, backend: Optional[str] = None,
              notes: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
        """Open a session folder and start logging into it."""
        participant = (participant or "").strip()
        if not re.match(self.participant_pattern, participant):
            raise SessionError(
                f'"{participant}" is not a pseudonymous participant ID. Use the study\'s '
                f"code, like P03 -- never a name. (Allowed pattern: {self.participant_pattern})")
        if mode not in MODES:
            raise SessionError(f'"{mode}" is not a mode. Use one of: {", ".join(MODES)}.')
        with self._lock:
            if self.active:
                raise SessionError(f"A session for {self.participant} is still running. "
                                   "End it first.")
            t = monotonic()
            folder = self._new_folder(participant, mode)
            snapshot = self._snapshot(folder)
            self._meta = {
                "schema_version": SCHEMA_VERSION,
                "session": folder.name,
                "participant": participant,
                "mode": mode,
                "backend": backend,
                "started": {"t_mono": round(t, 6), "t_wall": self.clock.wall(t)},
                "ended": None,
                "clock_anchor": self.clock.anchor(),
                "clock": CLOCK_NAME,
                "code": git_info(self.repo_root),
                "snapshot": snapshot,
                "sounds": self._sound_hashes(),
                "notes": [notes] if notes else [],
                "counts": {},
                "host": {"pid": os.getpid()},
                **clean(extra, "extra"),
            }
            write_json_atomic(folder / "meta.json", self._meta)
            self._writer = LineWriter(folder / "events.jsonl", self.flush_seconds)
            self.folder, self.session = folder, folder.name
            self.participant, self.mode, self.started_mono = participant, mode, t
            self._seq, self._counts = 0, {}
            # The vision process -- another program, in another virtualenv --
            # finds the running session through this file.
            write_json_atomic(self.data_dir / CURRENT_FILE,
                              {"session": folder.name, "folder": str(folder.resolve()),
                               "pid": os.getpid(), "started": self.clock.wall(t)})
            if not self._atexit_registered:
                atexit.register(self._at_exit)
                self._atexit_registered = True
            # Still inside the lock: session_start must be line 1. Written after
            # the lock was released, events from other threads (the robot's
            # idle loop, say) could slip in ahead of it -- and a session file
            # that does not begin with session_start is reported as BROKEN.
            return self.event("session_start", "wizard", t_mono=t, participant=participant,
                              mode=mode, backend=backend, schema_version=SCHEMA_VERSION)

    def end(self, reason: str = "operator", notes: Optional[str] = None) -> Dict[str, Any]:
        """Close the session: final event, flush, finish meta.json."""
        with self._lock:
            if not self.active:
                raise SessionError("No session is running.")
            t = monotonic()
            record = self.event("session_end", "wizard" if reason == "operator" else "system",
                                t_mono=t, reason=reason,
                                duration_s=round(t - (self.started_mono or t), 3))
            writer, self._writer = self._writer, None
            if writer is not None:
                writer.close()
            if notes:
                self._meta["notes"].append(notes)
            self._meta["ended"] = {"t_mono": round(t, 6), "t_wall": self.clock.wall(t),
                                   "reason": reason}
            self._meta["counts"] = dict(self._counts)
            self._meta["events"] = self._seq
            write_json_atomic(self.folder / "meta.json", self._meta)
            self._clear_current()
            self.folder = self.session = self.participant = self.mode = None
            self.started_mono = None
        return record

    def set_meta(self, key: str, value: Any) -> None:
        """Add (or replace) one field of the running session's meta.json, e.g. the
        calibration summary. Numbers and text only -- it goes through clean()."""
        with self._lock:
            if not self.active:
                raise SessionError("No session is running.")
            if key in ("schema_version", "session", "participant", "mode", "started", "ended"):
                raise SessionError(f'"{key}" is set by the session itself.')
            self._meta[key] = clean(value, f"meta.{key}")
            write_json_atomic(self.folder / "meta.json", self._meta)

    # ------------------------------------------------------------------ helpers

    def _new_folder(self, participant: str, mode: str) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        base = f"{datetime.now().astimezone():%Y-%m-%d}_{participant}_{mode}"
        for n in range(1, 1000):
            folder = self.data_dir / (base if n == 1 else f"{base}_{n}")
            try:
                folder.mkdir()          # atomic: two panels can never share one
                return folder
            except FileExistsError:
                continue
        raise SessionError("Too many sessions with the same name today.")

    def _snapshot(self, folder: Path) -> Dict[str, str]:
        hashes: Dict[str, str] = {}
        for pattern in SNAPSHOT_GLOBS:
            for source in sorted(self.repo_root.glob(pattern)):
                relative = source.relative_to(self.repo_root)
                target = folder / "snapshot" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                hashes[str(relative)] = _sha256(target)
        return hashes

    def _sound_hashes(self) -> Dict[str, str]:
        sounds = self.repo_root / "sounds"
        if not sounds.is_dir():
            return {}
        return {p.name: _sha256(p) for p in sorted(sounds.iterdir())
                if p.is_file() and p.suffix.lower() in
                (".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac")}

    def _clear_current(self) -> None:
        pointer = self.data_dir / CURRENT_FILE
        try:
            if json.loads(pointer.read_text()).get("session") == self.session:
                pointer.unlink()
        except (OSError, ValueError):
            pass

    def _at_exit(self) -> None:
        """Python is exiting with a session still open: close it properly."""
        if self.active:
            try:
                self.end(reason="process exit")
            except Exception:  # noqa: BLE001 -- nothing useful to do this late
                pass
