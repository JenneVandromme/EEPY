# eepy/perception — face features from the camera

Turns camera frames into numbers that describe tiredness and attention: how
open the eyes are, blinks, yawns, nodding off, where the head points, and
whether someone is at the desk at all. These numbers are recorded during a
session and, later, feed the HMM.

It uses **MediaPipe Face Landmarker**: a small model that finds 478 points
on the face (*landmarks*), 52 expression scores (*blendshapes*) and the
head's position, in about 5 ms per frame on a laptop. No GPU is needed.
**Images are never saved or logged**, only the numbers.

## Tutorial: test the face features (about 10 minutes)

Do this once after pulling the face-feature changes, on every laptop, and on
the robot's camera when you're at the robot. Each step says what you should see.

### Start here: open two terminals

Open **two** terminal windows, both in the `EEPY` folder, and paste the line
for your system into each one:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| **Terminal A** (camera, face) | `source .venv-vision/bin/activate` | `.venv-vision\Scripts\Activate.ps1` |
| **Terminal B** (robot, panel) | `source .venv-robot/bin/activate` | `.venv-robot\Scripts\Activate.ps1` |

From here on, **every command is the same on every system**. Type it in the
terminal named before it (A or B). The camera must already work: see
[eepy/camera/README.md](../camera/README.md) if it doesn't.

**0. Install (once).**

```bash
# A
pip install -r requirements-vision.txt
```

**1. The automated tests.** No camera needed.

```bash
# A
python -m pytest tests/test_perception.py -q
```

✅ Ends in `passed` (one `skipped` is normal). The test that runs the real
MediaPipe model downloads the face model the first time (3.7 MB, needs
internet once).

**2. Watch the values live.**

```bash
# A
python -m eepy.camera.preview
```

✅ Grey dots on your face (the landmarks; **L** hides them) and, bottom-left,
the values. Now try each of these:

| Do this | You should see |
|---|---|
| Look at the screen normally | `closed` near 0 %, `EAR` about 0.25–0.35; `face size` grows when you lean in |
| Close your eyes | `closed` near 100 %, and both `blink model` values high (0.5 or more) |
| Blink | a cyan flash: **BLINK 120 ms** (around 80–300 ms) |
| Open your mouth wide for ~2 s | `MAR` above 0.5, then a yellow flash: **YAWN** |
| Talk normally | `MAR` moves, but **no** YAWN flash |
| Turn your head to **your** left | `yaw` goes **+** (to your right: −) |
| Let your head drop, then lift it | `pitch` goes **+**, then a pink flash: **NOD** |

After a minute, the bottom line shows the last 60 s: PERCLOS, blinks per
minute, yawns, nods. Press **Q** to stop.

**3. The guided self-test.**

```bash
# A
python -m eepy.perception.selftest
```

✅ All five steps say **PASS**, and the frame rate is about 15.
❌ **CHECK** on a step: try it again more clearly (a slower blink, a longer
yawn, a deeper nod). If it keeps failing, tune that threshold (see *Tuning*
below). If it suggests a different `eyes.ear_open`, that's your personal
value; leave the shared config as it is unless the blink step fails.

**4. Record during a session.**

```bash
# B
python -m eepy.wizard --backend dry
# A
python -m eepy.perception
```

Open `http://127.0.0.1:8080` and start a session with a test code (e.g.
`P99`).
✅ Within 5 s, terminal A prints `recording into 2026-..._P99_...`.
Blink and yawn a few times, end the session in the panel, then check the files:

```bash
# B
python -m eepy.logs.check
```

✅ The test session is listed as **OK**. Its folder in `data/sessions/` has a
`features.csv` and an `events.vision.jsonl` with your blinks. You can delete
that test folder afterwards; `data/` is never committed.

**5. On the robot's camera** (needs the real robot).

```bash
# B
python -m eepy.wizard --backend real
# A
python -m eepy.perception.selftest --camera robot
```

✅ As in step 3. If blinks are missed, look at the frame rate it reports: below
about 15, blinks are too short to catch (see *How fast must the camera be?*).

**6. Calibration:** `python -m eepy.perception.calibrate` (A), about 2 minutes. Then
run the self-test again: it ends with **calibrated vs. default** detections. See
[calibration/README.md](calibration/README.md).

