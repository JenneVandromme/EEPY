# When EEPY nudges, and why

In the **rules** and **hmm** modes, EEPY acts on its own. The camera tells it how
the participant is doing ([the user state](../state/README.md): focused,
distracted, drowsy…; from the threshold rules in the rules mode, from the HMM in
the hmm mode). This part decides what the robot **does** about it. It climbs the
ladders in [`behaviors/refocus.yaml`](../../behaviors/refocus.yaml) and
[`behaviors/bedtime.yaml`](../../behaviors/bedtime.yaml): start barely
noticeable, and only get clearer when the gentle version didn't work.

All the timings are in [`config/escalation.yaml`](../../config/escalation.yaml),
each with its reason. You can try any change **without a camera** (see
*Simulate* below).

## What EEPY does in each state

| The participant is… | EEPY's role | What happens |
|---|---|---|
| **focused** | study buddy | Quiet presence (the idle). **Real focus is never interrupted.** |
| **distracted** | refocus | After 20 s: a glance at them and their notes. Then a curious head tilt, then a spoken "Back to it?", and finally the offer of a break. |
| **drowsy** | bedtime | After 30 s: a subtle hint, then a more attentive one, then "Getting late, isn't it?", then "15 more minutes?". Then goodnight. |
| **very drowsy** | bedtime | The same, but sooner (10 s) and starting at the second hint. |
| **absent** | paused | Nothing: EEPY doesn't talk to an empty chair. |
| **unknown** | — | Nothing (no camera data, or the vision program isn't running). |

The state itself already waits about 10 s before it says "distracted" (see
the state guide). The 20 s here come on top, so it takes **about half a minute
of looking away** before the first, wordless nudge. A glance never triggers
anything.

## How a ladder is climbed

After each nudge, EEPY waits for a reaction (the **reaction window**). If they
are back on task (focused for 10 s), it worked. Otherwise it goes **one level
up**:

```
distracted ─20 s─▶ level 1 (glance) ─30 s─▶ level 2 (head tilt) ─30 s─▶ level 3 ("Back to it?")
            ─45 s─▶ level 4 (offers a break) ─2 min─▶ pause 5 min ─▶ again from level 2
```

```
drowsy ─30 s─▶ level 1 ─60 s─▶ level 2 ─60 s─▶ level 3 ("Getting late?") ─90 s─▶ level 4 ("15 more minutes?")
       ─15 min─▶ level 5: goodnight, and the robot goes to sleep
```

- **Back to focused:** the ladder starts from scratch. If a nudge just worked,
  the robot gives an **approving nod**, but at most once every 10 minutes, so
  it doesn't feel like a dog trainer.
- **Top of the ladder:** if even the break offer is ignored, EEPY stops for
  **5 minutes**, then starts again at level 2. It never loops endlessly.
- **The words get firmer** ("direct" instead of "hedged", see
  [lines.yaml](../../behaviors/lines.yaml)) only when the participant is
  calibrated *and* the case is clear: very drowsy, or a repeat after no reaction.

## When EEPY never nudges

- **During real focus.**
- **While nobody is there** (absent) or the state is unknown. The clock of a
  waiting nudge stands still meanwhile.
- **In the first 3 minutes** after the calibration (or after the session start,
  without one). Settling in isn't distraction.
- **Within 20 s of the last nudge,** the operator's own included.
- **More than 12 times an hour,** about three whole ladders.
- **While the robot is still moving,** or while the participant seems to be
  getting back to work.
- **When the operator pressed Pause autonomy.**
- **In the hmm mode, while the HMM is not sure enough**: too little evidence yet, below
  70 %, or the belief is unclear (see *Belief-based decisions* below).

## How sure is sure enough (hmm mode)

The HMM says how sure it is of the state, e.g. *distracted · 82 %*. `confidence.act` is also the
start threshold of the belief decisions above; the rest of this section is A16's gate, used with
`hmm: decisions: gate`. In
[`config/escalation.yaml`](../../config/escalation.yaml), under `confidence:`:

```yaml
confidence:
  act:
    refocus: 0.7     # start the refocus episode / play a refocus nudge only from 70 %
    bedtime: 0.7
  direct: 0.85       # the firmer "direct" tone also needs 85 %
```

- **Below `act`,** nothing starts: no role switch, no nudge, and a running ladder
  does not climb. The autonomy line says *not sure enough yet: distracted 62 %
  (acts at 70 %)*.
- **The direct tone** (for very drowsy, or after a nudge without reaction, when
  calibrated) also needs `direct`; otherwise the nudge is hedged.
- **The rules mode** gives no percentage, so this section changes nothing there.
- Each time the gate starts holding a ladder back, and when it stops, an `escalation`
  event is logged (`waiting_for_confidence`, `confidence_wait_ended` with the reason), so
  the [explanation viewer](../../docs/EXPLANATION_VIEWER.md) can show it afterwards.
- **Every nudge carries an explanation (A20):** the top evidence over the decision window, the
  contrast with the runner-up, and one sentence spoken after the nudge's line (when the condition
  is on). [docs/A20_EXPLANATIONS.md](../../docs/A20_EXPLANATIONS.md).
