# The user's state: focused, distracted, drowsy…

From the camera's face features, EEPY decides once a second which **state** the
participant is in. The Wizard panel shows it in its status bar with the reason,
the preview shows it on the picture, and every second of it is recorded.

There are two ways to decide, and EEPY has both:

- **Threshold rules** (task A13): readable "if this, then that" rules in
  [`config/state_rules.yaml`](../../config/state_rules.yaml). Used in the **rules** mode.
- **The HMM** (task A16): a *belief*, a probability for each state, updated
  every second from the same signs, with numbers in
  [`config/state_hmm.yaml`](../../config/state_hmm.yaml). Used in the **hmm** mode.
  See [The HMM: a belief instead of a verdict](#the-hmm-a-belief-instead-of-a-verdict) below.

In both modes the robot acts on the state: when and how it nudges is in
[../interaction/README.md](../interaction/README.md). The rules are the baseline
the HMM must beat, so they are built to be as good as simple rules can be, not a
strawman. Both give their answer in the same form, so the rest of EEPY (the
escalation, the panel, the logs) works with either.

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
| `microsleep` | one eye closure lasted **≥ 1.5 s** in the last minute (with 20 s of eyes seen looking forward) | That long is a microsleep, not a blink. |
| `eyes_closed_now` | the eyes have been closed **≥ 1.5 s** right now (eyes seen in the last 10 s) | The same, while it's happening. |
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
| ~~`au_eyes_closing`~~ | *off for now:* the "eyes closed" action unit (AU43) used to read looking down as closing the eyes | — | Since A16, AU43 only counts moments of looking forward. The rule stays off so rules sessions before and after A16 stay comparable; it comes back after a live check. |

Since A16, `microsleep` and `eyes_closed_now` also need the eyes to have been
seen looking forward, like the other eye rules (a fix: looking down could look
like a long closure). On sessions recorded after the eye gating this changes
almost nothing; it changes the replay of older recordings.

### Absent

| Rule | Fires when | Why |
|---|---|---|
| `no_face` | no face for **≥ 80 %** of the last 10 s | Left the desk or turned fully away; a few lost frames don't count. |

### Distracted (any one of these)

| Rule | Fires when | Why |
|---|---|---|
| `looking_away_now` | looking away from the screen and notes for **≥ 5 s** right now | A glance isn't distraction; 5 s of looking away is. |
| `often_away` | looking away **≥ 35 %** of the last minute (while at the desk) | Repeatedly looking away, even in short bursts. |
| ~~`looking_down_at_lap`~~ | *off in the base version:* reading notes or typing looked the same | Looking **down counts as work** now. A phone in the lap is detected later (A29). |

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
- **Every eye rule** (PERCLOS, blinks) needs at least **20 s of eyes seen while
  looking forward** in the last minute (`eyes_measured_s_60s`). Eyes are only
  judged while the head is near its normal "looking at the screen" posture:
  looking down at notes or a keyboard, or aside, makes **open** eyes look half
  closed to the camera. In a real session that made reading count as
  microsleeps. Sleepiness is therefore only seen while the person looks at
  the screen; nodding off is still caught by the nod rules.
- **Blink rules** (`slow_blinks`, `long_blinks`) also need a camera fast enough
  to see blinks (about 14 pictures a second; see the perception README).
- **Drowsiness rules** need the face visible (see above).

The replay and the logs list what was skipped and why.

## Where you see it

- **The Wizard panel's status bar:** a **state** pill (green focused, amber
  distracted or drowsy, red very drowsy) with the main reason next to it.
  Hover over it for more. *"no word from the vision program"* means
  `python -m eepy.perception` isn't running or has stopped. With the HMM, the
  pill also shows how sure it is, and a small bar shows the whole belief.
- **The preview** (`python -m eepy.camera.preview`): the HMM's state and belief bar
  (or, with `--estimator rules`, a STATE line with the reason and the drowsy points).
- **The vision terminal:** `state focused 97% (hmm)` on the status line every 5 seconds.
- **The session's data:**
  - `state.csv` holds every second's state, its reason and the rules that fired
    (HMM: also the probabilities, the confidence and the strongest signs);
  - `events.vision.jsonl` holds a `state_changed` event at each change, with the
    reason, the values and the thresholds, and one `state_model` event saying
    which estimator and parameter file ran.

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
| `perclos_10s`, `perclos_60s` | share of the time the eyes were judged (looking forward) with the eyes ≥ 80 % shut |
| `eyes_measured_s_10s`, `eyes_measured_s_60s` | seconds the eyes were judged (looking roughly forward) |
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
| `AU43_active_60s`, `AU04_mean_10s`, … | action units: share of time active, mean (optional). AU43 counts only moments of looking forward (since A16) |
| `au_status`, `au_rate` | action units `running` / `paused` / …; results per second |
| `au_results_*`, `au_results_eyes_*` | action-unit results in the window; those while looking forward |
| `blink_count_*` | blinks seen while the eyes were judged (blink rate × judged seconds) |
| `face_seen_s_*` | seconds with a face in the window |
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

