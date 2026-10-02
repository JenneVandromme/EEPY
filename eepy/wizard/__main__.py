"""Start the Wizard-of-Oz panel.

    python -m eepy.wizard                      # backend from EEPY_BACKEND, else dry
    python -m eepy.wizard --backend sim
    python -m eepy.wizard --backend real --robot-host 192.168.1.50

Then open the address it prints, on this machine or any laptop on the same
network. Ctrl+C ends any running session, returns the robot to neutral and stops.
"""

from __future__ import annotations

from eepy.behaviors._deps import require as _require

_require()

import argparse
import importlib.util
import logging
import socket
import sys
from pathlib import Path


def _lan_address() -> str | None:
    """This machine's address on the local network, for teammates' laptops.

    Opens a UDP socket "towards" a private address: nothing is sent, but the
    operating system has to pick the outgoing network interface, and that
    interface's address is the one other laptops can reach.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            address = probe.getsockname()[0]
            return None if address.startswith("127.") else address
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    for module in ("fastapi", "uvicorn"):
        if importlib.util.find_spec(module) is None:
            print(f"Cannot start: {module} is not installed. Activate .venv-robot, or run\n"
                  "    pip install -r requirements-robot.txt", file=sys.stderr)
            return 2

    import uvicorn

    from eepy.behaviors.backends import BACKEND_NAMES
    from eepy.behaviors.loader import BEHAVIORS_DIR, SOUNDS_DIR
    from eepy.behaviors.play import _load_dotenv

    from .config import CONFIG_PATH
    from .server import create_app, make_wizard
    from .sessions import LOGS_DIR

    _load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m eepy.wizard",
                                     description="EEPY's Wizard-of-Oz control panel.")
    parser.add_argument("--backend", choices=BACKEND_NAMES,
                        help="dry, sim or real (default: $EEPY_BACKEND, else dry)")
    parser.add_argument("--robot-host", help="the real robot's IP (default: $REACHY_MINI_HOST)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0",
                        help="0.0.0.0 lets other laptops connect; 127.0.0.1 keeps it on this machine")
    parser.add_argument("--behaviors-dir", type=Path, default=BEHAVIORS_DIR)
    parser.add_argument("--sounds-dir", type=Path, default=SOUNDS_DIR)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--logs-dir", type=Path, default=LOGS_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    for noisy in ("reachy_mini", "uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    print("\n  Starting the EEPY Wizard panel...")
    wizard = make_wizard(args.backend, args.robot_host, behaviors_dir=args.behaviors_dir,
                         sounds_dir=args.sounds_dir, config_path=args.config,
                         logs_dir=args.logs_dir)
    status = "connected" if wizard.connected() else "NOT connected -- see the panel"
    print(f"  Robot: {wizard.backend_name}, {status}")
    print("\n  Open the panel:")
    print(f"    on this computer:      http://localhost:{args.port}")
    lan = _lan_address()
    if lan and args.host == "0.0.0.0":
        print(f"    on another laptop:     http://{lan}:{args.port}")
    print(f"\n  Session logs go to {args.logs_dir}")
    print("  Ctrl+C to stop (ends any session and returns the robot to neutral).\n")
    uvicorn.run(create_app(wizard), host=args.host, port=args.port, log_level="warning")
    print("  Panel stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
