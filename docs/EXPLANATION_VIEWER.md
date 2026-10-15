# Seeing why EEPY decided: the explanation viewer

EEPY's state estimator (the HMM, task A16) is meant to be **explainable**: not only *what*
it decided, but *why*, in a form a designer, a reviewer or a thesis reader can follow. This
guide covers the two ways to see that:

- **the explanation viewer**: a page for one session (recorded, or a simulated demo). It
  shows every second of the decision, and you can scrub through it or play it in slow
  motion;
- **the decision chain in the Wizard panel**: one line, live, during a session in the hmm
  mode.

Contents:

- [Quick start](#quick-start)
- [Live: watch it while it happens](#live-watch-it-while-it-happens)
- [What the viewer shows](#what-the-viewer-shows)
- [Reading it: a worked example](#reading-it-a-worked-example)
- [The live decision chain in the panel](#the-live-decision-chain-in-the-panel)
- [Why this design (and not a "decision graph")](#why-this-design-and-not-a-decision-graph)
- [How it works](#how-it-works)
- [Limits](#limits)
- [Ideas for later](#ideas-for-later)
- [For developers](#for-developers)

---

## Quick start

No camera or robot is needed. Only a recorded session, or not even that (the demo).

**macOS / Linux:**
```bash
cd EEPY
source .venv-robot/bin/activate                  # or .venv-vision (see below)
python -m eepy.state.explain --demo --open       # a simulated session, opened in the browser
python -m eepy.state.explain latest --open       # the newest recorded session
python -m eepy.state.explain P03                 # a session by (part of) its name
```

**Windows (PowerShell):**
```powershell
cd EEPY
.venv-robot\Scripts\Activate.ps1
python -m eepy.state.explain --demo --open
python -m eepy.state.explain latest --open
```

It writes **one HTML file** (by default `data/explain/<session>.html`, or `--out FILE`).
Double-click it to open it in any browser. It needs no internet and no running program,
so you can mail it, put it next to your thesis notes, or open it on the presentation
laptop. Like everything in `data/`, it is never committed.

Options:

| Option | What it does |
|---|---|
| `--demo` | a simulated session instead of a recording: working, on the phone, back, acting sleepy, leaving |
| `--params my_hmm.yaml` | explain with other HMM numbers (try a change before using it live) |
| `--prior none` / `--prior file.json` | without the session's starting belief / with another one |
| `--out FILE` | where to write the page |
| `--open` | open it in the browser afterwards |

**Which environment:** both work. In `.venv-vision` the **demo** shows the HMM only: the
robot's simulated actions need the behaviour library, which lives in `.venv-robot`.
Recorded sessions show their real robot actions in either environment.

---

## Live: watch it while it happens

To debug with your own face, use the live page. The camera, the rules, the HMM and the viewer
are all in **one browser window**, so you can keep looking at your screen (that counts as
looking at your work):

```bash
source .venv-vision/bin/activate
python -m eepy.perception.live                    # free run; opens http://127.0.0.1:8770
python -m eepy.perception.live --scenario         # the self-test parts, with a score in the terminal
python -m eepy.perception.live --record P99       # also records the features as a session
```

On top: a small camera picture (face box green while the eyes are judged, orange when not),
the live status (face, eyes judged now, eye closure now, gaze zone, calibrated), the raw signs
the HMM gets, and with `--scenario` the instructions and the time left. Below: the same five
steps as the viewer, every second, and a growing timeline. Click back to pause; **● live**
follows again. With `--record`, open the same run afterwards in the viewer or replay it with
other numbers. Only this computer can open the page; no pictures are written. Options:
`--camera`, `--hmm-params FILE`, `--no-aus`, `--port`, `--seconds`.

**With the robot (or the simulator): `python -m eepy.perception --live`.** The page above
is a stand-alone debugger: it keeps the camera for itself and drives nothing, so the panel
gets no state and the robot only idles. To watch the HMM while the robot acts on it, run the
panel in hmm mode and start the vision program with `--live` instead (`--live-port` for
another port). The same page then shows the vision program's own HMM (the one the panel
acts on), starts over at every session, and adds the robot's decisions (waits, holds,
nudges, outcomes) from the session's logs. Before a session starts, or in a rules
session, it says so and stays empty. No `--scenario` or `--record` there: the panel's
session records everything.

**Where to look when it says "focused" while you act drowsy:** *eyes judged now* and
`eyes_measured_s_60s`. If those drop (head down, turned), the eye signs are skipped. Then
the eye closure: acted half-closed lids stay below the 80 % that counts as closed.

To test versions on many simulated users instead: [SYNTHETIC_DATASET.md](SYNTHETIC_DATASET.md).

---

## What the viewer shows

The page has two halves: **one second, in detail** (top) and **the whole session**
(bottom). Moving through the bottom changes the top.

### Top: the decision pipeline, for the selected second

Read it left to right. It is the order in which EEPY actually decides:

```
1 Signs from the camera  →  2 Belief (the HMM)  →  3 Shown state  →  4 Sure enough to act?  →  5 The robot
```

**1 · Signs from the camera.** Every sign the HMM listens to, with what was measured
(*eyes closed 31% of the last minute*) and three small bars: how much this sign pushes
towards **focused**, **distracted** and **sleepy**. A bar to the right, in the state's
colour, means "makes this more likely"; a grey bar to the left means "makes this less
likely". Hover over a sign for the exact numbers and the sentence the HMM would say about it.

- Signs are **grouped** the way the HMM groups them. The title says how much the group
  counts together, e.g. *eye signs: 6 of 6 usable · together they count as 1.71 signs, not
  6*. That is the "not counted twice" rule made visible: one long eye closure moves all six
  eye signs, so together they get less weight.
- **Greyed** signs were not used this second, with the reason (*not used: needs
  eyes_measured_s_60s >= 20 (is 13.0)*: the eyes were not seen looking forward long
  enough). Nothing is guessed for them.

**2 · Belief (the HMM).** The four hidden states as circles, each filled to its
probability. The arrows between them are how often people switch, per hour: thick =
often (from distracted back to focused, about 60 times an hour), thin = rare (from
focused straight to very drowsy). They are fixed and come from `config/state_hmm.yaml`.

- **★** marks the most likely state; the **thick ring** marks the state shown in the panel
  (they can differ: see step 3).
- **▲ / ▼** shows how much each probability moved this second.
- **uncertainty** goes from 0 (completely sure) to 1 (no idea). "On hold" means nobody was
  there or the camera gave no usable data: the belief only drifts then, and the circles
  look faded.
- With the **drowsiness ladder** (HMM version 2) the bottom row shows its steps: *tired*
  (shown as focused), drowsy, very drowsy. The **drowsiness level** (0 alert … 1 very
  drowsy) under the diagram adds them up.

**3 · Shown state.** What the panel showed, and how sure the HMM was of it: the
probability of that state's *family* (drowsy and very drowsy together form "sleepy").
The state only switches when another family reaches **60 %**, or when this one falls
below **40 %**: no flicker. *→ distracted is becoming more likely* appears when another
state is gaining. For comparison: what **the rules** said in the same second.

**4 · Sure enough to act?** The confidence gate (hmm mode). A bar with the confidence,
a mark where EEPY starts acting (**70 %**) and one for the firmer, direct tone
(**85 %**). Then **✓ sure enough** or **✗ not sure enough**. When the robot really held
back in the session, the log says so: *Logged: held back: distracted 62% < 70% since 02:02*.

**5 · The robot.** The robot's role (study buddy, refocus, bedtime, paused) and the last
nudge: its ladder and level, the tone, how sure the HMM was when it decided, and the
outcome (*→ success*, *→ no reaction*). The box **lights up** at the moment of a nudge.

**Why.** Under the pipeline: the reasons the panel showed at that second, in the panel's
words.

Things that just changed **light up** briefly (the shown state, the gate's verdict, a new
nudge), so they stand out while playing.

### Bottom: the whole session

- **belief**: the four probabilities over time, stacked (green focused, amber distracted,
  orange drowsy, red very drowsy). The black line is the confidence in the shown state;
  the dotted line is the 70 % where EEPY acts. Faded stretches: on hold.
- **HMM shows / rules show**: the state each estimator showed, side by side: where do
  they disagree, and for how long?
- **markers**: what the operator pressed in the panel (*phone picked up*, *yawn*), or in
  the demo the parts of the scenario. These are your ground truth.
- **robot**: blue dots are nudges; a green tick is a successful outcome, a grey tick another
  outcome; an amber band is a time the gate held a nudge back.

Under it, a **list of every event**. Click one to jump there.

**Moving through it:** click or drag on the timeline; **▶ play** with a speed from ¼×
(slow motion) to 30×; **◀ 1 s / 1 s ▶** (or the arrow keys, with Shift for 10 s);
**next change ⏭** (or `n`) jumps to the next state change or event. Hover over the
timeline for a summary of that moment.

---

## Reading it: a worked example

The demo (`--demo`), around **02:06**: the person looked back at their work 21 s ago
after a phone call.

- **Signs:** *looking away now: looking at the work* pushes towards focused, but *looking
  away over the minute: 68 %* still pushes towards distracted (the minute is not over yet).
  The eye signs are greyed: the eyes were not seen long enough while looking away.
- **Belief:** focused 45 % ▲4, distracted 55 % ▼4. Focused is catching up.
- **Shown state:** still *distracted*, at 55 %: focused has not reached 60 %, and distracted
  has not fallen below 40 %.
- **Sure enough?** ✗: 55 % < 70 %, and the log confirms *held back: distracted 69 % < 70 %
  since 02:02*. The robot does **not** give the second refocus nudge to someone who is
  already back at work.
- **The robot:** the first nudge (01:32, at 100 %) got *no reaction*: the person only came
  back later.

A few seconds later the belief tips to focused, the shown state follows, the gate's wait
ends (*the state changed to focused*), and the ladder resets. That is the confidence gate
doing what it is for.

---

## The live decision chain in the panel

In a session in the **hmm** mode, the autonomy row of the Wizard panel shows the same
chain in one line:

```
autonomy: bedtime · bedtime level 1     drowsy 82 %  →  ✓ sure enough (acts from 70 %)  →  bedtime level 1
```

- **left:** the shown state and how sure the HMM is;
- **middle:** the confidence gate (green outline: acts; amber: holds back);
- **right:** what the robot is doing about it (quiet, the first nudge when due, a ladder
  level, or paused by you).

With the belief decisions (A19, `hmm:` in `config/escalation.yaml`) the chain has four steps:

```
distracted 72 %, focused 28 %  →  evidence for distracted: 60 %  →  waiting: not sure yet  →  quiet
```

the belief (the two most likely states), how far the evidence is towards a first nudge, the
decision (nudge, waiting, holding, switching ladder, back to focused, watching; hover it for the
reason and the top evidence), and what the robot does. The line under it says the same in words,
e.g. *Waiting: not sure yet (distracted 48%, focused 41%)*. In the viewer, the waits and holds are
shown like the gate's (amber on the robot row), with the decision and its evidence in step 4.

The middle step lights up when the verdict changes. The state pill and the belief bar
next to it are unchanged (see the
[Wizard panel guide](WIZARD_PANEL_GUIDE.md)). It is deliberately small: during a
session the operator watches the participant, not a diagram. The full picture is the
viewer, afterwards.

---

## Why this design (and not a "decision graph")

The first idea was a graph of decision nodes that light up as the decision flows through
them. That fits a **decision tree**, which takes one path through yes/no questions. The
HMM does not work that way:

- every second, **every sign nudges all four probabilities at once**, a little; there is no
  single path to light up. Lighting one node would show a yes/no step that never happens:
  a misleading explanation, which is worse than none;
- the **real yes/no decisions** come after the belief: the 60/40 band of the shown state,
  the 70 % gate, the ladder level. The viewer lights up exactly those.

So the viewer draws the pipeline as it really is: graded where the model is graded (bar
lengths, filled circles), discrete where it is discrete (the shown state, ✓/✗, the
nudge). Three principles guided it:

1. **Faithful.** Every number shown is one the model actually used: the signs' pushes are
   its log-likelihoods with the real weights, the belief is its belief, the gate uses the
   session's own thresholds, and the robot's actions are the logged events. Nothing is
   decorative.
2. **Contrastive.** People ask "why drowsy and not focused?". The bars show each sign's
   push per state side by side, and focused is explained by what speaks *against* the
   alternative (*eyes closed 2 %: speaks against drowsiness*).
3. **In context.** A single second is hard to judge. The timeline shows what came before
   and after, where the HMM and the rules disagree, and what the operator saw (the markers).

The audiences, as agreed: mainly **analysing sessions afterwards** (you and the designers)
and the **thesis / presentation**; a little for the **operator** during a session. Hence
an offline page first, and only a one-line chain live.

For the thesis: the belief diagram (step 2) is the standard way to draw an HMM (states
plus transition rates), and the demo page tells the whole story in five minutes: noticing
a distraction, a nudge, holding back when unsure, drowsiness building up, the bedtime
ladder, leaving.

---

## How it works

1. **Replay.** `explain` reads the session's logged window summaries
   (`events.vision.jsonl`) and runs the HMM over them, exactly as the replay command does.
   Replay is deterministic, so this gives the same beliefs as live (tested to 1e-12), with
   every detail the live run computed but did not store. `state.csv` keeps only a summary,
   so nothing extra has to be recorded. The rules are replayed alongside.
2. **The robot's side** comes from the session's `events.jsonl`: the operator's markers,
   `nudge_decision` / `nudge_outcome`, `role_changed`, and the confidence gate's waits
   (`escalation` with `what: waiting_for_confidence` / `confidence_wait_ended`, logged
   since this change; once per wait, not every tick). Free-text notes are left out.
3. **The thresholds** come from the session's own snapshot of `config/escalation.yaml`.
   Sessions from before A16 had no gate; then today's thresholds are used, and the page says
   so.
4. **The page** is one HTML file: the data as JSON inside it, drawn with plain JavaScript
   (an SVG for the belief, a canvas for the timeline), light and dark mode. About 1 kB per
   second of session (an hour ≈ 4 MB).
5. **The demo** simulates a person (`eepy/state/synthetic.py`, the same simulator as the
   tests), runs the HMM, and runs the real escalation engine on it in virtual time, without
   the 3-minute settling-in period so that something happens in five minutes.

---

## Limits

- **A rules-mode session** shows what the HMM *would* have said. The robot's actions are
  what really happened, driven by the rules. The page says so at the top.
- **Other HMM numbers** (`--params`) show what *those* numbers would have said; the robot's
  logged actions were based on the numbers used live. The page warns about it.
- **Gate waits are logged only from this change on.** In older hmm sessions the gate step
  shows the verdict from the confidence, but no "Logged: held back" line.
- **The demo is simulated**: the signs are made up, so it shows how the model *reacts*, not
  how a real person looks. Its robot needs `.venv-robot`.
- **The live chain** shows the current second only; there is no history in the panel.
- **Not checked by a person yet:** the pages were checked automatically (a headless browser,
  light and dark) and by the tests, but not yet by the designers or on Windows.

---

## Ideas for later

- **Live viewer:** the same page fed by the panel during a session (needs the per-sign
  evidence in the panel's STATE message, ~1.5 kB a second).
- **"What if" sliders:** change a sign's value in the viewer and see how the belief would
  move. The maths is in `hmm_model.py` and runs per second; this would need a small
  JavaScript port.
- **A18 / thesis export:** a still image or short video of the timeline for a chosen
  stretch, and the agreement with the markers as a table.
- **Information gain (A20+):** show which sign would have reduced the uncertainty most,
  when the policy starts asking or probing.
- **The rules' path** as the decision tree it really is (the rules *are* yes/no), next to
  the HMM's pipeline, for the comparison in A18.

---

## For developers

- `eepy/state/explain.py`: `from_session(folder, params, prior)` and `demo(params)` build
  the data; `render(data)` puts it into `eepy/state/explain_viewer.html` (placeholder
  `/*__DATA__*/null`); `main()` is the command.
- Each step is compact: `t, p[4], s, ml, c, u, pend, up, cal, r[reasons], ev[[feature index,
  supported family, push[3], effect this second[3], value text, no-news]], sk{feature: why},
  rules`.
- The evidence comes from `HMMEstimator._evidence()`, which now also returns `push` (the
  per-window evidence by family, centred: dt-free) and `step` (what it moved this second),
  plus `label`, `show` and `group`.
- The gate events are written by `EscalationEngine._unsure()` / `_end_wait()`.
- The panel's chain is `renderChain()` in `eepy/wizard/static/index.html`; it uses the
  STATE message and the autonomy status (`act_confidence`).
- Tests: `tests/test_hmm.py` (the demo page is self-contained and its data round-trips; a
  recorded session gives the live states; the robot's events are parsed) and
  `tests/test_escalation.py` (the gate events).
