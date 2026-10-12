# Gaze: is the student looking at their work?

EEPY works out, many times a second, whether the student is looking at their
**work** (the screen or their notes) or **away** from it. That's the main sign
of distraction. It doesn't need precise eye tracking, just a robust **on work /
away** signal with a confidence.

It combines two things the camera already measures (no extra model):
- **where the head points**, and
- **where the iris sits inside the eye**, so a glance with the eyes alone
  counts too.

When the iris can't be trusted (half-closed eyes, glasses reflecting, a face
too small or too far turned), it uses the head direction alone and says so.

## The work area is personal: calibrate it

Where "the screen" and "the notes" are depends on the person, the desk and
where the camera sits. So EEPY learns it, once per person and camera setup.
It takes about a minute.

In the **vision** terminal (`source .venv-vision/bin/activate`, Windows:
`.venv-vision\Scripts\Activate.ps1`):

```bash
python -m eepy.perception.calibrate_gaze --user P03
```

This is the gaze part of the full two-minute calibration
(`python -m eepy.perception.calibrate`, or the Wizard panel's **Calibration** card,
see [../calibration/README.md](../calibration/README.md)). That's what you
normally use; this command runs only the gaze part.

1. **Sit exactly as you'll work**, with your notes where they'll lie. A short face
   check comes first (in view, close enough, enough light).
2. The screen turns black and shows a **dot**. Look at it until it turns
   **green**. It goes to the centre, then the four corners.
3. Then **look at your notes**, first the middle, then the far edge. You can't
   watch the screen meanwhile, so it counts down, records, and **beeps** when
   you may look back.
4. Last, **look away** to the side (a window, a door) until the beep.
5. It shows the learned work area and saves it. Any warnings are printed in the
   terminal, for example "looking away landed inside the work area: turn
   further away".

- **`--user`:** the participant code. Leave it out to calibrate for the
  participant of the session the Wizard panel is running, or as `default`.
- **`--camera robot`:** calibrate with the robot's camera.
- **`--setup lab`:** keep a separate calibration for a particular desk.

**Calibrate again** whenever the camera, the screen or the notes move.

- **R** repeats the current point.
- **Q** stops without saving.

**Where it's kept:** `data/calibration/<user>/<camera setup>.json`. It holds
angles only, never a picture, and git never commits it. During a session,
EEPY automatically uses the calibration of that session's participant.

**Without calibration** EEPY uses a default ("the head roughly faces the
screen") and marks everything **uncalibrated**. That's fine for a quick test,
not for the study.

## What you see in the preview

`python -m eepy.camera.preview` shows a small **gaze map** on the left (**G**
hides or shows it):

- **ON WORK** (green) or **AWAY 12s** (red, with how long so far). This is the
  *confirmed* state, so a quick glance doesn't flip it.
- **The map:** the work area as **you** see it: looking at the screen's top-left
  corner puts the dot top-left.
  - **blue box** = the screen;
  - **green box** = the notes;
  - **white dot** = where you're looking now.
- **The line under it:** the current zone, the mode, the confidence, and
  **UNCALIBRATED** in yellow when no calibration is in use.
- **Flashes:** **LOOKED AWAY** when the student looks away, and **BACK ON
  WORK** (with how long they were gone) when they return.

**The zones:**

| Zone | Meaning |
|---|---|
| `screen` | looking at the screen (on work) |
| `notes` | looking at the notes on the desk (on work) |
| `down_other` | looking **down, but not at the notes**: a keyboard, a phone in the lap. **Counts as on work** in the base version (notes and lap could not be told apart reliably); kept apart in the data for phone detection later (A29) |
| `away` | to the side, or up |
| `no_face` | no face visible (left the desk, turned around) |

**The mode:**
- **`iris+head`:** the normal case.
- **`head only`:** the iris wasn't trustworthy on that frame, and gaze follows
  the head alone (less precise).

## What is recorded

**Per frame** (in `features.csv`):

| Value | In plain words |
|---|---|
| `gaze_zone` | which zone (table above) |
| `gaze_on_work` | 0–1: **1** well inside the screen or notes, **0.5** right on the edge, **0** clearly away |
| `gaze_confidence` | 0–1: lower near a zone edge, in head-only mode, and without calibration |
| `gaze_mode` | 1 = iris + head, 0 = head only |
| `gaze_calibrated` | 1 when the person's own calibration is used |
| `gaze_yaw`, `gaze_pitch` | the gaze direction in degrees (+ = towards their own left / down), relative to the camera |
| `away_s` | how long they have been away **right now** (0 when on work). The escalation logic can use it directly |

**Events** (in `events.vision.jsonl`):
- `looked_away`: with the zone. Its time is when they really looked away, not
  when it was confirmed.
- `back_on_work`: with `away_seconds` and the zone they were in.

**Every second:** `gaze_window`, over the last 10 s and 60 s:

| Value | Meaning |
|---|---|
| `off_work_ratio` | share of time away (including no face) |
| `screen_ratio`, `notes_ratio`, `down_other_ratio`, `away_ratio`, `no_face_ratio` | share of time in each zone |
| `away_episodes` | how many times they looked away |
| `away_mean_s`, `away_longest_s` | how long, on average and at most |
| `away_current_s` | how long the current look-away has lasted (0 when on work) |

Which calibration is in use (person, camera, or none) is logged as
`gaze_status`.

**Quick glances don't count.** Looking away must last **1 s**
(`gaze.min_away_seconds`), and looking back **0.5 s** (`gaze.min_back_seconds`),
before the state changes. So a confirmed change always comes that much after
the real one. Both are settings in `config/eepy.yaml`, section `gaze:`.

## Known limits

- **Up/down on the screen is weak.** The upper eyelid moves down together with
  the eye, so the iris stays at about the same place in the eye opening. Left
  and right separate well; top and bottom of a laptop screen barely do. (In the
  first calibration, the top corners even measured a little lower than the bottom
  ones.) For **on work vs. away** that doesn't matter: the screen zone covers
  the whole screen. But EEPY can't tell where on the screen someone looks.
- **Looking down narrows the eyes,** so the iris is often not trusted at the
  notes or the lap. Gaze then follows the head (mode `head only`). The
  calibration learns separate head-only zones for exactly this.
- **Looking down always counts as work** (base version): notes, keyboard and
  lap are all "down". Only turning away (or leaving) is off work. A phone in
  the lap needs phone detection (A29).

## The robot looks at the real student

With the Wizard panel and `python -m eepy.perception` both running, the face
position is sent to the panel. Behaviours that "look at the user" then aim at
where the student actually sits, instead of a fixed direction.
- **Smoothed and speed-capped,** so the robot doesn't jitter.
- **Kept within safe angles.**
- **Back to the default** after 1 s without a face.

It's aimed when a behaviour **starts**, not continuously while it plays (on
purpose: every behaviour is safety-checked before it moves). The panel's
camera pill tooltip says whether it's tracking.

