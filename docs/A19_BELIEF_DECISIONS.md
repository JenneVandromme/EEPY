# A19: belief-based decisions (hmm mode)

In the **rules** mode, EEPY nudges when a state has lasted long enough (a timer per state). Until
A19, the **hmm** mode did the same, with one extra check: the HMM had to be at least 70 % sure.
A19 lets the hmm mode use the HMM's **whole belief**: how likely each state is, how unclear the
belief is, and which signs pushed it there. The rules mode is unchanged.

This document gives the choices, why they were made, the results and the limits. In plain
words for designers: [eepy/interaction/README.md](../eepy/interaction/README.md#belief-based-decisions-hmm-mode).
The HMM itself: [A16_HMM_DECISIONS.md](A16_HMM_DECISIONS.md).

Contents: [In one paragraph](#in-one-paragraph) · [The choices](#the-choices) ·
[What was already there](#what-was-already-there) · [Results](#results) · [Limits](#limits) ·
[Future](#future) · [For developers](#for-developers)

## In one paragraph

Every second, EEPY adds up the **evidence**: how far the belief in *distracted* (or *drowsy*)
is above a floor of 55 %. A confident belief fills it fast, a borderline one slowly, and a coin
flip never. A ladder starts when there is enough evidence **and** the belief is at least 70 %
right now. Once running, a ladder goes on above 55 % (it doesn't stop and start at one line), holds
when focus is coming back, and resets when the person is clearly focused (75 %) for 10 s. When the
belief is unclear, EEPY **waits** and logs that as a decision. When *distracted* and *drowsy* are
both moderately likely, it chooses the **gentlest** option. When the belief moves from one ladder to
the other mid-way, it **switches** without starting over. Every decision carries the belief, the
confidence, the uncertainty and the top three signs.

## The choices

All numbers are in [`config/escalation.yaml`](../config/escalation.yaml), section `hmm:`, each
with a one-line reason. `confidence:` (A16) stays: its `act` (70 %) is the start threshold, its
`direct` (85 %) the firm tone.

### C1. Accumulated evidence instead of a timer

**Choice.** Per ladder: `evidence += (P − 0.55) × seconds`, kept between 0 and what is needed.
P(refocus) is P(distracted); P(bedtime) is P(drowsy) + P(very drowsy). The first nudge comes
when the evidence reaches `needed` **and** P ≥ `confidence.act` (70 %).

| Ladder | Needed | At 95 % | At 80 % | At 72 % | Below 70 % |
|---|---|---|---|---|---|
| refocus | 8 | 20 s | 32 s | 47 s | never |
| bedtime | 12 | 30 s | 48 s | 71 s | never |
| bedtime, very drowsy **and calibrated** | 6 | 15 s | | | |

**Why.** A sure belief should act sooner than a borderline one; a timer treats them the same.
The 95 % times equal the old timers (`onset`), so a clear case feels the same as before. Below
the floor the evidence drains, so a minute of 60/40 doesn't add up to a nudge.

**The very-drowsy path** (your change): 6 instead of 4, and only when the features are
calibrated. Very drowsy is the weakest-detected state, and a false alarm there jumps straight
to bedtime level 2. Uncalibrated, very drowsy uses the normal bedtime value.

### C2. Hysteresis on the belief

**Choice.**

- **Start:** the evidence, and P ≥ 70 %.
- **Go on (climb):** P ≥ 55 % (`keep`) **and** P(focused) < 30 % (`hold_if_focused`).
- **Hold:** otherwise. No new level, and an open nudge's reaction window runs on.
- **Reset:** P(focused) ≥ 75 % for 10 s (`reset`).
- **After a nudge that worked** (shown as focused again) but without a clear reset: no climbing.
  A new start becomes a fresh ladder from its first level.

**Why.** One line for start and stop makes a ladder flicker at the edge. `hold_if_focused` came
out of the benchmark (C8): the HMM's belief takes about a minute to come back after drowsiness,
and ladders kept climbing at someone already back at work. On the train users, holding once
P(focused) is back at 30 % lowered false nudges (2.9 → 2.2 per hour of work, outside the
tolerance) and missed fewer episodes.

### C3. Wait for more evidence

**Choice.** No start while the belief is unclear: the uncertainty (normalised entropy) is above
0.6, or the two most likely states are less than 15 points apart. This only counts while
*distracted* or *drowsy* is at least 40 % likely (`wait.consider`); below that, the person is
just focused. The wait is a `policy_decision` (`wait`), logged once when it starts. Its end
(`resume`) is logged too. The evidence keeps building meanwhile. Once waiting, the belief must be
`wait.margin` (0.05) clearer before the wait ends: uncertainty below 0.55, gap above 0.20.

**Why.** A 70 % belief with the rest spread over three states is not the same as 70 / 30. The
log shows that EEPY chose not to act and why, not that it simply did nothing. The margin came from
the live check (below): without it, the uncertainty hovered at 0.6 and the wait went on and off
four times in 30 s.

### C4. Ambiguity: the gentler option

**Rule.** When P(distracted) ≥ 30 % **and** P(drowsy + very drowsy) ≥ 30 %, and neither ladder is
ready, a third evidence counter adds up (distracted + sleepy − 0.55, needed 12). When it is full:

- **less than 45 minutes of work** since the start or the last break: **refocus level 1** only
  (the wordless glance), hedged, once. No climbing while it stays ambiguous;
- **45 minutes or more:** the **break offer** (refocus level 4), once. It fits both: a break helps
  someone distracted and someone tired.

If the belief then becomes clear, the normal ladder takes over: distracted goes on as refocus;
drowsy switches to bedtime (C5).

**Why.** Telling a tired person "back to work" is wrong, and so is telling a distracted person
"time for bed". A glance means nothing specific; a break offer suits both.

### C5. Switching ladders mid-way

**Choice.** A running ladder switches when the **other** ladder's start condition is met (its
evidence and 70 %), and the belief is clear (C3). Not on the shown state flipping. Then:

1. the open nudge closes as `censored` ("the belief moved to drowsy");
2. a `switch` decision is logged, with `from_ladder`, `from_level` and `start_level`;
3. the role switch plays (the `role_switch` behaviour, if the designers made one);
4. the new ladder starts at **old level − 1**, at least its start level, at most level 3
   (`switch.step_back`, `switch.max_level`). Very drowsy still starts at bedtime level 2 or higher;
5. the minimum gap (20 s) still applies.

While the belief moves (the old ladder below 55 %, the new one building), the old ladder holds:
"the belief is moving to drowsy; switching when there is enough evidence (40 %)".

**Why.** They already ignored some nudges, so the faintest hint again would be too soft. But it is
a new topic, so one step softer than where they were. Never straight to the break offer, the
wind-down or goodnight: those need their own path up.

### C6. Confidence → behaviour

**Tone** (unchanged rule): direct only when calibrated **and** at least 85 % sure **and** very
drowsy or a repeat after no reaction. Otherwise hedged. The speech module picks the hedged or
direct wording of the line.

**Intensity** (new, hmm mode only): the designer's intensity for the level × a factor from 0.7
(at the 70 % act threshold) to 1.0 (from 90 % sure). The designer's value is a ceiling and is
never exceeded. `intensity.scale: false` switches it off.

### C7. Evidence on every decision

The HMM already ranked its signs every second (the explanation viewer shows them). The vision
program now sends the **top 3** with each estimate (`evidence` in the STATE message: feature,
text, supports/against, strength, impact). Every `nudge_decision` (in its `context`) and every
`policy_decision` carries them, ready for A20's explanations.

### C8. Comparison on the synthetic dataset

`python -m eepy.state.dataset eval --nudges` runs the real escalation over every simulated user,
three ways: the rules; the HMM with A16's gate; the HMM with A19's belief decisions. Your changes:

- **Missed episodes for several minimum lengths** (distracted ≥ 45 / 60 / 90 s, drowsy
  ≥ 90 / 120 / 180 s). Episodes that cannot be caught by design are left out: those inside the
  first 3 minutes (the settling-in guard) and after a goodnight.
- **False nudges with and without a tolerance:** a nudge while the truth is focused, and the same
  without those within ±10 s of a change of behaviour (`--tolerance`).

## What was already there

| Requirement | Was there | A19 added |
|---|---|---|
| 1 evidence instead of a timer | onset timers + a 70 % gate | the evidence counters (C1) |
| 2 hysteresis | only in the HMM's shown state (60/40) | start / go on / hold / reset (C2) |
| 3 wait | `waiting_for_confidence` below 70 % | entropy, top-two gap, a logged decision (C3) |
| 4 ambiguity | — | C4 |
| 5 switching | the old ladder censored, the new one from level 1 | a clean switch with the level carried over (C5) |
| 6 confidence → behaviour | hedged / direct | intensity scaling (C6) |
| 7 evidence | computed by the HMM, kept in the logs | sent along, on every decision (C7) |
| 8 YAML | `confidence:` | `hmm:` (all of the above) |
| 9 logging + panel | `nudge_decision`, `escalation`, the panel's chain | `policy_decision`, plain-words panel line and chain |
| 10 scenarios + comparison | state scenarios; state scores | belief scenarios; `eval --nudges` (C8) |

## Results

### Scenarios (`python -m eepy.interaction.simulate <name>`)

| Scenario | What happens |
|---|---|
| `belief_sure_vs_borderline` | 95 % sure: first nudge after 20 s. 72 %: after 48 s, hedged, smaller movements |
| `belief_unsure` | 50/50: one logged *wait*, no nudge in 3 minutes |
| `belief_ambiguous` | 40/40: one wordless glance (refocus level 1), then holds |
| `belief_ambiguous_late` | the same after 46 minutes of work: the break offer |
| `belief_shift` | refocus levels 1–3, then drowsy: switch to bedtime level 2 (not 1) |
| `belief_wavering` | goes on at 60 % (hysteresis), holds at 45 %, a fresh ladder after a nudge that worked |

### Live check (2026-10-13, laptop camera, panel on the dry-run backend, speech off)

About 2 minutes in hmm mode (session `2026-10-13_P99_hmm`, calibration reused). The settling-in
guard was shortened to 20 s for this run only (a scratch copy of the escalation file); everything
else as shipped. Looking at the screen, then away for about a minute, then back:

```
00:48 WAIT    not sure yet (distracted 64%, focused 28%; uncertainty 0.62)
              evidence: looking away now (12 s) clearly suggests distraction; ...
00:49 RESUME  ... (on and off four times until 01:18: fixed with the wait margin)
01:28 NUDGE   refocus level 1 (hedged, intensity 1.0): distracted 100%: enough evidence after 41 s
01:42 HOLD    focus coming back (distracted 69%, focused 31%)
01:57 OUTCOME success (back to focused after 19 s)
02:01 RESET   focused 98% for 10 s; approving nod; role study buddy
```

The panel's line followed it in words: *Watching: distracted 79%; evidence 8% of what a first
nudge needs* → *Waiting: not sure yet (focused 52%, distracted 46%)* → *waiting for a reaction to
level 1* → *Holding (focused again? …)* → *focused: never interrupted*. The robot was not
connected (no daemon answered), so nothing moved.

### Synthetic users (`eval --nudges`, dataset v1; users never tuned on)

**test** (14 users):

| | rules | hmm gate (A16) | **hmm belief (A19)** |
|---|---|---|---|
| nudges per hour | 13.9 | 13.0 | 12.5 |
| false nudges / hour of work | 8.0 | 4.8 | **4.2** |
| … not within 10 s of a change | 5.9 | 2.9 | 2.9 |
| missed distracted ≥ 45 / 60 / 90 s | 45 / 38 / 29 % | 15 / 12 / 14 % | 15 / 12 / 14 % |
| missed drowsy ≥ 90 / 120 / 180 s | 29 / 27 / 33 % | 17 / 19 / 14 % | 22 / 19 / 14 % |
| first nudge, distracted / drowsy (median) | 30 / 48 s | 38 / 66 s | 41 / 68 s |

**test_hard** (12 users with extreme habits):

| | rules | hmm gate | **hmm belief** |
|---|---|---|---|
| false nudges / hour of work | 10.6 | 3.7 | 4.0 |
| … not within 10 s of a change | 8.8 | 2.4 | 2.6 |
| missed distracted ≥ 45 / 60 / 90 s | 32 / 21 / 20 % | 32 / 36 / 20 % | 37 / 36 / 20 % |
| missed drowsy ≥ 90 / 120 / 180 s | 25 / 30 / 33 % | 25 / 20 / 17 % | 25 / 20 / 17 % |
| first nudge, distracted / drowsy (median) | 49 / 35 s | 54 / 82 s | 66 / 91 s |

**What this says.**

- Both HMM versions make far fewer false nudges than the rules (about half on test, a third on
  test_hard) and miss fewer longer episodes.
- **Belief vs gate: about the same.** Slightly fewer false nudges on test, slightly more on
  test_hard; the same missed episodes from 60 s (distracted) and 120 s (drowsy) up.
- **The belief version is slower for borderline users** (test_hard: first refocus nudge 66 s vs
  54 s). That is the design (C1: borderline beliefs wait longer), and it shows where it costs.
- The simulated users rarely produce ambiguous or unclear beliefs, so what A19 adds (waiting,
  the ambiguity rule, switching) hardly shows in these numbers. The scenarios and tests check it.
- The counts are small (6–20 episodes per row): a difference of one episode is 5–15 points.

## Limits

- **Tuned on simulated users,** with the same assumptions as the HMM. The real check is a
  recorded hmm session (checkpoint 2).
- **The probabilities are decision scores,** not calibrated chances (A18 measures them). The
  floor, the thresholds and *needed* inherit that.
- **Ambiguity is rare in the HMM's output:** its families compete, so 40/40 between distracted and
  drowsy seldom happens. The rule mostly guards edge cases.
- **The ambiguous nudge is a refocus nudge** in the logs (ladder refocus). The `policy_decision`
  and the reason say it was the ambiguous option.
- **Switching needs the other ladder's full evidence:** a quick move from distracted to drowsy
  takes 15–30 s to switch. The old ladder holds meanwhile.
- **The guards still decide a lot:** 12 nudges an hour, 20 s apart and the cooldowns shape the
  numbers as much as the belief does (the synthetic distractible users reach the hourly limit).
- **Intensity scaling** is an untested design choice: whether smaller movements at 72 % feel
  right is for the designers and the pilot.
- **One live check of 2 minutes,** with acted distraction; no drowsiness live, no robot.
- `hold_if_focused` was added after looking at the benchmark (tuned on the train users,
  confirmed on test). It was not in the original plan.

## Future

- **A20:** turn the top evidence into a spoken explanation ("you've looked away for a while").
- **A24 (bandit):** learn the start level and tone; the policy interface and `propensity` are
  ready, and the switch carry-over is a good first arm.
- **Information gain:** a "check" behaviour (a glance) when the belief is unclear, to learn
  more instead of only waiting.
- **Per-person needed:** someone who glances a lot needs more evidence than someone who never does.
- **Recalibrate after A18,** then revisit the floor and thresholds.

## For developers

- `eepy/interaction/belief.py`: `BeliefTracker` (evidence counters, `ready`, `keeps`,
  `focus_returning`, `unsure`, `ambiguous_ready`, `reset_ready`, `intensity`).
- `eepy/interaction/engine.py`: `_belief_step` replaces the onset/gate path only when the
  estimate is from the HMM with `probs` and `hmm.decisions` is `belief`; `_switch`, `_hold`,
  `_decide` (the `policy_decision` event). The flows (break, wind-down, goodnight), guards,
  outcomes and the policy are shared.
- `eepy/interaction/policy.py`: `NudgeContext.start_level` / `start_reason` (only in hmm
  decisions; the rules' context is unchanged).
- `eepy/interaction/config.py`: `BeliefConfig`, the `hmm:` section and its checks.
- `eepy/state/runner.py`: `panel_view` sends the top 3 `evidence`.
- `eepy/interaction/simulate.py`: belief segments (`distracted@0.6`, `distracted@0.4+drowsy@0.4`),
  `--belief`, `--session X --estimator hmm`, the `belief_*` scenarios.
- `eepy/state/dataset.py`: `eval --nudges`, `score_nudges`, `combine_nudges`, `run_nudges`.
- Tests: `tests/test_belief.py`; the panel in `tests/test_wizard.py`
  (`test_hmm_mode_belief_decisions_in_the_panel`).
