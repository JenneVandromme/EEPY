"""The parts every log writer shares: one clock, the privacy guard, and a
background thread that puts lines on disk.

Standard library only, Python 3.9 compatible -- see schema.py for why.
"""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .clock import monotonic
from .schema import SchemaError

# Anything bigger than this, in one value, is refused: a camera frame or an
# audio buffer has no business in an event log.
MAX_STRING = 4000
MAX_ITEMS = 2000


class Clock:
    """The shared monotonic clock for ordering, and wall-clock time for humans.

    One anchor -- both clocks read at the same instant -- converts the first
    into the second, so an event's t_mono and t_wall can never disagree about
    order. t_mono comes from eepy.logs.clock.monotonic(), which every process on
    the machine shares -- NOT from time.monotonic(), which on macOS Python 3.9
    restarts at zero in every process (see clock.py).
    """

    def __init__(self) -> None:
        self.mono0 = monotonic()
        self.wall0 = datetime.now().astimezone()

    def wall(self, t_mono: float) -> str:
        return (self.wall0 + timedelta(seconds=t_mono - self.mono0)).isoformat(
            timespec="milliseconds")

    def anchor(self) -> dict:
        return {"t_mono": round(self.mono0, 6),
                "t_wall": self.wall0.isoformat(timespec="microseconds")}


def clean(value: Any, where: str = "value") -> Any:
    """Make a value safe and JSON-ready, or refuse it.

    Refuses raw bytes and large arrays outright. That is the GDPR guard: it
    makes it impossible to log a camera frame or an audio clip by accident,
    which would otherwise be one careless `log.event(..., frame=frame)` away.
    NaN and infinity become null (JSON has no way to write them).
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise SchemaError(f"{where} is {len(value)} characters long; the limit is "
                              f"{MAX_STRING}. Logs are for events, not documents.")
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise SchemaError(f"{where} is raw binary data. Images, audio and video must never "
                          "be logged -- log what was detected in them instead.")
    if hasattr(value, "shape") and hasattr(value, "dtype"):          # numpy array
        size = int(getattr(value, "size", 0))
        if size > MAX_ITEMS:
            raise SchemaError(f"{where} is an array of {size} values -- that looks like an "
                              "image or audio. Never log those; log what was detected in them.")
        return clean(value.tolist(), where)
    if hasattr(value, "item") and not hasattr(value, "__len__"):     # numpy scalar
        return clean(value.item(), where)
    if isinstance(value, dict):
        return {str(k): clean(v, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        if len(value) > MAX_ITEMS:
            raise SchemaError(f"{where} has {len(value)} items; the limit is {MAX_ITEMS}.")
        return [clean(v, f"{where}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise SchemaError(f"{where} is a {type(value).__name__}, which cannot be logged. "
                      "Use numbers, text, lists or dicts.")


def write_json_atomic(path: Path, data: dict) -> None:
    """Write a JSON file so that it is either the old version or the new one.

    Written to a temporary file, synced, then renamed over the original -- a
    crash in the middle can never leave a half-written meta.json.
    """
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class LineWriter:
    """Appends lines to one file from a background thread.

    write() only puts the line on a queue, so whoever logs is never held up by
    the disk. The thread writes in batches about once a second (or sooner when
    a lot has queued up), and syncs each batch to disk before the next.

    Each line is written whole, so after a crash the file ends with complete
    lines, plus at worst one cut-off line from a batch that was being written
    at that moment. The loader skips such a line; the check reports it.
    """

    def __init__(self, path: Path, flush_seconds: float = 1.0,
                 header: Optional[str] = None) -> None:
        self.path = Path(path)
        self.flush_seconds = flush_seconds
        self.lines_written = 0
        self.error: Optional[str] = None
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        new = not self.path.exists()
        self._file = self.path.open("a", encoding="utf-8", newline="")
        if header is not None and new:
            self._file.write(header + "\n")
            self._file.flush()
        self._thread = threading.Thread(target=self._run, name=f"eepy-log-{self.path.name}",
                                        daemon=True)
        self._thread.start()

    def write(self, line: str) -> None:
        self._queue.put(line)

    def close(self, timeout: float = 5.0) -> None:
        """Write everything still queued, sync, and close. Blocks until done."""
        self._queue.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        batch: list = []
        deadline = time.monotonic() + self.flush_seconds
        closing = False
        while not closing:
            try:
                item = self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
                if item is None:
                    closing = True
                else:
                    batch.append(item)
            except queue.Empty:  # the flush timer below is per-process: time.monotonic() is fine
                pass
            if closing or batch and (time.monotonic() >= deadline or len(batch) >= 2000):
                self._flush(batch)
                batch = []
                deadline = time.monotonic() + self.flush_seconds
            elif not batch:
                deadline = time.monotonic() + self.flush_seconds
        self._file.close()

    def _flush(self, batch: list) -> None:
        if not batch:
            return
        try:
            self._file.write("".join(line + "\n" for line in batch))
            self._file.flush()
            os.fsync(self._file.fileno())
            self.lines_written += len(batch)
        except OSError as exc:  # disk full, folder deleted: keep the program running
            self.error = f"{type(exc).__name__}: {exc}"
