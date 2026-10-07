# How the logging works

This package records everything that happens in a session — what the robot
did, what the operator pressed and observed, and (from stage 1) what the camera
measured — and loads it back for analysis. It replaced the panel's temporary
log writer.

> **Just want the data?** [docs/DATA_GUIDE.md](../../docs/DATA_GUIDE.md) is the
> short version. This page is about how it is built, and why.

---

## What it does

```
 panel + player (Python 3.12)                       vision process (Python 3.9, other venv)
   log.start("P03", mode="wizard")                    FeatureStream([...]).row(...)
   log.event("annotation", ...)                       ProcessEventLog("vision").event(...)
        │  validated at once, queued                       │  follows data/sessions/CURRENT
        ▼                                                  ▼
 ┌──────────────────── data/sessions/2026-10-06_P03_wizard/ ─────────────────────┐
 │ meta.json   events.jsonl   snapshot/        features.csv   features.json      │
 │                                             events.vision.jsonl               │
 └───────────────────────────────────────────────────────────────────────────────┘
        ▼
   load_session("P03")  ──►  pandas: events, annotations, behaviors, notes, features
```

- **One central logger,** `from eepy.logs import log`. `log.event(type, **data)`
  checks the event against the schema immediately, then hands it to a
  background thread that writes about once a second.
- **One folder per session**, named `<date>_<participant>_<mode>`.
- **The vision process doesn't start sessions.** It follows the one the panel
  started, through the small `data/sessions/CURRENT` file.

## The pieces

| File | Job | Needs |
|---|---|---|
| `schema.py` | Every event type, its required fields, the schema version, the modes. | stdlib |
| `clock.py` | The one clock every timestamp comes from. | stdlib |
| `writer.py` | The background line writer, the privacy guard, atomic JSON writes. | stdlib |
| `session.py` | `SessionLog` — sessions, folders, `meta.json`, snapshots, `CURRENT`. | stdlib |
| `features.py` | `FeatureStream` and `ProcessEventLog`, for the vision process. | stdlib |
| `check.py` | The OK / INCOMPLETE / BROKEN verdicts. `python -m eepy.logs.check`. | stdlib |
| `list.py` | `python -m eepy.logs.list`. | stdlib |
| `load.py` | `load_session()` and friends → pandas DataFrames. | pandas |
| `summary.py` | `python -m eepy.logs.summary`. | pandas |
| `../settings.py` | `config/eepy.yaml`: the mode, data folder, ID pattern, privacy. | pydantic |

The "stdlib" column is a hard rule, not an accident — see roadblock 1.

## The event schema

All event types live in `schema.py`, in these groups plus reserved ones:

| Group | Types |
|---|---|
| Session | `session_start`, `session_end`, `mode_not_implemented` |
| Robot | `behavior_started`, `keyframe_reached`, `behavior_finished`, `behavior_interrupted`, `behavior_refused`, `sound_played` (and `line_requested`, in sessions from before speech) |
| Speech | `speech_requested`, `speech_started`, `speech_finished`, `speech_interrupted`, `speech_failed` |
| Operator | `wizard_command`, `annotation`, `note` |
| System | `connection`, `reload`, `error` |
| Face | `blink`, `yawn`, `nod`, `face_lost`, `face_back`, `face_window` (per-second summaries), `au_status`, `au_window` (optional action units), `calibration` (A12, see [eepy/perception/calibration/README.md](../perception/calibration/README.md)), `looked_away`, `back_on_work`, `gaze_window`, `gaze_status` (gaze, see [eepy/perception/gaze/README.md](../perception/gaze/README.md)) — written by the vision process; see [eepy/perception/README.md](../perception/README.md) |
| Model | `state_changed` (the user's state: from, to, reason, evidence; every second's state is in `state.csv`) — written by the vision process; see [eepy/state/README.md](../state/README.md) |
| Camera | `camera_started`, `camera_error`, `camera_reconnected`, `camera_stats` — statistics only, never pixels; written by the vision process to `events.vision.jsonl` (see [eepy/camera/README.md](../camera/README.md)) |
| **Reserved** | `hmm_belief`, `bandit_decision`, `bandit_reward`, `reply_category`, `llm_output` |

Every line in `events.jsonl` carries `v` (schema version), `seq` (1, 2, 3 …),
`t_mono`, `t_wall`, `type` and `source`, plus the type's own fields. Extra
fields are always allowed, so the data can grow without a schema change; bump
`SCHEMA_VERSION` only when an existing field changes meaning. The reserved
types are defined now, so the HMM and bandit tasks inherit a settled format
instead of inventing one under deadline pressure.

## The main decisions, and why

**Validate at the call, write in the background.** An unknown type or a missing
field raises *immediately*, in the face of the developer who wrote it, rather
than quietly becoming a column nobody analyses. Writing is queued, so logging
never waits on the disk: about 5–6 µs per event, measured, from any thread.

**Each process owns its own files.** The panel writes `events.jsonl` and
`meta.json`; the vision process writes `features.*` and `events.vision.jsonl`.
Two programs never append to the same file, so there is no cross-process
locking to get wrong. The loader merges them into one timeline.

