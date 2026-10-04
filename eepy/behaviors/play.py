"""Play a behaviour on the simulator, the robot, or nothing at all.

    python -m eepy.behaviors.play refocus_curious --backend sim
    python -m eepy.behaviors.play --ladder bedtime --level 2 --intensity 0.5
    python -m eepy.behaviors.play refocus_curious --backend sim --watch

--watch is the one for tuning. It plays the behaviour, then waits. Every time
you save a file in behaviors/ (or change a sound), it re-checks everything and
plays the behaviour again -- so you can edit a number, save, and watch the
result straight away. Press Enter to replay without changing anything. If you
save a mistake, it explains it, and keeps the last good version until you fix
it. Ctrl+C returns the robot to neutral and quits.

The backend comes from --backend, or EEPY_BACKEND in .env, or else "dry", which
moves nothing and just prints what would happen.
"""

from __future__ import annotations

from ._deps import require as _require

_require()

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from .backends import BACKEND_ENV, BACKEND_NAMES, BackendError, make_backend
from .loader import BEHAVIORS_DIR, SOUNDS_DIR
from .player import BehaviorPlayer, PlayerError
from .validate import Validator, report


def load_env_file() -> None:
    """Read the repo's .env, so EEPY_BACKEND and REACHY_MINI_HOST can live there.

    Public because the Wizard panel's entry point uses it too.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _snapshot(dirs: list[Path]) -> dict[Path, float]:
    """mtime of every file we watch."""
    out: dict[Path, float] = {}
    for directory in dirs:
        for path in directory.glob("*"):
            if path.is_file():
                out[path] = path.stat().st_mtime
    return out


def _check(behaviors_dir: Path, sounds_dir: Path) -> bool:
    """Run the validator. Print its report if anything is wrong."""
    validator = Validator(directory=behaviors_dir, sounds=sounds_dir)
    validator.run()
    if validator.errors:
        report(validator, behaviors_dir.resolve().parent, strict=False)
        return False
    return True


def _resolve_name(player: BehaviorPlayer, args) -> str | None:
    try:
        if args.ladder is not None:
            return player.library.get_behavior(args.ladder, args.level).name
        player._lookup(args.name)  # friendly "did you mean"
        return args.name
    except (KeyError, PlayerError) as exc:
        print(f"\n{str(exc).strip(chr(34))}", file=sys.stderr)
        return None


def _play_once(player: BehaviorPlayer, name: str, args) -> None:
    context = {}
    if args.minutes is not None:
        context["minutes"] = args.minutes
    if args.evidence is not None:
        context["evidence"] = args.evidence
    player.play(name, intensity=args.intensity, tone=args.tone, context=context)


def _pose_printer(player: BehaviorPlayer, stop: threading.Event) -> None:
    while not stop.wait(0.2):
        print(f"      {player.pose}", flush=True)


def _install_signal_handlers() -> None:
    """Make Ctrl+C and SIGTERM both mean "return to neutral and quit".

    Installed explicitly rather than trusted to Python's defaults, for two
    reasons. A process launched in the background from a script inherits
    SIGINT as IGNORED, so Python never turns Ctrl+C into KeyboardInterrupt and
    the tool cannot be stopped. And an editor's stop button, or closing the
    terminal, sends SIGTERM, which Python does not handle at all -- the process
    would die mid-move, leaving the robot wherever it happened to be.
    """
    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _stop)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    _install_signal_handlers()
    parser = argparse.ArgumentParser(
        prog="python -m eepy.behaviors.play",
        description="Play a behaviour on the simulator, the robot, or nothing.",
    )
    parser.add_argument("name", nargs="?", help="the behaviour's name")
    parser.add_argument("--ladder", help="pick by ladder instead of name")
    parser.add_argument("--level", type=int, help="which rung of that ladder")
    parser.add_argument("--intensity", type=float, default=1.0,
                        help="0.0-1.0, how big the movement is (default 1.0)")
    parser.add_argument("--tone", choices=("hedged", "direct"), default="hedged",
                        help="which wording to use for spoken lines")
    parser.add_argument("--minutes", type=int, help="fills {minutes} in spoken lines")
    parser.add_argument("--evidence", help="fills {evidence} in spoken lines")
    parser.add_argument("--backend", choices=BACKEND_NAMES,
                        help=f"dry, sim or real (default: ${BACKEND_ENV}, else dry)")
    parser.add_argument("--robot-host", help="the real robot's IP (default: $REACHY_MINI_HOST)")
    parser.add_argument("--watch", action="store_true",
                        help="replay every time a behaviour file is saved")
    parser.add_argument("--show-poses", action="store_true",
                        help="also print the pose five times a second")
    parser.add_argument("--timing", action="store_true",
                        help="print frame timing statistics at the end")
    parser.add_argument("--behaviors-dir", type=Path, default=BEHAVIORS_DIR)
    parser.add_argument("--sounds-dir", type=Path, default=SOUNDS_DIR)
    args = parser.parse_args(argv)

    if args.name is None and args.ladder is None:
        parser.print_help()
        return 2
    if args.ladder is not None and args.level is None:
        print("--ladder also needs --level.", file=sys.stderr)
        return 2
    if not 0.0 <= args.intensity <= 1.0:
        print(f"--intensity must be between 0 and 1, not {args.intensity}.", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    logging.getLogger("reachy_mini").setLevel(logging.WARNING)

    # Check the files first: a mistake is far easier to read here than as a
    # refusal halfway through connecting to the robot.
    if not _check(args.behaviors_dir, args.sounds_dir):
        if not args.watch:
            return 1
        print("\n  Waiting for you to fix that and save...")
        before = _snapshot([args.behaviors_dir, args.sounds_dir])
        try:
            while True:
                time.sleep(0.3)
                now = _snapshot([args.behaviors_dir, args.sounds_dir])
                if now != before:
                    before = now
                    if _check(args.behaviors_dir, args.sounds_dir):
                        break
                    print("\n  Still not valid. Waiting...")
        except KeyboardInterrupt:
            return 0

    try:
        backend = make_backend(args.backend, robot_host=args.robot_host)
        player = BehaviorPlayer(backend, behaviors_dir=args.behaviors_dir,
                                sounds_dir=args.sounds_dir, print_events=True)
        print(f"\n  Connecting ({backend.name})...")
        player.start()
    except BackendError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    name = _resolve_name(player, args)
    if name is None:
        player.close()
        return 2

    printer_stop = threading.Event()
    if args.show_poses:
        threading.Thread(target=_pose_printer, args=(player, printer_stop), daemon=True).start()

    try:
        print(f"  Playing {name} (intensity {args.intensity}, backend {backend.name})\n")
        _play_once(player, name, args)
        if not args.watch:
            # Wait for it to finish -- or, for a behaviour that loops forever,
            # for Ctrl+C.
            while player.is_playing():
                time.sleep(0.05)
            return 0 if player.error is None else 1

        replay = threading.Event()
        if sys.stdin.isatty():
            def _enter() -> None:
                for _ in sys.stdin:
                    replay.set()
            threading.Thread(target=_enter, daemon=True).start()
        print("  Watching behaviors/ and sounds/. Save a file to replay, "
              "press Enter to replay, Ctrl+C to quit.\n")
        before = _snapshot([args.behaviors_dir, args.sounds_dir])
        while True:
            time.sleep(0.25)
            now = _snapshot([args.behaviors_dir, args.sounds_dir])
            if now != before:
                before = now
                result = player.reload()
                if not result.ok:
                    print(f"\n{result.message}\n\n  Kept the last good version. "
                          "Fix it and save again.\n", flush=True)
                    continue
                fresh = _resolve_name(player, args)
                if fresh is None:
                    continue
                name = fresh
                print(f"\n  Saved -- replaying {name}\n", flush=True)
                _play_once(player, name, args)
            elif replay.is_set():
                replay.clear()
                print(f"\n  Replaying {name}\n", flush=True)
                _play_once(player, name, args)
    except KeyboardInterrupt:
        print("\n  Stopping -- returning to neutral...", flush=True)
        return 0
    except PlayerError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    finally:
        printer_stop.set()
        player.close(return_to_neutral=True)
        if args.timing:
            stats = player.timing_stats()
            print("\n  frame timing (ms late vs schedule): " + ", ".join(
                f"{k} {v:.2f}" if isinstance(v, float) else f"{k} {v}" for k, v in stats.items()))
        print("  Done.")


if __name__ == "__main__":
    sys.exit(main())
