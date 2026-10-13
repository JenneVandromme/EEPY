"""Start the Wizard-of-Oz panel.

    python -m eepy.wizard                      # backend from EEPY_BACKEND, else dry
    python -m eepy.wizard --backend sim
    python -m eepy.wizard --backend real --robot-host 192.168.1.50
    python -m eepy.wizard --mode rules         # the experimental condition (eepy.yaml)

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


def _port_taken(port: int) -> str | None:
    """Is anything already answering on this port, over IPv4 OR IPv6?

    Checking both matters. Another program -- a PHP or Node dev server, say --
    can hold [::1]:8080 while the panel takes 127.0.0.1:8080. Both start
    without complaint, and a browser asked for "localhost" tries IPv6 first:
    it reaches the OTHER program and shows its page, often a blank white one.
    Returns a description of what answered, or None if the port is free.
    """
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex((address, port)) == 0:
                    return f"{address}:{port}"
        except OSError:
            continue  # e.g. no IPv6 on this machine
    return None


def _who_listens(port: int) -> str:
    """'php (process 29510)', if the operating system will say. Best effort."""
    import subprocess

    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=2).stdout.splitlines()[1:]
    except Exception:  # noqa: BLE001 -- lsof missing, Windows, ...
        return "another program"
    names = sorted({f"{line.split()[0]} (process {line.split()[1]})" for line in out if line.split()})
    return ", ".join(names) or "another program"


def main(argv: list[str] | None = None) -> int:
    from eepy.behaviors.backends import BACKEND_NAMES
    from eepy.behaviors.loader import BEHAVIORS_DIR, SOUNDS_DIR
    from eepy.logs.schema import MODES

    from .config import CONFIG_PATH

    # Arguments first, the web server's packages second: --help, or a mistyped
    # --mode, must give a straight answer even where uvicorn is not installed,
    # rather than a confusing complaint about a missing package.
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
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help="the panel's keys and markers")
    parser.add_argument("--mode", choices=MODES,
                        help="the experimental condition (default: mode in config/eepy.yaml)")
    parser.add_argument("--data-dir", type=Path,
                        help="where session folders go (default: data_dir in config/eepy.yaml)")
    parser.add_argument("--session-prior", type=Path,
                        help="hmm mode: a JSON file with a starting belief for each session "
                             "(eepy/state/README.md; needs session_prior.enabled in config/state_hmm.yaml)")
    args = parser.parse_args(argv)

    for module in ("fastapi", "uvicorn"):
        if importlib.util.find_spec(module) is None:
            print(f"Cannot start: {module} is not installed. Activate .venv-robot, or run\n"
                  "    pip install -r requirements-robot.txt", file=sys.stderr)
            return 2

    import uvicorn

    from eepy.behaviors.play import load_env_file
    from eepy.settings import SettingsError, load_settings

    from .server import create_app, make_wizard

    load_env_file()
    try:
        settings = load_settings(mode=args.mode)
    except SettingsError as exc:
        print(f"\n  Cannot start: config/eepy.yaml has a problem.\n  {exc}", file=sys.stderr)
        return 2
    if args.data_dir is not None:
        settings.data_dir = str(args.data_dir.resolve())

    taken = _port_taken(args.port)
    if taken:
        print(f"\n  Cannot start: port {args.port} is already in use by {_who_listens(args.port)}"
              f" (answering at {taken}).\n"
              f"  If a browser opened http://localhost:{args.port}, it showed THAT program,"
              " not the panel.\n\n"
              f"  Either stop that program, or start the panel on another port:\n"
              f"      python -m eepy.wizard --port {args.port + 1}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    for noisy in ("reachy_mini", "uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    print("\n  Starting the EEPY Wizard panel...")
    wizard = make_wizard(args.backend, args.robot_host, settings=settings,
                         behaviors_dir=args.behaviors_dir, sounds_dir=args.sounds_dir,
                         config_path=args.config, session_prior=args.session_prior)
    status = "connected" if wizard.connected() else "NOT connected -- see the panel"
    print(f"  Robot: {wizard.backend_name}, {status}")
    built = "" if settings.mode_implemented else "  (not built yet: manual control only)"
    print(f"  Mode:  {settings.mode}{built}")
    print("\n  Open the panel:")
    # 127.0.0.1, not "localhost": localhost can mean IPv6 to a browser, and
    # be answered by a different program than the one listening on IPv4.
    print(f"    on this computer:      http://127.0.0.1:{args.port}")
    lan = _lan_address()
    if lan and args.host == "0.0.0.0":
        print(f"    on another laptop:     http://{lan}:{args.port}")
    print(f"\n  Sessions are saved in {settings.data_path}")
    print("  Ctrl+C to stop (ends any session and returns the robot to neutral).\n")
    uvicorn.run(create_app(wizard), host=args.host, port=args.port, log_level="warning")
    print("  Panel stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