- Spoken lines with `{evidence}` get the HMM's short reason (e.g. *eyes closed 18%
  of the last minute*), or nothing when it has no clear one.

The percentages are decision scores, not exact chances (A18 checks them). Higher
thresholds mean fewer false alarms but later nudges.

## Belief-based decisions (hmm mode)

With the `hmm:` section in [`config/escalation.yaml`](../../config/escalation.yaml) (shipped:
`decisions: belief`), the hmm mode doesn't use the timers under `onset:`. It looks at the HMM's
whole belief instead. Why and the numbers: [docs/A19_BELIEF_DECISIONS.md](../../docs/A19_BELIEF_DECISIONS.md).

- **Evidence adds up.** Every second, EEPY adds how far the belief in *distracted* (or *drowsy*)
  is above 55 %. When enough has added up, *and* the belief is at least 70 % right now, the
  first nudge comes. So **a sure belief acts sooner, a borderline one later**:

  | | 95 % sure | 80 % | 72 % | below 70 % |
  |---|---|---|---|---|
  | distracted → first nudge | 20 s | 32 s | 47 s | never |
  | drowsy → first nudge | 30 s | 48 s | 71 s | never |
  | very drowsy (calibrated only) | 15 s | | | |

- **Once a ladder runs**, it goes on climbing above 55 % (no stop-start at one line). It **holds**
  (no next level) below that, or as soon as focus is coming back (30 % focused). It **resets**
  when they're clearly focused (75 %) for 10 s.
- **Not sure: wait.** When the belief is spread out, or the two most likely states are close,
  EEPY waits and says so: *"Waiting: not sure yet (distracted 48%, focused 41%)"*.
- **Distracted or tired? The gentle option.** Both moderately likely (30 % or more each): one
  wordless glance; after 45 minutes of work, the break offer instead. No climbing until it's clear.
- **Switching mid-way** (e.g. distracted → drowsy): the role switch plays (if designed), and the
  new ladder starts one level below where the old one was (at most level 3), not from scratch.
- **Movement size follows confidence:** 70 % of the designer's intensity at 70 % sure, the full
  value from 90 %. Never more than the designer set. The tone rule is unchanged.

**In the panel,** the line under the autonomy row says in words what the policy is doing, and the
chain shows *belief → evidence → decision → robot* (hover the decision for the reason and the top
evidence). Each decision is logged as a `policy_decision` (act, wait, hold, resume, switch,
reset) with the belief, confidence, uncertainty and the top 3 signs.

**Try it without a camera** with beliefs: `distracted@0.65` means 65 % distracted (the rest
focused), `distracted@0.4+drowsy@0.4` mixes:

```bash
python -m eepy.interaction.simulate belief_shift
python -m eepy.interaction.simulate "focused 4m, distracted@0.72 2m, focused 1m"
python -m eepy.interaction.simulate --session P03 --estimator hmm     # a recording, with the HMM
```

`decisions: gate` (or no `hmm:` section) brings back A16's behaviour: the onset timers plus the
70 % gate described below.

## The special moments

