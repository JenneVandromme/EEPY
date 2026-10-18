# A24: the contextual bandit (Thompson sampling)

In the **hmm_bandit** mode, EEPY learns *which* nudge works for this person, in this situation.
This page explains the model, the priors, the buckets, how it stays safe, and what the smoke
simulation shows. The settings are in `config/bandit.yaml`. For the whole decision pipeline,
see [HOW_EEPY_DECIDES.md](HOW_EEPY_DECIDES.md).

Contents: [In short](#in-short) · [Division of labour](#division-of-labour) · [The model](#the-model) ·
[Arms](#arms-what-it-chooses-between) · [Buckets](#buckets-the-situations) · [Priors](#priors-the-cold-start) ·
[Waiting](#waiting) · [Learning](#learning-updates-drift-wizard-nudges) · [Persistence](#persistence) ·
[Safety and the switch](#safety-and-the-switch) · [What you see](#what-you-see) ·
[The smoke simulation](#the-smoke-simulation) · [Limits](#limits) · [Try it](#try-it) · [Files](#files)

---

## In short

- **One Beta posterior per (situation, action)**, on the reward scaled to 0…1. Thompson sampling:
  draw one value from each allowed action's posterior, play the highest.
- **5 situations** (buckets): distracted early / after long work, drowsy before / after 23:00,
  and ambiguous.
- **Actions are ladder levels**. The tone (hedged/direct) keeps following A19's confidence rule.
- **Weak priors** (8 pseudo-observations), gentle levels first. An untrained bandit makes the
  same choice as the fixed escalation in 71% of its choices.
- **"Wait" has a fixed value (−0.7)**, so the bandit only vetoes a nudge once it has learned that
  nudging here mostly backfires.
- **It learns from A22's rewards**, per participant, and saves the numbers locally
  (`data/bandit/<code>.json`).
- **It never leaves A23's allowed set.** If it crashes or misbehaves, the fixed escalation plays.
  `policy: fixed` turns it off everywhere.
- **Smoke simulation** (made-up personas): after 100 evenings it found the persona's best level in
  14 of 16 situations and beat the fixed escalation for all four personas. After 30 evenings it was
  better for two personas and slightly worse for the two whose best nudge the fixed escalation
  already plays first.

## Division of labour

```
  the state (HMM, A16)  ──►  WHEN: A19's belief decisions (evidence, waiting when unsure)
                                    │  a nudge is due
                                    ▼
                         WHAT MAY: A23's allowed_actions(context)   (+ "wait", always)
                                    │
                                    ▼
                         WHICH: the bandit (hmm_bandit) or the fixed escalation (all other modes)
                                    │
                                    ▼
                         the behaviour plays (+ A20's explanation) ──► outcome (A14) ──► reward (A22)
                                                                                           │
                         the bandit's posterior for (situation, action)  ◄─────────────────┘
```

The bandit doesn't decide *when*, and it can't widen *what may* be played.

## The model

A22's rewards are **graded and bounded**: +1.0 … +0.4 for a refocus success (faster is higher),
+1.0 bedtime/break, +0.6 said OK, +0.1 snoozed, −0.1 no reaction, −0.5 dismissed, −1.0 interrupted
focus. Each reward is mapped to y = (r + 1) / 2 in [0, 1], and the action's posterior is updated:

    alpha += y          beta += 1 - y

This is the standard Thompson sampling scheme for bounded rewards (Agrawal & Goyal, 2012, who
prove its regret bounds). Why this model:

- **It fits the rewards.** It needs nothing but bounds, so it works with any mix of graded and
  discrete rewards, and its posterior mean is the expected reward.
- **It's honest with little data.** A Beta treats each outcome like a coin flip with that
  probability, which is the largest variance a bounded reward can have. So it never claims more
  certainty than the data gives. A Normal model would have to *learn* its variance from a handful
  of nudges, which is fragile at the start.
- **It's simple to explain.** For any action, "α and β are its pseudo-counts of good and bad
  outcomes" is a single sentence. The posterior mean and spread on the −1…+1 scale are what the
  panel shows.

The update is fractional and deterministic: the same session always gives the same posterior.

**Optional (`model: linear`, off by default):** one Bayesian linear regression per action on the
continuous context: P(distracted), P(drowsy), confidence, calibrated, late evening, hours worked.
It uses known noise and a Gaussian prior, with the intercept at the action's prior mean, and
Thompson sampling on the weights. It's meant for comparison in A25/A26. It needs more data than
the buckets, and it ignores `discount`.

## Arms: what it chooses between

An arm is a **ladder level** (`refocus:2`, `bedtime:4`, and `bedtime:5` for goodnight early) or
**wait**. A23 already decides which levels and tones are allowed. The bandit picks the level, and
the tone follows A19's rule: direct when calibrated, ≥ 85% sure, and very drowsy or after a nudge
without reaction. That halves the number of arms, and with little data per person that matters
more than learning tone. `arms: levels_and_tone` makes tone part of the choice, for later.

## Buckets: the situations

| Bucket | When | Why separately |
|---|---|---|
| `refocus_early` | distracted, < 45 min of work since the start or the last break | a fresh distraction |
| `refocus_late` | distracted, after 45+ min of work (A19's long-work line) | tired of working: a break may work better |
| `bedtime_early` | drowsy / very drowsy, before 23:00 | "go to bed" is a harder sell early |
| `bedtime_late` | drowsy / very drowsy, 23:00–05:00 | late: a direct goodnight may work |
| `ambiguous` | A19's "distracted or drowsy, unclear which" | in practice: act gently, or wait |

Only five, because each needs enough data. The bucket comes from information available at the
decision: the state, A19's ambiguity flag, minutes worked, and the local hour (the panel gives the
engine a wall clock; simulations use a set start time). An update always lands in the bucket of the
*decision*, from the context A22 logged with it.

## Priors: the cold start

Each arm starts as if it had already seen `strength` = **8** outcomes at its prior mean:
α₀ = 8·y₀, β₀ = 8·(1−y₀). A dozen real outcomes outweigh it.

| Level | Prior mean | Meaning |
|---|---|---|
| 1 (wordless glance / tired look) | +0.6 | gentle: try first |
| 2 | +0.25 | |
| 3 (spoken) | 0.0 | |
| 4 (break offer / wind-down) | −0.2 | intrusive: cautious |
| goodnight early | −0.2 | cautious |
| wait | −0.7 (fixed) | see below |

The prior centres the untrained bandit on the fixed escalation. Within A23's allowed sets (first
nudge L1/L2, then at most +2, never an ignored level), gentle-first priors make it climb
L1 → L2 → L3 → L4 most of the time. Measured over 2,000 untrained bandits climbing a whole ladder
without reactions: **71%** of their choices equal the fixed escalation's (`tests/test_bandit.py`
checks ≥ 65% over 300 bandits, a margin for sampling noise). The rest is exploration: sometimes L2 first, or a skipped level. In 1% of the
choices it waits instead, almost always when only the break offer is left.

When the designers' intrusiveness ranking (B8) arrives, these means should follow it. The level
order stands in for it until then.

**Population prior** (`prior.population: true`, off by default): a new participant starts at
everyone else's pooled posterior mean, scaled back to the same strength of 8. The bandit then
starts from what worked for others, without being any more certain.

## Waiting

"Wait" gives no outcome to learn from, so it has a **fixed value of −0.7**. The bandit waits only
when every allowed action draws below that. In practice that means it has learned that nudging in
this situation mostly *backfires* (dismissals −0.5, interrupted focus −1.0). Nudges that are only
ignored (−0.1) never silence it; the escalation's cooldowns already handle those. An untrained
bandit waits in about 1% of its choices.

After a wait, the bandit is asked again only after `hold_seconds` (30 s). Otherwise asking every
half second would turn the veto into a dice roll.

## Learning: updates, drift, wizard nudges

- **Updates:** the panel's `RewardTracker` (A22) emits a `RewardEvent` when a nudge's outcome is
  known. The bandit updates that action's posterior in the decision's bucket, saves it, and the
  panel logs a `bandit_update` event.
- **Censored nudges** (no fair verdict) don't update.
- **Wizard nudges** (the operator's own plays) don't update by default (`learn_from_wizard: false`):
  the policy didn't choose them.
- **Fallback choices:** when the fixed escalation stepped in, the action that actually played is
  what's learned.
- **Drift** (`discount`, 1.0 = off): e.g. 0.98 shrinks all evidence in the bucket back toward the
  prior at each update, so the bandit can follow a user who gets used to EEPY.

## Persistence

`data/bandit/<participant>.json` holds numbers only: per bucket and arm, the evidence
`[Σy, Σ(1−y), n]` and an update count. It never holds the prior, so changing the prior applies to
everyone at once. `data/` is never in git.

```bash
python -m eepy.bandit list            # who has saved posteriors
python -m eepy.bandit show P03        # bars per situation, and what it has learned
python -m eepy.bandit reset P03       # forget (asks first)
```

## Safety and the switch

- **The allowed set (A23)** is computed before every decision, and the bandit only draws for the
  actions in it.
- **The engine checks the choice.** If the bandit crashes (`policy_failed`) or returns something
  outside the set (`policy_outside_allowed`), the fixed escalation's choice plays instead, and both
  are logged.
- **`policy:` in `config/escalation.yaml`:**
  - `auto` (default): the bandit in hmm_bandit, the fixed escalation elsewhere.
  - `fixed`: the fixed escalation in every mode. **This is the one switch.**

  The policy actually used is recorded in each session's `meta.json` (`policy`).
- **The rules, hmm and wizard modes are unchanged.**

## What you see

- **Wizard panel (hmm_bandit):** a **Bandit** row under the autonomy rows, one bar per allowed
  action:
  - the posterior mean (the line) ± its spread (the band) on −1…+1;
  - what it drew (the triangle);
  - the chosen action in bold, with its chance (propensity);
  - "waiting" as a fixed dashed bar.
  
  Under it, a sentence: *"EEPY has learned that the wordless glance works best for you here
  (expected +0.44, 12 tries so far)"*. While the evidence is still smaller than the prior, it says
  *"currently expects"*.
- **Logs:** each `nudge_decision` in hmm_bandit has `bandit` (bucket, each arm's mean, spread, tries
  and draw, the chosen arm, the propensity) next to A23's `allowed`. A bandit wait is an
  `escalation` event `policy_wait`.
- **Launcher:**
  - **Bandit** tab: each participant's posteriors, reset, the smoke simulation.
  - **Decisions** tab: tick *bandit* to see its choices and draws next to the allowed set.

## The smoke simulation

`python -m eepy.bandit.smoke` (or the launcher's Bandit tab). Four simulated users with **hidden
preferences**, defined in `eepy/bandit/personas.yaml`. There's no D10 `personas.csv` in the repo
yet, so **the numbers are made up**: they give the bandit a known right answer, nothing more.

| Persona | Hidden preference | Best level |
|---|---|---|
| glance_responder | wordless nudges work; being spoken to annoys | refocus 1, bedtime 2 |
| needs_words | ignores wordless nudges; a spoken check works | 3 everywhere |
| break_taker | only a real break gets them back | 4 everywhere |
| late_sleeper | bedtime brushed off before 23:00; after 23:00 a spoken check works, negotiating annoys | refocus 4, bedtime 1 early / 3 late |

Every simulated evening has a distracted spell and a drowsy spell. Evenings alternate between early
and late starts and between short and long work, so all four buckets get data. The bandit keeps
learning across evenings, as with a real participant's saved posteriors. The comparison is the
fixed escalation on the same users and seeds.

**Results (2026-10-15, seed 1):**

| Persona | 30 evenings: fixed → bandit (total reward) | 100 evenings: fixed → bandit | False nudges (100 ev.) | Best level found (100 ev.) |
|---|---|---|---|---|
| glance_responder | +17.4 → +16.5 (−5 %) | +53.3 → +61.4 (+15 %) | 0 → 0 | 3 of 4 |
| needs_words | +26.1 → +31.6 (+21 %) | +91.6 → +100.5 (+10 %) | 0 → 0 | 4 of 4 |
| break_taker | +11.4 → +24.9 (+119 %) | +38.7 → +81.8 (+112 %) | 0 → 0 | 4 of 4 |
| late_sleeper | +18.3 → +16.9 (−8 %) | +62.3 → +74.8 (+20 %) | 2 → 1 | 3 of 4 |

How to read it:
- **When the fixed escalation is already right** (glance_responder: start at L1), the bandit pays a
  little for exploring at first, and only overtakes it later.
- **When the fixed escalation is wrong** (break_taker: three useless levels before the one that
  works), the bandit roughly doubles the reward and plays fewer nudges.
- **The two misses after 100 evenings** are near-ties (late_sleeper's refocus L3 vs L4) or
  situations with few nudges (glance_responder late bedtime).
- **The bandit keeps choosing L1/L2 often, even for needs_words.** A23 caps a first nudge at L2, so
  every episode has to start low. The bandit's freedom is deliberately modest.

## Limits

- **Real data will be sparse.** A participant sees maybe 5–15 nudges an evening, spread over 5
  situations and up to 5 levels. In a short study the bandit will mostly stay near its prior, which
  is by design: it then behaves like the fixed escalation. The smoke simulation shows it *can* learn;
  how fast real people would be learned is for A25/A26 and the study.
- **The personas are invented.** The results show the mechanism, not real preferences.
- **A22's baseline problem stays.** A user who would have recovered anyway still credits the nudge,
  unless the trend before the decision showed it. Waiting is valued by a fixed number, not learned
  from outcomes, because there are none.
- **Tone isn't learned** (unless `arms: levels_and_tone`), and neither is intensity.
- **Off-policy evaluation** (using the logged propensities to compare policies on recorded data) is
  prepared for but not built.

## Try it

**Launcher** (`python3 launch.py`):
1. **Bandit** tab → *Smoke simulation* → **Run** (30 evenings, about 40 s for all four).
2. **Decisions** tab → *Reactive user*, tick **bandit**, set *learned on* to 20 evenings →
   **Show decisions**: each choice with its draws, inside the allowed set.
3. **Developer** tab → Mode **HMM + bandit** → Save → **Start all**. Start a session in the Wizard
   panel tab and get distracted: the Bandit row shows its bars. Afterwards, in the **Bandit** tab,
   pick the participant → **Show** (or **Reset…**).

**Terminal:**

```bash
python -m eepy.bandit.smoke --sessions 30                    # bandit vs fixed on 4 personas
python -m eepy.interaction.decisions --responsive "focused 16m, distracted 6m, focused 3m, drowsy 12m" --bandit --evenings 20
python -m eepy.wizard --mode hmm_bandit --backend dry        # (.venv-robot) + python -m eepy.perception --live (.venv-vision)
python -m eepy.bandit show P03
```

## Files

| File | What |
|---|---|
| `config/bandit.yaml` | model, arms, priors, wait, discount, buckets, seed, the panel's words |
| `eepy/bandit/policy.py` | `BanditPolicy` (decide, observe, view), buckets, `Store` / `MemoryStore` |
| `eepy/bandit/linear.py` | the optional linear model |
| `eepy/bandit/simuser.py`, `personas.yaml` | the reactive simulated user and the personas |
| `eepy/bandit/smoke.py` | the smoke simulation |
| `eepy/bandit/__main__.py` | `list` / `show` / `reset` |
| `eepy/interaction/policy.py` | `make_policy` (auto/fixed, the mode), `tone_for` |
| `eepy/interaction/engine.py` | the hour, the wait hold, logging the bandit's detail |
| `eepy/wizard/server.py` | the hmm_bandit mode: start per participant, learn from rewards, meta |
| `eepy/launcher/bandit.py` | the launcher's Bandit tab |
| `tests/test_bandit.py` | updates per reward type, buckets, priors, allowed set only, seed, persistence, discount, population prior, wizard flag, waiting, the switch, fallback, linear model, smoke run |
