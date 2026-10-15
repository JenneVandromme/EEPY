"""
Real-hardware-only helper for eye_gaze_follow.py (no --sim).

Copied from BURTRAM (scripts/reachy_mini/eye_gaze_camera_relay.py) and
adapted to EEPY: runs in .venv-robot, the robot's address comes from
robot/connection.py, and the shared files live in the system temp folder.

Not needed for --sim, the laptop webcam is read directly there.

Why this is a separate process: reachy-mini hard-requires numpy>=2.2.5;
mediapipe (used for eye detection in eye_gaze_follow.py) crashes under
numpy 2.x (the reason EEPY has two venvs, see the README). The two can
never share one Python process. The daemon's REST API covers motion and pose
queries without needing reachy_mini at all (see eye_gaze_follow.py's "Why
REST, not the SDK"), but reading a camera frame has no REST equivalent,
only the SDK can do it. This script's only job is running that one call in
a loop and handing frames to eye_gaze_follow.py through a shared file,
written atomically (write to a temp path, then os.replace) so the reader
never sees a half-written frame.

It uses mini.media.get_frame_jpeg() (already-encoded JPEG bytes straight
from the daemon's WebRTC client), not get_frame() + a re-encode. That's
deliberate: this process runs in the reachy_mini venv (numpy>=2.2.5),
where opencv isn't installed and can't be without reintroducing exactly
the numpy conflict the venv split exists to avoid. get_frame_jpeg() means
no opencv/PIL dependency here at all — the bytes are just written to disk,
and eye_gaze_follow.py's cv2.imread (in the other venv) decodes them.

Run in its own terminal, in .venv-robot, alongside eye_gaze_follow.py
(no --sim, in .venv-vision) in a second terminal:
    source .venv-robot/bin/activate
    python robot/eye_gaze_camera_relay.py --robot-host 192.168.50.95

Target selection (a local daemon, or a wireless robot via --robot-host /
$REACHY_MINI_HOST) is handled by connection.py, same as every other
script in robot/.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time

import numpy as np
from reachy_mini import ReachyMini

import connection

# The system temp folder rather than /tmp, so it also works on Windows.
FRAME_PATH = os.path.join(tempfile.gettempdir(), "reachy_eye_frame.jpg")
# Same directory as FRAME_PATH so os.replace() is an atomic rename, not a
# cross-filesystem copy. The bytes are already-encoded JPEG, so the
# extension of the temp path doesn't matter here (unlike the cv2.imwrite
# path this replaced, where it did).
TMP_PATH = FRAME_PATH + ".tmp"

# Sidecar with the camera's calibrated intrinsics for the *streamed*
# resolution (the SDK scales its factory calibration to whatever frame size
# it's actually delivering). eye_gaze_follow.py reads this so its aim is a
# real angle, not a guessed FOV — it can't get it itself without importing
# reachy_mini (numpy>=2.2.5, the conflict the venv split exists for).
INTRINSICS_PATH = os.path.join(tempfile.gettempdir(), "reachy_cam_intrinsics.json")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Relay Reachy Mini's camera to a shared frame file.")
    connection.add_arg(p)
    return p.parse_args()


def write_intrinsics(mini: ReachyMini) -> None:
    """Drop the streamed camera's K/D/resolution next to the frame file, so
    eye_gaze_follow.py can aim in real angles. Best effort: a missing sidecar
    just makes the follower fall back to a rough FOV guess."""
    cam = getattr(mini.media, "camera", None)
    if cam is None or getattr(cam, "K", None) is None:
        return
    payload = {
        "K": np.asarray(cam.K, dtype=float).tolist(),
        "D": np.asarray(cam.D, dtype=float).tolist() if getattr(cam, "D", None) is not None else [],
        "width": int(cam.resolution[0]),
        "height": int(cam.resolution[1]),
    }
    tmp = INTRINSICS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, INTRINSICS_PATH)
    print(f"Wrote camera intrinsics to {INTRINSICS_PATH}")


def main() -> None:
    args = parse_args()
    with ReachyMini(media_backend="default", **connection.connection_kwargs(args)) as mini:
        write_intrinsics(mini)
        print(f"Relaying Reachy's camera to {FRAME_PATH} for eye_gaze_follow.py...")
        print("Press Ctrl+C to stop.")
        while True:
            jpeg = mini.media.get_frame_jpeg()
            if not jpeg:
                # WebRTC stream not delivering frames yet (e.g. right after
                # connecting), not an error, just wait for the next one.
                time.sleep(0.03)
                continue
            with open(TMP_PATH, "wb") as f:
                f.write(jpeg)
            os.replace(TMP_PATH, FRAME_PATH)
            time.sleep(0.03)  # ~30fps cap, no need to hammer the daemon faster


if __name__ == "__main__":
    main()