The HMM, and both side by side (same commands on Windows):

```bash
python -m eepy.state.replay latest --estimator hmm
python -m eepy.state.replay latest --estimator both            # rules | hmm | where they disagree
python -m eepy.state.replay P03 --estimator hmm --params my_hmm.yaml    # try other HMM numbers
python -m eepy.state.replay P03 --estimator hmm --prior none   # without the session's prior
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

## Seeing why: the explanation viewer

`python -m eepy.state.explain latest --open` (or `--demo`, without a recording) writes a
page that shows, for every second of a session, the whole decision: the signs and how
much each pushed, the belief, the shown state, whether EEPY was sure enough to act, and
what the robot did. Scrub through the timeline or play it in slow motion. In the hmm mode
the panel shows the same chain in one line. Guide:
[docs/EXPLANATION_VIEWER.md](../../docs/EXPLANATION_VIEWER.md).

## Self-test: the states with a real person (about 3 minutes)

```bash
python -m eepy.perception.selftest --states --no-aus     # in .venv-vision
```

It runs the rules **and** the HMM on the same pictures and reports both.

It guides you through five parts, with a beep at the start of each:

| Part | Time | Do | Expect |
|---|---|---|---|
| 1. Work normally | 40 s | read or type as usual | **focused** after ~10 s (data, then the dwell) |
| 2. Phone | 20 s | look at an imaginary phone **beside you** (turn your head to the side); a phone in the lap counts as looking down = work in the base version | **distracted** after ~10 s |
| 3. Back to work | 20 s | look at the screen | **focused** after ~5 s |
| 4. Act sleepy | 45 s | close your eyes slowly for about a second, every few seconds; yawn once or twice | **drowsy** (or very drowsy) after ~20–30 s |
| 5. Leave | 30 s | leave the camera's view; count to 30 and come back | **absent**, after ~20 s (leaving drowsy first) |

It reports, per part, PASS or CHECK and how long the switch took, then every
state change with its reason, for the rules and the HMM, then both side by side
with the HMM's belief every 5 seconds. The HMM is slower to say drowsy (about
35–40 s into "Act sleepy" on simulated data): a CHECK there is the known
difference, not a fault. Calibrate first for the best result
([calibration](../perception/calibration/README.md)); uncalibrated, the
changes are marked *[uncalibrated]*.

## The HMM: a belief instead of a verdict

*For everyone. The technical version follows in [The HMM model](#the-hmm-model-for-the-report).
Why each choice was made, its limits and what could come later:
[docs/A16_HMM_DECISIONS.md](../../docs/A16_HMM_DECISIONS.md).*

### What changes

The rules give a **verdict**: "drowsy", because enough points added up. The HMM
keeps a **belief**: for example *focused 12 %, distracted 3 %, drowsy 70 %, very
drowsy 15 %*. Every second it does two things:

1. it lets the belief **drift** a little, the way people tend to change state
   (distraction comes and goes in about a minute; drowsiness builds up over
   many minutes);
2. it **updates** the belief with what the camera sees: each sign makes some
   states more likely and others less.

It uses the same signs as the rules: eye closure, blink length, long blinks, a
microsleep, eyes shut right now, yawns, nods, looking away (over the minute and
right now), and, when they run, two action units (eyes closed, jaw drop).

### Reading the belief bar

In the Wizard panel, the bar after the state pill shows the belief:
**green** focused, **amber** distracted, **orange** drowsy, **red** very drowsy.
The pill says the state and how sure the HMM is of it, e.g. *drowsy · 82 %*.
Hover over the pill for the four numbers and the reasons.

- A **grey, faded** bar means the HMM is *on hold*: nobody at the desk (absent)
  or no usable camera data (unknown). The belief only drifts then.
- *→ distracted?* after the state means another state is becoming more likely.
- The percentage is a **decision score**, not a promise: "90 %" does not mean
  right 9 times out of 10 (the minute-long signs are counted several times a
  minute, on purpose, so it reacts in time). A18 measures how well it matches.

### Reading the reasons

The reasons next to the pill say which signs pushed the belief, in words:

- *eye closure (eyes closed 25% of the last minute) **clearly suggests** drowsiness*
- *looking away now (looking away for 14 s) **suggests** distraction*
- for **focused**, what speaks against the most likely alternative:
  *eye closure (eyes closed 2% of the last minute) **speaks against** drowsiness*
- *still drowsy (78 % sure): no new signs of drowsiness right now*: the belief
  holds; nothing new came in.

The words go from *slightly suggests* to *strongly suggests* with the strength
of the sign. **Related signs are not counted twice.** The six eye signs measure
one thing from different angles (one long eye closure moves all of them), so
together they count as less than 2 signs, not 6. The same for a yawn and the jaw
drop, and for looking away over the minute and right now.

### Absent and unknown

These are decided outside the belief:

- **absent**: no face for 8 of the last 10 seconds, for 5 seconds (as the rules).
  Back as soon as the face is there half of the last 10 seconds.
- **unknown**: no pictures, less than 5 s of camera data, a camera slower than
  5 pictures a second, or the first 5 seconds after a start.

While absent or unknown, nothing is learned from the camera: the belief only
drifts. A short break barely changes it; after a long one it has relaxed to
"usually focused, sometimes drowsy".

### When EEPY acts

In the hmm mode the robot only starts a refocus or bedtime episode, and only
plays a nudge, when the HMM is at least **70 %** sure; the firmer *direct* tone
also needs **85 %**. Below that the panel says *not sure enough yet: distracted
62 % (acts at 70 %)*. These numbers are in
[`config/escalation.yaml`](../../config/escalation.yaml) under `confidence:`;
they do nothing in the rules mode.

### How fast it reacts, and how it differs from the rules

Measured on simulated sessions (seconds after the behaviour starts):

| Change | Rules | HMM |
|---|---|---|
| start → focused | 10 s | 9 s |
| looking at a phone → distracted | 10 s | 12 s |
| back to work after the phone → focused | not within 20 s | 13 s |
| acting sleepy → drowsy | 10–17 s | 35–40 s |
| 2-s eye closures every 8 s → drowsy / very drowsy | 4 s / 4 s | 33 s / 73 s |
| leaving → absent | 25 s | 13 s |

On purpose, the HMM:

1. says **drowsy later** than the rules;
2. needs **eye signs** for drowsiness: yawns or nods with eyes that look alert
   are not enough (the rules: a nod every 25 s is very drowsy);
3. says **focused again sooner** after a distraction.

It is also less fooled by lowered eyelids while reading (eyes that look half
shut with no blinks), which made the rules say drowsy after about a minute.

### Changing a number

All numbers are in [`config/state_hmm.yaml`](../../config/state_hmm.yaml). Every
number has a `why`: one line saying where it comes from. To change one:

1. **Change the number, and its `why`.** A missing `why` stops the program with a
   message. Raise `version` by one.
2. **Try it on recorded sessions first:**
   `python -m eepy.state.replay P03 --estimator both --params my_hmm.yaml`
3. **Restart the vision program** (`python -m eepy.perception`).

Three examples:

- *Distraction should count sooner.* Under `features: away_now: params:
  distracted:`, move probability towards the longer bins. Or, under
  `transitions_per_hour: focused:`, raise `distracted` (more expected switches).
- *Drowsiness should be noticed faster.* Under `groups: eye_closure:`, lower
  `correlation` (e.g. 0.5 → 0.4: the eye signs count a bit more together), or
  lower `evidence_seconds` of `perclos` (10 → 8). Both make it more confident,
  too.
- *A single nod should count more.* Under `features: nods: params:`, raise
  `drowsy: rate_per_minute` (0.8 → 1.2), or lower `focused` (0.03 → 0.02).

A mistake says what and where, e.g.:

```
config/state_hmm.yaml: features.perclos.params.drowsy: mean must be between 0 and 1
(exclusive), not 1.2.
```

### Trying it

**macOS / Linux:**
```bash
cd EEPY
source .venv-vision/bin/activate
python -m eepy.state.replay latest --estimator both      # no camera needed
python -m eepy.perception.selftest --states --no-aus     # camera: rules and HMM side by side
python -m eepy.camera.preview                            # camera: the HMM's belief on the picture
```

**Windows (PowerShell):**
```powershell
cd EEPY
.venv-vision\Scripts\Activate.ps1
python -m eepy.state.replay latest --estimator both
python -m eepy.perception.selftest --states --no-aus
python -m eepy.camera.preview
```

**A session in the hmm mode:** start the panel with `python -m eepy.wizard --mode hmm`
(in `.venv-robot`) and the vision program with `python -m eepy.perception` (in
`.venv-vision`). The vision program follows the session's mode by itself
(`--estimator auto`, the default): the HMM in hmm sessions, the rules otherwise.
If the panel says the vision program sends the wrong estimate, restart it.

### A starting belief per session (switched off)

A session can start from another belief than the usual one, for example after a
sleepiness question ("how sleepy are you, 1–9?"). Start the panel with
`--session-prior my_prior.json`; it is copied into each session as `prior.json`:

```json
{"source": "kss", "initial": {"focused": 0.5, "distracted": 0.1, "drowsy": 0.3, "very_drowsy": 0.1},
 "drowsiness_multiplier": 2.0, "inputs": {"kss": 7}}
