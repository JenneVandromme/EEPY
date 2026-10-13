# A synthetic dataset: train and compare HMM versions without recording anyone

Recording yourself for every change is slow, and acting is not real behaviour. This tool
makes **simulated users**: each with their own habits, each living through a 20–40
minute study session. You can then **train** HMM numbers on some users and **compare
versions** on others, in seconds.

Contents: [Quick start](#quick-start) · [What is in it](#what-is-in-it) ·
[Reading the scores](#reading-the-scores) · [Comparing versions](#comparing-versions-the-workflow) ·
[Training](#training) · [Results](#results) · [Limits](#limits) ·
[For developers](#for-developers)

## Quick start

**macOS / Linux** (Windows: `.venv-robot\Scripts\Activate.ps1`, the rest is the same):
```bash
source .venv-robot/bin/activate        # or .venv-vision
python -m eepy.state.dataset build      # 60 simulated users, ~30 hours, in a few seconds
python -m eepy.state.dataset train      # fit HMM numbers on the 34 train users
python -m eepy.state.dataset eval       # rules vs shipped HMM vs trained HMM, on the 14 test users
python -m eepy.state.dataset eval --split test_hard             # the 12 users with extreme habits
python -m eepy.state.dataset eval --model hmm --model hmm:my_hmm.yaml   # your own version
```

Everything goes to `data/synthetic/v1/` (never committed). Use `--name v2` for another
dataset, `--users`, `--hard-users`, `--minutes 20 40`, `--seed` to change its make-up.

Look at one simulated user in the explanation viewer:
```bash
python -m eepy.state.explain data/synthetic/v1/U003_train_tired --open
```

## What is in it

**60 users** by default, split by user (nobody is in two splits):

| Split | Users | For |
|---|---|---|
| `train` | 34 | fitting numbers |
| `test` | 14 | comparing versions: the same kind of users, never trained on |
| `test_hard` | 12 | robustness: more extreme habits (see below) |

**Each user has their own habits**, drawn at random (reproducible with `--seed`):

- how often the eyes are judged while working (55–95 %; hard: 35–95 %);
- their usual blink length and how often they blink;
- **lowered lids while reading** that look half closed (0–4 %; hard: up to 10 %);
- how often and how long they glance away;
- yawns and quick looks down that read as nods, **while focused**;
- looking back at the work now and then while on the phone;
- how drowsiness shows: long closures, microsleeps, slower blinks, yawns, nods;
- camera speed (15 pictures/s mostly; some 8–12, hard also 6: blinks then unreliable);
- calibrated or not (80 %; hard 50 %); action units or not (60 %).

**Each user has a persona** that decides the course of the session:

| Persona | The session |
|---|---|
| diligent | long work stretches, short phone checks, now and then a break away |
| distractible | the phone every 1.5–4 minutes, for 20–120 s |
| tired | drowsy spells that grow longer, later also very drowsy; leaves near the end |
| mixed | a bit of everything |

Every user is an **ordinary session folder** (`events.vision.jsonl` with the window
summaries, `events.jsonl` with a marker at every change, `meta.json`) plus
**`truth.csv`**: what the user was really doing, second by second (work → focused,
phone → distracted, drowsy, very drowsy, leaving → absent). Replay and the explanation
viewer work on it like on a recording.

## Reading the scores

`eval` prints one column per version:

| Row | Meaning |
|---|---|
| accuracy (5 states) | share of seconds with the right state |
| accuracy (families) | the same, with drowsy and very drowsy counted as one ("sleepy") |
| balanced accuracy | the mean of the recalls below: rare states count as much as common ones |
| recall *state* | of the seconds that really were *state*, the share shown as *state* |
| false alarms | seconds per hour of real work shown as distracted or drowsy: **what the robot would nag about** |
| state changes per hour | how calm it is (fewer = less flicker) |
| *state* episodes found | share of episodes in which the right state appeared at all |
| median delay | from the start of an episode to the first right state |
| back to focused after drowsy | how long drowsiness "sticks" after waking up |

**Grace time:** right after a change of behaviour no estimator can know yet, so the first
seconds are not scored (focused 20 s, distracted 15 s, drowsy 60 s, very drowsy 45 s,
absent 20 s). Delays are measured separately.

## Comparing versions: the workflow

1. Copy `config/state_hmm.yaml` to e.g. `my_hmm.yaml` and change a number (with its `why`).
2. `python -m eepy.state.dataset eval --model hmm --model hmm:my_hmm.yaml`, then also on
   `--split test_hard`.
3. Better on both, without making false alarms or delays worse? Then check it on a real
   recording (`python -m eepy.state.replay <session> --estimator hmm --params my_hmm.yaml`),
   and only then copy it into `config/state_hmm.yaml` (bump `version`).
4. Never tune on `test` itself until it looks good there: that is fooling yourself. Tune on
   `train` (`--split train`), confirm on `test`.

## Training

`train` fits the HMM's numbers on the train users, using the ground truth:

- **emissions:** for each sign and hidden state, from the seconds whose window lies wholly
  inside that behaviour (a 60-s sign needs 60 s of the same behaviour), skipped signs left out.
  Beta: mean and spread; Student-t: median and robust spread; Poisson: events per minute;
  Bernoulli: the share; categorical: the bin shares. Each is **blended with the hand-set
  value** as a prior, so a state with little data keeps its old numbers;
- **transition rates:** switches per hour in the truth, with the hand-set rate as a prior;
- **kept:** the evidence times, the correlation groups, the `needs`, the reporting.

The result is a normal parameter file (`source: fitted`, with a `fit:` block and a `why`
per number) in `data/synthetic/v1/state_hmm_trained.yaml`. Use it with `eval`, replay
(`--params`), the viewer (`--params`) or the live page (`--hmm-params`).

## Results

Default dataset (`v1`, seed 1). **Test** split (14 users): the rules, HMM version 1 (one
drowsy step), version 2 (the drowsiness ladder, shipped) and numbers trained from version 2:

| | rules | HMM v1 | **HMM v2** | trained from v2 |
|---|---|---|---|---|
| accuracy (5 states) | 83 % | 88 % | 91 % | 96 % |
| false alarms (s/hour of work) | 391 | 390 | 244 | 132 |
| drowsy noticed after (median) | 22 s | 44 s | 38 s | 30 s |
| back to focused after drowsy | 65 s | 121 s | 79 s | 57 s |
| recall very drowsy | 100 % | 39 % | 39 % | 14 % |

**test_hard** (12 users): accuracy 78 / 86 / 88 / 92 %, false alarms 639 / 303 / 194 / 113
s/hour, recall very drowsy 100 / 2 / 25 / 0 %, recall distracted 79 / 48 / 48 / 55 %.

What it showed, and what was done (docs/A16_HMM_DECISIONS.md, Part H):

- **Drowsiness "stuck"** in v1 (121 s to get back to focused): fixed with the ladder (79 s).
- **Very drowsy was hardly ever shown**: partly a simulator mistake (fixed: a microsleep now
  lasts its full length), partly "no news" signs counting against it; better, still weak.
- **Distraction is missed for unusual users** (48 % on test_hard): users who look back at
  their work during a phone call, or glance a lot while working. Not yet addressed.
- **The rules over-call very drowsy** (their drowsy recall is 10 %: they say very drowsy).
- The **trained** numbers win on most rows but never say very drowsy and fit the simulator:
  not adopted. Use them as a direction, not as the answer.

## Limits

- **The users come from a simulator written with the same ideas as the model.** A version
  that wins here is better *under these assumptions*: for example, that drowsiness shows
  in the eyes, that a phone means looking away. Real people can differ. The trained
  numbers fit the simulator, so they will look very good on it. Use the dataset to compare
  and catch regressions; **real recordings stay the judge** before a version goes live.
- No talking, no other people, no lighting changes, no glasses glare, no head movement
  beyond nods and glances.
- The personas' rhythms (how often someone picks up the phone) decide the trained transition
  rates: those numbers say more about the simulator than about students.

## For developers

- `eepy/state/synthetic.py`: `simulate(phases, ..., profile=...)`; `_second_user()` is one
  second of a user with habits; without a profile the original simulator runs unchanged (the
  tests rely on its exact output). `write_session()` writes a session folder with `truth.csv`.
- `eepy/state/dataset.py`: `user_profile()`, `persona_phases()`, `build()`; `evaluate()` /
  `combine()` score estimates against the truth; `collect()` + `fit()` train; `main()` is the
  command.
- Tests: `tests/test_hmm.py` (`test_synthetic_dataset_build_train_eval`,
  `test_evaluate_scores_against_the_truth`).
