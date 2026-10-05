# Working with session data

Every session run through the Wizard panel is saved automatically. This page
covers where it goes, what is in it, and how to get it into Python.

## Where it is

```
data/sessions/
  2026-10-06_P03_wizard/        <date>_<participant>_<mode>
    meta.json                   who, when, which mode, which code version, notes
    events.jsonl                everything that happened, one line per event
    features.csv                what the camera measured, ~15 rows a second (once perception runs)
    features.json               which columns features.csv has
    events.vision.jsonl         events from the vision process: camera status and statistics
    snapshot/                   the exact behaviour and config files used
```

The **mode** is the experimental condition (`wizard`, `rules`, `hmm`,
`hmm_bandit`). It is set in `config/eepy.yaml`, or with `--mode` when starting
the panel, and cannot change mid-session.

## Look at it

From the EEPY folder, with `.venv-robot` active:

```bash
python -m eepy.logs.list                 # every session, with a verdict
python -m eepy.logs.summary P03          # one session, readable
python -m eepy.logs.check                # is everything complete and intact?
```

Every session gets a verdict:

| Verdict | Meaning | Use it? |
|---|---|---|
| **OK** | Complete and intact. | Yes. |
| **INCOMPLETE** | The panel crashed or was killed, so the session never ended properly. Everything up to that moment (minus at most the last second) is there. | Yes — but note when it stops. |
| **BROKEN** | A file is missing, unreadable, or was edited by hand. | Not before someone has looked. |

## Load it in Python

```python
from eepy.logs.load import load_session, sessions_table, all_annotations

s = load_session("P03")        # or the full folder name, or "latest"
s.events          # everything, in time order
s.annotations     # what the operator observed — the HMM labels
s.behaviors       # one row per behaviour: start, end, outcome, who triggered it
s.notes           # free-text notes made during the session
s.features        # camera features, or None
s.meta            # meta.json

sessions_table()  # one row per session
all_annotations() # every session's labels in one table (BROKEN sessions left out)
```

Every table has three time columns: **`t`** (seconds since the session
started — usually what you want), **`t_mono`** (for lining up with other
data), and **`t_wall`** (the local date and time).

Everything the robot *said* is in `s.events` too: `speech_started` has the exact
words, which engine spoke them, and `latency_ms`; `speech_interrupted` and
`speech_failed` say when words were cut off or never came out. These are the
robot's words, not the participant's.

`events.vision.jsonl` holds the camera's health: `camera_stats` every 10 s (frames per
second, `latency_ms`, `dropped` frames), plus `camera_error` / `camera_reconnected`
when the camera dropped out. Check these before trusting a stretch of features:
a gap in the camera is a gap in what perception could see.

`source` says who caused something: `wizard` (the operator), `auto` (the
autonomous system, once it exists), `idle`, `system`, or `perception`.

## Rules — the data is about real people

- **Participant IDs are codes like `P03`, never names.** The panel refuses
  anything else. The list linking codes to people is kept *outside* this
  project, by whoever is responsible for consent.
- **Nothing in `data/` ever goes into git.** It is ignored on purpose. Share
  sessions through the team's agreed storage, not GitHub.
- **No images, audio or video are ever logged** — only what was detected in
  them, and camera statistics. The logger refuses raw image or audio data outright.
- **Nothing is recorded outside a session.** The camera's features are thrown
  away unless a session is running.
- **Notes are part of the data.** Write what you observed, not opinions about
  the person, and no names.
- **Never edit a session file by hand.** Add a note instead. Hand edits are
  detected and make the session BROKEN.

## Something looks wrong

- **INCOMPLETE after a crash:** nothing to fix. Write down what happened.
- **BROKEN:** `python -m eepy.logs.check <session>` says exactly which file
  and what is wrong. Keep the folder as it is and ask in the team chat.
- **The panel says the last session "did not end cleanly":** the panel crashed
  during that session. Its data is kept; the check will mark it INCOMPLETE.

How the logging works, and why it is built that way:
[eepy/logs/README.md](../eepy/logs/README.md).
