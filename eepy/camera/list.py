"""List the cameras EEPY can use:  python -m eepy.camera.list

Tries camera numbers 0, 1, 2, ... the way EEPY opens them, says which work
and at what size, and whether the robot's camera is reachable. Run it in
.venv-vision. It shows nothing on screen and records nothing.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.camera.list",
                                     description="List the cameras EEPY can use.")
    parser.add_argument("--max", type=int, default=5, help="try camera numbers 0 .. MAX-1 (default 5)")
    args = parser.parse_args(argv)

    from . import choose_source
    from .config import CameraConfigError, load
    from .errors import CameraError
    from .laptop import camera_names, import_cv2, open_capture, platform_backends
    from .relay import RelayClient, RelayUnavailable

    try:
        config = load()
    except CameraConfigError as exc:
        print(f"\n  config/eepy.yaml has a problem: {exc}", file=sys.stderr)
        return 2
    try:
        cv2 = import_cv2()
    except CameraError as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2

    apis = " then ".join(name for name, _ in platform_backends(cv2))
    print(f"\n  Laptop cameras (opened with {apis}):")
    found: List[int] = []
    first_problem = None
    for index in range(args.max):
        try:
            opened = open_capture(index, config.laptop.resolution, cv2, wait=2.0)
        except CameraError as exc:
            first_problem = first_problem or (index, str(exc))
            continue
        height, width = opened.first.shape[:2]
        opened.capture.release()
        found.append(index)
        print(f"    camera {index}: works, {width}x{height} ({opened.api})")
    if not found:
        print("    none found.")
        if first_problem:
            print("\n  Camera 0 said:\n" + "\n".join("    " + line for line in first_problem[1].splitlines()))

    names = camera_names()
    if names:
        print("\n  What the operating system calls them (the order may differ from the numbers above):")
        for name in names:
            print(f"    - {name}")

    print("\n  The robot's camera:")
    client = RelayClient(config.relay_port, timeout=1.0)
    try:
        status = client.status()
        if status.get("camera"):
            print(f"    reachable through the panel / relay on port {config.relay_port}.")
        else:
            print(f"    the panel is running (port {config.relay_port}), but not with the real robot.")
    except RelayUnavailable:
        print("    not reachable: start the panel with --backend real, or python -m eepy.camera.relay.")
    finally:
        client.close()

    source = choose_source(config)
    index = config.laptop.index
    which = f"laptop camera {index if index != 'auto' else (found[0] if found else 0)}" \
        if source == "laptop" else f"{source} camera"
    print(f"\n  EEPY will use: the {which}  (camera.source = {config.source} in config/eepy.yaml).")
    if source == "laptop" and index == "auto" and len(found) > 1:
        print(f"  To pick another, set camera.laptop.index, e.g.  index: {found[1]}")
    print()
    return 0 if found or source != "laptop" else 1


if __name__ == "__main__":
    raise SystemExit(main())
