"""Calibrate only the work area (gaze zones):  python -m eepy.perception.calibrate_gaze

The same as `python -m eepy.perception.calibrate --only gaze` (A12's combined
calibration, gaze part): a quick face check, then look at the dots (centre and
corners of the screen), your notes, and away to the side. Same options:
--user, --setup, --camera, --index, --no-save. See
eepy/perception/gaze/README.md and eepy/perception/calibration/README.md.
"""

from __future__ import annotations

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    from .calibrate import main as calibrate

    args = list(sys.argv[1:] if argv is None else argv)
    return calibrate(["--only", "gaze"] + args, prog="python -m eepy.perception.calibrate_gaze")


if __name__ == "__main__":
    raise SystemExit(main())