**7. Gaze:** follow the tutorial in
[gaze/README.md](gaze/README.md#tutorial-test-it-about-5-minutes) (calibrate first).

**8. Action units** (only with the optional extra): follow the tutorial in
[aus/README.md](aus/README.md#tutorial-test-the-action-units-about-10-minutes).

**9. User state** (about 3 minutes, after calibrating):

```bash
# A
python -m eepy.perception.selftest --states --no-aus
```

✅ PASS for each part: focused, distracted (phone), focused, drowsy (act
sleepy), absent (leave). With the panel running (B) during step 4, the panel's
status bar shows the same states, and `python -m eepy.state.replay latest`
shows them again afterwards from the recorded session. See
[../state/README.md](../state/README.md).

## What the values mean

### Per frame (about 15 times a second, in `features.csv`)

| Value | In plain words | Typical values |
|---|---|---|
| `face_present` | Is there a face? 1 = yes, 0 = no | — |
| `face_size` | How much of the picture's height the face fills. **Bigger = closer** to the camera | 0.3–0.5 at a desk |
| `ear_left`, `ear_right`, `ear` | **Eye openness** ("Eye Aspect Ratio"): eyelid gap ÷ eye width, per eye and averaged | ~0.28 open, < 0.1 shut |
| `eye_closure` | How shut the eyes are **for this person**: 0 = open as usual, 1 = fully shut | blink ≈ 1 |
| `blink_left`, `blink_right` | MediaPipe's own "eyes closed" score, from a separate model: a **cross-check** on `eye_closure` | 0 open … 1 shut |
| `mar` | **Mouth openness** ("Mouth Aspect Ratio"): lip gap ÷ mouth width | ~0 closed, 0.1–0.4 talking, 0.6+ yawning |
| `jaw_open` | MediaPipe's "jaw dropped" score | 0 … 1 |
| `head_yaw` | Head turned: **+ = to their own left**, − = to their right (degrees) | ±10 normally |
| `head_pitch` | Head tilted: **+ = chin down** (looking down / drooping), − = chin up | |
| `head_roll` | Head tipped sideways: **+ = toward their left ear** | |
| `process_ms` | How long this frame took to analyse | ~5–10 ms |

Left and right are always the **person's own**, as for the robot.

### Events (in `events.vision.jsonl`)

| Event | When | Details |
|---|---|---|
| `blink` | The eye shuts at least half-way and opens again | `duration_ms`; `long` if over 500 ms (a drowsy blink) |
| `yawn` | The mouth stays wide open for at least 1.5 s | `duration_s`. Talking opens the mouth for well under a second at a time, so it doesn't count |
| `nod` | The head **drops** at least 12° within 1.5 s and **comes back up** within 3 s: the "nodding off" pattern | `depth_deg`. Slowly looking down at notes isn't a nod, and neither is a head that stays down |
| `face_lost` | No face for 1 s: they left the desk or turned away | — |
| `face_back` | The face is seen again (for 0.5 s) | `away_seconds` |

### Every second: summaries over the last 10 s and the last 60 s

Logged as a `face_window` event. Each name ends in `_10s` or `_60s`.

| Value | In plain words |
|---|---|
| `perclos` | **Share of the time the eyes were (nearly) shut.** The standard drowsiness measure. "Shut" means at least 80 % closed (the usual *P80* definition: `eye_closure` ≥ 0.8). Above about 0.15 is a warning sign |
| `blink_rate` | Blinks per minute. Normally 10–20; it rises with fatigue |
| `blink_mean_ms` | Average blink length. Blinks get **longer** when tired (normally 100–300 ms) |
| `blink_max_ms` | The longest eye closure. Over 1500 ms is a **microsleep** |
| `long_blinks` | Number of blinks over 500 ms |
| `yawns`, `nods` | How many |
| `look_away_ratio` | Share of the time the head was turned more than 30° away |
| `pitch_mean`, `pitch_var` | Average head tilt (chin down = +) and how much it moved |
| `face_absent_ratio` | Share of the time with nobody there |
| `fps` | Frames per second actually analysed |
| `blink_confidence` | `ok`, or `low` when there are too few frames per second to catch blinks reliably (see below) |
| `coverage_s` | How many seconds of data the window really holds (less right after starting) |

Plus, once per summary (no `_10s`/`_60s`): `eyes_closed_now_s`, how long the eyes
have been closed right now (a blink is only counted once the eyes reopen), and
`baseline_calibrated`, true when the values are relative to this person.

All shares are measured in **time**, not in frames, so a dip in frame rate
doesn't skew them.

**From these summaries EEPY decides the participant's state** (focused,
distracted, drowsy, very drowsy, absent): see [../state/README.md](../state/README.md).

## Calibration: each person's own normal

At the start of a session EEPY spends about two minutes getting to know the
participant: a face check, the gaze zones, and one minute of normal work. After
that every threshold is relative to **their own** face, and normalised values
(`ear_rel`, `head_pitch_rel`, ...) appear next to the raw ones. Start it from the
Wizard panel's **Calibration** card, or with `python -m eepy.perception.calibrate`.
What to do and what the messages mean: **[calibration/README.md](calibration/README.md)**.

## Gaze: on work or away

Whether the student is looking at their **work** (screen, notes) or **away**,
including a phone in the lap. The work area is **personal**: calibrate it with
`python -m eepy.perception.calibrate_gaze`. The preview shows a small gaze map
with ON WORK / AWAY. With the panel running, the robot also looks at where the
student really sits. Everything, in plain words: **[gaze/README.md](gaze/README.md)**.

## Action units (optional)

With the optional Py-Feat extra, the face features also include **action
units**: brows lowered (frown), eyelids tightened, eyes closed, jaw dropped, and
more, as probabilities from 0 to 1. They run alongside without slowing
anything down, on a GPU when there is one. Everything about them (what each one
means, installing per OS including CUDA, picking your machine's profile,
`bench_aus`) is in **[aus/README.md](aus/README.md)**.

## How fast must the camera be?

- **Blinks need about 15 frames per second.** A blink lasts 100–300 ms, which
  is only 2–4 frames at 15 fps. Below that, blinks get missed: `blink_confidence`
  then says `low`, and blink rate, blink length and long blinks should not be
  trusted.
- **PERCLOS, yawns, nods and presence** work fine down to about 5 fps.
- **Processing** is not the bottleneck: on a MacBook (M4 Pro), about 11 ms per
  frame with a face in view, against 67 ms between frames at 15 fps.
- The robot's camera delivers about 15 fps, which is just enough. The laptop
  camera is held to the same rate (`camera.fps`), so the simulator matches the
  robot.

Every duration is measured from timestamps, never by counting frames. Where a
threshold is crossed between two frames, the crossing time is estimated, so a
blink seen at 10 fps and at 30 fps comes out the same length (tested).

## Tuning (config/eepy.yaml, section `perception:`)

Every threshold is there, with a comment, so the team can experiment without
touching code. Restart the preview to see the effect. The most useful ones:

- `eyes.ear_open`: how open **this person's** eyes normally are. The
  self-test measures it and suggests a value if yours differs from 0.28.
- `blink.close` / `blink.open`: how shut the eye must get to count as a blink.
- `yawn.min_seconds`: raise it if talking or laughing counts as yawning.
- `nod.drop_deg`: lower it to catch smaller nods.

Thresholds are relative to the person's **baseline** (their normal face).
Until per-person calibration exists (A12), the defaults above stand in for it.

## The self-test

`python -m eepy.perception.selftest` (add `--camera robot` for the robot's
camera) asks you to:

1. look normally (6 s);
2. blink 5 times;
3. yawn once;
4. let your head drop and lift it back up;
5. turn your head to **your left**.

With the optional action units installed, five more steps follow: frown,
squint, raise your eyebrows, close your eyes for 3 seconds, and open your mouth
wide (see [aus/README.md](aus/README.md)). `--no-aus` skips them.

Each step ends as soon as it has seen what it needs; **SPACE** skips a step.
At the end it prints PASS or CHECK per step, the processing time per frame,
and your own normal eye and mouth openness. Use it as the acceptance test on
each laptop and on the robot.

`python -m eepy.perception.selftest --states` runs a different, longer test
(about 3 minutes): work, a phone, act sleepy, leave. It checks the **user
states** the rules show, and how long each switch takes. See
[../state/README.md](../state/README.md#self-test-the-states-with-a-real-person-about-3-minutes).

## Recording during a session

Start the face features next to the Wizard panel, in the vision environment:

```bash
python -m eepy.perception
```

It records into the session the panel is running: per-frame values go to
`features.csv`, events and summaries to `events.vision.jsonl`. Outside a
session it runs but records nothing. Every 5 s it prints the frame rate,
processing time, the user state, and whether it's recording.

It also decides the participant's **state** once a second, from the rules in
`config/state_rules.yaml`. It records it (`state.csv`, `state_changed`) and
sends it to the panel's status bar. See [../state/README.md](../state/README.md).

## For developers

```python
from eepy.camera import open_camera
from eepy.perception import FacePipeline

pipeline = FacePipeline(open_camera()).start()      # starts the camera too
result = pipeline.wait_for_result()                  # newest result, or None
result.observation    # FaceObservation: landmarks, face_box, head, blendshapes, frame
result.values         # the per-frame values above
result.events         # FaceEvents of this frame
result.window         # the summary, about once a second, else None
pipeline.stats()      # process_ms (median, p95), fps
pipeline.stop()
```

- **Latest frame only.** The pipeline runs in its own thread on the newest
  camera frame; if it's slower than the camera, frames are skipped, never
  queued.
- **`FaceObservation`** is computed once per frame and shared by every
  extractor:
  - `landmarks`: (478, 3), normalised to `frame.image`; 468–477 are the irises;
  - `face_box` and `landmarks.square_box()`: the 1.2× square crop face models expect;
  - `head`: yaw, pitch, roll;
  - `transform`: the 4×4 matrix;
  - `blendshapes`: all 52, including `eyeLook*` for gaze;
  - `frame`: pixels for A10, and `frame.box` for where the view sat in the webcam picture.
- **Adding a feature** (A10 Py-Feat, A11 gaze): subclass
  `eepy.perception.Extractor`, list its `columns`, implement
  `update(obs, baseline) -> Output(values, events)`, and pass it in
  `FacePipeline(camera, extractors=default_extractors() + [MyExtractor()])`.
  Any new event types go in `eepy/logs/schema.py`.
- **Optional plug-in hooks**, all no-ops by default:
  - `notice(t, values, events)` sees each frame's finished values from **all**
    extractors. The AU extractor uses it to cross-check with the eye features.
  - `summary(now)` adds `(event type, fields)` records whenever a window is due
    (`au_window`).
  - `drain()` hands over status records from the plug-in's own thread
    (`au_status`).
  - `close()` releases threads and models when the pipeline stops.
  - **A slow plug-in must not work inside `update()`**: copy the
    `ActionUnitExtractor` pattern (a one-place slot plus a worker thread), so
    MediaPipe keeps its full rate.
- **Calibration (A12):** give the pipeline a `baseline=` object with
  `current() -> Baseline` (normal EAR, MAR, head position). It's asked every
  frame, so a calibration that finishes mid-session takes effect immediately.
- **MediaPipe version:** pinned at 0.10.14, the newest with wheels for Python
  3.9–3.12 on Intel and Apple-Silicon Macs, Windows and Linux. Only the newer
  Tasks API is used (not the old `mp.solutions`, which 0.10.30+ removes).

| File | What |
|---|---|
| `pipeline.py` | `FacePipeline`, the MediaPipe wrapper (`Landmarker`) |
| `observation.py` | `FaceObservation`, `HeadAngles` |
| `extractors.py` | the plug-in interface; presence, eyes (blinks), mouth (yawns), head (nods) |
| `windows.py` | the 10 s / 60 s summaries |
| `landmarks.py` | landmark numbers, EAR/MAR maths, head angles from the matrix |
| `baseline.py` | `Baseline`, `BaselineProvider` (the A12 hook) |
| `config.py` | the `perception:` settings |
| `model.py` | downloads and checks the model file |
| `overlay.py`, `selftest.py`, `__main__.py` | preview drawing, the self-test, the vision process |
| `aus/` | the optional action units (Py-Feat): see [aus/README.md](aus/README.md) |
| `gaze/` | gaze on the work area, its calibration, and "look at user": see [gaze/README.md](gaze/README.md) |
| `calibrate_gaze.py` | `python -m eepy.perception.calibrate_gaze`: learn one person's work area (= `calibrate --only gaze`) |
| `calibration/`, `calibrate.py` | per-person calibration (face check, gaze, baseline) and its command: see [calibration/README.md](calibration/README.md) |
| `normalized.py` | the normalised `*_rel` features (relative to the person's baseline) |
| `bench_aus.py` | `python -m eepy.perception.bench_aus`: timings and a profile suggestion for this machine |

Tests: `tests/test_perception.py` uses synthetic signals, so it needs no camera
and no MediaPipe. It covers blinks, PERCLOS, yawning versus talking, nods, face
lost and back, the same signal at 10 and 30 fps, and low-fps flagging.
