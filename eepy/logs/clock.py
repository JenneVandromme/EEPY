"""The one clock every timestamp in EEPY's data is taken from.

Use eepy.logs.clock.monotonic() -- not time.monotonic() -- for anything that
gets logged or compared across processes.

WHY THIS EXISTS

The panel and player run on Python 3.12; the vision process runs on Python 3.9
(the two virtualenvs need incompatible numpy versions). On macOS those two
Pythons disagree about time.monotonic():

    Python 3.12   time.monotonic() = 1558752.6   (seconds since the Mac booted)
    Python 3.9    time.monotonic() =       0.004 (seconds since THIS process began)

Same name, same underlying counter, but 3.9 subtracts its own start time. Left
alone, every perception feature would be stamped about 18 days away from the
events it belongs to, and nothing would raise an error -- it would only show up,
much later, as an HMM fitted to nonsense.

clock_gettime(CLOCK_UPTIME_RAW) reads the same counter WITHOUT the per-process
offset, on both versions, and on 3.12 it equals time.monotonic() to the
microsecond. On Linux and Windows time.monotonic() is already system-wide in
every version, so it is used as-is there.

Only valid between processes on the SAME machine.
"""

from __future__ import annotations

import sys
import time

if sys.platform == "darwin" and hasattr(time, "CLOCK_UPTIME_RAW"):
    _CLOCK = time.CLOCK_UPTIME_RAW

    def monotonic() -> float:
        """Seconds on the machine-wide monotonic clock (macOS: since boot)."""
        return time.clock_gettime(_CLOCK)

    NAME = "clock_gettime(CLOCK_UPTIME_RAW)"
else:
    monotonic = time.monotonic
    NAME = "time.monotonic()"
