"""Camera problems, explained for the person in front of the laptop.

Each message says what is wrong AND what to do about it, per operating system.
OpenCV itself only says "could not open", so the cause is narrowed down from
what else is known: the OS, whether the device exists, whether it opened but
sent nothing.
"""

from __future__ import annotations

import sys


class CameraError(Exception):
    """A camera could not be used. str(exc) is a message for a person."""


def _platform() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def mac_permission(app_hint: str = "") -> str:
    return (
        "macOS is not letting this program use the camera.\n"
        "  1. Open System Settings > Privacy & Security > Camera.\n"
        "  2. Switch ON the app you run EEPY from"
        + (f" ({app_hint})" if app_hint else " (Terminal, iTerm, VS Code, PyCharm, ...)") + ".\n"
        "     Not in the list? Run this command once more: macOS asks the first time.\n"
        "  3. QUIT that app completely (Cmd+Q) and open it again -- macOS only\n"
        "     applies the change to a freshly started app."
    )


def no_camera(index: object = None) -> str:
    which = f"camera {index}" if index not in (None, "auto") else "a camera"
    lines = [f"Could not find {which} on this computer."]
    if index not in (None, "auto"):
        lines.append("  See which cameras there are:  python -m eepy.camera.list\n"
                     "  then set camera.laptop.index in config/eepy.yaml (or use --index).")
    if _platform() == "linux":
        lines.append("  On Linux, check `ls /dev/video*`, and that you are in the 'video' group:\n"
                     "      sudo usermod -aG video $USER     (then log out and in again)")
    elif _platform() == "windows":
        lines.append("  On Windows, check Settings > Privacy & security > Camera:\n"
                     "  'Camera access' and 'Let desktop apps access your camera' must be ON.")
    else:
        lines.append("  An external webcam? Unplug it and plug it in again.")
    return "\n".join(lines)


def in_use(index: object = None) -> str:
    which = f"Camera {index}" if index not in (None, "auto") else "The camera"
    hint = {
        "windows": "On Windows only one program can use a camera at a time.",
        "linux": "On Linux only one program can use a camera at a time.",
        "mac": "",
    }[_platform()]
    return (f"{which} exists, but it is not sending pictures: another program is probably "
            f"using it.\n  Close Teams, Zoom, Google Meet, FaceTime, OBS, Photo Booth or another "
            f"EEPY preview, then try again. {hint}".rstrip())


def open_failed(index: object = None) -> str:
    """Opening failed and the cause is unknown: list the likely ones, most likely first."""
    platform = _platform()
    if platform == "mac":
        return (mac_permission() + "\n\nIf the camera IS allowed:\n"
                + "\n".join("  " + line for line in in_use(index).splitlines()))
    return no_camera(index) + "\n\nOr: " + in_use(index)


def robot_unreachable(port: int) -> str:
    return (
        f"Cannot reach the robot's camera (nothing answers on port {port} of this computer).\n"
        "  The robot's camera reaches EEPY through the Wizard panel. Start it with the robot:\n"
        "      python -m eepy.wizard --backend real        (in .venv-robot)\n"
        "  or, without the panel:\n"
        "      python -m eepy.camera.relay                 (in .venv-robot)\n"
        "  No robot today? Use the laptop camera:  --camera laptop"
    )


def robot_no_pictures() -> str:
    return (
        "Connected to the panel, but the robot's camera sends no pictures.\n"
        "  Is the panel connected to the REAL robot (--backend real)? In the simulator\n"
        "  and dry run there is no robot camera: use --camera laptop.\n"
        "  If it is the real robot, check it is switched on and its daemon is running;\n"
        "  this keeps retrying by itself."
    )
