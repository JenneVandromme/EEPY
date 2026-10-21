# Launcher UI mockups, v5

These are design mockups for a new EEPY launcher UI, exported from the Claude Design canvas on 2026-10-19 (version 1792335045-7327). They are designs, not code: nothing in the launcher uses them yet.

## What's here

| Files | Screens |
|---|---|
| `V5-Home` | Home: Eepy greets you, then Start a session or Same as last time |
| `V5-S1-Setup` | Step 1: participant code, which robot, who decides, self-check |
| `V5-S2a-Starting`, `V5-S2a-Problem` | Waking up (loading only), and what happens if a part doesn't start |
| `V5-S2b-Calibration` | Your desk: pick a saved calibration or teach a new one |
| `V5-S2b-1` … `V5-S2b-8` | The desk check step by step (face, gaze dots, a minute of normal work), with a "something went wrong" screen after each step |
| `V5-S2-Control` | The control panel during a session, with a Live HMM tab |
| `V5-S3-WrapUp` | Session complete: results and the link into Analyze |
| `V5-A1-Sessions`, `V5-A2-Story`, `V5-A3-Learned` | Analyze: the list of sessions, one session's story, what Eepy learned per person |
| `V5-Advanced` | Advanced tools for developers |
| `V5-Flow` | How every screen connects, plus a feasibility key |
| `canvas.json` | The canvas layout: where each screen sits, and their titles |
| `ds/designsystem/` | The design system the screens use (tokens and components) |

## How to view them

The `.dc.html` files are Claude Design canvas sources. They need the canvas runtime (`support.js`, which isn't in this folder) and the image assets the canvas stores (the `/_blob/...` links). So they won't render correctly if you open them straight from disk.

To see and click through them, open the canvas. The owner can share it: https://claude.ai/artifact/QaDZYRxs87XEbivvv96x5x (page "v5 · Eepy design system (current)").

## Feasibility key (used in V5-Flow)

- ✓ works with data or APIs EEPY has today
- ◐ the data or API exists, but the launcher needs a small pass-through. Examples: the camera picture in the launcher, the Reachy volume.
- ✚ needs a change in EEPY's code, for example saved desks by name

Every error message on the desk-check screens is taken from the real code:

- `eepy/perception/calibration/face_check.py`
- `eepy/perception/calibration/flow.py`
- `eepy/perception/calibration/baseline.py`
- `eepy/launcher/__main__.py`
