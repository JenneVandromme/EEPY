"""The camera relay: robot camera pictures and head pose, from the robot's
virtualenv to the vision process, on THIS computer only.

Why it exists: the robot SDK (the only way to read the robot's camera) and
OpenCV/mediapipe (perception) need incompatible numpy versions, so they live
in two virtualenvs and two processes. The relay is the bridge:

    .venv-robot                                  .venv-vision
    Wizard panel ── CameraRelay ── 127.0.0.1 ──> RelayClient ── RobotCamera
      (or python -m eepy.camera.relay)                        └─ LaptopCamera
                                                                 (head pose only:
                                                                  moves the blue box)

The panel starts a relay by itself; `python -m eepy.camera.relay` runs one
without the panel. It also serves the HEAD POSE for every backend, which is
what moves the laptop camera's blue box in the simulator and dry run.

PRIVACY: it listens on 127.0.0.1 only, so no other computer can connect --
unlike the panel's own web page, which is open to the network on purpose. It
never writes a picture to disk, and keeps only the newest one in memory.

PROTOCOL (one request per line; the connection stays open):
    FRAME\\n  -> {"ok": true, "index": 12, "t": ..., "len": N}\\n + N bytes of JPEG
                (len 0 and index 0 while there is no picture yet)
    POSE\\n   -> {"ok": true, "yaw": .., "pitch": .., "roll": .., "body_yaw": .., "t": ..}
                ({"ok": false} when no pose is known)
    STATUS\\n -> {"ok": true, "camera": "real"|null, "frames": .., "clients": .., ...}
    USER {"yaw": .., "pitch": ..}\\n -> {"ok": true}   (vision -> panel: where the student's
                face is, relative to the robot's gaze; USER null when there is no face)
    SAY "line_id"\\n -> {"ok": .., "spoken": ..}  (vision -> panel: speak a line, A12)
    POLL\\n   -> {"ok": true, "command": {...} | null}  (panel -> vision commands, A12)
    CALIB {...}\\n -> {"ok": true}   (vision -> panel: calibration progress, A12)
    STATE {...}\\n -> {"ok": true}   (vision -> panel: the estimated user state, A13)

Python 3.9, standard library only (the SDK is imported only by main()).
"""

from __future__ import annotations

import json
import logging
import socket
import socketserver
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Tuple, Union

from eepy.logs.clock import monotonic

from .errors import CameraError, robot_unreachable

log = logging.getLogger("eepy.camera.relay")

HOST = "127.0.0.1"   # never anything else: see PRIVACY above

FrameFn = Callable[[], Optional[bytes]]          # newest JPEG, or None
PoseFn = Callable[[], Optional[Dict[str, float]]]


class RelayPortBusy(CameraError):
    pass


class RelayUnavailable(CameraError):
    pass


