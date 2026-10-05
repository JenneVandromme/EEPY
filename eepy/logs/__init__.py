"""EEPY's data logging: sessions, events, perception features, and loading them.

    from eepy.logs import log            # the one central logger
    log.start("P03", mode="wizard", backend="sim")
    log.event("annotation", source="wizard", marker="yawn")
    log.end()

    from eepy.logs.load import load_session   # analysis, needs pandas
    session = load_session("2026-10-06_P03_wizard")

The writing side (schema, session, features, writer, check) is standard-library
only and runs on Python 3.9, because the vision process imports it from the
other virtualenv. Only load.py needs pandas. See eepy/logs/README.md.
"""

from __future__ import annotations

from .schema import EVENT_TYPES, MODES, SCHEMA_VERSION, SchemaError
from .session import DATA_DIR, SessionError, SessionLog

# The process-wide logger. Configure it once at start-up with configure();
# everything else just imports `log` and calls log.event().
log = SessionLog()


def configure(**settings) -> SessionLog:
    """Set the central logger's settings (data_dir, participant_pattern,
    store_raw_speech, flush_seconds). Only while no session is running.

    Changes `log` IN PLACE rather than replacing it: a module that did
    `from eepy.logs import log` before this call must still be holding the
    logger that is actually in use.
    """
    return log.configure(**settings)


__all__ = ["DATA_DIR", "EVENT_TYPES", "MODES", "SCHEMA_VERSION", "SchemaError",
           "SessionError", "SessionLog", "configure", "log"]
