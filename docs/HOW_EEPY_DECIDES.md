# How EEPY decides: from the camera to a nudge, and back

This page is the map. It follows one moment through the whole system: the camera sees a face,
EEPY estimates how the student is doing, decides whether and how to nudge, says why, sees what
happened, and learns from it. For every step it says what the step does, where the code and
settings are, which document explains it in depth, and how to see it working in the launcher.

Contents: [The pipeline](#the-pipeline) · [Step by step](#step-by-step) · [The modes](#the-modes) ·
[The settings files](#the-settings-files) · [What is recorded](#what-is-recorded) ·
[Testing each part in the launcher](#testing-each-part-in-the-launcher) · [Where to go next](#where-to-go-next)

---

## The pipeline

```
 camera ──► face features ──► user state ──► WHEN? ──► WHAT MAY? ──► WHICH? ──► the robot ──► outcome ──► reward
 (A9)      blinks, PERCLOS,   rules (A13)    escalation  allowed      fixed       behaviour     (A14)       (A22)
           yawns, nods,       or HMM (A16)   (A14) +     actions      escalation  + speech                   │
           gaze, AUs                         belief      (A23)        or bandit   + explanation              │
           (A10-A12)                         decisions                (A24)       (A20)                      │
                                             (A19)                      ▲                                    │
                                                                        └───────── learns (hmm_bandit) ◄─────┘
```

Two processes do this together, on one computer:

- **The vision program** (`python -m eepy.perception`, in `.venv-vision`) has the camera. It
  computes the features and the user state once a second and sends the state to the panel.
- **The Wizard panel** (`python -m eepy.wizard`, in `.venv-robot`) has the robot. It decides,
  plays, records and learns.

The **launcher** (`python3 launch.py`) starts both with one click and shows every step in its
tabs. They talk over a local relay on 127.0.0.1 only.

## Step by step

### 1. The camera and the face features (A9–A12)

The camera (laptop or robot) gives frames. The face pipeline turns them into numbers over 10-s and
60-s windows:
- the share of time the eyes are closed (PERCLOS), blink length, long blinks, the longest closure;
- yawns, nods;
- where the gaze is (screen, notes, away), how long they've been looking away;
- optionally facial action units (AU43 eyes closed, AU26 jaw drop).

**Calibration** (A12) learns each participant's normal face and where their screen and notes are.
No picture is ever saved.

- Code: `eepy/camera/`, `eepy/perception/` · Settings: `config/eepy.yaml` (`camera`, `perception`,
  `aus`, `gaze`)
- Read: [eepy/perception/README.md](../eepy/perception/README.md),
  [eepy/camera/README.md](../eepy/camera/README.md)
- Launcher: **Live HMM** tab (the camera picture and the signs)

### 2. The user state (A13, A16)

Once a second the features become a state: *focused, distracted, drowsy, very drowsy, absent,
unknown*. Two estimators:

- **The rules** (A13): readable thresholds ("eyes closed 25% of the last minute → very drowsy").
  Settings: `config/state_rules.yaml`.
- **The HMM** (A16): a *belief* over the hidden states that adds up the evidence over time. It
  weighs overlapping signs once, and says how sure it is. Settings: `config/state_hmm.yaml`. It was
  tested and compared on simulated users (the synthetic dataset).

- Code: `eepy/state/`
- Read: [eepy/state/README.md](../eepy/state/README.md), [A16_HMM_DECISIONS.md](A16_HMM_DECISIONS.md),
  [EXPLANATION_VIEWER.md](EXPLANATION_VIEWER.md) (why the HMM decided what it did, second by
  second), [SYNTHETIC_DATASET.md](SYNTHETIC_DATASET.md)
- Launcher: **Live HMM** tab (the belief and its reasons, live)

### 3. WHEN to nudge (A14, A19)

The state sets the robot's **role**: study buddy (focused), refocus (distracted), bedtime (drowsy),
paused (absent). In the refocus and bedtime roles the robot climbs a **ladder** of ever clearer
nudges: L0 is quiet company, then a glance, a look, a spoken check, and finally a break offer or a
wind-down deal.

**The escalation (A14)** decides when the next rung may come:
- onset delays and reaction windows;
- the guards: at least 20 s between nudges, at most 12 per hour, none in the first 3 minutes;
- cooldowns, breaks, the wind-down timer and goodnight.

In the hmm modes, **belief decisions (A19)** decide the moment. Evidence for distraction or
drowsiness must build up first. When the belief is unclear the robot deliberately waits. When it's
unclear *which* (distracted or drowsy?), it gives one gentle nudge. A ladder can switch midway.

- Code: `eepy/interaction/engine.py`, `belief.py` · Settings: `config/escalation.yaml`
- Read: [eepy/interaction/README.md](../eepy/interaction/README.md), [A19_BELIEF_DECISIONS.md](A19_BELIEF_DECISIONS.md)
- Launcher: **Wizard panel** tab (the autonomy row and the ladder view); **Decisions** tab

### 4. WHAT MAY be played (A23)

When a nudge is due, `allowed_actions(context)` returns exactly the nudges that fit the moment:
- the ladder that matches the state;
- a first nudge at most at level 2;
- at most two levels up per step;
- never a level already ignored;
- the break offer only after 15 minutes of work;
- the firm tone only when sure.

"Wait" is always allowed. Whatever chooses next can only choose from this set.

- Code: `eepy/interaction/actions.py` · Settings: `config/escalation.yaml` → `actions:`
- Read: [A23_ALLOWED_ACTIONS.md](A23_ALLOWED_ACTIONS.md)
- Launcher: **Decisions** tab (every decision with its allowed set)

### 5. WHICH nudge (A14 / A24)

- **The fixed escalation** (rules and hmm modes): start low, one rung up after each nudge without
  a reaction. The tone follows the confidence.
- **The contextual bandit** (hmm_bandit mode): Thompson sampling over the allowed nudges. It has
  one posterior per situation and nudge, learned from this participant's rewards and saved per
  participant.
- **The switch:** `policy: fixed` in `config/escalation.yaml` turns the bandit off everywhere. If
  the bandit fails, or picks outside the allowed set, the fixed escalation plays instead.

- Code: `eepy/interaction/policy.py`, `eepy/bandit/` · Settings: `config/bandit.yaml`
- Read: [A24_BANDIT.md](A24_BANDIT.md)
- Launcher: **Bandit** tab (what it learned, the smoke simulation); **Decisions** tab (tick *bandit*)

### 6. The robot does it, and says why (A20)

The chosen rung is a **behaviour**: movements, sounds, a spoken line. Behaviours and lines live in
`behaviors/`, as editable YAML. After a spoken nudge, EEPY can add **one sentence explaining why**:
*"Your eyes were closed 29% of the last minute."* That sentence comes from the evidence that really
moved the belief over the decision window (A20). Explanations on/off is an experimental condition,
recorded per session.

- Code: `eepy/behaviors/`, `eepy/speech/`, `eepy/explanations/` · Settings: `behaviors/*.yaml`,
  `config/eepy.yaml` → `explanations:`
- Read: [BEHAVIORS_GUIDE.md](BEHAVIORS_GUIDE.md), [A20_EXPLANATIONS.md](A20_EXPLANATIONS.md),
  [EXPLANATION_CATALOG.md](EXPLANATION_CATALOG.md)
- Launcher: **Wizard panel** tab (the "Why #…" line under the ladder view)

### 7. What came of it (A14), what it was worth (A22), and learning (A24)

Every nudge gets exactly one **outcome**:
- *success*: back to work, said OK, took the break, went to bed;
- *snoozed*, *dismissed*, *no reaction*;
- *interrupted focus*: the nudge came while they were already back at work;
- *censored*: no fair verdict.

A22 turns it into a **reward**, from +1 (back to work at once) to −1 (nudged someone who was
working). The reward is discounted when the student was already recovering. Each reward comes with
the context known at the decision. In hmm_bandit, the bandit learns from each reward.

- Code: `eepy/bandit/rewards.py` · Settings: `config/rewards.yaml`
- Read: [A22_REWARDS.md](A22_REWARDS.md)
- Launcher: **Rewards** tab (live rewards, tables from recorded sessions, simulations)

## The modes

The **mode** is the experimental condition, chosen when the panel starts (`config/eepy.yaml`
`mode:`, `--mode`, or the launcher's Developer tab). It goes into each session's folder name and
`meta.json`.

| Mode | State | When | Which | Explanations | Rewards logged | Learns |
|---|---|---|---|---|---|---|
| `wizard` | shown only | the operator | the operator | — | for operator nudges (`source=wizard`) | no |
| `rules` | the rules (A13) | escalation (A14) | fixed escalation | yes (rules that fired) | yes | no |
| `hmm` | the HMM (A16) | escalation + belief decisions (A19) | fixed escalation | yes (summed evidence) | yes | no |
| `hmm_bandit` | the HMM (A16) | escalation + belief decisions (A19) | **bandit** (A24) | yes | yes | **yes**, per participant |

In every mode the operator can play anything by hand, and **Pause autonomy** hands control back at
once.

## The settings files

| File | What it decides | Who edits it |
|---|---|---|
| `config/eepy.yaml` | the mode, data folder, camera, speech, explanations on/off | researchers |
| `config/state_rules.yaml` | the rules' thresholds | researchers |
| `config/state_hmm.yaml` | the HMM's numbers (each with a `why`) | researchers |
| `config/escalation.yaml` | when to nudge (timings, guards, belief decisions), what may be played (`actions:`), the policy switch | researchers, designers |
| `config/rewards.yaml` | what each outcome is worth | researchers |
| `config/bandit.yaml` | the bandit's model, priors, buckets, waiting, the panel's words | researchers |
| `config/wizard_panel.yaml` | the panel's keys and markers | anyone |
| `behaviors/*.yaml` | the movements, sounds and lines (including `explain_*` lines) | designers |

Each file is checked when the panel starts. A mistake stops it with a message saying what and where.

## What is recorded

Each session is a folder in `data/sessions/`:
- `meta.json`: who (a code, never a name), the mode, the policy used, explanations on/off, the code
  version;
- `events.jsonl`: every press, behaviour, decision, outcome, explanation and reward;
- `events.vision.jsonl` and the feature tables: from the vision program.

The bandit's posteriors are in `data/bandit/<code>.json`, and reward tables in `data/rewards/`.
`data/` is never committed. Details: [DATA_GUIDE.md](DATA_GUIDE.md).

## Testing each part in the launcher

`python3 launch.py` opens http://127.0.0.1:8090. Most checks need no camera and no robot.

| To check | Tab | Do |
|---|---|---|
| everything runs | Overview | **Start all** (Developer: Robot *Dry run*, Camera *fake* for a check without a camera) |
| the state from your face | Live HMM | start vision with the laptop camera; watch the belief and its reasons |
| when it nudges (A14/A19) | Wizard panel | mode HMM, start a session, look away for a minute; watch the autonomy row and the ladder view |
| what may be played (A23) | Decisions | *Scripted states* `takes_the_break` → **Show decisions**; try `focused 4m, distracted 8m` (no break offer) |
| why it nudged (A20) | Wizard panel | after a nudge: the "Why #…" line (said, or would say) |
| what nudges were worth (A22) | Rewards | *Simulated* → **Run**; *Recorded* → **Build table** |
| the bandit (A24) | Bandit | *Smoke simulation* → **Run**; Decisions with *bandit* ticked; mode **HMM + bandit** for a live session |
| the old way still works | your terminals | the Developer tab shows each command, ready to copy |

## Where to go next

| You want to… | Read |
|---|---|
| set up a computer | [README](../README.md) |
| run a session | [WIZARD_PANEL_GUIDE.md](WIZARD_PANEL_GUIDE.md) |
| use the launcher | [eepy/launcher/README.md](../eepy/launcher/README.md) |
| write behaviours or explanation lines | [BEHAVIORS_GUIDE.md](BEHAVIORS_GUIDE.md), [EXPLANATION_CATALOG.md](EXPLANATION_CATALOG.md) |
| analyse sessions | [DATA_GUIDE.md](DATA_GUIDE.md) |
| understand one step deeply | the doc linked in that step above |
| see what's still open | [TODO.md](TODO.md) |
