"""LaptopCamera: this computer's webcam, standing in for the robot's camera.

Used with the simulator and the dry run. The robot sees only part of what the
webcam sees -- the BLUE BOX (see region.py) -- so only that part is passed on
to perception. The box follows the robot's head, using the head pose the
Wizard panel publishes through the camera relay; without the panel it stays
in the middle.

Works on macOS (AVFoundation), Windows (Media Foundation, then DirectShow)
and Linux (V4L2) without code changes; the OS is detected at run time.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from eepy.logs.clock import monotonic

from . import errors
from .config import CameraConfig
from .errors import CameraError
from .frame import Frame
from .region import HeadPose, robot_box, crop
from .relay import RelayClient, RelayUnavailable
from .source import CameraSource

log = logging.getLogger("eepy.camera")

# Windows: without this, Media Foundation can take 20-30 s to open a webcam.
# It must be set before OpenCV is imported, so it is set at import time here.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # its probing warnings only confuse people

FIRST_PICTURE_SECONDS = 3.0   # an opened camera that sends nothing for this long is "in use"
MAX_AUTO_INDEX = 4            # "auto" tries camera 0 .. 3


def import_cv2() -> Any:
    try:
        import cv2
    except ImportError:
        raise CameraError(
            "OpenCV is not installed in this environment. The camera runs in .venv-vision:\n"
            "    source .venv-vision/bin/activate     (Windows: .venv-vision\\Scripts\\Activate.ps1)\n"
            "    pip install -r requirements-vision.txt") from None
    return cv2


def platform_backends(cv2: Any) -> List[Tuple[str, int]]:
    """(name, OpenCV API id) to try, best first, for this operating system."""
    if sys.platform == "darwin":
        return [("AVFoundation", cv2.CAP_AVFOUNDATION)]
    if sys.platform.startswith("win"):
        return [("Media Foundation", cv2.CAP_MSMF), ("DirectShow", cv2.CAP_DSHOW)]
    return [("V4L2", cv2.CAP_V4L2), ("default", cv2.CAP_ANY)]


@dataclass
class Opened:
    capture: Any
    index: int
    api: str
    first: Any   # the first picture, proving it really works


def open_capture(index: int, resolution: Tuple[int, int], cv2: Any = None,
                 wait: float = FIRST_PICTURE_SECONDS) -> Opened:
    """Open camera `index` and read one picture. Raises CameraError explaining why not."""
    cv2 = cv2 or import_cv2()
    opened_somewhere = False
    for api_name, api in platform_backends(cv2):
        capture = cv2.VideoCapture(index, api)
        if not capture.isOpened():
            capture.release()
            continue
        opened_somewhere = True
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # keep the driver from queueing old pictures
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            ok, image = capture.read()
            if ok and image is not None and image.size:
                return Opened(capture, index, api_name, image)
            time.sleep(0.05)
        capture.release()
    raise CameraError(diagnose(index, opened_somewhere))


def diagnose(index: Any, opened: bool) -> str:
    """Why could this camera not be used? Best guess, in plain words."""
    if opened:                       # it opened but sent nothing
        return errors.in_use(index)
    if sys.platform == "darwin":
        names = camera_names()
        if names is not None and not names:
            return errors.no_camera(index)
        if isinstance(index, int) and names and index >= len(names):
            return errors.no_camera(index)
        return errors.open_failed(index)   # permission first, then "in use"
    if sys.platform.startswith("linux") and isinstance(index, int):
        if not Path(f"/dev/video{index}").exists():
            return errors.no_camera(index)
        if not os.access(f"/dev/video{index}", os.R_OK | os.W_OK):
            return (f"No permission to use /dev/video{index}.\n"
                    "      sudo usermod -aG video $USER     (then log out and in again)")
        return errors.in_use(index)
    return errors.open_failed(index)


def camera_names() -> Optional[List[str]]:
    """The cameras the OS knows, by name, or None if it will not say.
    On Linux the list position is NOT the OpenCV index; see list.py."""
    try:
        if sys.platform == "darwin":
            import json
            out = subprocess.run(["system_profiler", "-json", "SPCameraDataType"],
                                 capture_output=True, text=True, timeout=10).stdout
            return [c.get("_name", "?") for c in json.loads(out or "{}").get("SPCameraDataType", [])]
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -PresentOnly -Class Camera,Image | "
                 "Where-Object Status -eq 'OK' | ForEach-Object FriendlyName"],
                capture_output=True, text=True, timeout=15).stdout
            return [line.strip() for line in out.splitlines() if line.strip()]
        base = Path("/sys/class/video4linux")
        if base.is_dir():
            return [f"{p.name}: {(p / 'name').read_text().strip()}" for p in sorted(base.iterdir())
                    if (p / "name").is_file()]
    except Exception:  # noqa: BLE001 -- names are a nicety, never a reason to fail
        return None
    return None


class RelayPose:
    """The robot's head pose, from the camera relay. None when unknown.
    After a failure it waits a while before asking again, so a missing panel
    costs nothing per frame."""

    RETRY_SECONDS = 2.0

    def __init__(self, port: int) -> None:
        self._client = RelayClient(port, timeout=0.3)
        self._retry_at = 0.0
        self.known = False

    def __call__(self) -> Optional[HeadPose]:
        now = time.monotonic()
        if now < self._retry_at:
            return None
        try:
            pose = self._client.pose()
        except RelayUnavailable:
            self._retry_at, self.known = now + self.RETRY_SECONDS, False
            return None
        self.known = pose is not None
        if pose is None:
            return None
        return HeadPose(yaw=float(pose["yaw"]), pitch=float(pose["pitch"]), roll=float(pose["roll"]))

    def close(self) -> None:
        self._client.close()


class LaptopCamera(CameraSource):
    """The webcam, cropped to the robot's view.

    keep_full   also hand over the whole picture (Frame.full) -- for the preview
                only. Perception must use Frame.image.
    pose_fn     where the head pose comes from (default: the camera relay).
                Return None for "unknown": the box is then centred.
    """

    name = "laptop"

    def __init__(self, config: Optional[CameraConfig] = None, events: Any = None,
                 index: Any = None, keep_full: bool = False,
                 pose_fn: Optional[Callable[[], Optional[HeadPose]]] = None,
                 clock: Callable[[], float] = monotonic) -> None:
        super().__init__(config, events, clock=clock)
        self.index = self.config.laptop.index if index is None else index
        self.keep_full = keep_full
        self._own_pose = pose_fn is None
        self.pose_fn = pose_fn if pose_fn is not None else RelayPose(self.config.relay_port)
        self._opened: Optional[Opened] = None
        self._pending: Any = None   # the first picture, delivered by the first _read()
        self._failures = 0
        self.head_known = False

    def describe(self) -> str:
        if self._opened is not None:
            return f"laptop camera {self._opened.index} ({self._opened.api})"
        return "laptop camera" if self.index == "auto" else f"laptop camera {self.index}"

    def _open(self) -> None:
        cv2 = import_cv2()
        resolution = self.config.laptop.resolution
        if self.index != "auto":
            self._opened = open_capture(int(self.index), resolution, cv2)
        else:
            problems = []
            for index in range(MAX_AUTO_INDEX):
                try:
                    self._opened = open_capture(index, resolution, cv2, wait=2.0)
                    break
                except CameraError as exc:
                    problems.append(str(exc))
            else:
                # Camera 0's reason is the one that matters: the others usually just do not exist.
                raise CameraError(problems[0])
        self._pending, self._failures = self._opened.first, 0
        image = self._opened.first
        if (image.shape[1], image.shape[0]) != tuple(resolution):
            log.info("Asked the laptop camera for %dx%d; it gives %dx%d.",
                     resolution[0], resolution[1], image.shape[1], image.shape[0])

    def _read(self) -> Optional[Tuple[Any, float]]:
        assert self._opened is not None
        if self._pending is not None:
            image, self._pending = self._pending, None
            return image, self._clock()
        ok, image = self._opened.capture.read()
        t = self._clock()
        if not ok or image is None:
            self._failures += 1
            if self._failures >= 30:
                raise CameraError("The laptop camera stopped sending pictures. Unplugged, "
                                  "or taken over by another program (Teams, Zoom, ...)?")
            time.sleep(0.01)
            return None
        self._failures = 0
        return image, t

    def _make_frame(self, image: Any, t: float, index: int) -> Frame:
        height, width = image.shape[:2]
        pose = self.pose_fn() if self.pose_fn is not None else None
        self.head_known = pose is not None
        box = robot_box(width, height, self.config.laptop.fov_deg, self.config.robot.fov_deg,
                        self.config.robot.aspect, pose)
        return Frame(image=crop(image, box), t=t, index=index, source=self.name, box=box,
                     full=image if self.keep_full else None)

    def _close(self) -> None:
        if self._opened is not None:
            self._opened.capture.release()
            self._opened = None
        self._pending = None

    def stop(self, timeout: float = 3.0) -> None:
        super().stop(timeout)
        if self._own_pose and isinstance(self.pose_fn, RelayPose):
            self.pose_fn.close()
