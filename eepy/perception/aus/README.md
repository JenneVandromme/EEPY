# Action units (optional)

**Action units (AUs)** are the standard way to describe facial movements, one
muscle movement at a time: "brows lowered", "eyelids tightened", "jaw
dropped". EEPY reads them with **Py-Feat**, a trained model, on top of the
MediaPipe face features (see [../README.md](../README.md)).

They are **optional**. Without them everything else works: the camera, face
features, blinks, yawns, nods and recording. They need about 3 GB of extra
software, and a GPU to run at full speed.

## What each AU means

EEPY uses these 8 by default (`aus.units` in `config/eepy.yaml`):

| AU | In plain words | Why EEPY cares |
|---|---|---|
| **AU43** | eyes closed | fatigue (a learned cross-check on the eye features) |
| **AU07** | eyelids tightened, a squint | fatigue, straining to see |
| **AU25** | lips apart | yawning (together with AU26) |
| **AU26** | jaw dropped | yawning |
| **AU04** | brows lowered and pulled together, a frown | concentration and effort |
| **AU05** | upper eyelids raised, wide eyes | alertness, surprise |
| **AU01** | inner brows raised | alertness, interest |
| **AU02** | outer brows raised | alertness, interest |

Each value is a **probability from 0 to 1** that the movement is present.
From 0.5 (`aus.active_threshold`) the AU counts as **on**.

**How reliable they are.** Py-Feat's own published tests:
- **Well tested:** AU01, 02, 04, 25 and 26 (good accuracy).
- **Tested only as part of a group:** AU05.
- **No published figures:** AU07 and AU43. Treat those two with care.

EEPY therefore cross-checks AU43 against its own eye measurement, and AU26
against its yawn detection (see *Cross-checks* below). On the first test, AU43
agreed with the eye features in 97 % of frames. The model is licensed for
**research use only**.

## Install

The extra goes **into the vision environment**, which needs **Python 3.11 or
newer** (3.12 recommended). Check with `python --version` inside
`.venv-vision`. If it says 3.9 or 3.10, rebuild the environment as in the main
README's step 1 with `python3.12`.

Everything below is typed in the vision terminal (`.venv-vision` active).

**macOS, Apple Silicon (M1–M4).** The GPU is used automatically (MPS):
```bash
pip install -r requirements-aus.txt
```

**macOS, Intel:** not possible, because PyTorch no longer exists for Intel
Macs. AUs simply stay off; nothing else is affected.

**Windows with an NVIDIA GPU.** Install the **CUDA** build of PyTorch
**first**. Otherwise pip installs a CPU-only PyTorch and the GPU sits unused:
```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
pip install -r requirements-aus.txt
```
**RTX 50-series GPUs (e.g. RTX 5090) need `cu130` instead of `cu126`**: CUDA
12.6 has no support for them. `cu126` works with older drivers, so use it for
every other NVIDIA GPU. Not sure which you have? `nvidia-smi` shows the GPU's
name.

**Ubuntu with an NVIDIA GPU.** The normal PyTorch already includes CUDA:
```bash
pip install -r requirements-aus.txt
```

On Windows and Ubuntu only a recent **NVIDIA driver** is needed, not a
separate CUDA install: PyTorch brings its own. If the driver is too old, CUDA
is silently unavailable. Check that the GPU is really used:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

That should print `True` on Windows and Ubuntu with an NVIDIA GPU. On a Mac it
prints `False`, which is normal: Macs use MPS.

The first use downloads the AU model (~120 MB, once). After that it works
offline.

## Profiles: which machine is this?

How the AUs run depends on the machine, so each person picks a **profile**.
The profiles live in the shared `config/eepy.yaml` (`aus.profiles`).

| Profile | For | AUs | At most |
|---|---|---|---|
| `laptop` (default) | teammates' laptops, development | on if installed | 10 per second |
| `demo-desktop` | the lab or demo desktop with an NVIDIA GPU | expected; a **loud warning** if no GPU is found (AUs keep running, slower) | 15 per second (the camera's rate) |

Choose yours in **your own `.env`** (it is never committed), or per run:

```bash
# in .env
EEPY_PROFILE=demo-desktop
# or for one run
python -m eepy.perception --profile demo-desktop
```

The device is chosen automatically: CUDA, then Apple's MPS, then the CPU. You
can force one with `device:` in the profile.

The rate adapts by itself. AUs run as often as the machine manages, up to the
profile's maximum. On a machine too slow to be useful (fewer than 2 per
second), they **pause** and say so rather than handing on old values, then try
again a minute later.

## Measure your machine: `bench_aus`

```bash
python -m eepy.perception.bench_aus
```

It prints:
- what PyTorch sees (CUDA? which GPU?);
- how long MediaPipe and the AU model take per frame on each available device;
- the rate that allows;
- **the profile to put in your `.env`**.

Run it on **every machine that will record data**, and especially on the demo
desktop. Numbers from one machine say nothing about another. Add `--camera` to
time on a live frame instead of the sample face.

For reference, **development numbers from a MacBook Pro (M4 Pro)**:
- MediaPipe: 5.7 ms per frame;
- AUs on the Apple GPU: 14 ms per result (up to ~70 per second);
- AUs on the CPU (4 threads): 228 ms per result (~4 per second).

