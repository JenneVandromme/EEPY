# Calibration: getting to know each participant

Faces differ: how wide someone's eyes normally are, how their mouth rests, how
they hold their head, how far they sit from the camera. Without knowing that,
the same threshold works for one person and fails for the next. So at the
start of a session EEPY spends **about two minutes** getting to know the
participant. Everything it measures afterwards is compared with **their own**
normal face.

## What happens (about 2 minutes)

EEPY says what to do (through the robot when the Wizard panel runs, else this
computer's voice) and shows it on the screen:

| Step | Time | What the participant does | What EEPY learns |
|---|---|---|---|
| **1. Face check** | ~5 s | Sit as they normally work, facing the screen | that the camera sees the whole face, close enough, in good light |
| **2. Gaze** | ~40 s | Look at the dots (centre, 4 corners) until each turns green; then at their notes; then away to the side (look back at the **beep**) | where their screen and notes are (see [../gaze/README.md](../gaze/README.md)) |
| **3. One minute of normal work** | 60 s (up to 90) | **Just work as usual.** The screen goes back to normal; a small window in the corner shows the progress | their normal eye openness, blinks, resting mouth, head posture, distance (and resting facial action units, if installed) |

Blinks, yawns and short looks away during the minute **don't spoil it**: they
are left out automatically. Typing and glancing at the keyboard or the notes
**is** normal work, so that's fine too. Only looking away to the side, or leaving
the camera's view, counts as not working. Keep the laptop still: if the camera
stops sending pictures, the minute is extended.

## Starting it

**From the Wizard panel** (the normal way, during a study):

1. Start the session.
2. A **Calibration** card appears under it.
3. Press **Calibrate**.

The vision program (`python -m eepy.perception`, in the vision terminal) must
be running: it has the camera, and its calibration window opens on that
computer. The card shows:
- the progress ("calibration 2/3: gaze");
- the result: **calibrated**, **calibration FAILED** with the reason, **skipped** or **reused**.

The card's buttons:
- **Reuse (3 d old):** shown when this participant was calibrated with this
  camera in the last 14 days. Saves two minutes.
- **Skip:** asks for a reason, which is logged with the session. Everything
  still works on default values; the data is then flagged *uncalibrated*.
- **Stop:** ends a running calibration.

**Without the panel**, in the vision terminal:

```bash
python -m eepy.perception.calibrate --user P03           # the whole flow
python -m eepy.perception.calibrate --user P03 --only gaze       # only the screen / notes
python -m eepy.perception.calibrate --user P03 --only baseline   # only the minute of normal work
```

Add `--camera robot` for the robot's camera. **Q** stops at any moment, and
nothing is saved then. If the participant has a recent calibration, it asks
whether to reuse it.

## What the messages mean

| Message | What to do |
|---|---|
| *No face found* | Sit in front of the camera, facing the screen. |
| *Part of your face is outside the view* | Move so the whole face is inside the **blue rectangle** (laptop camera) or the picture (robot camera). |
| *You are a bit far from the camera* | Move closer: the eyes must be big enough to measure. |
| *Your face is too dark* / *too brightly lit* | Turn on a light, face a window, or move out of direct sunlight. |
| *… once more* (during the dots) | No face was seen for that dot: it repeats by itself. |
| *You looked away quite a lot* / *Just a little longer* | Keep working normally at the screen and notes; the minute is extended (up to 30 s). |
| *Your face was out of view too much* | Stay seated in view of the camera; it extends, and if needed starts the minute again. |
| *The camera stopped sending pictures for N seconds* | The laptop was moved, the camera covered, or another program took it. Keep it still and in view; the minute is extended. |
| *There was not enough calm, ordinary working time yet* | Keep working normally (typing is fine); the minute is extended. |
| *The camera delivered only N frames per second* | The computer is too busy. Close other programs and try again. Blinks can't be measured below ~14 frames per second. |
| *Your eyes could not be measured reliably* / *Your blinks could not be told apart from open eyes* | Usually light: reflections on glasses, a dark face, something in front of the eyes. Fix it and try again. |
| *Your mouth seemed open most of the time* | Keep the mouth relaxed and closed while working, then try again. |
| *Your head was turned far from the camera* | Face the screen and camera. |
| *Unusual blink rate* | Only a note: the calibration is still accepted. |
| *Taken late in the evening* | Someone who is already tired then looks "normal" when tired. Calibrate earlier in the day if possible. |

**If it fails twice:** check the light and the seating, and try again. Or skip
it, and the session is recorded on the default values. When it fails, the
command line also prints every check with its measured value: useful when
asking for help.

## What it changes

From then on, EEPY measures against **this participant's** normal face:
- **"Eyes closed" and PERCLOS** use their own open and closed eyes.
- **A yawn** is measured from their resting mouth.
- **"Looking away"** is measured from their usual head posture. That also
  evens out where the camera sits (robot or laptop, desk height).
- **Next to the raw values** come **normalised** ones (the HMM uses those):

| Value | Meaning |
|---|---|
| `ear_rel` | eye openness ÷ their usual: **1** as usual, **0.5** half open |
| `mar_rel` | mouth opening above their resting mouth |
| `head_yaw_rel`, `head_pitch_rel`, `head_roll_rel` | head angles relative to their usual posture (0 = as usual) |
| `face_size_rel` | **> 1** leaning in, **< 1** further away than usual |
| `au_AU04_rel`, … | facial action units above their resting level (0 = their resting face) |
| `blink_rate_rel`, `blink_mean_ms_rel` (per window) | **1.5** = 50 % more / longer blinks than usual |
| `calibrated` | 1 = their own baseline, 0 = defaults |

The preview (`python -m eepy.camera.preview`) shows **CALIBRATED** or
**UNCALIBRATED** at the top, the key baseline values, and the normalised values
next to the raw ones. The self-test (`python -m eepy.perception.selftest`) ends
with **calibrated vs. default** detections on the same frames.

## What is stored, and where

`data/calibration/<participant>/<camera>.json`:
- **Numbers only:** the gaze zones, the baseline values with their spread and
  quality checks, when it was taken, whether that was late in the evening, and
  an empty sleepiness rating (`kss`, filled in later by A15).
- **Never pictures.** `data/` is never committed to git.

Each session logs a `calibration` event (new / reused / skipped / failed, with
the values), and `meta.json` holds a summary.

## For developers

- **`calibration/flow.py`:** `SessionCalibration`, a state machine
  (face → gaze → baseline → done). It knows nothing about windows or speech.
  - It emits line IDs, plus `"beep"` when the person may look back.
  - `runner.py` drives it with the screen (`screen.py`) and the voice
    (`voice.py`).
  - `session.py` (`perform`) saves, applies and logs. The CLI and the vision
    process both use it.
- **`calibration/baseline.py`:** `BaselineCollector`. Robust statistics: the
  exclusions, median ± 3 MAD, medians, P25 for the mouth, P10/P90 stored. The
  quality checks return a `Verdict` with a plain-language message.
- **The panel → vision channel** (`calibration/remote.py`) runs over the camera
  relay:
  - the vision process asks `POLL` twice a second for a command (calibrate /
    reuse / skip / stop);
  - it reports progress with `CALIB`;
  - it asks the panel to `SAY` lines.
- **Applying the baseline:**
  - `StoredBaselineProvider` (`perception/baseline.py`) follows each session's
    participant;
  - `NormalizedExtractor` (`perception/normalized.py`) adds the `*_rel` columns
    through the `derive()` plug-in hook.
- **Tests:**
  - `tests/test_calibration.py` uses synthetic people. It covers robust
    statistics with blinks and outliers mixed in, every quality check, two
    different eye shapes giving the same normalised result, the uncalibrated
    fallback, save and reuse, the whole flow, and the relay messages.
  - `tests/test_wizard.py` covers the panel's calibration card.
