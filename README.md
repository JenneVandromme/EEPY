# EEPY

EEPY (from "sleepy") is a desk-side companion robot built on **Reachy Mini**.
It sits next to you while you study or work, watches for signs that you're
getting tired (things like your eyes closing more, blinking more, yawning,
or your posture slumping), and when it notices, it nudges you — through
movement, gaze, and maybe speech — to take a break instead of pushing
through. The point isn't a phone notification saying "you seem tired." It's
a small robot physically reacting to you, the way a person sitting next to
you might nudge your arm.

This is our project for the course **"Embodying Intelligent Behavior in
Social Context"** (Prof. Barakova, Ghent University), due end of October
2026. It also doubles as groundwork for Jenne's Master's thesis, which uses
this same robot — so a few of the building blocks here (the Gemini-based
conversation piece, eye-gaze tracking, emotion detection) started their life
in a related, bigger repo and get reused here.

Nothing about *how* we detect tiredness is decided yet — that's genuinely
open design work for the team. This repo currently just gives everyone a
working setup so we can start experimenting.

---

## 1. Setting everything up on your computer

This assumes you've already cloned the repo (i.e. you have an `EEPY` folder
on your computer with these files in it) and have a terminal open **inside
that folder**.

A quick note on why setup below has two separate parts ("vision" and
"robot") instead of one: the camera/face-detection library we use
(`mediapipe`) and the Reachy Mini robot library (`reachy-mini`) depend on
two *incompatible* versions of another library (`numpy`). Installing both
into the same environment silently breaks one of them. The fix is simple —
just keep them in two separate, isolated Python environments — but it means
you'll do the install steps below twice, once per environment. This isn't a
mistake, it's intentional.

### Step 0 — Install Python

