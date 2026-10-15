# The Wizard panel — one-page guide

You are EEPY's brain. You watch the student, and you press the buttons that
make the robot react. Everything you do is recorded, so do it the same way
every time.

## Start it (on the lab desktop)

```bash
source .venv-robot/bin/activate
python -m eepy.wizard --backend real        # or --backend sim, or --backend dry
```

It prints two addresses. Open **`http://127.0.0.1:8080`** on the desktop, or the
**`http://<numbers>:8080`** one on any laptop on the same network. Several
laptops can have it open at once — they all show the same thing.

Check the top bar: a **green dot** means the robot is connected. Red means it
is not — see "If something goes wrong".

**Record the face features too** (blinks, yawns, PERCLOS, …). In a second
terminal, start the vision process; it records into whatever session the
panel runs:

```bash
source .venv-vision/bin/activate          # Windows: .venv-vision\Scripts\Activate.ps1
python -m eepy.perception
```

Every 5 s it prints a line ending in `recording into <session>` while a session
runs, including the gaze zone, or `(uncalibrated)` when this participant's work area
has not been calibrated yet. **Calibrate each participant** right after
**Start session**: a **Calibration** card appears. Press **Calibrate** (about two
minutes, the participant follows the instructions), **Reuse** (offered when they were
calibrated in the last 14 days with this camera), or **Skip** (asks a reason, which is
logged). The card shows the progress and the result. The vision process must be running
(it has the camera; the calibration window opens on its computer). What the messages
mean: [eepy/perception/calibration/README.md](../eepy/perception/calibration/README.md). If it says `no face`, check the person sits inside the camera's view
(`python -m eepy.camera.preview`).

## Aiming the robot at the participant

> **Switched off for now** (`aim.enabled: false`): not seen working on the real robot yet.
> The exact problem and what is left: [TODO.md](TODO.md).

When the robot sits lower than the student's face (e.g. at chest height), its resting
position looks at their chest. The panel can point the resting position at the face
instead. All the robot's movements then play around that direction: it looks up at the
student and does its usual nods, glances and tilts from there.

- **It aims by itself**, no button needed: as soon as the vision program
  (`python -m eepy.perception`) sees the face off-centre for 2 s while the robot rests (the
  idle may run), and again whenever the student has clearly moved (at most every 15 s).
  It never re-aims while a behaviour plays or a calibration runs.
- **Calibrate** aims first, so the calibration matches the robot's view; the card then
  says *aiming at the participant first*. If it re-aims after the calibration (the student
  moved a lot), the card suggests calibrating again: the robot's camera now sees the desk
  from a different angle.
- **Aim at participant** on the calibration card aims right away.
- **Reuse** restores the aim saved with that calibration.
- A face within **8°** of the centre of the robot's view is left as it is.
- **Not further than is safe:** the robot never aims further than every behaviour can
  still play safely from. With the shipped behaviours that is about **13° up** when the
  student is straight in front (less when they also sit to the side). If the face is still
  above the centre, the card says so: put the robot higher (a book under it).
- The card's line says where the robot rests: *aimed at the face (resting at yaw +5°,
  pitch −12°; face within 8°)*. Each change is logged (`robot_base`).

Settings, in [`config/wizard_panel.yaml`](../config/wizard_panel.yaml) under `aim:`:
`enabled`, `auto`, `auto_after_s`, `auto_min_gap_s`, `on_calibrate`, `tolerance_deg`, the
yaw / up / down limits, `rounds`, `settle_s`. Restart the panel after changing code or this
file's `aim:` section.

## Run a session

1. Type the **participant code** (e.g. `P03` — always the code, never a name;
   the panel refuses anything else) and press **Start session**. The bar turns
   red and says **RECORDING**.
2. Watch the student. Trigger behaviours, and mark what you observe (below).
3. Press **End session** when they leave. You can add a note about the
   session as a whole (or leave it empty); **Cancel** keeps recording. It is
   saved in `data/sessions/` — see [DATA_GUIDE.md](DATA_GUIDE.md).

The **mode** pill in the top bar shows the experimental condition. Normally it
says `wizard`. If it is **amber** ("not built, manual only"), the panel was
started in a mode whose controller does not exist yet: everything still works
by hand, and the session is recorded under that mode.

**The rules mode** (`--mode rules`) makes the robot act **on its own**: it nudges
when the camera says the participant is distracted or drowsy. The vision program
must run next to the panel (`python -m eepy.perception`). An **autonomy** line
under the session bar shows:
- the robot's role, ladder and level;
- what comes next and when.

