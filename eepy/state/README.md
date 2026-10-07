# The user's state: focused, distracted, drowsy…

From the camera's face features, EEPY decides once a second which **state** the
participant is in. The Wizard panel shows it in its status bar with the reason,
the preview shows it on the picture, and every second of it is recorded.

This first version uses **threshold rules**: readable "if this, then that"
rules in [`config/state_rules.yaml`](../../config/state_rules.yaml) (task A13).
Later an HMM (A16) does the same job, learned from data. The rules are the
baseline it must beat, so they are built to be as good as simple rules can be,
not a strawman. Both give their answer in the same form, so the rest of EEPY
(the escalation in A14, the panel, the logs) works with either.

## The states

| State | Means | Typical reason shown |
|---|---|---|
| **focused** | Working: face on the screen or the notes, no signs of drowsiness. | *face on the work; no signs of drowsiness or distraction* |
| **distracted** | Looking away from the work (phone, window, a person) for a while: not a glance. | *looking away for 7 s* |
| **drowsy** | Signs of sleepiness: eyes often closed, long or slow blinks, yawning, a nod. | *eyes closed 14% of the last minute; yawned in the last minute* |
| **very drowsy** | Strong signs: eyes shut for 1.5 s or more at once, nodding off repeatedly, eyes closed a quarter of the time. | *eyes closed for 1800 ms at once (microsleep)* |
| **absent** | No face in view: left the desk, or turned fully away. | *no face for 100% of the last 10 s* |
| **unknown** | Not enough reliable camera data to say: just started, camera too slow, or no pictures. | *only 3 s of camera data so far* |

**uncalibrated** after the state means the participant wasn't calibrated
([calibration](../perception/calibration/README.md)). The rules then use
default values for the eyes, head and gaze zones, so the state is less reliable.

## The rules, in plain words

About once a second every rule is checked against the newest features. Most look
at the **last minute** (some at the last 10 s), so one blink or one glance
counts for little; a pattern counts for a lot.

### Very drowsy (any one of these)

| Rule | Fires when | Why this value |
|---|---|---|
| `perclos_very_high` | eyes closed **≥ 25 %** of the last minute | Above ~25 % "PERCLOS" the drowsiness research sees severe drowsiness and microsleep risk. |
| `microsleep` | one eye closure lasted **≥ 1.5 s** in the last minute | That long is a microsleep, not a blink. |
| `eyes_closed_now` | the eyes have been closed **≥ 1.5 s** right now | The same, while it's happening. |
| `repeated_nodding` | the head dropped (nodded off) **≥ 2 times** in a minute | The classic "fighting sleep" pattern. |
| `many_drowsy_signs` | the drowsy points (below) reach **2.0** | Many signs of sleepiness at once. |

### Drowsy (by points)

Each sign adds **points**; at **1.0** the person counts as drowsy. One strong
sign is enough; weaker ones only count together. A single yawn, for example, is
never drowsy on its own: people also yawn when bored.

| Rule | Sign | Points | Why |
|---|---|---|---|
| `perclos_high` | eyes closed **≥ 12 %** of the last minute | **1.0** | 12–15 % is where drowsiness usually starts: enough on its own. |
| `nodding` | nodded off once in the last minute | 0.7 | A strong sign, but one quick look down can look like a nod. |
| `slow_blinks` | blinks **1.5×** as long as the person's usual (uncalibrated: on average **≥ 300 ms**) | 0.6 | Blinks get longer with fatigue; alert blinks are 100–250 ms. |
| `long_blinks` | **≥ 3** blinks longer than half a second in a minute | 0.6 | Eyes "resting" rather than blinking. |
| `yawning` | a yawn in the last minute | 0.5 | Weak alone; adds up with the others. |
| `au_eyes_closing` | the "eyes closed" action unit (AU43) active **≥ 10 %** of the minute | 0.5 | Only when the optional [action units](../perception/aus/README.md) run. |

### Absent

| Rule | Fires when | Why |
|---|---|---|
| `no_face` | no face for **≥ 80 %** of the last 10 s | Left the desk or turned fully away; a few lost frames don't count. |