**The break (refocus level 4).** If the participant leaves within 2 minutes
of the break offer, or the operator presses **user accepted**, it's a break.
- EEPY pauses for 10 minutes.
- It says **"Welcome back"** (line `break_welcome_back`) when they return, also
  if they come back early.
- Leaving without a break offer is different: after 5 minutes away, the ladder
  just starts fresh when they return.

**The wind-down (bedtime level 4): "15 more minutes?"**
- The timer starts **at the offer**.
- **user accepted:** the deal is on.
- **user snoozed:** the timer starts again, once.
- **user dismissed:** no deal. Bedtime pauses for 10 minutes, then comes back
  at level 3.
- **No answer:** the timer runs anyway.

Shortly before the end it says the **reminder** (`wind_down_reminder`), then
comes **goodnight** (level 5). After goodnight, EEPY stays asleep and does
nothing else in the session; end the session when the participant has gone.

**Leaving after the bedtime nudges** (level 3 or higher, gone for 5 minutes)
counts as "went to bed": the nudges worked.

## In the Wizard panel

Start the panel in the rules (or hmm) mode, with the vision program next to it:

```bash
python -m eepy.wizard --backend real --mode rules   # (.venv-robot); or --mode hmm
python -m eepy.perception                           # (.venv-vision), the camera and the state
```

The vision program follows the session's mode by itself. If the panel says it
sends the wrong estimate (rules in an hmm session), restart it: autonomy waits
until the right one arrives.

Under the session bar, an **autonomy** line shows:
- the **role** (study buddy, refocus, bedtime, paused);
- the **ladder and level**;
- **what comes next and when**, e.g. *"waiting for a reaction to level 2;
  next level in 0:18"*, *"break: … 6:40"*, *"wind-down: goodnight in 11:02"*.

You stay in charge:

- **Pause autonomy:** the robot stops doing things on its own at once, and you
  drive it by hand. **Resume autonomy** starts afresh. Use it for demos, and
  whenever something goes wrong.
- **The participant's answers:** the marker buttons **user accepted**, **user
  snoozed** and **user dismissed** (keys `.`, `/` and `m`). They're recorded as
  observations, like every marker, and in the rules and hmm modes they also steer the
  robot (see the special moments above). Later, speech will do this (A28).
- **Anything you play yourself** is still recorded as yours (`source=wizard`),
  never as the robot's. The robot then waits at least 20 s before its own
  next nudge.

In the **wizard** mode none of this runs: the robot only does what you press.

## Tuning: what designers can change

Everything is in [`config/escalation.yaml`](../../config/escalation.yaml). Change
a number, then **try it in the simulation** before a session.

| Setting | Default | What it does |
|---|---|---|
| `onset.distracted` / `drowsy` / `very_drowsy` | 20 / 30 / 10 s | How long a state must last before the first nudge |
| `ladders.refocus.reaction` | 30, 30, 45, 120 s | How long to wait for a reaction after level 1, 2, 3, 4 |
| `ladders.bedtime.reaction` | 60, 60, 90, 120 s | The same for bedtime (drowsiness changes slowly) |
| `ladders.*.start_level`, `start_level_very_drowsy` | 1; bedtime 2 when very drowsy | Where a ladder starts |
| `ladders.*.cooldown`, `restart_level` | refocus 5 min → level 2; bedtime 10 min → level 3 | The pause at the top of a ladder (or after "dismissed"), and where it starts again |
| `ladders.*.intensity` | 1.0 | How big the movements are, per level (0–1) |
| `focused.reset_after` | 10 s | How long back on task before the ladder resets |
| `focused.approving_nod`, `nod_only_after_success`, `nod_min_interval` | `approving_nod`, yes, 10 min | The nod when they're back |
| `guards.min_gap`, `max_per_hour`, `after_calibration` | 20 s, 12, 3 min | The limits in *When EEPY never nudges* |
| `absent.reset_after`, `went_to_bed_after` | 5 min, 5 min | Gone this long: start fresh / went to bed |
| `break.absent_within`, `seconds`, `welcome_line` | 2 min, 10 min, `break_welcome_back` | The break |
| `wind_down.seconds`, `snoozes`, `snooze_seconds`, `reminder_before` | 15 min, 1, 15 min, 1 min | The wind-down |
| `hmm.*` | see *Belief-based decisions* | The hmm mode's evidence, hysteresis, waiting, ambiguity, switching and intensity; each number has its reason in the file |
| `role_switch` | (none exist yet) | Behaviours played when EEPY switches role, e.g. a `role_to_bedtime` you design. They're used automatically **if a behaviour with that name exists** in `behaviors/`. |

