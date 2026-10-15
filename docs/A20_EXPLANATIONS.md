# A20: explaining each nudge decision

After a spoken nudge, EEPY can add one sentence saying **why**: *"Getting late, isn't it? Your eyes were
closed 29% of the last minute."* This page explains where that sentence comes from, why it is
faithful to what the model actually did, and how to check that. The reasons themselves, their
placeholders and default sentences: [EXPLANATION_CATALOG.md](EXPLANATION_CATALOG.md).

Contents: [In short](#in-short) · [The Explanation object](#the-explanation-object) ·
[How the evidence is summed](#how-the-evidence-is-summed-hmm-mode) · [Rules mode](#rules-mode) ·
[Is it faithful?](#is-it-faithful) · [Speech and the on/off condition](#speech-and-the-onoff-condition) ·
[Try it](#try-it) · [Limits](#limits) · [For developers](#for-developers)

---

## In short

- **Every nudge decision gets an Explanation**, in the rules and the hmm mode alike, logged with the
  `nudge_decision` event and shown in the Wizard panel ("Why #3 … said: …").
- **hmm mode:** the HMM keeps an *evidence ledger*: per evidence type, how much it has pushed each
  hidden state since the session started. The explanation is the change in that ledger over the
  **decision window**, so it covers everything since the belief started shifting, not one second.
- **One item per evidence type.** The types follow the HMM's correlation groups, so "eyes closed
  34%" and "eyes-closed action unit 30%" are one reason (*eye closure*), never two.
- **Exact:** the items plus a *dynamics* term add up to the change in log-odds between the decided
  state and the runner-up, to rounding (≈ 10⁻⁵).
- **Contrastive:** *"drowsy rather than distracted, because your eyes were closing more and you
  kept looking at your work"*.
- **Spoken as one extra sentence** after the nudge's own line, about the top reason. Off
  (`explanations.enabled: false`), EEPY logs what it would have said and says nothing more: the
  two conditions differ only in that sentence.

## The Explanation object

Logged as `nudge_decision.explanation` (and `autonomy.explanation` in the panel's status):

```json
{"mode": "hmm", "ladder": "bedtime", "level": 3, "tone": "direct", "state": "drowsy",
 "runner_up": "focused", "confidence": 0.98, "calibrated": true, "ambiguous": false,
 "window": {"start": 1314.0, "seconds": 60.0, "steps": 61},
 "items": [{"type": "eye_closure", "contribution": 14.13, "share": 1.0, "direction": "for",
            "value": {"perclos_60s": 0.29, "blink_mean_ms_rel_60s": 4.5}, "peak": {"perclos_60s": 0.33},
            "placeholders": {"closed_pct": "29", "times_normal": "4.5", "minutes": "1"},
            "lead": "perclos_60s"}],
 "against": [{"type": "nodding", "contribution": -0.21, "direction": "against"}],
 "dynamics": -13.86,
 "contrast": {"runner_up": "focused", "because": ["eye_closure"], "despite": ["nodding"],
              "log_odds_change": 0.27,
              "text": "drowsy rather than focused, because your eyes were closing more, although your head stayed up"},
 "sentence": "Your eyes were closed 29% of the last minute.",
 "text": "eyes closing 100% (closed 29%, blinks 4.5x normal)",
 "checks": {"log_odds_change": 0.2714, "evidence": 14.1309, "dynamics": -13.8595,
            "sum_error": 5.7e-05, "compared": ["drowsy", "tired"]},
 "condition": "on", "spoken": true}
```

| Field | Meaning |
|---|---|
| `state`, `runner_up` | the decided state (the ladder's family) and the most likely other family |
| `window` | the time the evidence was summed over |
| `items` | the top evidence types (`explanations.top_k`, default 3), strongest first: `contribution` in nats (hmm) or `seconds` fired (rules), `share` of the positive evidence, the values at the decision and their `peak` in the window, the filled `placeholders` |
| `direction` | `for`: the sign of the decided state was present (eyes closing); `against`: the sign of the runner-up was absent (kept looking at the work) |
| `against` | evidence that pointed to the runner-up instead |
| `dynamics` | the part of the change that came from the model itself: drift over time and the probability floor |
| `contrast` | why this state rather than the runner-up, in words |
| `sentence` | what EEPY says (the default sentence; a designer's `explain_<type>` line replaces it when spoken) |
| `checks` | the exact decomposition (hmm only) |
| `condition`, `spoken`, `not_spoken` | the on/off condition, whether it was said, and if not, why (off, a silent level, nothing to say) |

## How the evidence is summed (hmm mode)

At every update the HMM adds each sign's weighted log-likelihood to the belief (A16:
correlation-group weights, evidence time). The ledger (`eepy/state/hmm.py`) keeps, since the last
reset and per evidence type, the running sum of exactly those contributions for every hidden
state, plus `drift`: everything else that changed the belief (the transition over time and the
probability floor). Because normalisation cancels in a ratio, for any two hidden states *i, j*:

    Δ log(b_i / b_j)  =  Σ_types Δ(ev_i − ev_j)  +  Δ(drift_i − drift_j)        (exactly)

The panel gets the ledger with every estimate. At a decision, the engine's `Explainer`
(`eepy/explanations/build.py`) takes the ledger at the start of the window and at the decision:

- **Decided state:** the ladder's family (refocus → distracted, bedtime → drowsy / very drowsy),
  represented by its most likely hidden state.
- **Runner-up:** the most likely other family at the decision (focused may be represented by its
  hidden "tired" state, as A16 does for the families).
- **Window:** for the first nudge of a climb, since the evidence for that ladder began building
  (A19's evidence start); for later levels, since the previous nudge of the climb, so the
  explanation says what is new. At least 60 s (the measurements' own window), at most 10 min.
- **Contribution** of a type = its change in (ev_decided − ev_runner-up). Items below 0.05 nats are
  not reasons.
- **Direction:** compared with the third family, did the type mostly raise the decided state, or
  mostly lower the runner-up? That chooses "your eyes were closing more" or "your eyes stayed open".

## Rules mode

The rules have no evidence strength, so the same object lists the **rules that fired** during the
window for the decided state, grouped by evidence type (via each rule's feature): `seconds` fired,
the rules' ids, the last value against its threshold. `contribution` and `dynamics` are `null`,
`checks` is empty, and items are ordered by seconds fired. The runner-up is the other ladder's
state if any of its rules fired in the window, else focused.

## Is it faithful?

Two checks, as tests (`tests/test_explanations.py`) and as a diagnostic over any session:

```bash
python -m eepy.explanations.check latest                        # a recorded hmm session
python -m eepy.explanations.check --synthetic "work 3m, sleepy 8m"
```

1. **Sum:** items + dynamics = the change in log-odds, |error| < 10⁻³ (the ledger is rounded to 6
   decimals for the panel; the real errors are ≈ 5·10⁻⁵).
2. **Deletion:** re-run the HMM over the same features with the top evidence type left out during
   the window only. The decided state's probability must drop by at least 10 points. On the
   simulated sessions: drowsy 94% → 1%, 95% → 21%; distracted 100% → 6%, 100% → 38%.

**What a contribution means (a finding worth knowing).** Early in a climb, evidence moves the
belief: the contribution is most of the log-odds change. Once the belief is settled (later
levels), the evidence still pushes (+14 nats over a minute) but the model's drift pulls back about
as much (−14), and the belief barely moves (+0.3). The decomposition is still exact, but the
contribution then means *the evidence that kept the belief where it is*. That is why the deletion
test asks for a clear drop in probability, not a drop by the whole contribution: without the
evidence the drift itself changes (it depends on the belief), so the two are not additive.

## Speech and the on/off condition

- **When:** only after a nudge that speaks (bedtime and refocus level 3 and up). Silent levels log
  the explanation with `not_spoken: "this level does not speak"`, and the panel shows "would say".
- **What:** one sentence about the top item, in the nudge's tone, appended to the behaviour's first
  spoken line **in the same clip** (`Speaker.speak_line(..., then=...)`), so neither cuts the other
  off. A designer's `explain_<type>` line in `behaviors/lines.yaml` is used when it exists and can
  be filled; else the catalog's default sentence. Sentences with numbers are synthesised live
  (Piper, ~30–100 ms).
- **The condition:** `explanations.enabled` in `config/eepy.yaml`, or
  `python -m eepy.wizard --explanations on|off` for one run. Recorded in every session's
  `meta.json` (`explanations`). Off, the explanation is still built and logged, never spoken.

## Try it

```bash
python -m eepy.interaction.simulate --synthetic "work 3m, sleepy 8m"            # explanations per nudge
python -m eepy.interaction.simulate --synthetic "work 3m, phone 5m, work 2m"
python -m eepy.interaction.simulate --synthetic "work 3m, sleepy 8m" --estimator rules
python -m eepy.explanations.catalog                                            # the catalog as Markdown
```

## Limits

- **Values are the last minute's**, not counted over the whole window: "looked away 6 times in
  10 minutes" is not something the model weighs. `{away_count}` is background detail only.
- **Only blink length is relative to the person's own normal** (`{times_normal}`); PERCLOS, yawns
  and gaze are absolute.
- **No phone evidence:** looking at a phone counts as looking away.
- **The runner-up's representative is a hidden state**, so with the extra "tired" state the
  focused family is compared through whichever of focused/tired is more likely.
- **One spoken sentence**, about the top item; the contrast and the other items are logged and shown,
  not said. Whether that is the right amount is part of what the designers (C6) and the evaluation
  decide.
- **Timer decisions** (the wind-down timer's goodnight) are not evidence-based and get no explanation.

## For developers

| File | What |
|---|---|
| `eepy/explanations/catalog.py` | the evidence types, placeholders, default sentences; writes EXPLANATION_CATALOG.md |
| `eepy/explanations/build.py` | `Explainer`: the Explanation from the ledger (hmm) or the fired rules |
| `eepy/explanations/check.py` | the faithfulness diagnostic (sum + deletion) |
| `eepy/state/hmm.py` | the ledger (`ledger_ev`, `ledger_drift`, `epoch`) and `muted` for the deletion test |
| `eepy/state/runner.py` | `panel_view` sends `ledger`, `values` and (rules) `fired` |
| `eepy/interaction/engine.py` | `_explain`: the window, the condition, logging, the context for speech |
| `eepy/behaviors/player.py`, `eepy/speech/speaker.py` | the explanation after the first spoken line |
| `eepy/behaviors/validate.py` | `explain_<type>` lines: known type, only its placeholders |

After changing the catalog: `python -m eepy.explanations.catalog --write` (a test checks the doc is
up to date).
