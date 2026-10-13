# Working with session data

Every session run through the Wizard panel is saved automatically. This page
covers where it goes, what is in it, and how to get it into Python.

## Where it is

```
data/sessions/
  2026-10-06_P03_wizard/        <date>_<participant>_<mode>
    meta.json                   who, when, which mode, which code version, notes
    events.jsonl                everything that happened, one line per event
    features.csv                face features, ~15 rows a second (python -m eepy.perception)
    features.json               which columns features.csv has
    aus.csv / aus.json          action units, one row per result (only with the optional extra)
    state.csv / state.json      the participant's state, once a second (focused, drowsy, ...)
    events.vision.jsonl         from the vision process: blinks, yawns, nods, face lost/back,
                                per-second summaries (PERCLOS ...), camera status
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

**Face features.** `features.csv` has one row per analysed frame: eye openness
(`ear`, `eye_closure`), mouth openness (`mar`), head angles, `face_present`, ...
`events.vision.jsonl` has the events (`blink`, `yawn`, `nod`, `face_lost`,
`face_back`) and, every second, a `face_window` with PERCLOS, blink rate, yawn and
nod counts over the last 10 s and 60 s. Every column and field is explained in plain
words in [eepy/perception/README.md](../eepy/perception/README.md). Where
`blink_confidence` is `low`, the camera was too slow to count blinks reliably.

**Calibration**: `meta.json` has a `calibration` summary (new / reused / skipped /
failed), and `events.vision.jsonl` the full `calibration` event (baseline values, quality
checks). `features.csv` has `calibrated` (1/0) and the normalised columns `ear_rel`,
`mar_rel`, `head_*_rel`, `face_size_rel`; `face_window` has `blink_rate_rel` and
`blink_mean_ms_rel`. **Use the normalised ones to compare people**, and check
`calibrated`. See [eepy/perception/calibration/README.md](../eepy/perception/calibration/README.md).

**Gaze**: `features.csv` also has `gaze_zone` (screen / notes / down_other / away /
no_face), `gaze_on_work`, `gaze_confidence`, `gaze_mode` and `away_s`; the events
`looked_away` / `back_on_work` mark each look-away, and `gaze_window` summarises 10 s /
60 s. Check `gaze_calibrated` (or the `gaze_status` event): uncalibrated sessions used a
default work area. See [eepy/perception/gaze/README.md](../eepy/perception/gaze/README.md).

**User state**: `state.csv` has one row a second: `state` (focused / distracted / drowsy /
very_drowsy / absent / unknown), `pending` (a state waiting out its dwell time, or for the HMM
the state becoming more likely), `calibrated`, the `rules` that fired, the `reason` as shown in
the panel, `p_<state>` scores, and `estimator` (`rules` or `hmm`; empty in sessions from before
A16 = rules). For the rules the scores are one-hot. For the HMM (hmm mode, from A16) they are its
probabilities over focused / distracted / drowsy / very_drowsy (`p_absent` and `p_unknown` are
always 0: those are flags, shown in `state`), and the row also has `most_likely` (the most likely
of the four), `confidence` (P of the shown state), `uncertainty` (0 certain, 1 uniform),
`updated` (0 while absent / unknown: the belief only drifted), `n_features` (signs used) and
`evidence` (the strongest signs, e.g. `perclos->sleepy +0.12`).

`events.vision.jsonl` has a `state_changed` event at every change: `from`, `to`, the reason, the
evidence (feature, value, threshold) and what was skipped; the HMM adds `probs`, `most_likely`,
`confidence`, `uncertainty`, `pending`, `updated` and `hmm_evidence` (its top signs). A
`state_model` event, once per session, says which estimator ran with which parameter file
(`params`, `version`, `params_source` hand_set / fitted, `sha256`), and the session prior applied
(`prior`). A `prior.json` in the session folder (only with the panel's `--session-prior`) is the
starting belief that was asked for.

Rules sessions from before and after A16 differ slightly: the `microsleep` and `eyes_closed_now`
rules now need the eyes judged (the snapshotted `state_rules.yaml` shows which). In `au_window`,
`AU43_*` only counts moments of looking forward from A16 on (`results_eyes_*` counts them);
older sessions include looking down.

Re-run the states with other thresholds or HMM numbers, and compare them with the operator's
markers: `python -m eepy.state.replay <session> [--estimator rules|hmm|both]`. See
[eepy/state/README.md](../eepy/state/README.md). To *see* every second of the HMM's decision
(signs, belief, gate, robot) on a page: `python -m eepy.state.explain <session>`, written to
`data/explain/` ([docs/EXPLANATION_VIEWER.md](EXPLANATION_VIEWER.md)).

**Autonomy** (rules and hmm modes): every nudge the robot chose is a `nudge_decision` (ladder, level,
behaviour, tone, reason, `propensity`, and the `context` it decided on), followed by exactly one
`nudge_outcome` with the same `decision_id`: `success`, `no_reaction`, `dismissed`, `snoozed`,
`interrupted_focus` (a false alarm) or `censored` (no fair verdict), with `seconds` and a `detail`.
`role_changed` and `escalation` events (breaks, the wind-down, goodnight, pauses, replies; in the
hmm mode also `waiting_for_confidence` / `confidence_wait_ended` when the confidence gate holds a
ladder back, with the confidence and the threshold) fill in the rest. The robot's own behaviours have `source` `auto`, the operator's `wizard`. The markers
`user_accepted` / `user_snoozed` / `user_dismissed` are the participant's answers. See
[eepy/interaction/README.md](../eepy/interaction/README.md#what-is-recorded-for-the-analysis-and-the-bandit).

**Action units** (optional): `s.aus` has one row per result, timed to the frame it came
from; `au_window` events hold their 10 s / 60 s averages and the cross-checks with the eye
and yawn features. See [eepy/perception/aus/README.md](../eepy/perception/aus/README.md).

`events.vision.jsonl` also holds the camera's health: `camera_stats` every 10 s (frames per
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
