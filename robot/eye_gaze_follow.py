"""
Make Reachy Mini's head turn to look into your eyes, in the vision venv
(.venv-vision: mediapipe + opencv), driving the robot over the daemon's REST
API rather than the reachy_mini Python package.

Copied from BURTRAM (scripts/reachy_mini/eye_gaze_follow.py) and adapted to
EEPY's two venvs: no container, no --sim-mac / webcam relay (the laptop
webcam and the window open natively here), the daemon address comes from
robot/connection.py like every other script in robot/. See "Why REST, not the SDK"
below for why that split exists, it isn't a style choice.

Matches both where your eyes are (yaw/pitch, aiming the head) and how
they're tilted (roll, around the aim direction), not just their midpoint,
see get_eye_target()'s roll computation and ROLL_SIGN's comment (untested,
may need flipping).

If the head holds roughly the same direction for STABLE_SECONDS, the body
turns to catch up and face the same way, ramped smoothly over
BODY_TURN_DURATION. Every value actually sent, head pose and body yaw
alike, is funneled through safety_limits.enforce() first, the single
source of truth for all four of Reachy Mini's physical limits (head
pitch/roll, head yaw, body yaw, and the head-vs-body yaw delta), see that
module for why each one matters and why they're checked together, not as
separate, easy-to-forget checks scattered across scripts.

--sim (this test's actual design):
    Your laptop webcam stands in for a big, wide-angle camera. Reachy's own
    (much narrower) camera is represented as a rectangle drawn over that
    wide frame, sized and centered from the head's *actual current* pose
    (queried from the daemon, not just the last command sent), so the
    rectangle visually lags and catches up exactly like the physical head
    would. Detection and the overlay both run on the same webcam frame.

Real hardware (no --sim, the default):
    Reachy's own camera frame is the only frame there is, no rectangle to
    draw, "what Reachy sees" and "what we detect on" are the same image.
    Getting that frame here requires eye_gaze_camera_relay.py running in a
    second terminal, see "Why REST, not the SDK" below for why.

Why REST, not the SDK:
    reachy-mini hard-requires numpy>=2.2.5. mediapipe (and opencv) crash
    under numpy 2.x, which is why EEPY has two venvs (.venv-vision and
    .venv-robot, see the README). So this script and the reachy_mini
    package can never share one Python process. The
    daemon's REST API (confirmed against
    reachy_mini/daemon/app/routers/{state,move}.py in the SDK source) sidesteps
    that for motion: /api/state/present_head_pose and /api/move/set_target
    are plain HTTP, no reachy_mini import needed here at all. The one thing
    REST can't do is read a camera frame, that's SDK-only
    (mini.media.get_frame()), which is the entire reason
    eye_gaze_camera_relay.py exists as a separate process for real hardware.

Why the look-at math is duplicated here instead of imported:
    reachy_mini.vision.look_at.look_at_image_pose does exactly this
    computation already, but importing it means importing reachy_mini,
    which means numpy>=2.2.5, which breaks mediapipe in this same process.
    So the small, pure numpy/scipy math (not the SDK call) is copied here,
    adapted for a caller-supplied synthetic camera instead of the real
    calibrated one. This is the one deliberate exception to "don't
    double-write code": the alternative (a third process just for math) is
    worse than a ~15-line duplicated function with this comment attached.

Setup: runs in .venv-vision, which already has mediapipe 0.10.14 (the
legacy mp.solutions.face_mesh API this uses), opencv, numpy<2 and scipy.
    source .venv-vision/bin/activate
    python robot/eye_gaze_follow.py --sim --camera 0      # simulator + laptop webcam
    python robot/eye_gaze_follow.py --robot-host 192.168.50.95
                                    # real robot, needs robot/eye_gaze_camera_relay.py
                                    # running too (in .venv-robot)
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import tempfile
import time

import cv2
import mediapipe as mp
import numpy as np
from scipy.spatial.transform import Rotation as R

import connection
import safety_limits

# Written by eye_gaze_camera_relay.py. The system temp folder rather than
# /tmp, so it also works on Windows.
RELAY_FRAME_PATH = os.path.join(tempfile.gettempdir(), "reachy_eye_frame.jpg")
# Written by eye_gaze_camera_relay.py: the streamed camera's calibrated
# intrinsics. Real-hardware aiming reads fx/fy/cx/cy from here so the head
# is steered by a true measured angle instead of a guessed field of view.
CAM_INTRINSICS_PATH = os.path.join(tempfile.gettempdir(), "reachy_cam_intrinsics.json")

# Same eye-corner landmarks BURTRAM's face_landmark_extractor.py uses as an
# inter-eye reference (see that script's "Scale invariance note"), reused
# here as the gaze target: their midpoint is a good stand-in for "between
# the eyes" without needing iris-refined landmarks.
LEFT_EYE_OUTER = 33
RIGHT_EYE_OUTER = 263


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Make Reachy Mini's head follow your eyes.")
    p.add_argument("--sim", action="store_true",
                    help="use the laptop webcam as a wide-angle stand-in and draw "
                         "the 'what Reachy would see' rectangle. Omit for real "
                         "hardware (needs eye_gaze_camera_relay.py running too).")
    connection.add_arg(p)
    p.add_argument("--camera", type=int, default=0,
                    help="webcam device index, --sim only (default: 0).")
    p.add_argument("--wide-fov-deg", type=float, default=78.0,
                    help="assumed horizontal FOV of the laptop webcam, --sim "
                         "only (default: 78, a typical laptop webcam)")
    p.add_argument("--reachy-fov-deg", type=float, default=50.0,
                    help="assumed horizontal FOV of Reachy's own camera, used for "
                         "the --sim overlay rectangle and as a fallback if the "
                         "relay's calibrated-intrinsics sidecar isn't found "
                         "(default: 50; real-hardware runs use the sidecar).")
    p.add_argument("--aim-gain", type=float, default=1.0,
                    help="real-hardware only: fraction of the measured face-offset "
                         "angle to command per frame (default 1.0 = aim straight at "
                         "the face; lower is gentler). Smoothing is applied on top.")
    p.add_argument("--aim-deadzone", type=float, default=1.5,
                    help="real-hardware only: don't chase a face within this many "
                         "degrees of centre (default 1.5), to stop micro-hunting.")
    p.add_argument("--invert-yaw", action="store_true",
                    help="real-hardware only: flip the horizontal steering direction "
                         "if the head turns away from the face instead of toward it.")
    p.add_argument("--invert-pitch", action="store_true",
                    help="real-hardware only: flip the vertical steering direction.")
    p.add_argument("--daemon-host", default=None,
                    help="explicit Reachy Mini daemon REST API host:port. Normally "
                         "left unset: it defaults to localhost:8000, or the wireless "
                         "robot's address with --robot-host / $REACHY_MINI_HOST.")
    p.add_argument("--smoothing", type=float, default=SMOOTHING_ALPHA,
                    help=f"head smoothing, 0..1 (default {SMOOTHING_ALPHA}): fraction of "
                         "the remaining angle to the target closed per frame. Lower is "
                         "calmer and laggier, 1.0 disables smoothing.")
    p.add_argument("--max-head-speed", type=float, default=MAX_HEAD_SPEED_DEG,
                    help=f"cap on commanded head angular speed, deg/s "
                         f"(default {MAX_HEAD_SPEED_DEG:.0f}).")
    args = p.parse_args()
    if args.daemon_host is None:
        args.daemon_host = connection.daemon_host_str(args)
    return args


def synthetic_camera_matrix(width: int, height: int, hfov_deg: float) -> np.ndarray:
    """Simple pinhole K for an assumed horizontal FOV, square pixels, zero distortion."""
    hfov = np.deg2rad(hfov_deg)
    fx = (width / 2) / np.tan(hfov / 2)
    return np.array([
        [fx, 0, width / 2],
        [0, fx, height / 2],
        [0, 0, 1],
    ], dtype=np.float64)


def look_at_world_pose(direction: np.ndarray) -> np.ndarray:
    """Head pose whose local +X axis points at `direction` (x-fwd, y-left, z-up).

    Adapted from reachy_mini.vision.look_at.look_at_world_pose, see the
    module docstring for why this is a copy, not an import.
    """
    target = direction / np.linalg.norm(direction)
    straight = np.array([1.0, 0.0, 0.0])
    axis = np.cross(straight, target)
    axis_norm = np.linalg.norm(axis)
    if axis_norm < 1e-8:
        rot = np.eye(3) if np.dot(straight, target) > 0 else R.from_rotvec(
            np.pi * np.array([0.0, 0.0, 1.0])
        ).as_matrix()
    else:
        axis /= axis_norm
        angle = np.arccos(np.clip(np.dot(straight, target), -1.0, 1.0))
        rot = R.from_rotvec(angle * axis).as_matrix()
    pose = np.eye(4)
    pose[:3, :3] = rot
    return pose


# Camera convention (x-right, y-down, z-fwd) -> head/world convention
# (x-fwd, y-left, z-up). This is reachy_mini's own DEFAULT_HEAD_TO_CAMERA_TRANSFORM
# rotation (only its rotation matters here, translation drops out of the
# (A@B)[:3,:3] product in the original look_at_image_pose). Reused here as a
# simplifying assumption for the --sim wide webcam too, since it's just "some
# camera pointed roughly forward", same as Reachy's own. If the head ends up
# turning the wrong way relative to where the eyes actually are once this is
# tested, this is the matrix to correct, not the overall architecture.
CAM_TO_HEAD_ROT = np.array([
    [0.0, 0.0, 1.0],
    [-1.0, 0.0, 0.0],
    [0.0, -1.0, 0.0],
])


def look_at_pixel_pose(u: float, v: float, K: np.ndarray, camera_world_rotation: np.ndarray) -> np.ndarray:
    """Head pose that looks at pixel (u, v), assuming zero lens distortion.

    `camera_world_rotation` is the 3x3 rotation that turns a camera-frame ray
    into world/head convention *at the moment this pixel was captured*:
    - Real hardware: Reachy's own camera moves with the head, so this is
      current_head_pose[:3,:3] @ CAM_TO_HEAD_ROT (matches
      reachy_mini.vision.look_at.look_at_image_pose's T_world_head usage).
    - --sim: the wide webcam is fixed in the world, it never moves, so this
      is just CAM_TO_HEAD_ROT alone, current head pose plays no part in
      where a given wide-frame pixel points.

    Adapted from reachy_mini.vision.look_at.look_at_image_pose, simplified
    for a synthetic (distortion-free) camera, see the module docstring for
    why this is a copy, not an import.
    """
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    ray_cam = np.array([(u - cx) / fx, (v - cy) / fy, 1.0])
    ray_world = camera_world_rotation @ ray_cam
    return look_at_world_pose(ray_world)


def read_relay_frame(path: str, relay_script_name: str):
    """Read the latest frame written by a relay process, or None if not ready yet.

    Used for the real-hardware relay (eye_gaze_camera_relay.py).
    """
    if not os.path.exists(path):
        print(f"Waiting for {relay_script_name} to write a frame...")
        time.sleep(0.5)
        return None
    return cv2.imread(path)


# One kept-alive HTTP connection per daemon host, reused across calls.
# urllib opens a fresh TCP connection every call; measured directly, when
# eye_gaze_camera_relay.py's WebRTC video stream is also running over
# Wi-Fi, roughly one handshake in ten stalls for a full second (its SYN
# loses out to the video packets), which is what made the tracking loop
# crawl. Reusing one connection keeps p90 REST latency around 15 ms.
DAEMON_ERRORS = (OSError, http.client.HTTPException)
_daemon_conns: dict[str, http.client.HTTPConnection] = {}


def rest_call(daemon_host: str, method: str, path: str, payload: dict | None = None) -> bytes:
    """One REST call to the daemon over its kept-alive connection.

    Retries once on a connection error: a pooled socket can go stale (idle
    timeout, daemon restart) and only fail when next used, so drop it and
    reconnect before giving up. Raises on the second failure; callers catch
    DAEMON_ERRORS.
    """
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    for attempt in (1, 2):
        conn = _daemon_conns.get(daemon_host)
        if conn is None:
            host, _, port = daemon_host.partition(":")
            conn = http.client.HTTPConnection(host, int(port or 8000), timeout=1.0)
            _daemon_conns[daemon_host] = conn
        try:
            conn.request(method, path, body=body, headers=headers)
            return conn.getresponse().read()
        except DAEMON_ERRORS:
            conn.close()
            _daemon_conns.pop(daemon_host, None)
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def get_head_pose(daemon_host: str) -> np.ndarray:
    data = rest_call(daemon_host, "GET", "/api/state/present_head_pose?use_pose_matrix=true")
    return np.array(json.loads(data)["m"], dtype=np.float64).reshape(4, 4)


def send_head_pose(daemon_host: str, pose: np.ndarray, body_yaw_rad: float | None = None) -> None:
    payload = {"target_head_pose": {"m": pose.flatten().tolist()}}
    if body_yaw_rad is not None:
        payload["target_body_yaw"] = body_yaw_rad
    rest_call(daemon_host, "POST", "/api/move/set_target", payload)


def glide_to_neutral(daemon_host: str, head_rpy, body_yaw: float,
                      antennas: tuple[float, float] | None = None,
                      duration: float = 1.2, hz: float = 30.0) -> None:
    """Ease the head to level and the body (and optionally the antennas) to
    zero over `duration` seconds, then leave it there. Runs on exit so an
    activity always ends in the same neutral pose instead of frozen mid-gaze.

    Uses the same set_target path and safety_limits.enforce() funnel as the
    tracking loop, stepped through a raised-cosine ease so it's a smooth
    settle, not a snap. Silently stops if the daemon becomes unreachable
    (which is often *why* we're quitting).
    """
    head_rpy = np.asarray(head_rpy, dtype=np.float64)
    steps = max(1, round(duration * hz))
    prev_body_yaw = body_yaw
    for i in range(1, steps + 1):
        # raised cosine: 0 -> 1 with zero slope at both ends
        s = 0.5 - 0.5 * np.cos(np.pi * i / steps)
        rpy = head_rpy * (1.0 - s)
        target_body_yaw = body_yaw * (1.0 - s)
        pose = np.eye(4)
        pose[:3, :3] = R.from_euler("xyz", rpy).as_matrix()
        safe_pose, safe_body_yaw = safety_limits.enforce(pose, target_body_yaw, prev_body_yaw)
        prev_body_yaw = safe_body_yaw if safe_body_yaw is not None else prev_body_yaw
        payload = {"target_head_pose": {"m": safe_pose.flatten().tolist()},
                   "target_body_yaw": safe_body_yaw}
        if antennas is not None:
            payload["target_antennas"] = [a * (1.0 - s) for a in antennas]
        try:
            rest_call(daemon_host, "POST", "/api/move/set_target", payload)
        except DAEMON_ERRORS:
            return
        time.sleep(1.0 / hz)


# If the head holds roughly the same direction for this long, turn the body
# to catch up and face the same way, per the user's request.
STABLE_SECONDS = 2.0
# Detection jitter alone shouldn't reset the stability timer, only an
# actual, meaningful head movement should.
STABLE_TOLERANCE_DEG = 5.0
# How long the body's catch-up turn takes, smoothly, not a snap.
BODY_TURN_DURATION = 1.5

# Head-tracking smoothing. The daemon's set_target is instant, so sending
# the raw look-at pose every frame makes the head snap to every mediapipe
# jitter — that's the "way too fast" twitchiness. Instead the commanded
# orientation eases toward the target: close SMOOTHING_ALPHA of the
# remaining angle each frame, then clamp the result to MAX_HEAD_SPEED_DEG
# deg/s. Both are overridable with --smoothing / --max-head-speed.
SMOOTHING_ALPHA = 0.15
MAX_HEAD_SPEED_DEG = 150.0


def smooth_towards(current_rpy: np.ndarray, target_rpy: np.ndarray, dt: float,
                    alpha: float, max_rad_per_s: float) -> np.ndarray:
    """Ease a commanded roll/pitch/yaw triple toward a target: an
    exponential step (alpha * remaining error) capped at a maximum angular
    speed. Each component's error is wrapped to (-pi, pi] first, so yaw near
    +-180 deg takes the short way round instead of unwinding the long way.
    """
    err = np.arctan2(np.sin(target_rpy - current_rpy), np.cos(target_rpy - current_rpy))
    step = np.clip(alpha * err, -max_rad_per_s * dt, max_rad_per_s * dt)
    return current_rpy + step


def load_stream_intrinsics(path: str) -> tuple[float, float, float, float] | None:
    """(fx, fy, cx, cy) for the streamed frames, from the sidecar
    eye_gaze_camera_relay.py writes out of the SDK's calibrated,
    resolution-scaled values. None if it isn't there (relay not started, or
    --sim), in which case the caller falls back to a pinhole guess.
    """
    try:
        with open(path) as f:
            K = json.load(f)["K"]
        return float(K[0][0]), float(K[1][1]), float(K[0][2]), float(K[1][2])
    except (OSError, KeyError, ValueError, IndexError, TypeError):
        return None


def aim_at_face(u: float, v: float, roll: float,
                 intrinsics: tuple[float, float, float, float],
                 current_pose: np.ndarray, gain: float, deadzone_deg: float,
                 invert_yaw: bool = False, invert_pitch: bool = False) -> np.ndarray:
    """Target (roll, pitch, yaw) that steers the head to bring the face
    pixel (u, v) onto the camera's optical axis — i.e. centre the person in
    Reachy's own view.

    Closed loop: the aim is expressed relative to `current_pose` (the head's
    *actual* orientation this frame), so repeated calls converge the face to
    centre even though the lens is wide and distorted and the calibration is
    only approximate — the error just gets smaller each frame until it sits
    inside the deadzone. `gain` (0..1) is how much of the measured offset to
    command per call; the outer smoothing loop eases into whatever this
    returns.

    Pitch sign: +pitch tilts the head down (matches the SDK's SLEEP pose and
    create_head_pose). Yaw sign: +yaw turns the head left. Flip either with
    invert_pitch / invert_yaw if the calibrated axes on a given unit differ.
    """
    fx, fy, cx, cy = intrinsics
    ang_x = np.arctan2(u - cx, fx)   # + : face is right of the optical axis
    ang_y = np.arctan2(v - cy, fy)   # + : face is below the optical axis
    dead = np.deg2rad(deadzone_deg)
    ang_x = 0.0 if abs(ang_x) < dead else ang_x
    ang_y = 0.0 if abs(ang_y) < dead else ang_y
    cur_roll, cur_pitch, cur_yaw = R.from_matrix(current_pose[:3, :3]).as_euler("xyz")
    return np.array([
        roll,
        cur_pitch + (-1.0 if invert_pitch else 1.0) * gain * ang_y,
        cur_yaw + (1.0 if invert_yaw else -1.0) * gain * ang_x,
    ])


# Flip this to -1.0 if testing shows the head rolls the opposite way from
# the actual head tilt: mediapipe's landmarks are in image space (y grows
# downward), the world/head convention is z-up, and which sign that flip
# needs depends on webcam mirroring, easier to verify by looking at it than
# to derive with certainty here.
ROLL_SIGN = 1.0


def get_eye_target(frame, face_mesh) -> tuple[float, float, float] | None:
    """Returns (u, v, roll_rad): the midpoint between the eyes to aim at, and
    the tilt angle of the line between them, to match head roll, not just
    gaze direction, to how the eyes are actually tilted.
    """
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = face_mesh.process(rgb)
    if not result.multi_face_landmarks:
        return None
    h, w = frame.shape[:2]
    landmarks = result.multi_face_landmarks[0].landmark
    left = landmarks[LEFT_EYE_OUTER]
    right = landmarks[RIGHT_EYE_OUTER]
    left_px = (left.x * w, left.y * h)
    right_px = (right.x * w, right.y * h)
    # atan2 on actual pixel coordinates, not raw normalized landmark values,
    # since mediapipe normalizes x/y separately by width/height, using the
    # raw values directly would distort the angle on a non-square frame.
    roll = ROLL_SIGN * np.arctan2(right_px[1] - left_px[1], right_px[0] - left_px[0])
    u = (left_px[0] + right_px[0]) / 2
    v = (left_px[1] + right_px[1]) / 2
    return (u, v, roll)


def main() -> None:
    args = parse_args()
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1, min_detection_confidence=0.5, min_tracking_confidence=0.5
    )

    cap = None
    if args.sim:
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            raise SystemExit(f"ERROR: could not open camera at index {args.camera}.")

    K = None                  # synthetic pinhole matrix, --sim overlay only
    aim_intr = None            # (fx, fy, cx, cy) for real-hardware aiming
    aim_intr_calibrated = False  # True once it comes from the relay's sidecar
    last_target = None

    # Body-catch-up state, see STABLE_SECONDS above.
    last_stable_yaw_deg = None
    stable_since = None
    recentered_for_this_stability = False
    current_body_yaw_sent = 0.0
    body_yaw_ramp = None  # (start_value, start_time, target_value) while turning

    # Head-smoothing state, see SMOOTHING_ALPHA / smooth_towards() above.
    # commanded_rpy is what we last told the head to do (not what mediapipe
    # asked for); each frame it eases toward the fresh target.
    commanded_rpy = None
    last_loop_t = time.monotonic()

    print("Press Q to quit.")
    try:
        while True:
            if args.sim:
                ret, frame = cap.read()
                if not ret:
                    continue
            else:
                frame = read_relay_frame(RELAY_FRAME_PATH, "eye_gaze_camera_relay.py")
                if frame is None:
                    continue

            h, w = frame.shape[:2]
            if args.sim and K is None:
                K = synthetic_camera_matrix(w, h, args.wide_fov_deg)
            if not args.sim and not aim_intr_calibrated:
                from_sidecar = load_stream_intrinsics(CAM_INTRINSICS_PATH)
                if from_sidecar is not None:
                    aim_intr, aim_intr_calibrated = from_sidecar, True
                    print(f"Using calibrated camera intrinsics: fx={from_sidecar[0]:.0f} "
                          f"fy={from_sidecar[1]:.0f} cx={from_sidecar[2]:.0f} cy={from_sidecar[3]:.0f}")
                elif aim_intr is None:
                    # Relay sidecar not there yet — fall back to a pinhole
                    # guess from --reachy-fov-deg (rough: the real lens is
                    # ~88 deg and non-square). Keeps re-checking for the
                    # sidecar so it upgrades itself once the relay writes it.
                    f = (w / 2) / np.tan(np.deg2rad(args.reachy_fov_deg) / 2)
                    aim_intr = (f, f, w / 2, h / 2)
                    print(f"camera intrinsics sidecar not found at {CAM_INTRINSICS_PATH}; "
                          f"using a rough {args.reachy_fov_deg:.0f}-deg pinhole guess for now")

            target = get_eye_target(frame, face_mesh)
            overlay = frame.copy()

            if target is not None:
                u, v, roll = target
                try:
                    current_pose = None
                    if args.sim:
                        # Fixed external wide camera, no current-pose dependency:
                        # geometric look-at from the pixel (see look_at_pixel_pose).
                        aim_pose = look_at_pixel_pose(u, v, K, CAM_TO_HEAD_ROT)
                        aim_pose[:3, :3] = aim_pose[:3, :3] @ R.from_euler("x", roll).as_matrix()
                        target_rpy = np.array(R.from_matrix(aim_pose[:3, :3]).as_euler("xyz"))
                    else:
                        # Real hardware: closed-loop centring. Measure how far
                        # the face sits off Reachy's own optical axis and steer
                        # to close that gap; expressed relative to the actual
                        # current pose so it converges despite lens distortion
                        # and calibration slop. See aim_at_face().
                        current_pose = get_head_pose(args.daemon_host)
                        target_rpy = aim_at_face(
                            u, v, roll, aim_intr, current_pose,
                            args.aim_gain, args.aim_deadzone,
                            invert_yaw=args.invert_yaw, invert_pitch=args.invert_pitch,
                        )

                    # Ease the commanded orientation toward the target instead
                    # of snapping to it. dt is clamped so a one-off network or
                    # detection stall can't turn into a lurch.
                    now = time.monotonic()
                    dt = min(0.1, now - last_loop_t)
                    last_loop_t = now
                    if commanded_rpy is None:
                        # Start from where the head actually is (real hw) or
                        # from the target (--sim, no pose query), so the very
                        # first move isn't a jump out of the zero pose.
                        commanded_rpy = (
                            np.array(R.from_matrix(current_pose[:3, :3]).as_euler("xyz"))
                            if current_pose is not None else target_rpy.copy()
                        )
                    commanded_rpy = smooth_towards(
                        commanded_rpy, target_rpy, dt,
                        args.smoothing, np.deg2rad(args.max_head_speed),
                    )
                    new_pose = np.eye(4)
                    new_pose[:3, :3] = R.from_euler("xyz", commanded_rpy).as_matrix()

                    # Has the head been pointing roughly the same way for
                    # STABLE_SECONDS? Track it off the yaw component only
                    # (horizontal), pitch jitter shouldn't trigger a body turn.
                    gaze_yaw_deg = np.degrees(np.arctan2(new_pose[1, 0], new_pose[0, 0]))
                    if (last_stable_yaw_deg is None
                            or abs(gaze_yaw_deg - last_stable_yaw_deg) > STABLE_TOLERANCE_DEG):
                        last_stable_yaw_deg = gaze_yaw_deg
                        stable_since = now
                        recentered_for_this_stability = False
                    elif not recentered_for_this_stability and now - stable_since >= STABLE_SECONDS:
                        # Raw target, not yet clamped, safety_limits.enforce()
                        # below clamps whatever actually gets sent each frame,
                        # so the ramp can ease right up to the limit and stop
                        # there instead of needing to be pre-clamped here too.
                        body_yaw_ramp = (current_body_yaw_sent, now, np.deg2rad(gaze_yaw_deg))
                        recentered_for_this_stability = True

                    body_yaw_to_send = None
                    if body_yaw_ramp is not None:
                        start_val, ramp_start, target_val = body_yaw_ramp
                        t = min(1.0, (now - ramp_start) / BODY_TURN_DURATION)
                        body_yaw_to_send = start_val + (target_val - start_val) * t
                        if t >= 1.0:
                            body_yaw_ramp = None

                    # Single funnel point for every safety limit (head
                    # pitch/roll/yaw, body yaw, and the head-vs-body yaw
                    # delta), see safety_limits.py, not re-implemented here.
                    safe_pose, safe_body_yaw = safety_limits.enforce(
                        new_pose, body_yaw_to_send, current_body_yaw_sent
                    )
                    if safe_body_yaw is not None:
                        current_body_yaw_sent = safe_body_yaw

                    send_head_pose(args.daemon_host, safe_pose, body_yaw_rad=safe_body_yaw)
                    last_target = (u, v)
                except DAEMON_ERRORS as e:
                    print(f"Could not reach the daemon at {args.daemon_host}: {e}")

                cv2.circle(overlay, (int(u), int(v)), 6, (0, 255, 0), -1)
            else:
                cv2.putText(overlay, "no face detected", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)

            if args.sim and last_target is not None:
                # Rectangle centered on the *actual current* head pose
                # (queried fresh below), not just the last commanded pixel,
                # so it visually lags and catches up like the real head would.
                try:
                    current_pose = get_head_pose(args.daemon_host)
                    gaze_dir_world = current_pose[:3, 0]
                    # Reproject the current gaze direction back into the fixed
                    # wide camera's own pixel space (inverse of CAM_TO_HEAD_ROT
                    # alone, no current-pose composition, same reasoning as
                    # look_at_pixel_pose's --sim branch: the wide camera is
                    # fixed, so this mapping never depends on the head's pose).
                    ray_cam = CAM_TO_HEAD_ROT.T @ gaze_dir_world
                    if abs(ray_cam[2]) > 1e-6:
                        u_c = K[0, 0] * (ray_cam[0] / ray_cam[2]) + K[0, 2]
                        v_c = K[1, 1] * (ray_cam[1] / ray_cam[2]) + K[1, 2]
                        rect_w = int(frame.shape[1] * args.reachy_fov_deg / args.wide_fov_deg)
                        rect_h = int(frame.shape[0] * args.reachy_fov_deg / args.wide_fov_deg)
                        cv2.rectangle(
                            overlay,
                            (int(u_c - rect_w / 2), int(v_c - rect_h / 2)),
                            (int(u_c + rect_w / 2), int(v_c + rect_h / 2)),
                            (255, 0, 0), 2,
                        )
                except DAEMON_ERRORS:
                    pass

            cv2.imshow("Eye gaze follow", overlay)
            # 'q' or Esc — but only when the OpenCV window has keyboard
            # focus. If a click on the window doesn't give it focus in
            # your setup, Ctrl+C in the terminal does the same clean exit.
            if (cv2.waitKey(1) & 0xFF) in (ord("q"), ord("Q"), 27):
                break
    except KeyboardInterrupt:
        pass  # Ctrl+C
    finally:
        # Always settle back to a neutral head/body pose on the way out,
        # smoothly, rather than leaving the robot frozen mid-gaze. Skip it
        # only if nothing was ever commanded (no face seen this run).
        if commanded_rpy is not None:
            print("Returning to neutral pose...")
            glide_to_neutral(args.daemon_host, commanded_rpy, current_body_yaw_sent)
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