```

It may only set the starting belief and how much faster (> 1) or slower (< 1)
drowsiness sets in. It is clipped to safe bounds and moved only part of the way
from the usual values (`trust` per source). Anything wrong falls back to the
usual values, with the reason in the log. It is **off** until
`session_prior: enabled: true` in `config/state_hmm.yaml`. Later a KSS prompt or
an LLM can write the same file (A17/A18).

## The HMM model (for the report)

**States and flags.** Four hidden states S = {focused, distracted, drowsy,
very_drowsy}. *absent* and *unknown* are flags decided outside the model (A13's
conditions); during them a step is predict-only.

**Transitions: continuous time.** A generator Q (rates per hour in the YAML,
Q[i][i] = −Σ). The step matrix for Δt seconds is P = expm(QΔt), computed by
scaling and squaring with a 12th-order Taylor series (numpy only; ≤ 1e-15 from
scipy for Δt ≤ 1 day). Predict-only stretches are exactly rate-independent
(expm(Qa)·expm(Qb) = expm(Q(a+b))). Mean dwell times: focused 511 s, distracted
59 s, drowsy 327 s, very drowsy 277 s. A drowsiness factor (session prior; later
time of day) multiplies the rates into drowsiness.

**Emissions** (per state, see the YAML for every number and its reason):

| Sign | Input | Family | Evidence time τ |
|---|---|---|---|
| eye closure | `perclos_60s` | Beta (mean, concentration) | 10 s |
| blink length | ln `blink_mean_ms_rel_60s` (uncalibrated: ln(ms/200)) | Student-t, ν = 3 | 10 s |
| long blinks | `long_blinks_60s`, exposure = judged seconds | Poisson | 60 s |
| microsleep | `blink_max_ms_60s ≥ 1500` | Bernoulli | 60 s |
| eyes shut now | `eyes_closed_now_s ≥ 1.5` | Bernoulli | 2 s |
| eyes-closed AU | `AU43_active_60s` (judged frames) | Beta | 10 s |
| yawns | `yawns_60s`, exposure = face seconds | Poisson | 60 s |
| jaw drop AU | `AU26_active_60s` | Beta | 60 s |
| nods | `nods_60s`, exposure = face seconds | Poisson | 60 s |
| looking away (minute) | `gaze_off_ratio_60s` | Beta | 20 s |
| looking away now | `away_current_s`, bins 0/5/10/20/60 s | categorical | 2 s |

A sign only counts when its `needs` hold (e.g. 20 s of eyes judged in the
minute); otherwise it is skipped, never filled in.

**Evidence time and rate independence.** At an update Δt_ev seconds after the
previous one, sign f adds `w_f · min(Δt_ev, τ_f)/τ_f · ℓ̃_f(x | s)`. A window value
thus counts at most once per τ_f, whatever the camera rate, so the total evidence
does not depend on the update rate (for Δt ≤ 2 s). With 60-s windows and τ = 10 s
the minute-long ratios are counted six times a minute: a deliberate overcount for
responsiveness, which makes the posterior over-confident (a decision score; A17
fits τ, A18 measures calibration). ℓ̃ is bounded: no sign makes one state more than
e^8 ≈ 3000× less likely than another per window (`max_log_ratio`).

**Double counting: Kish groups.** A group of n valid correlated signs with
correlation ρ counts n_eff = n / (1 + (n−1)ρ) signs in total, shared by base
weight. Eye closure (ρ 0.5): 5 signs count 1.67 without AUs, 6 count 1.71 with. Yawning (yawns +
jaw drop, ρ 0.8): 1.11. Looking away (ρ 0.5): 1.33. Nods: alone.

**The forward step.** log b ← logsumexp-predict(log b, P), plus the evidence;
normalised; no state below `min_prob` = 1e-6.

**Reporting.** Families focused / distracted / sleepy (drowsy + very drowsy). The
shown family switches when another reaches 60 %, or when the current one falls
below 40 %; within sleepy, very drowsy enters at 60 % of the sleepy share and
leaves at 40 %. No time-based dwell. `confidence` = P(shown family);
`uncertainty` = normalised Shannon entropy (0 certain, 1 uniform; the BURTRAM
convention); `most_likely` = the 4-way argmax.

**Reasons.** A sign qualifies on what it says *now*: for focused, it favours
focused over the most likely other family (worded "speaks against …"); for the
other states, it favours that family over all others and is not "no news". At
least 0.1 nats per window. Qualifying signs are ranked by a 30-s exponential
running sum of their evidence (exactly dt-invariant).

**Why an HMM** (the slide):

1. a belief, not a verdict: the robot can be unsure, and say so;
2. principled smoothing instead of hand-tuned dwell times;
3. rate-independent by construction (continuous time, evidence time);
4. readable, fittable numbers: every number has a reason, and A17 fits them in
   the same file (`source: fitted`, with a `fit:` block);
5. uncertainty for the policy: hedged vs direct now, information gain later.

**The A17 contract.** `config/state_hmm.yaml`, format `eepy.state_hmm/1`. A fitted
file has `source: fitted` and a `fit:` block (method, fitted_at, sessions,
n_sessions, n_seconds, log_likelihood, …). Any number may carry a
`prior_strength` (pseudo-observations) for MAP fitting. `dump()` writes exactly
what `load()` read.

**References.** Wierwille & Ellsworth 1994 and Dinges & Grace 1998 (PERCLOS);
Schleicher et al. 2008 (blink duration); Kish 1965 (effective sample size); Moler
& Van Loan 2003 (matrix exponential); Rabiner 1989 (HMMs).

**The time-of-day slot (not implemented).** `modulation:` in the YAML keeps the
formula for later: m(t) = min(max_factor, exp(per_late_hour · clip(h_late, 0, 4) +
per_session_hour · clip(h_session, 0, 4))), multiplying the rates into
drowsiness. `enabled: true` is refused in A16.

## For developers

- **The interface** (`estimate.py`). Every estimator has
  `update(features) -> StateEstimate` and `reset()`. A `StateEstimate` holds:
  - `state`;
  - `scores` (one-hot here, probabilities for the HMM);
  - `reasons` (rule, feature, value, threshold, text);
  - `calibrated`, `skipped`, `since`, `pending`, `estimator`, `values`.

  The HMM adds `most_likely`, `confidence`, `uncertainty`, `evidence`, `updated`
  and `say` (None / empty for the rules).

  Estimators register by name (`register("rules", ...)`), and
  `make_estimator(name, **kwargs)` builds one (the HMM is imported only then, so
  the rules work without numpy). `for_mode(mode)` picks "hmm" for the HMM modes.
  Downstream code (A14, panel, logs) must only use this interface.
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
  - `StateRecorder` writes `state_changed`, `state_model` (retried until it is
    written) and `state.csv`.
  - `StatePublisher` sends `STATE` over the camera relay to the panel.
  - `SessionEstimators` picks the estimator (and the HMM's session prior) at every
    session change: `--estimator auto`. A re-entrant lock keeps the pipeline
    thread (`feed`) and the main loop (`idle`) apart.
- **The HMM** (`hmm_params.py` loads/checks/writes the YAML; `hmm_model.py` holds
  the pure maths: expm, emissions, Kish weights, forward step; `hmm.py` is the
  estimator; `hmm_prior.py` the session prior). numpy + standard library only, no
  scipy at run time, Python 3.9.
- **Same states live and in replay.** This is tested
  (`test_replay_gives_the_same_states_as_live`): a scenario with a phone,
  drowsiness, an empty desk and a camera pause, fed live and then replayed
  from the written files.
- **Tests:** `tests/test_state.py` (each rule, points, priority, dwell,
  uncalibrated, low confidence, YAML errors, replay, the scenario report, the
  relay), `tests/test_hmm.py` (the HMM: parameters, maths, behaviour on simulated
  sessions, missing data, reasons, replay = live, the prior) and
  `tests/test_perception.py` (pipeline → state).