You need **Python 3.11 or newer** (3.12 is what we've tested most).

Check what you already have by running this in a terminal:

- **macOS / Linux:** `python3 --version`
- **Windows:** `python --version`

If that prints `3.11` or higher, skip to Step 1. Otherwise, install Python
from [python.org/downloads](https://www.python.org/downloads/) (**on
Windows, tick "Add python.exe to PATH"** during install, it's a checkbox
on the first install screen) and re-open your terminal afterwards.

> **macOS gotcha:** macOS ships its own ancient Python 3.9 at
> `/usr/bin/python3` (from Xcode Command Line Tools), and it's common for
> this to stay first on your `PATH` even *after* you install a newer
> Python — so `python3 --version` can still print 3.9, or worse, a plain
> `python3 -m venv ...` can silently create the venv with that old 3.9
> instead of the one you just installed. If you installed via
> [python.org](https://www.python.org/downloads/) or `brew install
> python@3.12`, don't rely on the bare `python3` command — use the
> versioned one explicitly (`python3.12`) as shown in Step 1, and use the
> version-check right after creating the venv to confirm it took.

### Step 1 — Create the two virtual environments

A "virtual environment" (venv) is just an isolated folder of Python
packages, so installing things for this project doesn't affect anything
else on your computer. We keep ours out of git (see `.gitignore`) — every
teammate creates their own locally.

**macOS / Linux:**

```bash
python3.12 -m venv .venv-vision
python3.12 -m venv .venv-robot
```

(If you installed a different version in Step 0, e.g. 3.11, use that
instead: `python3.11 -m venv ...`. Just don't use the bare `python3` — see
the macOS gotcha note in Step 0.)

**Windows (PowerShell):**

```powershell
python -m venv .venv-vision
python -m venv .venv-robot
```

This creates two folders, `.venv-vision/` and `.venv-robot/`. You only do
this once.

**Verify it actually used the right Python** before moving on — this
catches the macOS gotcha above before it wastes your time:

```bash
.venv-robot/bin/python --version      # Windows: .venv-robot\Scripts\python.exe --version
```

This must print `3.11` or higher. If it prints `3.9` (or anything below
3.11), delete the folder (`rm -rf .venv-robot`) and recreate it with the
correct versioned command above.

### Step 2 — Install the vision (camera) dependencies

Activate the `vision` environment, then install into it:

**macOS / Linux:**

```bash
source .venv-vision/bin/activate
pip install -r requirements-vision.txt
```

**Windows (PowerShell):**

```powershell
.venv-vision\Scripts\Activate.ps1
pip install -r requirements-vision.txt
```

> If PowerShell refuses to run the activate script with a "running scripts
> is disabled" error, open PowerShell as Administrator once and run:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`,
> then try again.

You'll know it worked if the install finishes with no red error text, and
your terminal prompt now starts with `(.venv-vision)`.

Type `deactivate` to leave this environment when you're done.

### Step 3 — Install the robot dependencies

Same idea, different environment — open a **new terminal tab** (or
`deactivate` first) so you're not still inside `.venv-vision`:

**macOS / Linux:**

```bash
source .venv-robot/bin/activate
pip install -r requirements-robot.txt
```

**Windows (PowerShell):**

```powershell
.venv-robot\Scripts\Activate.ps1
pip install -r requirements-robot.txt
```

This one command installs everything robot-related, **including the
MuJoCo simulator** (the `[mujoco]` part) — there's no separate simulator
download or install step later. `reachy-mini-daemon --sim` further down
will just work once this finishes.

**If you're on Linux**, `reachy-mini` also needs a system package
(GStreamer) that isn't installed via `pip`. If Step 3 installs fine but
running a robot script later fails with an error mentioning `Gst` or
`GObject`, install it with:

```bash
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base gir1.2-gst-plugins-base-1.0
```

**Then, once: download the robot's voice** (about 60 MB — it shows progress,
and picks up where it left off if your connection drops):

```bash
python -m eepy.speech.voices --download en_US-lessac-medium
```

Until you do, the robot still talks — in your computer's built-in voice — and
says so in the log. On **Linux**, speech playback also needs
`sudo apt install libportaudio2`.

**On an Intel Mac, or a Mac older than macOS 14:** check which robot SDK you
ended up with, with `pip show reachy_mini`. The version this project is built
and tested against (1.11.0) needs `onnxruntime 1.27.0`, which only exists for
**Apple Silicon Macs on macOS 14+**, Windows and Linux. On other Macs, pip may
quietly install an *older* `reachy_mini` instead — different code from what
everyone else runs. If yours is not 1.11.0, say so in the team chat before
relying on the robot or simulator on that machine.

**If `pip install -r requirements-robot.txt` fails with something like**
`ERROR: Could not find a version that satisfies the requirement
reachy-mini[mujoco] (from versions: none)` — this looks like a mujoco/
install problem but almost always isn't. It means `.venv-robot` was
created with too old a Python (`reachy-mini` needs 3.11+; if your venv is
3.9 or 3.10, pip can't find *any* matching version and gives this
unhelpful error instead of saying so directly). Fix:

```bash
deactivate                          # if currently active
rm -rf .venv-robot
python3.12 -m venv .venv-robot      # use your versioned Python, see Step 0/1
source .venv-robot/bin/activate
python --version                    # confirm this prints 3.11+ before continuing
pip install --upgrade pip
pip install -r requirements-robot.txt
```

**If you're on Windows:** this setup (`reachy-mini[mujoco]` specifically,
and whether the simulator viewer opens cleanly) hasn't actually been
tested by anyone on the team yet — everything above is our best expectation
from the package's published support, not a confirmed result the way macOS
and Linux are. If Step 3 or the simulator step below fails in a
Windows-specific way, that's not you doing something wrong — flag it in
the team chat, and once it's fixed, add what you did here so the next
Windows teammate doesn't hit it too.

### Step 4 — Copy the environment file

```bash
cp .env.example .env
```

Then open `.env` in a text editor and fill in a `GEMINI_API_KEY` (needed
for `robot/conversation.py` — see below; get a free one at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey)). You
don't need this for the camera/robot sanity checks below, only for talking
to the robot. `.env` is gitignored, so your personal key never gets
committed.

Two more settings in `.env` are worth knowing:

- `EEPY_BACKEND` — where the behaviour tools and the Wizard panel send motion:
  `dry` (nothing moves; the default), `sim` (the simulator) or `real` (the
  robot). Set it to `sim` while you work in the simulator, and you can leave
  `--backend sim` off every command.
