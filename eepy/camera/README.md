# eepy/camera — the camera stream

Delivers camera frames to perception (face landmarks, action units, gaze: task
A9). It only delivers frames: no feature extraction happens here.

**Privacy.** Frames are never written to disk, logged, or sent anywhere except
the local perception pipeline. Only statistics are logged (frame rate, latency,
dropped frames). The robot's camera travels between the two Python
environments over `127.0.0.1`, which no other computer can reach.

## Which camera?

| Backend (`EEPY_BACKEND` / `--backend`) | Camera used | What perception gets |
|---|---|---|
| `real` | the **robot's** camera | the whole picture, ~720p at ~15 fps |
| `sim`, `dry` | this **laptop's** camera | only the **blue box**: what the robot would see |

Override it with `--camera laptop|robot|fake` on the tools, or with
`camera.source` in `config/eepy.yaml`.

### The blue box (laptop camera)

The laptop camera sees more than the robot does. The blue box marks the robot's
view, and only the content of the box is passed on, as if the robot's camera
had taken it.

- **Size:** box width = picture width × `robot.fov_deg / laptop.fov_deg`
  (50 / 78 by default, the same as the BURTRAM project). Its shape is the robot
  camera's shape: 16:9, from `robot.resolution`.
- **Position:** it **follows the robot's head**. If the robot looks to its
  right, the box moves right; if it looks down, the box moves down. The head
  position comes from the Wizard panel. Without the panel running, the box stays
  in the middle.
- Where the box goes past the edge of the webcam picture, that part is black:
  the laptop camera cannot see there.

The picture is **not mirrored**. It is what a camera in the robot's place sees,
so when you move to your left you move right on screen.

## For the design team: check your setup

In the **vision** environment:

**macOS / Linux**
```bash
source .venv-vision/bin/activate
pip install -r requirements-vision.txt      # once, after pulling this change
python -m eepy.camera.list                  # which cameras are there?
python -m eepy.camera.preview               # see what EEPY sees
```

**Windows (PowerShell)**
```powershell
.venv-vision\Scripts\Activate.ps1
pip install -r requirements-vision.txt      # once, after pulling this change
python -m eepy.camera.list
python -m eepy.camera.preview
```

Sit so that your face is **well inside the blue box**. The small picture in the
top-right corner is exactly what perception receives. In the vision
environment the preview also shows the **face features** (eye and mouth
openness, head angles, a flash on blinks, yawns and nods; **L** toggles the
landmarks, `--no-face` turns them off): see
[eepy/perception/README.md](../perception/README.md). It also shows a small
**gaze map** with ON WORK / AWAY (**G** toggles it): see
[eepy/perception/gaze/README.md](../perception/gaze/README.md). Press **Q** or **Esc**
(or close the window) to stop.

To make the box follow the robot's head, start the Wizard panel in a second
terminal (in `.venv-robot`) and play some behaviours:

```bash
python -m eepy.wizard --backend sim     # or dry: no simulator needed
```

The panel's header then shows a **camera** pill. Hover over it for details.

### The robot's own camera

1. Start the panel with the real robot (in `.venv-robot`):
   `python -m eepy.wizard --backend real`
2. In `.venv-vision`: `python -m eepy.camera.preview --camera robot`

Without the panel, `python -m eepy.camera.relay` (in `.venv-robot`) serves the
robot's camera on its own.

## Tutorial: test everything (about 15 minutes)

Do this once after pulling the camera changes, and whenever something seems
off. Each step says what you should see.

### Start here: open two terminals

Open **two** terminal windows, both in the `EEPY` folder (macOS: Terminal;
Windows: PowerShell; Linux: any terminal). Paste the line for your system into
each one:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| **Terminal A** (camera) | `source .venv-vision/bin/activate` | `.venv-vision\Scripts\Activate.ps1` |
| **Terminal B** (robot, panel) | `source .venv-robot/bin/activate` | `.venv-robot\Scripts\Activate.ps1` |

The prompt now starts with `(.venv-vision)` or `(.venv-robot)`. **Every
command below is the same on every system** from here on. Just type it in the
terminal named before it (A or B).

> Windows refuses to run `Activate.ps1` ("running scripts is disabled")? Run
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once,
> then try again.
> No `.venv-...` folders at all? Set up the computer first: main README, section 1.