**Crash-safe by construction.** Lines are written whole and synced to disk each
second; `meta.json` is replaced atomically, never half-written. A hard crash
loses at most the last second, and the session's verdict says INCOMPLETE
instead of pretending all is well.

**Snapshot what defines a session.** `meta.json` records the git commit and
whether the working tree had uncommitted changes — because if it did, the
commit alone does not describe what ran. The behaviour and config YAMLs are
copied into `snapshot/` with their hashes, so a session can be replayed with
exactly the behaviours it used, even after the files have changed.

**CSV, not Parquet, for features.** Parquet needs `pyarrow` — a large download
not installed anywhere — and cannot be appended to row by row, which a
crash-safe stream needs. CSV is crash-safe line by line, opens in Excel, and
converting to Parquet later is one line in the loader.

**The mode is fixed per session.** It is the experimental condition, so it is
chosen at start-up (`config/eepy.yaml` or `--mode`) and recorded in the folder
name and `meta.json`. A mode without a controller yet still records, keeps the
panel fully usable by hand, and logs `mode_not_implemented`.

## Privacy, built in

- **No images or audio, ever.** The logger refuses raw bytes and any array big
  enough to be a frame or a sound, with an explanation.
- **Pseudonyms only.** Participant IDs must match `participant_pattern` (by
  default `P` + 2–3 digits); `Jenne` or an email address is refused.
- **What a participant says is personal data.** Fields marked `personal` in the
  schema (the words of a reply, a prompt sent to a language model) are dropped
  unless `privacy.store_raw_speech` is switched on.
- **Nothing outside a session.** The vision process's features are discarded
  whenever no session is running.
- **Nothing in git.** `data/` is ignored, and a test fails if that ever changes.

---

## Roadblocks, and how we got around them

**1. The vision process lives in a different world.**
It runs in the other virtualenv, on **Python 3.9**, without pydantic, pyyaml or
pandas. *Fix:* the whole writing side is standard-library only and 3.9-safe,
and that is enforced: CI runs its tests on a bare Python 3.9 with only pytest
installed. Settings (pyyaml) and loading (pandas) live in separate modules the
vision side never imports.

**2. On macOS, Python 3.9 and 3.12 disagree about the time.**
`time.monotonic()` on 3.12 counts from boot (1,558,752 s); on 3.9 it restarts
at **zero in every process**. Same function name, same underlying counter — but
every feature from the vision process would have been stamped about 18 days
away from the events it belongs to, with no error anywhere. It would only have
surfaced much later as an HMM trained on misaligned data. *Fix:* `clock.py`
reads `CLOCK_UPTIME_RAW` directly — the same counter without the per-process
offset — on both versions, and everything that is logged (including the
player's events) uses it. A test reads the clock from the real vision venv and
checks it lands between two 3.12 readings.

**3. Two programs, one session.**
The panel starts sessions; the vision process has to find them. *Fix:* the
`CURRENT` file names the running session. The vision process checks it twice a
second and rolls over to each new session by itself.

**4. A crashed panel would leave the vision process recording forever.**
`CURRENT` would keep naming a session nobody is running anymore, into which the
vision process would keep writing — potentially for hours, into the wrong
participant's folder. *Fix:* `CURRENT` records the panel's process ID; the
vision side stops recording within half a second of that process disappearing.
The next panel to start clears the stale pointer and reports the crashed
session.
Whether that process still runs is asked with `pid_alive()` (`session.py`),
never with `os.kill(pid, 0)`: on Windows, signal 0 is Ctrl+C, so that "check"
would interrupt the panel or the vision process.

**5. File order is not time order.**
The player's events reach the log through another thread a few milliseconds
after they happen, and an operator's command is written once the player has
accepted it but stamped with the moment of the press. *Fix:* nothing relies on
file order. `seq` proves no line is missing; the loader sorts by `t_mono`; the
check only flags time going backwards by more than two seconds.

**6. `session_start` wasn't always the first line.**
Found by the tests: between opening the file and writing `session_start`,
events from the robot's idle loop could slip in first — and a file that does
not start with `session_start` is BROKEN. *Fix:* `session_start` is written
before the session's lock is released.

**7. The central logger had to stay the same object.**
A first version of `configure()` replaced `log` with a new logger — so a module
that had already done `from eepy.logs import log` would keep writing into the
old one. *Fix:* settings are applied to the existing logger, in place.

**8. A hard crash cannot clean up after itself.**
`kill -9`, a power cut, or `os._exit` runs no exit handlers. *Fix:* there is no
fix — so the design makes it harmless and visible instead: everything up to the
last flush survives, and the verdict says INCOMPLETE. The test suite simulates
exactly this with `os._exit`.

## Not verified yet

- **A full lab session with a real face.** The real vision process
  (`python -m eepy.perception`, A9) has recorded into a session from the
  laptop camera (`check` says OK), but not yet during an actual study session
  with a participant and the panel side by side.
- **A long session.** Tested for seconds to minutes; an hour-long pilot will be
  the first real test of file sizes and the flush rate.
