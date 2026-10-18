# A16 — the HMM state estimator: choices, reasons, limits, next steps

This file records the design choices of the HMM state estimator (task A16): what was chosen,
why, what the choice cannot do, and what could be improved later (A17 and beyond). It is
written for the whole team; technical terms are explained the first time they appear.

**Status:** the choices were agreed on 2026-10-13, before implementation, and implemented the
same week. All timings in this file are **measured on the real code** (`tests/test_hmm.py`'s
simulator: simulated sessions, not real faces). Part F lists what implementing taught us.

---

## Contents

- [What the HMM is, in one paragraph](#what-the-hmm-is-in-one-paragraph)
- [Overview of the choices](#overview-of-the-choices)
- [Part A — Main design choices](#part-a--main-design-choices)
- [Part B — Choices made after the second review](#part-b--choices-made-after-the-second-review)
- [Part C — What was deliberately left out](#part-c--what-was-deliberately-left-out)
- [Part D — Limits that apply to the whole model](#part-d--limits-that-apply-to-the-whole-model)
- [Part E — Future improvements, collected](#part-e--future-improvements-collected)
- [Part F — What implementing taught us](#part-f--what-implementing-taught-us)
- [Part G — Explainability: the viewer](#part-g--explainability-the-viewer)
- [Part H — The drowsiness ladder (option A)](#part-h--the-drowsiness-ladder-option-a)
- [Part I — Yawns and nods count twice a minute (version 3)](#part-i--yawns-and-nods-count-twice-a-minute-version-3)

---

## What the HMM is, in one paragraph

The rules (A13) give a verdict: "you are drowsy" when enough points add up. The HMM (a hidden
Markov model) keeps a **belief** instead: a probability for each of four hidden states —
focused, distracted, drowsy, very drowsy. Every second it does two things: it lets the belief
drift a little according to how often people switch between states (the *transitions*), and it
updates the belief with what the camera sees (the *emissions*: how likely each sign is in each
state). The panel shows the most likely state with a percentage, and EEPY only acts when it is
sure enough.

---

## Overview of the choices

| # | Choice | In one line |
|---|---|---|
| A1 | Absent and unknown are flags, not states | No face or no data → the belief only drifts, no evidence |
| A2 | 5-s warm-up | Say nothing until 5 s of usable data |
| A3 | Deliberate differences from the rules | Later to drowsy, needs eye signs, faster back to focused |
| A4 | Acting thresholds 70 % / 85 % | Act from 70 % sure; the firm tone needs 85 % |
| A5 | The vision program follows the session's mode | `--estimator auto` |
| A6 | A13's AU43 rule stays off | Keeps rules sessions comparable |
| A7 | Hand-set parameters with a `why` for every number | No learning yet; A17 fits them |
| A8 | Continuous time | Same result at any camera speed |
| A9 | Three families for the shown state | No flicker between drowsy and very drowsy |
| B1 | Jaw drop (AU26) counts once per minute | One yawn does not count twice |
| B2 | Eye signs: stronger overlap discount | Correlation 0.5; one knob, not two |
| B3 | Looking away over the minute updates more slowly | Back to focused ~18 s after looking back |
| B4 | "Focused" is explained as a contrast | "eyes look alert – speaks against drowsiness" |
| B5 | Only AU43 is limited to forward-looking frames | AU05/AU07 keep their meaning |
| B6 | Smaller fixes and a time buffer | Robustness; ~3 days total |

---

## Part A — Main design choices

### A1. Absent and unknown are flags, not hidden states

**Choice.** There is no fifth "absent" state in the model. When the face has been gone for 8 of
the last 10 s, for 5 s in a row (the A13 condition), EEPY shows `absent`. When there is no
usable camera data (no pictures, too few seconds, camera too slow), it shows `unknown`. In both
cases the belief is not updated with evidence; it only drifts with the transitions.

**Why.**
- Absence is *seen*, not guessed: there is no need to infer it.
- All other signs (eyes, yawns, gaze) are meaningless without a face; an absent state would need
  a made-up "no face" value for every sign.
- Leaving the desk is driven by breaks and bedtime (A14), not by drowsiness. Mixing it into the
  drowsiness transitions would distort what A17 has to learn.
- With continuous time, drifting is principled: a 30-s break barely changes the belief; two
  hours away relax it to the long-run average.
- A18 then compares inference only, because absent is decided the same way in both modes.

**Limits.**
- During the ~7 s between leaving and `absent`, the last glimpses of the face can look like
  looking away, so the belief can lean towards distracted (the rules have the same issue). The
  20-s distracted onset and the 70 % threshold keep a nudge from reaching an empty chair.
- After a long absence the belief returns to the long-run average (about 70 % focused, 15 %
  drowsy), not to "fresh after a break". A break that wakes someone up is not modelled.

**Future.** A break could reset the belief towards focused (a "break effect"), or the break
length could feed the per-session prior. A18 can test whether that helps.

### A2. A 5-second warm-up

**Choice.** After a start or a session change, the state is `unknown` until 5 s of usable data
have been processed. From a cold start the first real state comes at about 9 s (rules: 10 s).

**Why.** The minute-long windows are nearly empty at the start; a state based on 2 s of data is
noise. It matches A13.

**Limits.** 5 s is a guess, not measured. The minute-long signs (eye closure, blink length) are
still based on less than a minute of data for the first 60 s; they are weighted the same.

**Future.** Weight the minute-long signs by how full their window is.

### A3. Deliberate differences from the rules (A13)

**Choice.** We accept that the HMM:
1. reaches (very) drowsy **later** than the rules (about 25–30 s later on simulated data;
   25 s later on the recorded pilot session);
2. needs **eye signs** for drowsiness: nods or yawns with alert-looking eyes are not enough
   (rules: two nods in a minute → very drowsy; a yawn + a nod → drowsy). *Changed in version 3
   (Part I): yawns or nods that keep coming are drowsy after a minute or more;*
3. returns to **focused sooner** after a distraction.

**Why.**
1. The rules add points quickly from single events; the HMM weighs signs against how often they
   also happen when alert, so isolated events count less.
2. Nodding with eyes measured as open is more often looking down at notes than nodding off. The
   eye signs are the best-studied drowsiness signs (PERCLOS, blink length).
3. The rules' `often_away` keeps firing for a minute; the HMM lets the "right now" sign speak.

**Limits.**
- The first bedtime nudge comes later in hmm mode. In a comparison (A18) this is a real
  difference in user experience, not just a model difference, and must be reported.
- Drowsiness while reading notes (looking down, so the eyes are not judged) is seen only through
  nods and yawns, which are weak by design. That is a blind spot.
- On the other hand, the HMM is more robust to a known live artefact (lowered eyelids while
  reading look like closed eyes): the reviewer measured that the rules turn drowsy after
  ~1 minute even at mild levels, the HMM only at stronger levels after ~100 s.

**Future.** A17 fits how fast drowsiness should be reached, from labelled sessions. Head-pose
signs while looking down could be added once looking down is better understood.

### A4. Acting thresholds: 70 % and 85 %

**Choice.** In `config/escalation.yaml`: EEPY starts a refocus or bedtime episode, and plays a
nudge, only when it is at least 70 % sure of the reported family (focused / distracted /
sleepy). The firmer "direct" tone also needs 85 %. Below the threshold, EEPY waits and the
panel says "not sure enough yet". The rules give no confidence, so nothing changes for them.

**Why.** The HMM's main promise is "a belief, not a verdict". Using it for decisions is what
makes it useful; it also prepares the bandit and information-gain work (A20+).

**Limits.**
- The percentages are **decision scores, not frequencies**. Because minute-long signs are
  counted several times per minute (see A8 / B2), the model is more confident than it should be:
  "90 %" does not mean right 9 times out of 10.
- The thresholds are hand-picked, not tuned.

**Future.** A18 measures calibration against the wizard's annotations (does "80 %" mean right
80 % of the time?). If not, a calibration step (for example temperature scaling) or fitted
evidence times can correct it. The thresholds can then be chosen from data, or replaced by the
bandit's own decision.

### A5. The vision program follows the session's mode (`--estimator auto`)

**Choice.** By default, the vision program reads the mode of the current session (from
`meta.json`) and runs the matching estimator: the HMM in hmm sessions, the rules otherwise. It
can be forced with `--estimator rules` or `--estimator hmm`. The panel warns when the estimate
it receives does not match the mode (after a 3-s grace at the session start).

**Why.** Sessions alternate between modes (within-subject design). Without this, someone would
have to restart the vision program with the right option for every session, and forgetting it
would silently give the wrong estimator.

**Limits.**
- About 1 hour of extra work, and a lock between two threads (more moving parts).
- At the switch the belief restarts, so the first ~5–9 s of each session are `unknown`.
- If the HMM cannot load (for example numpy missing), the vision program keeps running the rules
  and the panel guard blocks autonomy; the cause is printed in the vision terminal.

**Future.** The vision program could run both estimators side by side in every session and log
both. That makes every session usable for comparison, at a small CPU cost.

### A6. A13's AU43 rule stays off

**Choice.** The rule `au_eyes_closing` in `config/state_rules.yaml` stays commented out, even
though AU43 is now limited to forward-looking frames (see B5).

**Why.** The rules mode is the baseline in the pilot sessions; changing its inputs now would make
rules sessions before and after A16 harder to compare. The limited AU43 has also not been checked
on a live camera.

**Limits.** The rules miss a sign that the HMM uses, which makes the comparison slightly unfair
in the HMM's favour.

Note: A16 does make one fix to the rules — the `microsleep` and `eyes_closed_now` rules now also
require the eyes to have been judged (as all other eye rules already do). That is a bug fix: on
sessions recorded after the eye gating it is nearly invisible, because closures are only
measured while the eyes are judged; it only changes the replay of older recordings. Sessions
before and after can be told apart by the snapshotted `state_rules.yaml`.

**Future.** After one live check of the limited AU43, re-enable the rule (one line) at the start
of a new batch of sessions.

### A7. Hand-set parameters, each with a reason

**Choice.** All numbers live in `config/state_hmm.yaml`. Every number carries a `why` (where it
comes from: the literature, the A13 thresholds, or the P99 pilot recordings). The file has a
version, a `source` (`hand_set` or `fitted`) and a `fit:` block that A17 fills in. A per-session
prior hook exists but is off.

**Why.** There is no labelled data yet to learn from. A readable file lets designers see and
change values, and gives A17 a clear format to write into.

**Limits.**
- The values rest on **one pilot participant (P99) acting** the states, plus the literature.
  Acted drowsiness differs from real drowsiness.
- AU43 has no data at all after the new limitation; its values are a guess.
- Some numbers were chosen so that the synthetic self-test reacts in time; that is tuning on
  made-up data.

**Future.** A17 fits the parameters from labelled sessions (wizard annotations, KSS
self-reports), with the hand-set values acting as priors (the file already has room for a
`prior_strength` per value). The prior hook can then be tested with a KSS question or an LLM at
the session start.

### A8. Continuous time

**Choice.** Transitions are rates per hour, turned into probabilities for the actual time between
two updates. Each sign has an *evidence time*: a minute-long count counts once per minute, a
"right now" sign every 2 s, the minute-long ratios every 10–20 s.

**Why.** The camera does not deliver a steady rate (it varies between computers, and with load).
A model that assumes one update per second would become more or less confident depending on the
computer. The prototype showed that a naive per-step model agrees with itself only 62–68 % of
the time at different speeds; the continuous-time model agrees 99 %.

**Limits.**
- The minute-long ratios are counted several times per minute (every 10 s = 6×, every 20 s =
  3×). That is a deliberate overcount for responsiveness; it makes the percentages too
  confident (A4).
- Around a change, a slower camera sees it one step later (differences of up to ~0.5 in
  probability for a second or two at 0.5 Hz; the shown state stays within ±1 s).

**Future.** A17 fits the evidence times. A more exact model would use the per-second
observations instead of minute windows, which removes the need for evidence times but needs a
different feature pipeline.

### A9. Three families for the shown state

**Choice.** The shown state is chosen among three families: focused, distracted, sleepy (=
drowsy + very drowsy). It switches when another family reaches 60 %, or when the current one
drops below 40 %. Within sleepy, very drowsy needs 60 % of the sleepy share and drops back below
40 %. The panel also shows the single most likely of the four states.

**Why.** A14 treats drowsy and very drowsy as one ladder. A plain "highest of four" would flicker
on 45/45 splits and would show "focused" at 30/35/35 when the person is clearly sleepy. The
band avoids flicker without time-based dwells, so it does not depend on the camera speed.

**Limits.** The 60/40 band is hand-set. Near the band edges the shown state can lag behind the
belief by a few seconds.

**Future.** The bandit (A20+) could act on the full belief instead of the shown state.

---

## Part B — Choices made after the second review

### B1. The jaw drop (AU26) counts once per minute, like the yawn count

**Choice.** The jaw-drop action unit (AU26) gets an evidence time of 60 s instead of 10 s. The
test simulator gets AU fields, and the "one yawn is not drowsy" tests run with and without AUs.

**Why.** Yawns and the jaw drop are in one group whose weight is discounted for their overlap.
But with 10 s, the jaw drop was counted six times per minute while the yawn count was counted
once, so one yawn counted about 2.8× as much with the AUs on. The reviewer showed that a yawn
every 20 s with alert eyes then turned into drowsy — exactly the double counting the task
forbids. AU26 is high for a few seconds per yawn, so it behaves like an event, and an event
should count once per minute.

**Limits.** The group discount still counts *signs*, not how often each sign speaks. Any future
sign with a short evidence time inside a group with slow ones would bring the same problem back.

**Future.** Make the group discount split an "evidence-per-second budget" over its members, so
the evidence time no longer matters for double counting. That is a cleaner rule, but more work
and harder to explain; A17 is a good moment.

### B2. Eye signs: a stronger overlap discount (correlation 0.5)

**Choice.** The six eye-closure signs (eye closure share, blink length, long blinks, microsleep,
eyes shut now, AU43) form one group with correlation 0.5 (was 0.3). The minute-long eye ratios
keep their 10-s evidence time. The `why` in the file says honestly: "one long closure moves all
six signs; the 10-s evidence time is chosen for responsiveness; A17 fits both".

**Why.** The previous version turned two knobs (lower correlation *and* shorter evidence time),
which roughly doubled the eye evidence per second, and its stated reason contradicted the
measurements. One knob is enough: with correlation 0.5 and 10 s, drowsy is still reached inside
the self-test's 45-s sleepy phase. The posterior also stays further away from 97–99 %, so the
70 % / 85 % thresholds still mean something.

**Effect** (prototype for 0.3; real code for 0.5; with AUs in brackets):

| Case | before (0.3) | now (0.5) |
|---|---|---|
| act sleepy → drowsy | 33 s | 40 s (39 s) |
| self-test sleepy phase → drowsy | 29 s | 35 s (36 s) |
| 2-s closures every 8 s → drowsy / very drowsy | 26 s / 58 s | 33 s / 73 s (34 s / 82 s) |

While drowsy in the simulated sleepy phase, the confidence rises from about 0.6 to 0.86, so
the 70 % / 85 % thresholds do filter (on the recorded pilot session: 58–87 %).

**Limits.**
- Drowsiness is detected later still. The self-test may show CHECK for the HMM in the "act
  sleepy" phase on a real face; that is reported as the known difference, and the phase length
  is not changed.
- 0.5 is a judgement, not a measurement of how correlated the signs really are.

**Future.** A17 measures the real correlations between the eye signs on recorded sessions and
fits the correlation and the evidence time together.

### B3. Looking away over the minute updates more slowly (evidence time 20 s)

**Choice.** The "looked away x % of the last minute" sign (`gaze_off`) gets an evidence time of
20 s instead of 10 s, with its own reason: "the minute share lags; the fast part is the 'looking
away now' sign".

**Why.** With 10 s, the minute share kept saying "distracted" long after the person looked back:
focused returned only ~33 s later. A nudge fired at the distraction onset could then reach
someone already back, and the outcome would be logged as a *success* (the person "returned after
the nudge") — a false reward for the bandit (A22/A24). With 20 s, focused returns after
13–16 s (measured). The onset of distraction is driven by the "right now" sign anyway, so it
barely slows down (12 s after picking up the phone).

**Limits.** The underlying problem remains, in a smaller form, and the rules share it: a nudge
fired at the onset can reach someone who just looked back, and be credited as a success.

**Future.**
- A14 could check whether the most likely of the four states is already focused just before
  playing a nudge (a cheap extra guard).
- A18 and A22 should treat "success within a few seconds of the nudge" with care, or use a
  short delay before judging the outcome.

### B4. "Focused" is explained as a contrast

**Choice.** A reason for the shown state is ranked by how much it favours that state over the
*most likely alternative*, and worded as a contrast: "eye closure (eyes closed 2 % of the last
minute) speaks against drowsiness", "looking at the work speaks against distraction". The phrase
"no difference" disappears.

**Why.** Before, every sign tied focused with some other state (open eyes fit focused and
distracted equally), so focused could never get a reason and always said "no clear signs either
way (99 % sure)". That sentence is false: the 99 % comes from clear signs. It is what the panel
shows most of the time, and what A20 would use to explain itself.

**Limits.**
- The wording is still a template; it reads mechanically.
- "Speaks against drowsiness" says nothing about distraction, so the explanation is always about
  one competitor at a time.

**Future.** A20 can turn the evidence list into natural sentences (possibly with an LLM), and
explain against more than one alternative when the belief is split.

### B5. Only AU43 is limited to forward-looking frames

**Choice.** The eyes-closed action unit (AU43) is computed only over frames where the eyes were
judged (looking roughly forward), like the other eye signs. AU05 and AU07 (also around the eyes)
are left unchanged.

**Why.** Looking down at notes made AU43 read "eyes closed" — the same artefact that led to the
eye gating. AU05 and AU07 are not used by the HMM, so changing them would only change the
meaning of logged data with no benefit.

**Limits.**
- From A16 on, `AU43_*` in the logs means "while looking forward"; older sessions keep the old
  meaning, so replays of older sessions are not comparable for AU43 (noted in the data guide).
- The limitation has not been checked on a live camera.
- AU05 and AU07 still contain the looking-down artefact if someone uses them later.

**Future.** Limit AU05/AU07 the same way when a later task starts using them.

### B6. Smaller fixes and a time buffer

**Choice.** The smaller review findings are fixed while implementing:
- the spoken `{evidence}` is empty (not the long reason) when the HMM has no clear reason;
- a re-entrant lock, so switching estimators cannot freeze the vision program;
- the reporting pseudocode defines all its variables;
- the AU code stays Python 3.9-compatible (no `bisect(key=...)`);
- a calibrated person without a blink-length ratio *skips* that sign (no raw fallback);
- the first update after a gap counts at most one normal step at any camera speed;
- every number in the file has a `why`, including `missing_value`;
- a new test pins that lowered eyelids while reading do not quickly turn into drowsy;
- the forced-blink-length test has an exact, reproducible setup;
- the plan's ~26 h gets a ~3 h buffer for the re-measuring round. If the work runs over 3 days
  at the replay/self-test step, two cuts are applied: no belief bar in the camera preview, and
  `--estimator auto` replaced by an explicit `--estimator hmm`.

**Why.** Each one is either a bug in the plan (deadlock, undefined variables, Python 3.9) or a
small inconsistency that would show up in the data.

**Limits.** None of these fixes can be checked on a live camera by the tests.

---

## Part C — What was deliberately left out

| Left out | Why | When it could come |
|---|---|---|
| Time of day / session length changing the drowsiness rates | Needs a clock and testing; the slot exists in the file (switched off) | A17 or later, once there is data on late sessions |
| An LLM or KSS prior at the session start | Only the hook is built (off by default) | A17/A18, as a comparison condition |
| Learning the parameters | No labelled data yet | A17 |
| A confusion matrix in the replay | Agreement % and the list of disagreements are enough for now | A18 |
| Saving self-test runs (`--save`), the simulator with the HMM | Time | When needed |
| Rules + HMM side by side in the camera preview | The self-test already shows both | When needed |

---

## Part D — Limits that apply to the whole model

1. **Little data behind the numbers.** One acted pilot plus the literature. Real drowsiness may
   look different (slower, subtler).
2. **Too confident by design.** Minute-long signs are counted several times per minute. The
   percentages are decision scores; A18 must measure calibration.
3. **Looking down is a blind spot for the eyes.** Only nods and yawns can show drowsiness then,
   and they are weak alone.
4. **Not verified without a camera:** live switch times, whether normal work gives enough
   forward-looking eye time (P99: median 31 s per minute, but 10 % of minutes under 6 s), the
   AU43 limitation on real frames, the look of the panel bar and overlay, and Python 3.9 on
   Windows/Ubuntu (CI only).
5. **The states are a simplification.** Four states, one person at a time, no "bored", "stressed"
   or "thinking with eyes closed". A person thinking with closed eyes will look drowsy.
6. **Fixed for everyone.** Apart from the calibration baseline, all parameters are the same for
   every participant.

---

## Part E — Future improvements, collected

**A17 (fitting)**
- Fit emissions, transitions, evidence times and group correlations from labelled sessions, with
  the hand-set values as priors (MAP).
- Measure the real correlations between the eye signs.
- Replace the group discount by an evidence-per-second budget (B1).

**A18 (evaluation)**
- Measure calibration of the percentages; recalibrate if needed (A4).
- Report detection delays against the rules, and the share of nudges that reach someone already
  back (B3).
- Compare the session priors (none / KSS / LLM) using the hook.

**A20–A24 (explanation, bandit)**
- Let the policy act on the full belief, and use the uncertainty for information gain.
- Turn the evidence list into natural explanations (B4).
- Treat very fast "successes" with care when rewarding the bandit (B3).

**Model and perception**
- Per-person parameters (beyond the calibration baseline).
- A "break effect" after absence (A1).
- Time of day / session length on the drowsiness rates (the slot exists).
- Weight minute-long signs by how full their window is (A2).
- Limit AU05/AU07 to forward-looking frames when they get used (B5).
- Run both estimators in every session for comparison (A5).

---

## Part F — What implementing taught us

Small changes made while building, each with its reason:

1. **A reason must be true right now.** The plan ranked reasons only by the last ~30 s of
   evidence. That running sum lags: right after picking up the phone it still favoured
   focused, which produced sentences like *"looking away for 11 s speaks against
   distraction"*. Now a sign only becomes a reason when its *current* value points the same
   way; the 30-s sum only orders the reasons.
2. **A minimum strength for a reason** (0.1 nats per window). Otherwise a 2-s glance showed up
   as *"speaks against drowsiness"*: true in the model, but meaningless to a reader.
3. **The hold text names what is rising.** *"still drowsy (78 % sure); distracted is becoming
   more likely"* instead of "no new signs either way" while looking away.
4. **The `state_model` field is `params_source`**, not `source`: `source` is already the
   event's own field (who wrote it). Found by the integration test.
5. **An estimate without an `estimator` field counts as the rules** in the panel's guard:
   vision programs from before A16 always ran the rules.
6. **Wording at the default weights.** With the eye correlation at 0.5, PERCLOS 25 % "clearly"
   (not "strongly") suggests drowsiness: each eye sign carries a third of a sign's weight.

Limits found:

- **Sustained yawning without action units.** Three yawns a minute for over two minutes, with
  alert eyes, raises P(drowsy) to about 0.3 (never shown as drowsy in the tested runs). With the
  action units on, the alert-looking jaw between yawns keeps it near 0.05.
- **The rules on the recorded pilot session P99_rules_3** (eye-artefact recording): after the
  microsleep fix they no longer say very drowsy there; HMM and rules now agree 74 % of the time
  (the HMM calls less of it distracted: 25 % vs 50 %). On P99_rules_4 they agree 81 %.
- **Not checked live** (no camera was used for A16): the switch times on a real face, the AU43
  limitation on real frames, the panel bar and overlay, and Windows/Ubuntu (CI only).

---

## Part G — Explainability: the viewer

**Choice.** An offline **explanation viewer** (`python -m eepy.state.explain`): one page per
session that shows the real decision pipeline (signs → belief → shown state → confidence gate
→ robot) for every second, with a timeline to scrub or play in slow motion. Plus a one-line
**decision chain** in the panel's autonomy row (hmm mode). Full guide:
[EXPLANATION_VIEWER.md](EXPLANATION_VIEWER.md).

**Why.** Explainability is a core goal of the project and a main reason for the HMM. A
"decision graph" with nodes lighting up was considered, but the HMM does not take one path:
every sign nudges all four probabilities at once, so lighting single nodes would explain
something that does not happen. The viewer is graded where the model is graded and discrete
where it is discrete (the 60/40 band, the 70 % gate, the nudge). It shows only numbers the
model actually used. The audiences: mainly analysis afterwards and the thesis, a little the
operator, hence offline first and small live.

**Limits.** A rules-mode session shows what the HMM *would* have said. Gate waits are logged
only from this change on. The demo's robot needs `.venv-robot`. No designer has used it yet.

**Future.** A live version, "what if" sliders, exports for A18, showing information gain
(A20+), and the rules drawn as the decision tree they are.

---

## Part H — The drowsiness ladder (option A)

**Choice.** A fifth hidden state, **tired** (awake but tired), between focused and drowsy:
focused → tired → drowsy → very drowsy. Tired is **shown as focused** (no nudge) and has
the **same sign values as focused**: it is a step reached through the switch rates, not a
sign pattern of its own. Waking up goes drowsy → tired fast (40 an hour) and then to focused.
Also: *looking away now* is the same for drowsy and very drowsy (looking away says nothing
about how deep drowsiness is). `config/state_hmm.yaml` version 2. The code now supports any
number of hidden states, each shown as one of the four reported states (`reported_as`); a
four-state file still works.

**Why.** The synthetic benchmark (docs/SYNTHETIC_DATASET.md) showed two problems:

1. **Drowsiness stuck:** after waking up, the eye signs were normal again after ~60 s (the
   minute window emptied), but the belief stayed above 90 % drowsy until ~100 s and only fell
   below 40 % after ~140 s. One "drowsy" state with a slow way back (3 an hour) held it there.
2. **Very drowsy was hardly ever shown.** Partly a simulator mistake: a microsleep lasted one
   simulated second, so *eyes shut now* never fired. Fixed in the simulator. Partly "no news"
   signs that kept counting against very drowsy: every look at the work (*looking away now*),
   a minute without nods.

**What was tried** (tuned on the train users, confirmed on test and test_hard, then checked on
the real recordings):

| Version | What | Outcome |
|---|---|---|
| L1–L3 | a tired state with its own, in-between sign values | false alarms and recovery much better; drowsy noticed later |
| L7 | faster step tired → drowsy | delay back to the old value |
| L8–L10 | also weaker nods / eyes-shut-now for very drowsy | very drowsy over-called: rejected |
| L12 | L7 + *looking away now* fix | best on simulated users, but on the real recording P99_rules_4 it **never said drowsy**: acted, weak signs were absorbed by "tired". **Rejected** |
| **L14** | tired with **focused's** sign values (a pure step) | real recording as before, and the benchmark gains kept: **adopted** |

**Result** (shipped v1 → v2, users never tuned on):

| | test | test_hard |
|---|---|---|
| accuracy | 88.3 → 91.1 % | 85.8 → 88.0 % |
| false alarms (s/hour of work) | 390 → 244 | 303 → 194 |
| drowsy noticed after (median) | 44 → 38 s | 36 → 34 s |
| back to focused after drowsy | 121 → 79 s | 110 → 78 s |
| very drowsy found (recall) | 38.5 → 38.5 % | 2 → 25 % |
| drowsy episodes found | 100 → 100 % | 94 → 94 % |

Real recordings (P99_rules_2/3/4): agreement with the rules unchanged or slightly higher; no
stretch of drowsiness lost.

**Limits.**

- **Very drowsy is still weak** for users whose eyes are rarely judged (head down): then only
  nods can show it. The same blind spot as acting drowsy with the head down, live.
- **Drowsy recall** (seconds) is a bit lower: the first moments of mild drowsiness can sit in
  *tired* (shown as focused). On real faces this is unmeasured.
- **Tuned on simulated users**, with one real recording as a veto. The real check is a normal
  recorded study session.
- The **trained** file (`python -m eepy.state.dataset train`) scores even higher on the
  benchmark but never says very drowsy, and fits the simulator: not adopted.

**Future.** The two-process model (attention fast, drowsiness as a slow level 0–1 with its own
uncertainty: "option C"), more ladder steps once real data shows them, and a fuzzy front-end
for the signs as soft evidence (a thesis variant).

---

## Part I — Yawns and nods count twice a minute (version 3)

**Choice.** The yawn count and the nod count get an evidence time of 30 s instead of 60 s: a
minute-long count now speaks twice a minute. The jaw drop (AU26) stays at 60 s.
`config/state_hmm.yaml` version 3.

**Why.** In the live test, acting tired (yawning, nodding, heavy lids) never reached drowsy. The
eye signs kept saying "awake" every second: acted heavy lids stay below the 80 % that counts as
closed, and blinks stay normal. A yawn or a nod spoke once a minute, about as hard as the eyes
the other way, so it ended in a near tie, and the ladder's fast way back (drowsy → tired) won.
Timed on simulated acting:

| What the user does (eyes alert) | Drowsy after, v2 | v3 |
|---|---|---|
| a yawn every 20 s | never | 140 s |
| a nod every 15 s | never | 88 s |
| a yawn every 20 s + a nod every 15 s | 74 s | 40 s |
| head down (eyes not judged) + a nod every 15 s | 80 s | 46 s |
| 2-s eye closures every 8 s | 31 s | 33 s |

**What was tried.** 20 s (three times a minute): faster still, but on test_hard false alarms rose
from 194 to 242 s/hour. 30 s won on both splits:

| | test v2 → v3 | test_hard v2 → v3 |
|---|---|---|
| accuracy | 91.1 → 92.0 % | 88.0 → 88.7 % |
| false alarms (s/hour of work) | 244 → 222 | 194 → 186 |
| recall drowsy | 73.9 → 78.3 % | 62.1 → 71.6 % |
| drowsy noticed after (median) | 38 → 36 s | 34 → 34 s |
| back to focused after drowsy | 79 → 73 s | 78 → 74 s |
| recall very drowsy | 38.5 → 38.5 % | 24.8 → 10.6 % |

Real recordings: P99_rules_2 and P99_rules_3 unchanged (no new drowsiness); on P99_rules_4 the
acted yawn and nod are picked up sooner (agreement with the rules 82 → 86 %).

**This partly reverses A3.2 and B1's test.** A3.2 said yawns and nods with alert eyes are never
enough; the reviewer's example in B1 (a yawn every 20 s with alert eyes) is now drowsy after
about 70 s. The double counting B1 fixed stays fixed: the jaw drop still counts once a minute,
and one yawn plus one nod still stays focused (P(sleepy) at most ~0.23). The tests pin the new
behaviour: repeated yawns or nods may reach drowsy, not before 50 s, never very drowsy.

**Limits.**

- **"No news" counts twice as much too:** a minute without yawns or nods now speaks a little
  harder against drowsiness. Simulated drowsiness without yawns is noticed a few seconds later
  (37 → 40 s) and very drowsy later (2-s closures every 8 s: 62 → 76–88 s). Very drowsy for the
  hard users dropped (25 → 11 %); it was already weak.
- Someone who yawns a lot while alert (bored, or a habit) can now be shown as drowsy after a
  minute or two. The test_hard users include such habits; false alarms still went down there.
- Acted yawning is not real drowsiness. A normal recorded study session stays the real check.

**Future.** Count "no yawns" weaker than "yawns" (an asymmetric evidence time), so a quiet minute
does not cost very drowsy; and hand signs (head on a hand, rubbing the eyes) as weak extra hints.