**The words** are in [`behaviors/lines.yaml`](../../behaviors/lines.yaml). The two new
lines `break_welcome_back` and `wind_down_reminder` are **placeholder text**:
rewrite them.

A mistake in the file stops the panel with a message that says what and where,
e.g. *"ladders.refocus.reaction: levels must have no gaps, found [1, 2, 4]"*.

## Simulate: try it without a camera

The simulation plays a made-up timeline of states against the escalation and
prints every decision and outcome. A 30-minute scenario takes a second. It uses
the real behaviours and their real lengths, but nothing moves.

**macOS / Linux:**
```bash
source .venv-robot/bin/activate
python -m eepy.interaction.simulate --list            # the built-in scenarios
python -m eepy.interaction.simulate phone_break
python -m eepy.interaction.simulate "focused 5m, distracted 2m, focused 3m"
python -m eepy.interaction.simulate "drowsy 6m, accepted, drowsy 20m"
python -m eepy.interaction.simulate late_evening --config my_escalation.yaml   # try your changes
python -m eepy.interaction.simulate --session P03     # the states of a recorded session
```

**Windows (PowerShell):** the same, after `.venv-robot\Scripts\Activate.ps1`.

**Writing a scenario:**
- **States with a time:** `focused`, `distracted`, `drowsy`, `very_drowsy`,
  `absent`, `unknown`, e.g. `5m`, `90s` or `4m30s`.
- **The participant's answers in between:** `accepted`, `snoozed`, `dismissed`.

| Scenario | Shows |
|---|---|
| `phone_break` | 2 minutes on the phone: the nudges climb until they look back; reset and nod |
| `takes_the_break` | ignores the whole ladder, leaves after the break offer: break, welcomed back 12 minutes later |
| `late_evening` | drowsy: the bedtime ladder, the wind-down accepted, the reminder, goodnight |
| `flicker` | glancing away every 15 s never triggers anything; very drowsy starts at bedtime level 2 |
| `belief_sure_vs_borderline` | hmm: 95 % sure nudges after 20 s, 72 % only after 48 s |
| `belief_unsure`, `belief_ambiguous`, `belief_ambiguous_late` | hmm: waiting; the gentle glance; the break offer after 45 minutes of work |
| `belief_shift`, `belief_wavering` | hmm: switching ladders mid-way; holding and going on |

Example (`phone_break`):

```
  04:00  --- state: distracted for 02:00
  04:20  role: study_buddy -> refocus
  04:20  NUDGE  #1 refocus level 1 (refocus_joint_attention, hedged) -- refocus level 1: distracted for 20 s
  04:50    outcome #1: NO_REACTION after 30 s (still distracted after 30 s)
  04:50  NUDGE  #2 refocus level 2 (refocus_curious, direct) -- refocus level 2: no reaction to level 1
  05:20    outcome #2: NO_REACTION after 30 s (still distracted after 30 s)
  05:20  NUDGE  #3 refocus level 3 (refocus_verbal, direct) -- refocus level 3: no reaction to level 2
  06:00  --- state: focused for 03:00
  06:10    outcome #3: SUCCESS after 50 s (back to focused after 40 s)
  06:10  approving nod (behavior approving_nod)
```

`--realtime` plays a short scenario live on the DryRunBackend instead: real
seconds, silent, with the spoken lines printed.

## What is recorded (for the analysis and the bandit)

Every nudge gives **two events**, linked by `decision_id`:

**`nudge_decision`** holds:
- the `ladder` and `level`, the `behavior`, `tone` and `intensity`;
- the `policy` that chose it (`fixed_escalation`, or `wind_down_timer` for
  goodnight), and its `propensity` (1.0 for fixed rules; the bandit's chance of
  choosing it, A24);