### Distracted (any one of these)

| Rule | Fires when | Why |
|---|---|---|
| `looking_away_now` | looking away from the screen and notes for **≥ 5 s** right now | A glance isn't distraction; 5 s of looking away is. |
| `often_away` | looking away **≥ 35 %** of the last minute (while at the desk) | Repeatedly looking away, even in short bursts. |
| `looking_down_at_lap` | looking down, but not at the notes, **≥ 50 %** of the last 10 s | The phone-in-the-lap pattern. |

"Looking away" uses the gaze zones of [A11](../perception/gaze/README.md): the
screen and the notes are **work**; the rest is away.

### Unknown

| Rule | Fires when |
|---|---|
| `starting_up` | less than **5 s** of camera data so far |
| `camera_too_slow` | fewer than **5** pictures a second |
| *(no pictures)* | no pictures for **3 s** (`gap_seconds`): the state doesn't freeze on the last one |

### When several states fire: the order

**very drowsy > drowsy > absent > distracted > focused**

- **Drowsiness before distraction.** A drowsy head drops and the eyes go down,
  which *looks* like looking away. But the right response is about sleep, not
  focus.
- **Absent before distracted.** With no face there, "distracted at the desk"
  doesn't apply.
- **An empty desk is never "drowsy".** The drowsiness rules need the face
  visible at least half of the last 10 s. Without that, the minute-long window
  would still remember the closed eyes from before the person left.

When nothing fires, the state is **focused**.

### No flicker: dwell times

A new state is only shown after it has lasted a moment (`dwell: enter`), and a
milder state only after some time **without any sign** of the current one
(`dwell: leave`):

| Becoming | after | Leaving | after |
|---|---|---|---|
| focused | 5 s | drowsy | 20 s without drowsy signs |
| distracted | 5 s (after the 5 s of looking away) | very drowsy | 20 s without signs |
| drowsy | 10 s | | |
| very drowsy | **3 s** (urgent) | | |
| absent | 5 s | | |
| unknown | 2 s | | |

Moving to a more serious state is quick; coming back down is slow. One
alert-looking moment doesn't undo drowsiness. While a state waits out its dwell
time, the panel shows it as **"focused → drowsy?"**.

### Evidence that is skipped, not used

Some rules can't be trusted in some moments. They are then **skipped**: they
count neither for nor against the state.
- **Blink rules** (`slow_blinks`, `long_blinks`) need a camera fast enough to
  see blinks (about 14 pictures a second; see the perception README).
- **PERCLOS rules** need at least 30 s of data in the minute: a share of a few
  seconds is noise.
- **Drowsiness rules** need the face visible (see above).
- **The action-unit rule** needs the action units running.

The replay and the logs list what was skipped and why.

## Where you see it

- **The Wizard panel's status bar:** a **state** pill (green focused, amber
  distracted or drowsy, red very drowsy) with the main reason next to it.
  Hover over it for more. *"no word from the vision program"* means
  `python -m eepy.perception` isn't running or has stopped.
- **The preview** (`python -m eepy.camera.preview`): a STATE line with the
  reason, and the drowsy points.
- **The vision terminal:** `state focused` on the status line every 5 seconds.
- **The session's data:**
  - `state.csv` holds every second's state, its reason and the rules that fired;
  - `events.vision.jsonl` holds a `state_changed` event at each change, with the
    reason, the values and the thresholds.

  See the [DATA_GUIDE](../../docs/DATA_GUIDE.md).

## Changing a threshold

All numbers are in [`config/state_rules.yaml`](../../config/state_rules.yaml),
each with a comment explaining why. To change one:

1. **Open the file** in any text editor and change the number, e.g. make
   distraction react sooner:
   ```yaml
   - id: looking_away_now
     state: distracted
     when: away_current_s >= 4        # was 5
   ```
2. **Try it on recorded sessions first**, without a camera (see *Replay* below):
   ```bash
   python -m eepy.state.replay P03                  # with the current rules
   python -m eepy.state.replay P03 --rules my.yaml  # with your copy
   ```
   Compare the timelines and the agreement with the operator's markers.