Under it, **one row per ladder** (refocus and bedtime) shows where the robot is:
- **filled** rungs were played in this climb, with how it went (✓ it worked,
  · no reaction, ✗ interrupted focus; hover for the words);
- the **solid** rung is the level it is on now, **pulsing** while it moves (L0 is
  the ladder's quiet idle);
- the **dashed** rung is the next level if there is no reaction, with its countdown;
- "on a break", "winding down" or "goodnight" when the robot is in that phase;
- in the hmm mode, before a first nudge: how much evidence has built up toward it.

What you do in this mode:
- **Pause autonomy** stops it at once, and you drive by hand; **Resume** starts
  afresh. Use it for demos and whenever something looks wrong.
- **The participant's answers:** press **user accepted** (`.`), **user snoozed**
  (`/`) or **user dismissed** (`m`) after a break offer or "15 more minutes?".
  They're recorded as markers and steer the robot.
- **What you play yourself** stays marked as yours.

When and why the robot nudges: [eepy/interaction/README.md](../eepy/interaction/README.md).

**The hmm mode** (`--mode hmm`) works the same, but the state comes from the HMM
(A16): a belief instead of a verdict. The state pill shows how sure it is
(*drowsy · 82 %*), and a small **bar** next to it shows the whole belief: green
focused, amber distracted, orange drowsy, red very drowsy (faded: on hold, while
nobody is there or the camera gives no usable data). Hover for the numbers and the
reasons. The robot only acts when the HMM is at least **70 %** sure; the autonomy
line then says *not sure enough yet*. How to read it:
[eepy/state/README.md](../eepy/state/README.md#the-hmm-a-belief-instead-of-a-verdict).

- The autonomy row adds the **decision chain**: *drowsy 82 % → ✓ sure enough (acts from
  70 %) → bedtime level 1*: the state, whether EEPY is sure enough to act, and what the
  robot does. After the session, `python -m eepy.state.explain latest --open` shows the
  whole session this way, second by second
  ([docs/EXPLANATION_VIEWER.md](EXPLANATION_VIEWER.md)).
- **The full HMM live, while the robot acts on it:** start the vision program with
  `--live` (`python -m eepy.perception --live`). It also opens the live HMM page
  (http://127.0.0.1:8770): the camera picture, the signs, the belief, *sure enough to
  act?* and the robot's decisions (waits, holds, nudges) in the timeline. It shows the
  same HMM the robot acts on, and starts over at every session. Do not use
  `python -m eepy.perception.live` for this: it takes the camera for itself and does not
  feed the panel, so the robot only idles.
- The vision program follows the session's mode by itself. If the panel says
  *Mode "hmm" acts on the hmm estimate, but the vision program sends the rules
  one*, restart the vision program (`python -m eepy.perception`); its terminal says
  why the HMM did not start. Autonomy waits until then.
- **A starting belief per session** (switched off by default): start the panel
  with `--session-prior my_prior.json`. It is copied into each session as
  `prior.json`; a broken file is reported here and the HMM starts as usual.

**macOS / Linux:**
```bash
source .venv-robot/bin/activate
python -m eepy.wizard --backend real --mode hmm
```

**Windows (PowerShell):**
```powershell
.venv-robot\Scripts\Activate.ps1
python -m eepy.wizard --backend real --mode hmm
```

Markers pressed with **no session running are not saved** — the panel warns
you in orange. Start the session first.

The **state** pill in the top bar shows what EEPY estimates from the camera:
- **focused** (green);
- **distracted** or **drowsy** (amber);
- **very drowsy** (red);
- **absent** or **unknown** (grey).

The main reason is next to it, e.g. *"eyes closed 14% of the last minute"*.
**"focused → drowsy?"** means a change is waiting to be sure. **· uncalibrated**
means the participant wasn't calibrated, so it is less reliable. In the
`wizard` mode it is only information: **your markers are what count**. They are
also what the states are checked against afterwards. What each state means:
[eepy/state/README.md](../eepy/state/README.md).

## The keys

The key for every button is printed on it. With the default settings:

| | Keys |
|---|---|
| **STOP** — robot back to neutral | **Esc** (always works, even while typing) or **Space** |
| Refocus ladder, levels 0–4 | `1` `2` `3` `4` `5` |
| Bedtime ladder, levels 0–5 | `Q` `W` `E` `R` `T` `Y` |
| Idle on / off · approving nod | `A` · `S` |
| Spoken lines | `6` `7` `8` `9` `0` `-` |
| Switch lines between hedged / direct | `Tab` |
| **I observed…** markers | `Z` phone picked up · `X` looked away · `C` back on task · `V` yawn · `B` eyes closing · `N` user replied · `M` user dismissed · `,` user went to bed |

- The **intensity slider** applies to the *next* behaviour you trigger.
- **Hedged** lines sound unsure ("Losing focus a bit?"); **direct** ones are
  confident ("Back to it?"). Pick per moment.
- **Say this…** speaks anything you type, and **Note…** adds a free-text note.
  Press Enter to send either.
- Speech comes out of the robot (or, with the dry backend, this computer).
  **Esc cuts speech too.** A new behaviour cuts whatever the robot was saying.
- A line that needs a number (like "…in {minutes} minutes") cannot be sent
  from its button in that tone; the panel says so. Use the other tone, or type
  it in **Say this…**.
- While you are typing in a box, the other keys type letters instead of doing
  things. Esc leaves the box — and stops the robot.

## Annotating — the part that matters for the study

The markers are the ground truth the HMM learns from, so:

- **Press the moment you see it**, not when you are sure. The time of the key
  press is what gets recorded, to the millisecond.
- **Mark what you observe, not what you think it means.** "looked away", not
  "lost focus".
- One press per event. Holding a key down counts once.
- If you made a mistake, add a **note** saying so ("previous yawn was a
  sneeze"). Never edit the log file.

## If something goes wrong

| What you see | What to do |
|---|---|
| Robot doing something wrong | **Esc**. Always first. |
| Red **NOT CONNECTED** | Keep annotating — it is still recorded. Fix the robot, then press **Reconnect robot**. The session carries on. |
| "Kept the last good version" after Reload | A behaviour or config file has a mistake. The red box says which file and line. The panel keeps working with the old version meanwhile. |
| STOP seems slow at bedtime level 5 | The goodnight routine cannot be interrupted. It finishes in a few seconds. |
| Page says it lost contact with its server | The panel was stopped on the desktop. Start it again; nothing recorded so far is lost. |
| Page looks stuck | Refresh it. The session keeps running on the desktop. |
| "The last session did not end cleanly" | The panel crashed during that session. Its data is kept. Tell whoever handles the data. |
| Red box "The robot could not speak" | Speech failed (the box says why). The words are in the box — say them yourself if it matters. Everything else keeps working. |
| The robot's voice sounds different from usual | The chosen voice is not downloaded on this computer, so it fell back to the computer's own voice. See the README, Step 3. |
| Page is completely white | You reached a different program. Use the exact address the panel printed (`127.0.0.1`, not `localhost`). |
| "Cannot start: port 8080 is already in use" | Another program has that port. Stop it, or start the panel with `--port 8081` and open that address. |
| Calibration card says *waiting for the vision process* | Start `python -m eepy.perception` in the vision terminal. |
| *calibration FAILED* | The card says why (light, seating, camera too slow...). Fix it and press **Calibrate again**, or **Skip**. |
| Rules mode: the robot never nudges | Is the vision program running (state pill)? Was the session started? Look at the autonomy line: it says what it's waiting for (settling in after the calibration, the minimum gap, paused...). |
| Rules mode: the robot does something unwanted | **Pause autonomy** (or **Esc** for the current movement), then carry on by hand. Note what happened. |
| No **state** pill, or *"no word from the vision program"* | Start `python -m eepy.perception` in the vision terminal (or it stopped: look at its terminal). |
| The state looks wrong | Keep annotating what you see. Note it (**note**), and the thresholds can be tuned afterwards on this session ([eepy/state/README.md](../eepy/state/README.md)). |
| Amber **camera** pill, or "camera relay could not start" | The vision process gets no robot camera or head position from the panel. Hover over the pill for the reason; usually a second panel is running. Driving the robot is unaffected. |
| Laptop cannot open the address | Same Wi-Fi as the desktop? On the desktop, allow "Python" when macOS asks about incoming connections. |

**To stop the panel:** press **Ctrl+C** in its terminal. It ends any running
session, saves it, and returns the robot to neutral.

Anyone on the lab network can open the panel and drive the robot — there is no
login. Do not leave it running unattended on a shared network.

*Changing keys or markers:* edit `config/wizard_panel.yaml` and press **Reload
files**. New behaviours added to `behaviors/` appear after Reload too.
