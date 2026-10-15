"""EEPY's launcher: one page to start, watch and stop the simulator, the panel and vision.

    python3 launch.py

See eepy/launcher/README.md. The separate terminal commands keep working.
"""

import sys

from eepy.launcher.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