**0. Install (once).** The camera environment gained `pyyaml` and `pytest`:

```bash
# A
pip install -r requirements-vision.txt
```

**1. The automated tests.** No camera or robot needed.

```bash
# A
python -m pytest tests/test_camera.py tests/test_perception.py -q
# B
python -m pytest tests/ -q
```

✅ Both end in `passed`. A few `skipped` are normal: each environment lacks
something the other has.

**2. Which cameras are there?**

```bash
# A
python -m eepy.camera.list
```

✅ `camera 0: works, 1280x720 (...)` and, at the end, `EEPY will use: the laptop camera ...`.
❌ An explanation instead: follow it (see *Problems and what to do* below).

**3. The laptop camera and the blue box.**

```bash
# A
python -m eepy.camera.preview
```

✅ A window with your webcam picture, a **blue box** in the middle, the robot's
view (the box's content) in the top-right corner, and about `15 fps` at the top.
The third line says *head position unknown: box centred*, which is right
because the panel isn't running yet. Leave the preview open.
At the bottom-left you'll also see the **face values** (eye and mouth
openness, head angles). Testing those has its own tutorial in
[eepy/perception/README.md](../perception/README.md#tutorial-test-the-face-features-about-10-minutes).
**macOS, first time:** a grey window explains that the camera is not allowed.
Allow it (System Settings → Privacy & Security → Camera → switch on your
terminal or editor), **quit that app with Cmd+Q, reopen it**, and run the
command again.
**Windows, first time:** opening the camera can take a few seconds.

**4. The box follows the robot's head.**

```bash
# B
python -m eepy.wizard --backend dry      # no simulator needed
```

Open `http://127.0.0.1:8080` and click some behaviours: `bedtime subtle` sags
the head, and `refocus joint attention` glances at you and then down at the notes.
✅ In the preview, the text changes to *blue box follows the robot's head*,
and the box moves: down when the head looks down, sideways when it turns. (A
head *tilt* doesn't move it: the robot keeps looking at the same spot.)
✅ In the panel's header, the **camera** pill reads `camera: laptop` with a
green dot. Hover over it: *1 vision program connected*.
Want to watch the robot move as well? Start the simulator first and use
`--backend sim` (see docs/BEHAVIORS_GUIDE.md).

**5. A camera problem is explained and recovers by itself.** Keep the preview
running and open the camera in another app (Photo Booth, the Windows Camera
app, Teams, …), or unplug an external webcam.
✅ On Windows and Linux the preview switches to a grey *Waiting for the laptop
camera…* screen that explains the problem, and comes back once you close the
other app. (macOS usually lets two apps share a camera, so nothing may happen
there.)

**6. Settings take effect on the next start.** In `config/eepy.yaml`, set
`camera.robot.fov_deg: 60`, then restart the preview.
✅ A bigger box. Put it back to `50` afterwards.

**7. No camera at all.**

```bash
# A
python -m eepy.camera.preview --camera fake
```

✅ A moving coloured test pattern. This is what the CI tests use.

**8. The robot's own camera** (needs the real robot).

```bash
# B
python -m eepy.wizard --backend real
# A
python -m eepy.camera.preview --camera robot
```

✅ The robot's picture, about `15 fps`, no blue box. The panel's pill reads
`camera: robot · 15 fps`.
✅ Switch the robot off, or move it out of Wi-Fi range: the preview shows
*Waiting…*, and the picture returns by itself when the robot comes back.

## Problems and what to do

**macOS: "macOS is not letting this program use the camera"**
1. System Settings → Privacy & Security → **Camera**.
2. Switch on the app you run EEPY from: Terminal, iTerm, VS Code, PyCharm…
   If it isn't listed, run the preview once more; macOS asks the first time.
3. **Quit that app completely (Cmd+Q) and open it again.** macOS only applies
   the change to a freshly started app.

**Windows: the camera isn't found**
Settings → Privacy & security → Camera: both *Camera access* and
*Let desktop apps access your camera* must be on. The first start can take a
few seconds.

**Linux: the camera isn't found**
Check `ls /dev/video*`. If you lack permission:
`sudo usermod -aG video $USER`, then log out and back in.

**"another program is probably using it"**
Close Teams, Zoom, Meet, FaceTime, OBS or another EEPY preview. On Windows and
Linux only one program can use a camera at a time.

**Wrong camera (e.g. your phone's, via Continuity Camera)**
Run `python -m eepy.camera.list`, then set `camera.laptop.index` in
`config/eepy.yaml`, or try `python -m eepy.camera.preview --index 1`.

**"Cannot reach the robot's camera"**
The panel isn't running with `--backend real`, or it is on another port.
Without a robot, use `--camera laptop`.

The camera never gives up: after a problem it retries every
`reconnect_seconds`, and recovers by itself once the problem is fixed.

## Settings (`config/eepy.yaml`, section `camera:`)

| Setting | Default | Meaning |
|---|---|---|
| `source` | `auto` | `auto`, `robot`, `laptop` or `fake` |
| `fps` | 15 | frames per second delivered, from **either** camera, so sim matches the robot |
| `stats_seconds` | 10 | how often `camera_stats` is logged |
| `relay_port` | 8766 | local port for robot pictures and head position |
| `reconnect_seconds` | 2 | wait between retries |
| `laptop.index` | `auto` | camera number, or `auto` (the first that works) |
| `laptop.resolution` | [1280, 720] | asked of the webcam (it may give something else) |
| `laptop.fov_deg` | 78 | how wide the webcam sees |
| `robot.resolution` | [1280, 720] | the robot's stream; sets the box's **shape** |
| `robot.fov_deg` | 50 | how wide the robot sees; sets the box's **width** |

`robot.fov_deg = 50` is BURTRAM's value, kept so the two projects behave the
same. The robot lens's own calibration (in the SDK) says about 87°, which is
wider than most laptop cameras: with 87 the box would be bigger than the
picture. Experiment freely; the preview shows the result immediately.

## For developers (A9): the API

```python
from eepy.camera import open_camera
from eepy.logs.features import ProcessEventLog

camera = open_camera(events=ProcessEventLog("vision"))   # picks the camera as above
camera.start()                       # capture runs in a background thread
frame = camera.wait_for_frame()      # blocks up to 1 s; None on timeout
while running:
    frame = camera.wait_for_frame(newer_than=frame)   # the next NEW frame
    if frame is None:
        continue
    landmarks = run_model(frame.image)   # BGR uint8, height x width x 3
camera.stop()
```

- `get_latest() -> Frame | None` never blocks and returns the newest frame.
  Compare `frame.index` to see whether it is new.
- `subscribe(callback) -> unsubscribe`: called on the capture thread for every
  frame. Keep it fast; do heavy work in your own thread with `get_latest()`.
- **Latest-frame semantics.** Only the newest frame is kept. A slow consumer
  skips frames instead of falling behind, and skipped frames count as
  `dropped`.
- `Frame`: `image`, `t` (`eepy.logs.clock.monotonic()`, the same clock as
  every logged event), `index` (gaps = dropped), `source`, `width`, `height`,
  `box` (laptop only: where the crop sits in the webcam picture), `full`
  (preview only: never use it for perception).
- `status` (`starting`, `running`, `reconnecting`, `stopped`) and `error` (a
  message for a person).
- Logged events (through `events`): `camera_started`, `camera_error`,
  `camera_reconnected`, `camera_stats` (`fps`, `latency_ms`, `dropped`).
- Tests: `FakeCamera` (moving test pattern, or `clip=` a video file). Keep clips
  of real people in `.cache/`, which git ignores.

### Files

| File | What |
|---|---|
| `source.py` | `CameraSource`: thread, latest-frame slot, rate limit, reconnect, statistics |
| `laptop.py` | webcam: per-OS OpenCV backend (AVFoundation / Media Foundation, then DirectShow / V4L2), crop |
| `robot.py` | the robot's camera, via the relay |
| `relay.py` | `CameraRelay` (robot environment, started by the panel) and `RelayClient` |
| `region.py` | the blue box's geometry (numpy only) |
| `fake.py` | `FakeCamera` |
| `config.py` | the `camera:` settings, readable without pydantic (Python 3.9) |
| `errors.py` | the friendly messages |
| `list.py`, `preview.py` | the two tools above |

Everything here runs on Python 3.9 and imports OpenCV only where it needs it:
the relay runs in the robot environment, which has no OpenCV.