3. **Restart the vision program** (`python -m eepy.perception`). It reads the
   file at start-up.

A mistake stops the program with a message that says what and where, e.g.:

```
config/state_rules.yaml: rule "looking_away_now": unknown feature "away_curent_s"
(did you mean "away_current_s"?). The features are listed in eepy/state/README.md.
```

**What a rule looks like:**

```yaml
- id: slow_blinks                       # a short name (logs, replay)
  state: drowsy                         # very_drowsy, drowsy, absent or distracted
  points: 0.6                           # drowsy rules only
  when: blink_mean_ms_rel_60s >= 1.5    # <feature> <compare> <number>
  uncalibrated: blink_mean_ms_60s >= 300   # used instead when not calibrated
  needs: [face_present_ratio_10s >= 0.5, blink_confidence_60s == ok]   # else skipped
  says: "blinks {value:.1f}x as long as usual"    # the reason in the panel
  says_uncalibrated: "slow blinks ({value:.0f} ms on average)"
```

Comparisons: `>=  >  <=  <  ==  !=`. In `says`, `{value}` is the measured value
and `{threshold}` the limit. `{value:.0%}` shows 0.25 as 25%; `{value:.0f}`
shows a whole number. You can also add rules, change `priority`, `points_needed`
and the `dwell` times. Remember that the HMM will be compared against these
rules, so keep notes on what you changed and why.

### The features a rule can use

`_10s` / `_60s` = over the last 10 or 60 seconds.

| Feature | Meaning |
|---|---|
| `perclos_10s`, `perclos_60s` | share of the time (face visible) with the eyes ≥ 80 % shut |
| `blink_rate_*`, `blink_mean_ms_*`, `blink_max_ms_*`, `long_blinks_*` | blinks per minute, mean and longest duration (ms), blinks > 500 ms |
| `blink_rate_rel_*`, `blink_mean_ms_rel_*` | the same relative to the person's usual (calibrated only; 1.5 = 50 % more) |
| `blink_confidence_*` | `ok`, or `low` when the camera is too slow for blinks |
| `eyes_closed_now_s` | how long the eyes have been closed right now (s) |
| `yawns_*`, `nods_*` | how many |
| `look_away_ratio_*`, `pitch_mean_*`, `pitch_var_*` | head turned away (share), head pitch (degrees, chin down +) |
| `face_present_ratio_*`, `face_absent_ratio_*` | share of the time with / without a face |
| `screen_ratio_*`, `notes_ratio_*`, `down_other_ratio_*`, `away_ratio_*`, `no_face_ratio_*` | share of the time looking at each gaze zone |
| `off_work_ratio_*` | share of the time not looking at the work (an empty desk included) |
| `gaze_off_ratio_*` | share of the time **at the desk** that the gaze was off the work |
| `away_current_s` | how long the person has been looking away right now (s) |
| `away_episodes_*`, `away_mean_s_*`, `away_longest_s_*` | looks away: how many, mean and longest (s) |
| `fps_*`, `coverage_s_*` | pictures per second; seconds of data in the window |
| `baseline_calibrated`, `gaze_calibrated` | `true` when calibrated |
| `AU43_active_60s`, `AU04_mean_10s`, … | action units: share of time active, mean (optional) |
| `au_status`, `au_rate` | action units `running` / `paused` / …; results per second |
| `drowsy_points` | the drowsy rules' points added up |

## Replay: try the rules on recorded sessions

`replay` runs the rules over a session that was recorded with the vision program
running. It needs no camera and no robot, and works in either environment.

**macOS / Linux:**
```bash
cd EEPY
source .venv-vision/bin/activate          # or .venv-robot
python -m eepy.state.replay latest        # the newest session
python -m eepy.state.replay P03 --csv states_P03.csv
```

**Windows (PowerShell):**
```powershell
cd EEPY
.venv-vision\Scripts\Activate.ps1          # or .venv-robot
python -m eepy.state.replay latest
```

It prints:

