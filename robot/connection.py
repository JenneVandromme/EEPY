"""
Shared "where is the robot?" helper for scripts under robot/.

Three ways to run a script, resolved in this order:

  1. Default, no flags: a daemon running on this same machine (started with
     `reachy-mini-daemon` for the real robot over USB, or
     `reachy-mini-daemon --sim` for the MuJoCo simulator). The SDK's own
     localhost default is already correct here.
  2. --robot-host <ip>: a wireless Reachy Mini, reached over Wi-Fi/Ethernet
     at the robot's own address. Use the IP, not reachy-mini.local (mDNS
     resolution is unreliable across platforms).
  3. $REACHY_MINI_HOST environment variable (e.g. set in .env): same as
     --robot-host, so you don't have to pass it on every command. A flag
     passed explicitly always wins over the environment variable.
"""

import argparse
import os

ROBOT_HOST_ENV = "REACHY_MINI_HOST"
DEFAULT_PORT = 8000


def add_arg(parser: argparse.ArgumentParser) -> None:
    """Add the --robot-host flag to a script's argument parser."""
    parser.add_argument(
        "--robot-host",
        default=None,
        metavar="HOST[:PORT]",
        help="address of a wireless Reachy Mini's on-board daemon, e.g. "
        "'192.168.1.50'. Defaults to the REACHY_MINI_HOST environment "
        "variable when set, otherwise a local daemon on this machine.",
    )


def _split_host_port(value: str) -> tuple[str, int]:
    if ":" in value:
        host, _, port = value.rpartition(":")
        return host, int(port)
    return value, DEFAULT_PORT


def daemon_host_str(args: argparse.Namespace) -> str:
    """'host:port' for scripts that talk to the daemon over its REST API
    instead of the SDK (eye_gaze_follow.py, in the vision venv)."""
    robot_host = getattr(args, "robot_host", None) or os.environ.get(ROBOT_HOST_ENV)
    if not robot_host:
        return f"localhost:{DEFAULT_PORT}"
    host, port = _split_host_port(robot_host)
    return f"{host}:{port}"


def connection_kwargs(args: argparse.Namespace) -> dict:
    """kwargs to merge into ReachyMini(**kwargs).

    Empty for a local daemon: the SDK's own defaults are already correct
    there. Only set explicitly when --robot-host or $REACHY_MINI_HOST
    points at a robot on the network.
    """
    robot_host = getattr(args, "robot_host", None) or os.environ.get(ROBOT_HOST_ENV)
    if not robot_host:
        return {}
    host, port = _split_host_port(robot_host)
    return {"host": host, "port": port, "connection_mode": "network"}