## Tutorial: test the action units (about 10 minutes)

In the vision terminal (see [../README.md](../README.md#start-here-open-two-terminals)
for opening it on your system):

**1. Install and check.** See *Install* above, then:

```bash
python -m pytest tests/test_aus.py -q
```

✅ Ends in `passed`. One `skipped` is normal (that test needs the robot
environment). With the extra installed, this also runs the real model once.

**2. Measure the machine.**

```bash
python -m eepy.perception.bench_aus
```

✅ On Windows or Ubuntu it shows `CUDA yes` and your GPU's name. On an
Apple-Silicon Mac it shows `MPS yes`. Set the suggested `EEPY_PROFILE` in your
`.env`.
❌ `!! An NVIDIA GPU is present but PyTorch cannot use it`: install the CUDA
build of PyTorch (see *Install*).

**3. Watch them live.**

```bash
python -m eepy.camera.preview
```

✅ Bottom right: one bar per AU, plus the device, results per second and ms
per result. **A** hides or shows them. Frown and the AU04 bar turns **green**
(on). Raise your eyebrows: AU01/AU02. Close your eyes: AU43. Open your mouth
wide: AU25/AU26.
If it says `AUs loading`, wait a few seconds. `AUs off` comes with the reason.

**4. The self-test.**

```bash
python -m eepy.perception.selftest
```

After the five face steps come five AU steps: frown, squint, raise your
eyebrows, close your eyes for 3 seconds, open your mouth wide.
✅ **PASS** where the AU rose clearly above your neutral face (measured in
step 1). At the end it shows the device, the achieved rate and how often AU43
agreed with the eye features.

## What is recorded

| Where | What |
|---|---|
| `aus.csv` (session folder) | one row per AU result: the 8 probabilities plus `infer_ms` (how long the model took). `t_mono` is the time of the **frame** it was computed on, not when the result arrived |
| `au_status` event | AUs changed state: `loading`, `running`, `paused` (too slow) or `off`, plus the device, profile, precision and why |
| `au_window` event, every second | per AU over the last 10 s / 60 s: `AU04_mean_10s` (average probability) and `AU04_active_10s` (share of the time it was on), plus `rate`, `infer_ms` and the cross-checks |

Load them with `load_session(...).aus`.

**Staleness.** A result older than 1 s (`aus.stale_seconds`) compared with the
newest frame is **stale**. It's shown as `au_stale = 1`, is not handed on, and
is not counted in the windows. This happens when the machine falls behind.

**Cross-checks** (in `au_window`) help decide which signals the HMM should
trust:
- `au43_eyes_agree` and `au43_eyes_kappa`: how often AU43 (eyes closed)
  agrees with the eye features' "80 % shut".
  - `agree` is the plain share of agreement.
  - `kappa` corrects for chance agreement: 1 is perfect, 0 is no better than
    guessing.
  - `au43_eyes_both`, `au43_only` and `eyes_only` are the counts behind them.
- `jaw_in_yawn` and `jaw_outside_yawn`: the average AU26 during detected yawns
  versus outside them. A big gap means both agree on what a yawn is.

**Precision.** AUs always run in full precision (fp32), so values from CUDA,
a Mac GPU and a CPU are comparable across machines.

## For developers

- **`ActionUnitExtractor`** (`extractor.py`) is a FacePipeline plug-in. Its
  `update()` only drops the newest face into a one-place slot, so it never
  blocks the MediaPipe loop. A worker thread runs the model on the newest face
  whenever it's free.
- **`PyFeatDetector`** (`detector.py`) reuses MediaPipe's face box. It cuts the
  face out with Py-Feat's own crop (a square, 1.2×, 256 px, matching the
  training) and runs only Py-Feat's multitask model. That means no RetinaFace,
  no identity model and no results table: about 2.7× faster than Py-Feat's
  standard path on a GPU. The model is pinned at `py-feat==2.1.2`, because this
  calls Py-Feat's model class directly.
- **Never `import feat` directly; use `import_pyfeat()`.** PyTorch must be
  imported **before** Py-Feat. Py-Feat imports scikit-learn, and torch and
  scikit-learn each bring their own OpenMP library. In the wrong order the
  process crashes on macOS (`OMP: Error #179`, a segmentation fault).
- Importing Py-Feat also drops PyTorch to 1 CPU thread. `device.cpu_threads()`
  sets it explicitly. Auto means half the cores, between 2 and 4: on a MacBook,
  4 threads beat 8.
- `requirements-aus.txt` includes `requirements-vision.txt` on purpose.
  Installed alone, Py-Feat upgrades numpy to 2.x, which breaks MediaPipe 0.10.14.
- **CI** runs the AU tests without the extra (the "AUs off" path) on Ubuntu,
  macOS and Windows, and once with the real model on the CPU on Ubuntu. GPUs
  can only be checked on the machines themselves, with `bench_aus`.
- **Tests:** `tests/test_aus.py` uses a mock detector. It covers device
  choice, profiles, the "off" path, never blocking, the max rate, pausing when
  too slow, staleness, windows and the cross-checks.