- `REACHY_MINI_HOST` — the real robot's IP address, for `real`.

---

## 2. Running / testing things

### Everything at once: the launcher

```bash
python3 launch.py                          # no virtualenv needed
```

One page (http://127.0.0.1:8090) that starts the simulator, the Wizard panel
and the vision program in the right order, shows whether each one is up and
what it prints, and has the panel and the live HMM page in tabs. Pick dry /
simulator / real robot and the mode on its **Developer** tab. It runs the same
commands as below, so they keep working as a fallback; the Developer tab shows
each one, ready to copy. See [eepy/launcher/README.md](eepy/launcher/README.md).

Each time you come back to work on this, you need to **activate** the
right environment first (Step 2/3 commands above, minus the `pip install`
part — you only install once, but you activate every session).

### Test the camera setup

```bash
source .venv-vision/bin/activate      # Windows: .venv-vision\Scripts\Activate.ps1
python vision/test_camera.py
```

A window should open showing your webcam feed with a dot-mesh traced over
your face. Press `q` (with that window focused) to close it. If this
works, your camera + `opencv` + `mediapipe` setup is good.

First run on macOS will pop up a camera-permission prompt — click Allow
(System Settings → Privacy & Security → Camera if you miss it).

If no window opens or you get a camera error, another app (Zoom, Teams,
another browser tab) may already be using the camera — close it and try
again. If you have more than one camera, try `python vision/test_camera.py
--camera 1`.

### See what EEPY's camera sees

```bash
source .venv-vision/bin/activate      # Windows: .venv-vision\Scripts\Activate.ps1
python -m eepy.camera.list            # which cameras are there?
python -m eepy.camera.preview         # live view: Q or Esc to stop
```

With the simulator or the dry run, your **laptop camera** stands in for the
robot's. The **blue box** shows what the robot sees: only that part goes to
perception, so sit well inside it. With the Wizard panel running, the box
follows the robot's head. With `--backend real`, the preview shows the
**robot's own camera** instead. Nothing is recorded.

On macOS, the first run needs camera permission for your terminal or editor:
System Settings → Privacy & Security → Camera, switch it on, then **quit and
reopen** that app. Everything else (wrong camera, "in use", robot camera,
settings) is in [`eepy/camera/README.md`](eepy/camera/README.md).

**Testing the camera properly?** Follow the step-by-step tutorial for macOS,
Windows and Linux: [eepy/camera/README.md → Tutorial: test everything](eepy/camera/README.md#tutorial-test-everything-about-15-minutes).

### Test the face features (blinks, yawns, nods)

```bash
source .venv-vision/bin/activate      # Windows: .venv-vision\Scripts\Activate.ps1
python -m eepy.perception.selftest    # guided, ~1 minute: blink, yawn, nod, look away
```

It prints PASS or CHECK per step and how long each frame takes to analyse.

**Calibration (each participant, start of a session, ~2 minutes):** from the Wizard
panel's **Calibration** card, or `python -m eepy.perception.calibrate --user P03`. It
learns the person's normal face, so thresholds fit them. What to do and what the
messages mean: [`eepy/perception/calibration/README.md`](eepy/perception/calibration/README.md).

**Gaze on the work area:** calibrate each person's screen and notes once
(about a minute), then the preview shows ON WORK / AWAY:
`python -m eepy.perception.calibrate_gaze --user P03`. Details:
[`eepy/perception/gaze/README.md`](eepy/perception/gaze/README.md).

**Optional: action units** (frown, squint, eyes closed, jaw drop... with
Py-Feat). An extra install of ~3 GB (Python 3.11+, a GPU recommended), never
required:
`pip install -r requirements-aus.txt` (Windows/Ubuntu with an NVIDIA GPU: first
`torch torchvision torchcodec` from PyTorch's CUDA index, see the link). Everything, per OS: [`eepy/perception/aus/README.md`](eepy/perception/aus/README.md). The
preview above also shows the face features live: eye and mouth openness, head
angles, and a flash on every blink, yawn or nod. What every value means, in
plain words, is in [`eepy/perception/README.md`](eepy/perception/README.md).

**Testing the face features properly?** Follow the step-by-step tutorial for
macOS, Windows and Linux: [eepy/perception/README.md → Tutorial: test the face features](eepy/perception/README.md#tutorial-test-the-face-features-about-10-minutes).

### Test the user state (focused, distracted, drowsy…)

From the face features, EEPY decides once a second whether the participant is
**focused, distracted, drowsy, very drowsy, absent** or **unknown**, with the
reason. The Wizard panel's status bar and the preview show it. The rules and
their numbers are in `config/state_rules.yaml`.

```bash
source .venv-vision/bin/activate      # Windows: .venv-vision\Scripts\Activate.ps1
python -m eepy.perception.selftest --states --no-aus   # guided, ~3 minutes: work, phone, sleepy, leave
python -m eepy.state.replay latest                     # the states of a recorded session, no camera
```

Every state and rule in plain words, how to change a threshold, and the replay:
[`eepy/state/README.md`](eepy/state/README.md).

### Simulate what the robot would do (no camera, no robot)

```bash
source .venv-robot/bin/activate       # Windows: .venv-robot\Scripts\Activate.ps1
python -m eepy.interaction.simulate --list          # built-in scenarios
python -m eepy.interaction.simulate phone_break     # or: "focused 5m, distracted 2m, focused 3m"
```

It prints every nudge, ladder level and outcome over time. The timings are in
`config/escalation.yaml`; the guide is [`eepy/interaction/README.md`](eepy/interaction/README.md).

### Test the robot connection

This needs **two terminals at once**: one running the robot's "daemon"
(the process that actually talks to the robot or the simulator), one
running your script.

**Terminal 1 — start the daemon:**

```bash
source .venv-robot/bin/activate       # Windows: .venv-robot\Scripts\Activate.ps1
reachy-mini-daemon --sim              # opens a simulator window, no real robot needed
```

> **On macOS only:** the simulator needs a special launcher instead of
> plain Python:
> ```bash
> mjpython $(which reachy-mini-daemon) --sim
> ```

Leave this running — you should see a 3D viewer window with the robot in
it.

**Terminal 2 — run the test script:**

```bash
cd EEPY                                # make sure you're back in the project folder
source .venv-robot/bin/activate        # Windows: .venv-robot\Scripts\Activate.ps1
python robot/test_connection.py
```

You should see `Connected!` printed, and the robot in the simulator window
should do a small greeting movement (head/antenna wiggle). That confirms
your robot setup works — without needing the physical hardware.

**To run against the real robot instead of the simulator:** connect it via
USB, then in Terminal 1 run `reachy-mini-daemon` (no `--sim`). On Linux
you'll need a one-time USB permission rule the first time — see the
["USB permissions" section of the official install guide](https://huggingface.co/docs/reachy_mini/SDK/installation)
if the daemon can't find the robot.

**To run against a wireless robot on the network** (no daemon step needed —
skip Terminal 1 entirely):

```bash
python robot/test_connection.py --robot-host 192.168.1.50
```

(replace with the robot's actual IP — ask whoever set it up, or put it in
`.env` as `REACHY_MINI_HOST` so you don't need the flag every time).

### Make the robot follow your eyes (eye gaze)

`robot/eye_gaze_follow.py` turns the robot's head to look into your eyes: it
aims at the point between your eyes, copies how your head is tilted, and turns
the body to catch up when the head stays turned for 2 seconds. Copied from
BURTRAM, a standalone script: it is **not** part of the panel or the
escalation yet. Don't run it together with the panel or `python -m
eepy.perception`: they would fight over the camera and the head.

It runs in **`.venv-vision`** (it needs mediapipe), and moves the robot over the
daemon's web API instead of the robot library, which lives in the other venv.
A window shows what the camera sees; **Q** or **Esc** in that window (or
Ctrl+C in the terminal) stops it, and the head glides back to neutral.

**With the simulator and your laptop webcam:** start the simulator (Terminal 1,
as in "Test the robot connection" above), then:

```bash
source .venv-vision/bin/activate       # Windows: .venv-vision\Scripts\Activate.ps1
python robot/eye_gaze_follow.py --sim --camera 0
```

The blue rectangle in the window is roughly what the robot's own camera would
see from where its head points.

**With the real robot:** its camera is read through the robot library, so that
part runs in `.venv-robot`, in a second terminal:

```bash
# Terminal 1
source .venv-robot/bin/activate        # Windows: .venv-robot\Scripts\Activate.ps1
python robot/eye_gaze_camera_relay.py --robot-host 192.168.50.95

# Terminal 2
source .venv-vision/bin/activate       # Windows: .venv-vision\Scripts\Activate.ps1
python robot/eye_gaze_follow.py --robot-host 192.168.50.95
```

If the head turns away from you instead of toward you, add `--invert-yaw`
(left/right) or `--invert-pitch` (up/down). `--smoothing` (default 0.15) and
`--max-head-speed` (default 150 °/s) make it calmer or quicker; `--help` lists
everything. Every movement goes through `robot/safety_limits.py` first.

### Talk to EEPY (Gemini API)

`robot/conversation.py` lets you actually talk to the robot: it listens
(robot mic or typed text), sends what you said to Gemini, and speaks the
reply back out loud while nodding. It needs `GEMINI_API_KEY` in `.env`
(Step 4 above) and a daemon running (simulator or real robot, same as
"Test the robot connection" above).

```bash
source .venv-robot/bin/activate        # Windows: .venv-robot\Scripts\Activate.ps1
python robot/conversation.py --text    # type instead of speaking — good first test
```

Once that works, drop `--text` to talk via the robot's own microphone
instead. `Ctrl+C` to stop.

**This is a building block, not the finished feature.** It's a generic
"press Enter, talk, get a reply" loop — useful for confirming the API key
and the robot connection both work. EEPY's actual behavior should be the
robot speaking *proactively* when the fatigue-detection side (`vision/`)
decides a nudge is warranted, not waiting for someone to start a
conversation. That trigger logic doesn't exist yet and is an open design
question for the team — how eager should it be, does it wait for a lull,
does it always ask before nudging, etc. When someone builds it,
`robot/conversation.py` already exposes the two pieces needed for it
without going through the interactive loop:

```python
from conversation import setup, say_and_nod
robot, mic, voice = setup(args)   # connect once
say_and_nod(robot, voice, "Hey — you've been at this a while, want a break?")
```

---

## Project structure

```
EEPY/
├── vision/                # camera-based detection code (own environment: .venv-vision)
│   └── test_camera.py     # sanity check — camera + mediapipe working?
├── robot/                 # Reachy Mini control code (own environment: .venv-robot)
│   ├── safety_limits.py   # clamps any movement to the robot's real physical limits
│   ├── connection.py      # shared "how do I reach the robot?" helper (sim/USB/wireless)
│   ├── test_connection.py # sanity check — can we connect and move the robot?
│   ├── greetings.py       # a scripted greeting sequence
│   ├── conversation.py    # talk to the robot via Gemini (STT + reply + TTS)
│   ├── eye_gaze_follow.py # the head follows your eyes (from BURTRAM; runs in .venv-vision, REST)
│   └── eye_gaze_camera_relay.py  # real robot only: hands its camera frames to eye_gaze_follow.py
├── behaviors/             # WHAT the robot does, as editable YAML — see docs/BEHAVIORS_GUIDE.md
│   ├── _blocks.yaml       # shared building blocks + named gaze targets
│   ├── idle.yaml          # quiet background presence
│   ├── refocus.yaml       # the "back to work" ladder, levels 0-4
│   ├── bedtime.yaml       # the "time to sleep" ladder, levels 0-5
│   └── lines.yaml         # everything the robot can say
├── sounds/                # the sounds behaviours play (see sounds/README.md)
├── eepy/behaviors/        # reads, checks, previews and PLAYS behaviors/ — how it works: eepy/behaviors/README.md
├── eepy/wizard/           # the Wizard-of-Oz control panel — how it works: eepy/wizard/README.md
├── eepy/logs/             # recording and loading session data — how it works: eepy/logs/README.md
├── eepy/speech/           # the robot's voice (text-to-speech) — how it works: eepy/speech/README.md
├── eepy/camera/           # the camera stream for perception — how it works: eepy/camera/README.md
├── eepy/perception/       # face features: blinks, yawns, nods, PERCLOS — what they mean: eepy/perception/README.md
├── eepy/state/            # the user's state (focused, drowsy, …) from those features — eepy/state/README.md
├── eepy/interaction/      # the escalation: what the robot does about that state (rules and hmm modes) — eepy/interaction/README.md
├── config/
│   ├── eepy.yaml          # system settings: the MODE (experimental condition), data folder, privacy, camera, face features
│   ├── state_rules.yaml   # the threshold rules: features -> focused / distracted / drowsy / ... (eepy/state/README.md)
│   ├── escalation.yaml    # when the robot nudges: onset delays, reaction windows, cooldowns, guards (eepy/interaction/README.md)
│   └── wizard_panel.yaml  # the panel's keys, markers and defaults
├── data/sessions/         # every recorded session (gitignored: participant data)
├── data/calibration/      # per-person calibrations: gaze zones + baseline (gitignored; numbers only)
├── docs/
│   ├── BEHAVIORS_GUIDE.md # how to write a behaviour — start here for design work
│   ├── WIZARD_PANEL_GUIDE.md  # how to run a session with the Wizard panel
│   └── DATA_GUIDE.md      # where session data is, and how to load it
├── tests/                 # pytest: python -m pytest tests/
├── models/                # trained model weights go here (gitignored, see models/README.md)
├── requirements-vision.txt
├── requirements-aus.txt   # OPTIONAL action units (Py-Feat) -- see eepy/perception/aus/README.md
├── requirements-robot.txt
└── .env.example           # copy to .env for API keys / robot address (gitignored)
```

As real detection and movement logic gets built, it goes in `vision/` and
`robot/` respectively. Perception gets its frames from `eepy.camera` (`open_camera()`),
not by opening a camera itself — see [eepy/camera/README.md](eepy/camera/README.md). Any script in `robot/` that sends a movement to the
robot should pass it through `robot/safety_limits.py`'s `enforce()` first —
it's what keeps a bug in our code from trying to twist a joint past what the
hardware can actually do.

The behaviour player (`eepy/behaviors/`) is the one deliberate exception. It
checks the **same** limits — it reads them straight from `safety_limits.py` —
but where `enforce()` quietly *clamps* a bad value, the player *refuses* it and
says why, so a mistake in a behaviour file is seen and fixed rather than
silently softened. It also checks things `enforce()` cannot, like whether the
head can physically reach a pose and how fast it moves. Details in
[eepy/behaviors/README.md](eepy/behaviors/README.md).

### Which document to read

| You want to… | Read |
|---|---|
| set up your computer, or run the sanity checks | this README |
| edit or add a behaviour, and watch it in the simulator | [docs/BEHAVIORS_GUIDE.md](docs/BEHAVIORS_GUIDE.md) |
| run a session with the Wizard-of-Oz panel | [docs/WIZARD_PANEL_GUIDE.md](docs/WIZARD_PANEL_GUIDE.md) |
| find, check or analyse recorded sessions | [docs/DATA_GUIDE.md](docs/DATA_GUIDE.md) |
| check the camera | [eepy/camera/README.md](eepy/camera/README.md) |
| understand the face values (blinks, PERCLOS, …), or work on perception | [eepy/perception/README.md](eepy/perception/README.md) |
| understand the user states and their reasons, or change a threshold | [eepy/state/README.md](eepy/state/README.md) |
| see why the HMM decided what it did, second by second (a page per session, a demo, or live) | [docs/EXPLANATION_VIEWER.md](docs/EXPLANATION_VIEWER.md) |
| train and compare HMM versions on simulated users | [docs/SYNTHETIC_DATASET.md](docs/SYNTHETIC_DATASET.md) |
| understand when the robot nudges on its own (rules and hmm modes), or change its timings | [eepy/interaction/README.md](eepy/interaction/README.md) |
| why the hmm mode decides on the whole belief, and how it compares to the rules | [docs/A19_BELIEF_DECISIONS.md](docs/A19_BELIEF_DECISIONS.md) |
| how EEPY explains each nudge ("your eyes were closed 34 % of the last minute"), and how faithful that is | [docs/A20_EXPLANATIONS.md](docs/A20_EXPLANATIONS.md) |
| write explanation lines (the evidence types and their placeholders) | [docs/EXPLANATION_CATALOG.md](docs/EXPLANATION_CATALOG.md) |
| how each nudge becomes a training example for the bandit (rewards), and build the table | [docs/A22_REWARDS.md](docs/A22_REWARDS.md) |
| open problems we parked (e.g. keeping the face in the robot camera's view) | [docs/TODO.md](docs/TODO.md) |
| replace or add a sound | [sounds/README.md](sounds/README.md) |
| choose the robot's voice | [docs/BEHAVIORS_GUIDE.md](docs/BEHAVIORS_GUIDE.md#choosing-the-robots-voice) |
| understand how the behaviour system and player are built, and why | [eepy/behaviors/README.md](eepy/behaviors/README.md) |
| understand how the Wizard panel is built, and why | [eepy/wizard/README.md](eepy/wizard/README.md) |
| understand how logging and the mode switch are built, and why | [eepy/logs/README.md](eepy/logs/README.md) |
| understand how speech is built, and why | [eepy/speech/README.md](eepy/speech/README.md) |

### Editing what the robot does

The robot's movements are **not** hard-coded in Python — they live in
`behaviors/*.yaml`, in degrees and seconds, so they can be edited without
touching any code. If that is the part you are working on, read
**[docs/BEHAVIORS_GUIDE.md](docs/BEHAVIORS_GUIDE.md)**, not this section.

The tools need no robot and no simulator. With `.venv-robot` active (Step 3
above installs everything):

```bash
python -m eepy.behaviors.validate                   # check every file
python -m eepy.behaviors.preview --list             # what behaviours exist
python -m eepy.behaviors.preview refocus_curious    # what one will actually do
```

A GitHub Action runs that same check on every pull request, so a broken
behaviour file cannot be merged.

To **see** a behaviour move, start the simulator in one terminal (see "Test
the robot connection" above), then in another:

```bash
python -m eepy.behaviors.play refocus_curious --backend sim --watch
```

`--watch` replays it every time you save a behaviour file, so you can tune
values and watch the result immediately. The guide covers the details.

### Driving the robot by hand (Wizard-of-Oz panel)

```bash
python -m eepy.wizard --backend sim       # or real, or dry
```

Then open the address it prints — on this machine, or on any laptop on the same
network. See **[docs/WIZARD_PANEL_GUIDE.md](docs/WIZARD_PANEL_GUIDE.md)**.

Every session is saved in `data/sessions/`, under the **mode** — the
experimental condition — set in `config/eepy.yaml` (or `--mode wizard|rules|hmm|hmm_bandit`).
In the **rules** and **hmm** modes the robot nudges on its own (the user state plus
the escalation ladders; in the hmm mode the robot decides on the HMM's whole belief:
evidence that adds up, waiting when unsure, the gentler option when unclear
([docs/A19_BELIEF_DECISIONS.md](docs/A19_BELIEF_DECISIONS.md))); the panel's **Pause autonomy** hands
control back to you. The vision program (`python -m eepy.perception`) follows the
session's mode by itself (`--estimator auto`). How the HMM works and how to read
its belief: [eepy/state/README.md](eepy/state/README.md#the-hmm-a-belief-instead-of-a-verdict).
When it nudges and why, and how to try the timings without a camera:
[eepy/interaction/README.md](eepy/interaction/README.md).
To look at the data:

```bash
python -m eepy.logs.list                  # every session, with a verdict
python -m eepy.logs.summary P03           # one session
```

See **[docs/DATA_GUIDE.md](docs/DATA_GUIDE.md)**.

## Stuck?

Ask in the team chat before spending too long on an install issue — chances
are someone already hit the same one. If you fix something not covered
here, add it to this README so the next person doesn't have to debug it
again.

## TEST
Hello
