# A22: rewards for the bandit

The bandit (A24) will learn which nudge works best in which situation. For that, every nudge
becomes one training example: **(context at decision time, action, reward)**. This page explains
how the reward is computed, why, and how to check it. The numbers are in `config/rewards.yaml`.

Contents: [In short](#in-short) · [Where the outcome comes from](#where-the-outcome-comes-from) ·
[The reward function](#the-reward-function) · [Spontaneous recovery](#spontaneous-recovery) ·
[Censored and overlapping nudges](#censored-and-overlapping-nudges) ·
[The context](#the-context-no-leakage) · [Online and offline](#online-and-offline) ·
[Operator nudges](#operator-nudges-sourcewizard) · [Simulation](#simulation) ·
[First results](#first-results) · [Limits](#limits) · [Try it](#try-it)

---

## In short

- The **escalation** already decides each nudge's outcome (A14, A19). A22 **prices** it: from +1
  (back to work at once, went to bed) to −1 (nudged someone who was already working).
- A refocus success is **graded by speed**, and **discounted** when P(focused) was already rising
  before the nudge.
- **Censored** nudges (no fair verdict, e.g. the belief switched ladders) get no reward; they are
  excluded from training but counted.
- Each nudge's **context** is the snapshot logged at the decision: the belief, its trend, the
  ladder and level, the time of day, the session so far. Nothing from after the decision.
- The same `RewardTracker` runs **live** in the panel (a `nudge_reward` event per nudge) and
  **offline** over logged sessions, so both give the same rewards.
- Nudges the **operator** played by hand are included, flagged `source=wizard`.

## Where the outcome comes from

| Outcome | Decided by the escalation when … |
|---|---|
| `success` | back to focused for 10 s within the reaction window (refocus; `how: focus`), said OK (`reply`), took / accepted the break (`break`), goodnight confirmed, left after the bedtime nudges, or the session ended after they had left (`bed`) |
| `snoozed` / `dismissed` | the participant's answer (the operator's markers) |
| `no_reaction` | the reaction window ran out |
| `interrupted_focus` | they were back to focused within 5 s of the nudge: it came too late |
| `censored` | no fair verdict: the belief switched ladders, the state changed role, they left the desk, the operator paused autonomy or played something, the session ended mid-refocus |

A22 added two fields to `nudge_outcome`: `how` (above) and `back_after`, the seconds from the nudge
until the focus **began** (`seconds` is when the success was *confirmed*, 10 s later).

## The reward function

| Outcome | Reward | Why |
|---|---|---|
| refocus success | **+1.0 → +0.4**, linear in `back_after` over the reaction window | fast compliance is what refocus wants; a slow return still beats nothing |
| bedtime success | **+1.0** | going to bed is the goal; timing is the wind-down's job |
| break taken / accepted | **+1.0** | the intended answer to a break offer |
| said OK (lower levels) | **+0.6** | willing, but not yet seen back at work |
| snoozed | **+0.1** | a valid, engaged answer to a negotiation, but not compliance |
| no reaction | **−0.1** | an interruption that achieved nothing; small, so gentle nudges stay worth trying |
| dismissed | **−0.5** | explicitly unwanted |
| interrupted focus | **−1.0** | nudging someone who was already working: the worst outcome |
| censored | **none** | excluded from training, counted in the report |

The asymmetry is deliberate: a wasted nudge (−0.1) costs little, an unwanted one (−0.5) more, a
wrong one (−1.0) most. That keeps the bandit willing to try, and afraid of nagging the focused.

## Spontaneous recovery

A user who was already drifting back to focus would make any nudge look good. So the engine stores,
with every decision, **`focused_trend_20s`**: P(focused) at the decision minus P(focused) 20 s
before. A refocus success is discounted:

    discount = 0.75 · min(1, trend / 0.3)        (only when trend > 0)
    reward   = raw reward · (1 − discount)

So a rise of 0.3 or more keeps 25 % of the credit (maybe the nudge still helped); a flat or falling
belief keeps all of it. The rules give no probabilities: there, "focused" already pending at the
decision (the rules' dwell) gives a 50 % discount. Bedtime successes are not discounted (waking up a
little is not the same as going to bed).

## Censored and overlapping nudges

- **Censored nudges** get `reward: null` / `included: false`, and the report counts them with
  their reasons.
- **Overlap:** the newest nudge gets the credit. The escalation only ever has one open nudge (the
  next level comes after the previous window closes); an operator play during it closes it as
  censored (A14). An operator nudge still open when another nudge starts is closed as censored too.
- **Delayed rewards:** a nudge stays pending until its outcome is known (at the latest when its
  window closes), then the tracker emits a `RewardEvent`. The bandit (A24) subscribes to these.

## The context (no leakage)

Logged with the decision (`nudge_decision.context`), so it can only hold what was known then:

| Field | |
|---|---|
| `p_focused` … `p_very_drowsy`, `most_likely`, `confidence`, `uncertainty` | the HMM's belief (empty for the rules) |
| `focused_trend_20s`, `pending` | the trend before the decision, the state waiting out its dwell |
| `state`, `estimator`, `calibrated` | |
| `ladder`, `level` (played now), `previous_level`, `last_outcome` | |
| `hour` | the local time of day (from the decision's `t_wall`) |
| `minutes_in_session`, `minutes_since_break`, `minutes_since_last_nudge` | |
| `nudges_session`, `nudges_last_hour` | ("tonight" = this session: one session is one evening) |

The trend is computed only from estimates up to the decision (a test feeds a later estimate and
checks the trend does not change). The action is `ladder`, `level`, `tone`, `intensity`, plus the
`policy` and its `propensity` (for off-policy learning in A24).

## Online and offline

The panel feeds every logged event, and each user state, to a `RewardTracker`, which logs a
`nudge_reward` event per nudge (the launcher's **Rewards → Live** shows them). The offline tool reads
a session's `events.jsonl` (and the user states in `events.vision.jsonl`) and feeds the **same**
tracker in time order. Every outcome is timed where it happened (not when it was checked), so both
give the same rewards; a test runs a panel session and compares.

```bash
python -m eepy.bandit.rewards all --out data/rewards/table.csv     # every recorded session
python -m eepy.bandit.rewards P03 latest                           # some sessions
```

One row per nudge: `session, decision_id, source, t_session, ladder, level, tone, intensity, policy,
propensity, outcome, how, latency, window, rule, raw_reward, discount, reward, included, detail`,
and the context as `ctx_*` columns. The report adds rewards per action and per context bucket (state,
confidence, calibrated, hour, source), the censored share with reasons, and a "worth a look" list.

## Operator nudges (`source=wizard`)

When the operator plays a ladder behaviour by hand (in any mode), the panel logs a `wizard_nudge`
with the context at that moment. The tracker judges it with the escalation's own rules and reaction
windows (back to focus, replies, leaving, the window running out). It has no `policy` or
`propensity`: the bandit can decide whether to learn from it (`wizard.include` in the config).
Operator plays recorded before A22 have no `wizard_nudge` event; the report says so.

## Simulation

- `python -m eepy.interaction.simulate …` now prints each nudge's reward.
- `--synthetic` / `--dataset`: simulated faces through the real HMM. **These users never react to
  nudges** (their timeline is fixed), so they are useful for checking the pipeline, not for learning.
- `--responsive`: a **reactive simulated user** (`eepy/bandit/simuser.py`). A refocus nudge brings
  them back with a chance that grows with the level (and a little for the direct tone); a bedtime
  nudge may send them to bed (likelier after 23:00); spoken levels may get a snooze or a dismissal;
  personas `average`, `responsive`, `stubborn`. **Its numbers are made up**: it exists so A24 can be
  developed and checked against a known pattern, not as evidence about real users.

## First results

*(2026-10-15; run them again with the commands below.)*

**Recorded sessions** (18 sessions, all P99 tests): 22 nudges in 5 sessions, 19 with a reward (mean
+0.25), 3 censored (the operator played over one, two ended with the session). Refocus L1 +0.08
(n=9), L4 +0.76 (n=2). Flagged: two bedtime "successes" are only the test session ending; 3 operator
plays from before A22 are missing. Too few for any conclusion: this checks the pipeline.

**Reactive user, average, 50 runs:** 329 nudges, mean +0.10, 3 % censored. The reward rises with the
level as the simulator was built to (refocus L1 +0.03 → L4 +0.57): the pattern A24 should find.

**Synthetic dataset, test users (14):** 87 nudges, mean +0.04. They never react, yet 16 refocus
nudges ended in "success": recoveries that would have happened anyway. The recovery discount caught
5 of them (the others were still drifting away at the decision and recovered later in the window).

## Limits

- **Recoveries that start after the nudge cannot be told apart** from nudges that worked, with
  information from before the decision; using later information would be leakage. The fix belongs
  in A24: compare against a baseline (how often users recover without a nudge), e.g. by giving the
  bandit a "not yet" option and learning the advantage over it.
- **The session ending after a bedtime nudge** counts as going to bed only when the student had
  already left the desk; still sitting there, it's censored (no verdict). Before 2026-10-16 it always
  counted as success, which taught the bandit a false +1.0 when an operator ended a test session; the
  report flags such older outcomes.
- **Reaction windows pause** while the user is absent or the state unknown, so a success can come
  long after the window's nominal end; it is priced as the slowest success and flagged.
- **Operator nudges** are judged without the window pause, and with the user states the vision
  program logged (none: they can only time out).
- **Very little real data** so far, all test sessions (one, P99_hmm_5, contains a minute of
  fake-camera rows).

## Try it

**In the launcher** (`python3 launch.py`), the **Rewards** tab:
- **Live:** during a session (mode rules or hmm, or the operator playing ladder levels), every
  nudge's reward as it comes in.
- **Recorded:** pick sessions (or all) → **Build table**: the report, and the table as a CSV in
  `data/rewards/`.
- **Simulated:** *Reactive user* (persona, number of runs), *Simulated faces*, or *Scripted states with
  replies* → **Run**.

Each shows the terminal command it ran. In a terminal:

```bash
python -m eepy.bandit.rewards all --out data/rewards/table.csv
python -m eepy.bandit.rewards --responsive "focused 3m, distracted 6m, focused 3m, drowsy 15m" --runs 50
python -m eepy.bandit.rewards --responsive "focused 3m, distracted 6m" --persona stubborn --runs 20
python -m eepy.bandit.rewards --dataset test
python -m eepy.bandit.rewards --scenario "focused 2m, distracted 3m, snoozed, distracted 6m, focused 4m"
python -m eepy.interaction.simulate "drowsy 6m, snoozed, drowsy 10m"          # rewards per nudge
```

| File | What |
|---|---|
| `config/rewards.yaml` | the reward values and the recovery discount |
| `eepy/bandit/rewards.py` | `price`, `RewardTracker`, `RewardEvent`, the offline tool and report |
| `eepy/bandit/context.py` | `FocusTrend` (the trend before a decision), the context snapshot |
| `eepy/bandit/simuser.py` | the reactive simulated user |
| `eepy/interaction/engine.py` | `how`/`back_after` on outcomes; trend, pending, level played in the context |
| `eepy/wizard/server.py` | the live tracker, `wizard_nudge`, `nudge_reward` |
| `eepy/launcher/rewards.py` | the launcher's Rewards tab |
