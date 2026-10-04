"""The robot, behind a small interface, in three flavours.

    DryRunBackend     no robot at all. Records every pose it is sent. Used by
                      the tests, and on any laptop without the robot.
    SimBackend        the MuJoCo simulator: a daemon started on THIS machine
                      with `reachy-mini-daemon --sim` (macOS: via mjpython).
    RealRobotBackend  the actual Reachy Mini, over the network.

The simulator is not a separate API: the SDK's sim is the same daemon running
MuJoCo instead of motors, and the same client talks to both. So Sim and Real
share one implementation, and differ only in which daemon they insist on.
Each refuses to run against the other kind -- so nobody drives the real robot
believing it is the simulator.

Which backend is used comes from --backend or EEPY_BACKEND, defaulting to dry
(see `make_backend`). Nothing moves a robot unless someone asked for it.

Two SDK details shape the SDK backend, both found by reading its source:

  * Sounds go through the daemon's REST API (/api/media/sounds/upload and
    /api/media/play_sound), not through `ReachyMini.media`. Over the network,
    the SDK's own play_sound uploads the whole file on every call, on the
    calling thread. Here a file is uploaded once and then played by name, on
    the player's audio thread, so a sound never stalls the motion.
  * The client is created with media_backend="default", NOT "no_media" --
    even though the player needs no camera or microphone. "no_media" tells
    the daemon to RELEASE its audio hardware, after which the robot cannot
    play any sound at all.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import numpy as np

from .pose import NEUTRAL, Pose

log = logging.getLogger("eepy.backend")

BACKEND_ENV = "EEPY_BACKEND"
BACKEND_NAMES = ("dry", "sim", "real")

# What the SDK's goto_sleep() leaves the robot in, decoded from
# reachy_mini/reachy_mini.py (SLEEP_HEAD_POSE, SLEEP_ANTENNAS_JOINT_POSITIONS).
SLEEP_POSE = Pose(pitch=24.4, x=-21.0, z=-44.0, antenna_right=-174.8, antenna_left=174.8)


class BackendError(RuntimeError):
    """Could not connect, or connected to the wrong thing. Message is for humans."""


class Backend(Protocol):
    name: str

    def connect(self) -> Pose:
        """Connect, and return the pose the robot is actually in."""
        ...

    def send(self, pose: Pose) -> None:
        """Command a pose, right now. Must return quickly: called 50x a second."""
        ...

    def read_pose(self) -> Pose:
        """The pose the robot reports."""
        ...

    def play_sound(self, path: Path) -> None:
        """Play a sound file. May be slow: called off the motion thread."""
        ...

    def stop_sound(self) -> None:
        ...

    def run_action(self, action: str) -> Pose:
        """Run a built-in SDK routine (goto_sleep, wake_up) to completion.

        Blocks. Returns the pose the robot ends up in.
        """
        ...

    def is_connected(self) -> bool:
        """Is the link to the robot alive right now? Must be cheap: polled often.

        Without this, a dropped connection is only noticed the next time a pose
        is sent -- so a robot sitting quietly would look fine while unreachable.
        """
        ...

    def close(self) -> None:
        ...


# --- Dry run --------------------------------------------------------------


class DryRunBackend:
    """No robot. Remembers everything it was told, with monotonic timestamps.

    `start` is the pose to pretend the robot is in at connect. `action_seconds`
    is how long goto_sleep / wake_up pretend to take -- short by default so
    tests stay fast. `echo` prints each pose as it is sent (very chatty).
    """

    name = "dry"

    def __init__(self, start: Pose = NEUTRAL, action_seconds: float = 0.05,
                 echo: bool = False) -> None:
        self.pose = start
        self.action_seconds = action_seconds
        self.echo = echo
        self.sent: list[tuple[float, Pose]] = []
        self.sounds: list[tuple[float, str]] = []
        self.actions: list[tuple[float, str]] = []
        self.sound_stops: list[float] = []
        self.connected = False
        self._lock = threading.Lock()

    def connect(self) -> Pose:
        self.connected = True
        return self.pose

    def send(self, pose: Pose) -> None:
        with self._lock:
            self.sent.append((time.monotonic(), pose))
            self.pose = pose
        if self.echo:
            print(f"    [dry] {pose}")

    def read_pose(self) -> Pose:
        return self.pose

    def play_sound(self, path: Path) -> None:
        with self._lock:
            self.sounds.append((time.monotonic(), Path(path).name))

    def stop_sound(self) -> None:
        with self._lock:
            self.sound_stops.append(time.monotonic())

    def run_action(self, action: str) -> Pose:
        with self._lock:
            self.actions.append((time.monotonic(), action))
        time.sleep(self.action_seconds)
        self.pose = SLEEP_POSE if action == "goto_sleep" else NEUTRAL
        return self.pose

    def is_connected(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.connected = False


# --- The SDK: simulator and real robot -------------------------------------


def _load_connection_module():
    """robot/connection.py, by file path -- see limits.py for why."""
    path = Path(__file__).resolve().parents[2] / "robot" / "connection.py"
    spec = importlib.util.spec_from_file_location("_eepy_robot_connection", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_eepy_robot_connection"] = module
    spec.loader.exec_module(module)
    return module


class _SDKBackend:
    """Shared by SimBackend and RealRobotBackend."""

    name = "sdk"
    expect_simulation: bool = False

    def __init__(self, connection_kwargs: dict | None = None) -> None:
        self._kwargs = dict(connection_kwargs or {})
        self.mini = None
        self._http: str | None = None
        self._no_media = False
        self._uploaded: dict[Path, tuple[float, str]] = {}
        # Sounds (player's audio thread) and speech (speaker's thread) upload
        # concurrently.
        self._upload_lock = threading.Lock()
        self._warned_no_media = False

    def connect(self) -> Pose:
        try:
            from reachy_mini import ReachyMini
        except ImportError as exc:
            raise BackendError(
                "The robot SDK (reachy_mini) is not installed in this environment.\n"
                "Activate .venv-robot, or use --backend dry."
            ) from exc
        try:
            # automatic_body_yaw=False: the body turns only when a behaviour
            # says so. Our own safety check enforces the 65 deg neck rule.
            self.mini = ReachyMini(
                automatic_body_yaw=False, media_backend="default", **self._kwargs
            )
        except Exception as exc:
            raise BackendError(self._connect_hint(exc)) from exc

        status = self.mini.client.get_status()
        is_sim = bool(getattr(status, "simulation_enabled", False))
        if is_sim != self.expect_simulation:
            self.close()
            if is_sim:
                raise BackendError(
                    "You asked for the REAL robot, but the daemon you reached is "
                    "running the SIMULATOR.\nUse --backend sim, or stop the "
                    "simulator and start the real robot's daemon."
                )
            raise BackendError(
                "You asked for the SIMULATOR, but the daemon you reached is "
                "driving a REAL robot.\nUse --backend real if that is what you "
                "meant -- or start the simulator first:\n"
                "    mjpython $(which reachy-mini-daemon) --sim"
            )
        self._no_media = bool(getattr(status, "no_media", False))
        self._http = f"http://{self.mini.client.host}:{self.mini.client.port}"
        # enable_motors() pins every target to the present pose before
        # switching torque on, so this cannot make the robot jump.
        self.mini.enable_motors()
        return self.read_pose()

    def _connect_hint(self, exc: Exception) -> str:
        return (
            f"Could not connect to the Reachy Mini daemon ({exc}).\n"
            "Is the daemon running? Start it first, in another terminal."
        )

    def send(self, pose: Pose) -> None:
        self.mini.set_target(
            head=pose.head_matrix(),
            antennas=[float(np.deg2rad(pose.antenna_right)),
                      float(np.deg2rad(pose.antenna_left))],
            body_yaw=float(np.deg2rad(pose.body_yaw)),
        )

    def read_pose(self) -> Pose:
        head = self.mini.get_current_head_pose()
        joints, antennas = self.mini.get_current_joint_positions()
        return Pose.from_robot(head, antennas, joints[0])

    def upload_sound(self, path: Path) -> str:
        """Put a clip on the daemon, once. Returns its name there.

        Uploaded again only if the file changed. Speech pre-uploads every
        cached line at connect, so the first time a line is spoken it does not
        wait for an upload over Wi-Fi.
        """
        import requests

        path = Path(path)
        mtime = path.stat().st_mtime
        with self._upload_lock:
            cached = self._uploaded.get(path)
            if cached is not None and cached[0] == mtime:
                return cached[1]
        with path.open("rb") as handle:
            reply = requests.post(f"{self._http}/api/media/sounds/upload",
                                  files={"file": (path.name, handle)}, timeout=10)
        reply.raise_for_status()
        remote = reply.json().get("path", path.name)
        with self._upload_lock:
            self._uploaded[path] = (mtime, remote)
        return remote

    def play_sound(self, path: Path) -> None:
        import requests

        if self._no_media:
            if not self._warned_no_media:
                log.warning("The daemon was started with --no-media, so it cannot "
                            "play sounds. Sounds are skipped.")
                self._warned_no_media = True
            return
        remote = self.upload_sound(Path(path))
        requests.post(f"{self._http}/api/media/play_sound",
                      json={"file": remote}, timeout=5).raise_for_status()

    def stop_sound(self) -> None:
        import requests

        if self._no_media or self._http is None:
            return
        try:
            requests.post(f"{self._http}/api/media/stop_sound", timeout=2)
        except Exception as exc:
            log.warning("could not stop the sound: %s", exc)

    def run_action(self, action: str) -> Pose:
        getattr(self.mini, action)()
        return self.read_pose()

    def is_connected(self) -> bool:
        # The SDK has no public "am I connected" call. Its websocket client
        # flips the private _is_alive flag the moment its receive loop dies,
        # which is exactly the signal wanted, and reading it costs nothing.
        # If a future SDK renames it, this degrades to "assume connected" --
        # the same as before this check existed -- rather than to a crash.
        if self.mini is None:
            return False
        return bool(getattr(self.mini.client, "_is_alive", True))

    def close(self) -> None:
        if self.mini is None:
            return
        # Closing is best effort: we are often here BECAUSE the link died, and
        # a failure to tidy up a dead connection must not hide the real error.
        try:
            self.mini.set_automatic_body_yaw(True)  # the SDK's default, restored
        except Exception as exc:  # noqa: BLE001
            log.debug("could not restore automatic body yaw: %s", exc)
        try:
            self.mini.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not close the SDK client cleanly: %s", exc)
        self.mini = None


class SimBackend(_SDKBackend):
    """The MuJoCo simulator, running on this machine."""

    name = "sim"
    expect_simulation = True

    def __init__(self) -> None:
        # localhost_only: never wander off to a robot on the network.
        super().__init__({"connection_mode": "localhost_only"})

    def _connect_hint(self, exc: Exception) -> str:
        return (
            f"Could not reach the simulator ({exc}).\n"
            "Start it first, in another terminal, from the EEPY folder:\n"
            "    source .venv-robot/bin/activate\n"
            "    mjpython $(which reachy-mini-daemon) --sim        (macOS)\n"
            "    reachy-mini-daemon --sim                          (Linux / Windows)"
        )


class RealRobotBackend(_SDKBackend):
    """The real Reachy Mini. Address from --robot-host or REACHY_MINI_HOST."""

    name = "real"
    expect_simulation = False

    def __init__(self, robot_host: str | None = None) -> None:
        connection = _load_connection_module()
        super().__init__(connection.connection_kwargs(SimpleNamespace(robot_host=robot_host)))

    def _connect_hint(self, exc: Exception) -> str:
        return (
            f"Could not reach the robot ({exc}).\n"
            "Check it is switched on and on the same network, and that "
            "REACHY_MINI_HOST in .env (or --robot-host) is its IP address."
        )


def make_backend(name: str | None = None, robot_host: str | None = None) -> Backend:
    """Build the backend named by `name`, or by $EEPY_BACKEND, or dry."""
    chosen = (name or os.environ.get(BACKEND_ENV) or "dry").strip().lower()
    if chosen == "dry":
        return DryRunBackend()
    if chosen == "sim":
        return SimBackend()
    if chosen == "real":
        return RealRobotBackend(robot_host)
    raise BackendError(
        f'"{chosen}" is not a backend. Use one of: {", ".join(BACKEND_NAMES)} '
        f"(from --backend, or {BACKEND_ENV} in .env)."
    )
