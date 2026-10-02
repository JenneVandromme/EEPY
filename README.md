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
│   └── conversation.py    # talk to the robot via Gemini (STT + reply + TTS)
├── behaviors/             # WHAT the robot does, as editable YAML — see docs/BEHAVIORS_GUIDE.md
│   ├── _blocks.yaml       # shared building blocks + named gaze targets
│   ├── idle.yaml          # quiet background presence
│   ├── refocus.yaml       # the "back to work" ladder, levels 0-4
│   ├── bedtime.yaml       # the "time to sleep" ladder, levels 0-5
│   └── lines.yaml         # everything the robot can say
├── sounds/                # the sounds behaviours play (see sounds/README.md)
├── eepy/behaviors/        # reads, checks, previews and PLAYS behaviors/ — how it works: eepy/behaviors/README.md
├── eepy/wizard/           # the Wizard-of-Oz control panel — how it works: eepy/wizard/README.md
├── config/
│   └── wizard_panel.yaml  # the panel's keys, markers and defaults
├── logs/                  # session logs from the panel (gitignored: participant data)
├── docs/
│   ├── BEHAVIORS_GUIDE.md # how to write a behaviour — start here for design work
│   └── WIZARD_PANEL_GUIDE.md  # how to run a session with the Wizard panel
├── tests/                 # pytest: python -m pytest tests/
├── models/                # trained model weights go here (gitignored, see models/README.md)
├── requirements-vision.txt
├── requirements-robot.txt
└── .env.example           # copy to .env for API keys / robot address (gitignored)
```

As real detection and movement logic gets built, it goes in `vision/` and
`robot/` respectively. Any script in `robot/` that sends a movement to the
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
| replace or add a sound | [sounds/README.md](sounds/README.md) |
| understand how the behaviour system and player are built, and why | [eepy/behaviors/README.md](eepy/behaviors/README.md) |
| understand how the Wizard panel is built, and why | [eepy/wizard/README.md](eepy/wizard/README.md) |

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

## Stuck?

Ask in the team chat before spending too long on an install issue — chances
are someone already hit the same one. If you fix something not covered
here, add it to this README so the next person doesn't have to debug it
again.

## TEST
Hello