```
  from   to     state         why
  00:00  00:10  unknown       only 1 s of camera data so far
  00:10  00:41  focused       face on the work; no signs of drowsiness or distraction
  00:41  01:02  distracted    looking away for 5 s; looking down (not at the notes) 82% of the last 10 s
  ...
  Time per state: focused 71%   distracted 14%   drowsy 9%   unknown 6%

  Agreement with the operator's markers:
    phone_picked_up    3/4 agreed (distracted within +0..+15 s), median +9 s after the marker
    yawn               2/2 agreed (drowsy/very_drowsy within -20..+30 s), median +4 s after the marker

  Same as the live run: 1203/1203 seconds (100.0%). Lower means the rules file changed since.
```

**Agreement** compares the states with the markers the operator pressed in the
Wizard panel:

| Marker | Agrees when the state was… |
|---|---|
| phone picked up, looked away | **distracted** within 15 s after it |
| back on task | **focused** within 20 s after it |
| yawn, eyes closing | **drowsy** or **very drowsy** from 20 s before to 30 s after it |
| user went to bed | **absent** within 60 s after it |

The time ranges allow for the dwell times and for the operator pressing a
moment late. The other markers (replied, dismissed) aren't about the state and
are left out. Use this to tune the thresholds (A17) and later to compare the
HMM with the rules on the same sessions (A18).

## Self-test: the states with a real person (about 3 minutes)

```bash
python -m eepy.perception.selftest --states --no-aus     # in .venv-vision
```

It guides you through five parts, with a beep at the start of each:

| Part | Time | Do | Expect |
|---|---|---|---|
| 1. Work normally | 40 s | read or type as usual | **focused** after ~10 s (data, then the dwell) |
| 2. Phone | 20 s | look down at an imaginary phone in your lap | **distracted** after ~10 s |
| 3. Back to work | 20 s | look at the screen | **focused** after ~5 s |
| 4. Act sleepy | 45 s | close your eyes slowly for about a second, every few seconds; yawn once or twice | **drowsy** (or very drowsy) after ~20–30 s |
| 5. Leave | 30 s | leave the camera's view; count to 30 and come back | **absent**, after ~20 s (leaving drowsy first) |

It reports, per part, PASS or CHECK and how long the switch took, then every
state change with its reason. Calibrate first for the best result
([calibration](../perception/calibration/README.md)); uncalibrated, the
changes are marked *[uncalibrated]*.

## For developers

- **The interface** (`estimate.py`). Every estimator has
  `update(features) -> StateEstimate` and `reset()`. A `StateEstimate` holds:
  - `state`;
  - `scores` (one-hot here, probabilities for the HMM);
  - `reasons` (rule, feature, value, threshold, text);
  - `calibrated`, `skipped`, `since`, `pending`, `estimator`, `values`.

  Estimators register by name (`register("rules", ...)`), and
  `make_estimator(name)` builds one. `for_mode(mode)` picks "hmm" for the HMM
  modes once A16 registers it. Downstream code (A14, panel, logs) must only use
  this interface.
- **The features** (`features.py`). `assemble()` turns the logged window
  summaries (face_window, gaze_window, au_window) into one flat dict. It is
  **the only** builder, used live (`runner.from_result`) and in replay
  (`replay.session_features`). `catalog()` lists the names the YAML may use.
- **The rules** (`rules.py`, `config.py`). The YAML is checked completely at
  start-up. `RulesEstimator.evaluate()` gives the raw state (no dwell);
  `update()` adds the dwell times.
- **Live** (`runner.py`).
  - `StateTracker` feeds the estimator and fills camera gaps with "no data"
    (at `gap_seconds`, then every second). It also resets on a new session, so
    live and replay start alike.
  - `StateRecorder` writes `state_changed` and `state.csv`.
  - `StatePublisher` sends `STATE` over the camera relay to the panel.
- **Same states live and in replay.** This is tested
  (`test_replay_gives_the_same_states_as_live`): a scenario with a phone,
  drowsiness, an empty desk and a camera pause, fed live and then replayed
  from the written files.
- **Tests:** `tests/test_state.py` (each rule, points, priority, dwell,
  uncalibrated, low confidence, YAML errors, replay, the scenario report, the
  relay) and `tests/test_perception.py` (pipeline → state).