- the `reason` and the `reaction_window`;
- the `context` it decided on: the state and how long it lasted, calibrated,
  previous outcome, nudges this session and this hour, time since the last
  nudge, break and calibration; the `estimator`, and for the HMM its
  `confidence`, `uncertainty` and `most_likely` state.

**`nudge_outcome`**, exactly once per decision. It holds the outcome below,
`seconds` (the time to the outcome), the state at that moment, and the `detail`.

| Outcome | Refocus | Bedtime |
|---|---|---|
| `success` | focused for 10 s within the window; or took/accepted the break | "user accepted"; goodnight confirmed; left after level ≥ 3 (went to bed); or the session ended after bedtime nudges |
| `no_reaction` | the window passed, still not focused | the window passed without an answer |
| `dismissed` | "user dismissed" | the same |
| `snoozed` | "user snoozed" | "user snoozed" at the wind-down |
| `interrupted_focus` | focused within 5 s of the nudge: they were already back (a false alarm) | the same |
| `censored` | no fair verdict: the operator paused autonomy or played something, the participant left for 5 min, the state switched ladder, or the session ended | the same |

`censored` exists so that the bandit's rewards (A22) neither reward nor
punish a nudge whose result can't be credited to it.

**Other events:**
- `role_changed`;
- `escalation`, with `what`: cooldown_started, episode_reset, approving_nod,
  break_started/ended, welcome_back, wind_down_started/snoozed/reminder/cancelled,
  goodnight, went_to_bed, reply, autonomy_paused/resumed, waiting_for_confidence /
  confidence_wait_ended (hmm mode: the confidence gate);
- `policy_decision` (hmm mode, belief decisions): act / wait / hold / resume / switch / reset, with
  the belief, confidence, uncertainty, top evidence and how far each ladder's evidence is.

The robot's own behaviours carry `source=auto` and the operator's
`source=wizard`. See the [DATA_GUIDE](../../docs/DATA_GUIDE.md).

## For developers

- **`policy.py`:** `NudgePolicy.decide(NudgeContext) -> Action` decides **which**
  level, tone and intensity, with a `propensity`. `FixedEscalation` goes one
  level up after no reaction. The bandit (A24) implements the same interface
  and replaces only this choice.
- **`engine.py`:** `EscalationEngine` decides **when**:
  - onset delays, reaction windows (frozen while absent or unknown), cooldowns
    and guards;
  - the break and wind-down flows, absent handling and pausing;
  - outcomes and logging.

  Its inputs are `on_state()`, `tick()`, `reply()`, `operator_played()`,
  `calibration()`, `set_paused()` and `session_started()`/`session_ended()`.
  Times are passed in, so it runs on the shared clock in the panel and in
  virtual time in the simulation and the tests. **The A28 hook:** speech
  replies call `reply("accepted" | "snoozed" | "dismissed")`, like the buttons.
- **`actuator.py`:** `PlayerActuator` (the panel's BehaviorPlayer, `source=auto`)
  and `SimActuator` (virtual time, real behaviour lengths).
- **`config.py`:** loads and checks `config/escalation.yaml`. `check_library()`
  checks it against `behaviors/`.
- **The panel** (`eepy/wizard/server.py`, rules and hmm modes):
  - `_start_autonomy()` builds the engine and a tick thread;
  - the vision process's `STATE` relay messages go to `on_state()`, only when they
    come from the mode's estimator (after a 3-s grace at the session start; a
    mismatch is reported once per session and the engine is not fed);
  - the reply markers go to `reply()`;
  - `POST /api/autonomy {"paused": true}` pauses it.
- **Tests:**
  - `tests/test_escalation.py`: onset, step-up, reset and nod, cooldown,
    guards, absent, break, wind-down, goodnight, pause, every outcome, the
    config checks, the scenarios and simulating over a recorded session;
  - `tests/test_wizard.py`: the rules and hmm modes in the panel, the estimator guard.
- **The confidence gate** (`engine._unsure`): before the episode and role switch,
  and in `_guard` for later nudges; the policy's tone checks `direct_confidence`.