## Tutorial: test it (about 5 minutes)

Two terminals, as in [../README.md](../README.md#start-here-open-two-terminals).
**A** is the vision terminal.

**1. Tests.** `python -m pytest tests/test_gaze.py -q` (A)
✅ Ends in `passed`. A few `skipped` are normal in the vision environment.

**2. Calibrate.** `python -m eepy.perception.calibrate_gaze` (A)
✅ The work area is shown and saved, with no warnings (or ones you understand).

**3. Watch it live.** `python -m eepy.camera.preview` (A)
✅ The gaze map shows two boxes and no "UNCALIBRATED". Read something on the
screen: **ON WORK**, and the dot sits in the blue box. Look at your notes: the
dot moves to the green box, still ON WORK. Look at your lap: `down other`, still
**ON WORK** (base version: looking down counts as work). Look out of the window: `away`.

**4. The self-test.** `python -m eepy.perception.selftest --no-aus` (A)
✅ The four gaze steps (screen, away, notes, phone: alternating on and off work) say **PASS**. Each shows
how soon the zone was seen (including your reaction time) and when it was
confirmed (plus the 1 s / 0.5 s hysteresis).

**5. The robot looks at you** (panel in **B**: `python -m eepy.wizard --backend sim`;
in **A**: `python -m eepy.perception`).
✅ Hover over the panel's camera pill: it says it is tracking the student. Play
**approving nod**: the robot first looks towards **you**, not a fixed spot.

## For developers

- **`GazeExtractor`** (`extractor.py`) is a FacePipeline plug-in. It adds the
  per-frame columns, the events (via its update output), `gaze_window` (via
  the `summary()` hook) and `gaze_status` (via `drain()`).
- **`GazeEstimator`** (`estimate.py`): the iris's position in the eye opening
  (an idea from BURTRAM's `gaze_down_ratio`, extended to both directions) times
  a gain, plus the head angles. It also has the head-only fallback rules.
- **`ZoneModel`** (`zones.py`): rectangles in angle space, with **two sets** per
  calibration. When you look at a screen corner, the eyes do part of the
  turning, so head-only angles are smaller. Each frame is classified with the
  set for its mode.
- **`GazeCalibration`** (`calibration.py`) is a step-by-step state machine
  (`step`, `add()`, `next()`, `finish()`), so the Wizard panel can drive it
  later. That needs a panel → vision command channel, which doesn't exist yet.
  `CalibrationStore` keeps one file per user and setup, with a section per
  calibration kind: `gaze` now, and `baseline` is reserved for A12.
- **Look at user:**
  - `look_at.py` (vision) sends `USER {"yaw", "pitch"}` over the camera relay:
    the face offset from the view's centre, in robot head angles, aimed between
    the eyes.
  - `TrackingTargetProvider` (`eepy/behaviors/targets.py`, panel) adds it to the
    current head pose, smooths it, clamps it, and falls back after 1 s.
  - The robot camera uses its real lens field of view (`gaze.robot_lens_fov_deg`,
    87°). The laptop camera uses the blue box.
- **Timing:** short intervals are measured with `time.perf_counter()`, because
  on Windows (Python ≤ 3.12) `time.monotonic()` only advances in 15.6 ms steps.
  The same lesson as in the AU worker.
- **Tests:** `tests/test_gaze.py` uses synthetic faces with exact head and iris
  positions. It covers zones with and without calibration, the hysteresis at
  10 and 30 fps, the fallback, the windows, the store, and look-at-user.
  `tests/test_wizard.py` covers the full path, vision → relay → panel →
  behaviour.