class CameraRelay:
    """Serves the newest robot camera JPEG and the head pose on 127.0.0.1:port.

    frame_fn   returns the newest JPEG from the robot's camera, or None. It is
               called from the relay's own thread, `fps` times a second. None
               when there is no robot camera (simulator, dry run).
    pose_fn    returns {"yaw", "pitch", "roll", "body_yaw"} in degrees, or None.
    label      what the camera is, for STATUS (e.g. "real"), or a function
               returning that -- None while there is no robot camera.
    """

    def __init__(self, port: int, frame_fn: Optional[FrameFn] = None,
                 pose_fn: Optional[PoseFn] = None, fps: float = 15.0,
                 label: Union[None, str, Callable[[], Optional[str]]] = None,
                 user_fn: Optional[Callable[[Optional[Dict[str, float]]], None]] = None,
                 say_fn: Optional[Callable[[str], bool]] = None,
                 command_fn: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
                 calib_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
                 state_fn: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.port = port
        self.frame_fn = frame_fn
        self.pose_fn = pose_fn
        self.user_fn = user_fn      # receives "where the student's face is" from the vision process
        # The calibration (A12): the vision process asks the panel to SPEAK a line,
        # POLLs it for commands ("calibrate", "reuse"...) and reports progress (CALIB).
        self.say_fn, self.command_fn, self.calib_fn = say_fn, command_fn, calib_fn
        self.state_fn = state_fn    # the user state (A13), for the panel's status bar
        self.fps = fps
        self._label = label if frame_fn is not None else None
        self._grabs: Deque[float] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._t = 0.0
        self._index = 0
        self._clients = 0
        self._served = 0
        self._error: Optional[str] = None
        self._stop = threading.Event()
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._threads: list = []

    # --- lifecycle -------------------------------------------------------------------

    def start(self) -> "CameraRelay":
        relay = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:  # one client connection
                relay._client(+1)
                try:
                    for raw in self.rfile:
                        header, payload = relay._answer(raw.strip())
                        self.wfile.write(json.dumps(header).encode() + b"\n" + payload)
                        self.wfile.flush()
                except (ConnectionError, OSError):
                    pass
                finally:
                    relay._client(-1)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = not _windows()   # on Windows it would let two relays share a port

        try:
            self._server = Server((HOST, self.port), Handler)
        except OSError as exc:
            raise RelayPortBusy(
                f"Port {self.port} is already in use, so the camera relay cannot start.\n"
                "  Probably the Wizard panel (or another relay) is already serving the camera:\n"
                "  then you do not need this one. Otherwise change camera.relay_port in\n"
                f"  config/eepy.yaml. ({exc})") from None
        self._stop.clear()
        self._spawn(self._server.serve_forever, "camera-relay-server")
        if self.frame_fn is not None:
            self._spawn(self._grab_loop, "camera-relay-grab")
        log.info("Camera relay on %s:%d (%s).", HOST, self.port,
                 "robot camera + head pose" if self.frame_fn else "head pose only")
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        for thread in self._threads:
            thread.join(2)
        self._threads = []
        with self._lock:
            self._jpeg = None   # drop the last picture from memory too

    @property
    def label(self) -> Optional[str]:
        label = self._label
        if callable(label):
            try:
                return label()
            except Exception:  # noqa: BLE001
                return None
        return label

    def status(self) -> Dict[str, Any]:
        now = monotonic()
        with self._lock:
            age = round(now - self._t, 1) if self._index else None
            recent = [t for t in self._grabs if now - t <= 2.0]
            fps = round((len(recent) - 1) / (recent[-1] - recent[0]), 1) if len(recent) > 2 else 0.0
            return {"ok": True, "camera": self.label, "frames": self._index, "fps": fps,
                    "last_frame_age": age, "clients": self._clients,
                    "error": self._error, "port": self.port}

    # --- internals ---------------------------------------------------------------------

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _client(self, delta: int) -> None:
        with self._lock:
            self._clients += delta

    def _grab_loop(self) -> None:
        assert self.frame_fn is not None
        period = 1.0 / self.fps
        while not self._stop.is_set():
            started = monotonic()
            if self._clients:            # nobody watching: leave the camera alone
                try:
                    jpeg = self.frame_fn()
                    if jpeg:
                        with self._lock:
                            self._jpeg, self._t = bytes(jpeg), monotonic()
                            self._index += 1
                            self._grabs.append(self._t)
                            self._error = None
                except Exception as exc:  # noqa: BLE001 -- the robot dropped out; keep trying
                    with self._lock:
                        if self._error is None:
                            log.warning("Robot camera: %s", exc)
                        self._error = str(exc) or type(exc).__name__
            self._stop.wait(max(0.0, period - (monotonic() - started)))

    def _answer(self, line: bytes) -> Tuple[Dict[str, Any], bytes]:
        word, _, argument = line.partition(b" ")
        command = word.upper()
        if command == b"USER":
            return self._user(argument)
        if command == b"SAY":
            return self._call(self.say_fn, argument, lambda fn, value: {"spoken": bool(fn(str(value)))})
        if command == b"POLL":
            command_value = None
            if self.command_fn is not None:
                try:
                    command_value = self.command_fn()
                except Exception:  # noqa: BLE001
                    log.exception("The command source failed")
            return {"ok": True, "command": command_value}, b""
        if command == b"CALIB":
            return self._call(self.calib_fn, argument, lambda fn, value: fn(dict(value)) or {})
        if command == b"STATE":
            return self._call(self.state_fn, argument, lambda fn, value: fn(dict(value)) or {})
        if command == b"FRAME":
            with self._lock:
                jpeg, t, index = self._jpeg, self._t, self._index
            if jpeg is None:
                return {"ok": True, "index": 0, "len": 0, "error": self._error}, b""
            return {"ok": True, "index": index, "t": t, "len": len(jpeg)}, jpeg
        if command == b"POSE":
            try:
                pose = self.pose_fn() if self.pose_fn is not None else None
            except Exception:  # noqa: BLE001
                pose = None
            if pose is None:
                return {"ok": False}, b""
            return dict(pose, ok=True, t=monotonic()), b""
        if command == b"STATUS":
            return self.status(), b""
        return {"ok": False, "error": f"unknown request {command[:20]!r}"}, b""


    def _call(self, fn: Optional[Callable[..., Any]], argument: bytes,
              use: Callable[[Callable[..., Any], Any], Dict[str, Any]]) -> Tuple[Dict[str, Any], bytes]:
        """A JSON argument handed to a panel-side callback; {"ok": false} when there is none."""
        if fn is None:
            return {"ok": False, "error": "not available here"}, b""
        try:
            value = json.loads(argument or b"null")
            return dict(use(fn, value), ok=True), b""
        except Exception as exc:  # noqa: BLE001 -- the panel must never crash on a message
            log.warning("Relay message failed: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}, b""

    def _user(self, argument: bytes) -> Tuple[Dict[str, Any], bytes]:
        """USER {"yaw": .., "pitch": ..} -- the student's face, as head angles
        relative to the robot's current gaze (or USER null: no face)."""
        try:
            value = json.loads(argument or b"null")
            offset = None if value is None else {"yaw": float(value["yaw"]), "pitch": float(value["pitch"])}
        except (ValueError, KeyError, TypeError):
            return {"ok": False, "error": "USER needs {\"yaw\": .., \"pitch\": ..} or null"}, b""
        if self.user_fn is not None:
            try:
                self.user_fn(offset)
            except Exception:  # noqa: BLE001 -- a bad receiver must not break the relay
                log.exception("The user-target receiver failed")
        return {"ok": True}, b""


class RelayClient:
    """Talks to a CameraRelay. Not thread-safe: one per thread."""

    def __init__(self, port: int, timeout: float = 2.0) -> None:
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._file: Any = None

    def _connect(self) -> None:
        try:
            self._sock = socket.create_connection((HOST, self.port), timeout=self.timeout)
        except OSError:
            self._sock = None
            raise RelayUnavailable(robot_unreachable(self.port)) from None
        self._file = self._sock.makefile("rb")

    def request(self, command: str) -> Tuple[Dict[str, Any], bytes]:
        if self._sock is None:
            self._connect()
        try:
            assert self._sock is not None
            self._sock.sendall(command.encode() + b"\n")
            line = self._file.readline()
            if not line:
                raise ConnectionError("the relay closed the connection")
            header = json.loads(line)
            size = int(header.get("len", 0))
            payload = self._file.read(size) if size else b""
            if len(payload) != size:
                raise ConnectionError("the relay stopped mid-picture")
            return header, payload
        except (OSError, ValueError, ConnectionError) as exc:
            self.close()
            raise RelayUnavailable(f"Lost the connection to the camera relay ({exc}).") from None

    def frame(self) -> Tuple[Dict[str, Any], bytes]:
        return self.request("FRAME")

    def pose(self) -> Optional[Dict[str, float]]:
        header, _ = self.request("POSE")
        return header if header.get("ok") else None

    def status(self) -> Dict[str, Any]:
        return self.request("STATUS")[0]

    def send_user(self, offset: Optional[Dict[str, float]]) -> None:
        """Tell the panel where the student's face is (None: no face)."""
        self.request("USER " + json.dumps(offset))

    def say(self, line_id: str) -> bool:
        """Ask the panel to speak a line (robot voice). False when it could not."""
        header, _ = self.request("SAY " + json.dumps(line_id))
        return bool(header.get("ok") and header.get("spoken"))

    def poll(self) -> Optional[Dict[str, Any]]:
        """A command from the panel for the vision process, or None."""
        header, _ = self.request("POLL")
        return header.get("command") if header.get("ok") else None

    def report(self, progress: Dict[str, Any]) -> None:
        """Calibration progress / result, for the panel's status indicator."""
        self.request("CALIB " + json.dumps(progress))

    def send_state(self, state: Dict[str, Any]) -> None:
        """The estimated user state (A13), for the panel's status bar."""
        self.request("STATE " + json.dumps(state))

    def close(self) -> None:
        for thing in (self._file, self._sock):
            try:
                if thing is not None:
                    thing.close()
            except OSError:
                pass
        self._file = self._sock = None


def _windows() -> bool:
    import sys
    return sys.platform.startswith("win")


def pose_dict(pose: Any) -> Dict[str, float]:
    """eepy.behaviors.pose.Pose -> what POSE sends."""
    return {"yaw": float(pose.yaw), "pitch": float(pose.pitch),
            "roll": float(pose.roll), "body_yaw": float(pose.body_yaw)}


# --- standalone: python -m eepy.camera.relay ----------------------------------------------


def main(argv: Optional[list] = None) -> int:
    """Serve the real robot's camera without the panel."""
    import argparse
    import sys
    import time
    from types import SimpleNamespace

    from .config import CameraConfigError, load

    parser = argparse.ArgumentParser(
        prog="python -m eepy.camera.relay",
        description="Serve the robot's camera to EEPY's vision process, without the Wizard panel. "
                    "(The panel does this by itself; you only need this when the panel is not running.)")
    parser.add_argument("--robot-host", help="the robot's IP (default: $REACHY_MINI_HOST)")
    parser.add_argument("--port", type=int, help="default: camera.relay_port in config/eepy.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    logging.getLogger("reachy_mini").setLevel(logging.WARNING)

    try:
        config = load()
    except CameraConfigError as exc:
        print(f"\n  config/eepy.yaml has a problem: {exc}", file=sys.stderr)
        return 2
    port = args.port or config.relay_port

    try:
        from reachy_mini import ReachyMini
    except ImportError:
        print("\n  The robot SDK is not installed here. This runs in .venv-robot:\n"
              "      source .venv-robot/bin/activate        (Windows: .venv-robot\\Scripts\\Activate.ps1)",
              file=sys.stderr)
        return 2
    from eepy.behaviors.backends import _load_connection_module
    from eepy.behaviors.play import load_env_file
    from eepy.behaviors.pose import Pose

    load_env_file()
    kwargs = _load_connection_module().connection_kwargs(SimpleNamespace(robot_host=args.robot_host))
    print("\n  Connecting to the robot's camera...")
    try:
        # Camera and pose only: the motors are not switched on.
        mini = ReachyMini(media_backend="default", **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  Could not reach the robot ({exc}).\n"
              "  Check it is switched on and on the same network, and that REACHY_MINI_HOST\n"
              "  in .env (or --robot-host) is its IP address.", file=sys.stderr)
        return 1

    def read_pose() -> Dict[str, float]:
        joints, antennas = mini.get_current_joint_positions()
        return pose_dict(Pose.from_robot(mini.get_current_head_pose(), antennas, joints[0]))

    relay = CameraRelay(port, frame_fn=mini.media.get_frame_jpeg, pose_fn=read_pose,
                        fps=config.fps, label="real")
    try:
        relay.start()
    except RelayPortBusy as exc:
        print(f"\n  {exc}", file=sys.stderr)
        mini.__exit__(None, None, None)
        return 1
    print("  Serving the robot's camera. Watch it with:  python -m eepy.camera.preview --camera robot\n"
          "  (in .venv-vision). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        relay.stop()
        try:
            mini.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
