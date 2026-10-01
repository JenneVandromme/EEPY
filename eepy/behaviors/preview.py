"""Print what a behaviour will actually do, without a robot.

    python -m eepy.behaviors.preview refocus_joint_attention
    python -m eepy.behaviors.preview --ladder bedtime --level 5
    python -m eepy.behaviors.preview --list

Every building block is expanded and every inherited value filled in, so the
table is the real sequence the robot would be sent -- not the YAML shorthand.
Useful for checking timing and for seeing what `intensity` did to the angles.
"""

from __future__ import annotations

# First, before any third-party import, so a missing package gets a readable
# message instead of a traceback.
from ._deps import require as _require

_require()

import argparse
import difflib
import sys
from pathlib import Path

from pydantic import ValidationError

from . import reach
from .loader import BEHAVIORS_DIR, BehaviorFileError, Library, Step, load_library

_COLUMNS = (
    ("time", 11),
    ("dur", 9),
    ("easing", 12),
    ("head y/p/r", 20),
    ("z mm", 7),
    ("antennas R/L", 15),
    ("body", 7),
    ("sound / say", 22),
)


def _header() -> str:
    cells = [name.ljust(width) for name, width in _COLUMNS]
    line = "  ".join(cells)
    return line + "\n" + "-" * len(line)


def _row(step: Step) -> str:
    window = f"{step.start_s:5.2f}-{step.end_s:5.2f}"
    hold = f"+{step.hold:.1f}" if step.hold else ""
    extras = []
    if step.sound:
        extras.append(f"♪ {step.sound}")
    if step.say:
        extras.append(f'"{step.say}"')
    if step.action:
        extras.append(f"→ robot.{step.action}()")
    cells = [
        window.ljust(_COLUMNS[0][1]),
        f"{step.duration:.2f}{hold}".ljust(_COLUMNS[1][1]),
        step.easing.ljust(_COLUMNS[2][1]),
        f"{step.head_yaw:6.1f} {step.head_pitch:6.1f} {step.head_roll:6.1f}".ljust(
            _COLUMNS[3][1]
        ),
        f"{step.head_z:6.1f}".ljust(_COLUMNS[4][1]),
        f"{step.antenna_right:6.1f} {step.antenna_left:6.1f}".ljust(_COLUMNS[5][1]),
        f"{step.body_yaw:6.1f}".ljust(_COLUMNS[6][1]),
        " ".join(extras).ljust(_COLUMNS[7][1]),
    ]
    return "  ".join(cells).rstrip()


def preview(library: Library, name: str) -> str:
    """The resolved timeline for one behaviour, as text."""
    behavior = library.behaviors[name]
    steps = library.resolve(behavior)
    total = steps[-1].end_s if steps else 0.0

    out: list[str] = []
    out.append(f"{behavior.name}")
    if behavior.ladder is not None:
        out.append(f"  ladder     {behavior.ladder}, level {behavior.level}")
    if behavior.group:
        out.append(f"  group      {behavior.group}")
    out.append(f"  loop       {behavior.loop}")
    out.append(f"  intensity  {behavior.intensity}")
    if behavior.intensity != 1.0:
        out.append(
            f"             (every angle below is already scaled by "
            f"{behavior.intensity})"
        )
    out.append(f"  keyframes  {len(behavior.keyframes)} written, "
               f"{len(steps)} after expanding blocks")
    out.append(f"  duration   {total:.2f} s")
    out.append("")
    out.append(behavior.description.strip())
    out.append("")
    out.append(_header())
    for step in steps:
        out.append(_row(step))
    out.append("")

    # Angles are useless if the robot cannot hold them, so say so here too.
    if reach.available():
        bad = [
            s for s in steps
            if s.action is None
            and not reach.is_reachable(
                yaw=s.head_yaw, pitch=s.head_pitch, roll=s.head_roll,
                z_mm=s.head_z, body_yaw=s.body_yaw,
            )
        ]
        if bad:
            out.append(f"  ! {len(bad)} pose(s) are not physically reachable. "
                       "Run the validator for details.")
        else:
            out.append("  ✓ every pose is physically reachable")
    else:
        out.append("  (reachability not checked: "
                   "reachy_mini_rust_kinematics is not installed)")

    # The source trail, so a designer can find which block produced a row.
    out.append("")
    out.append("  where each row came from:")
    for step in steps:
        out.append(f"    {step.start_s:5.2f}  {step.source}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eepy.behaviors.preview",
        description="Show a behaviour's resolved timeline, without a robot.",
    )
    parser.add_argument("name", nargs="?", help="the behaviour's name")
    parser.add_argument("--ladder", help="find it by ladder instead of name")
    parser.add_argument("--level", type=int, help="which rung of that ladder")
    parser.add_argument("--list", action="store_true",
                        help="list every behaviour and exit")
    parser.add_argument("--behaviors-dir", type=Path, default=BEHAVIORS_DIR)
    args = parser.parse_args(argv)

    try:
        library = load_library(args.behaviors_dir)
    except BehaviorFileError as exc:
        print(f"Could not read {exc.path}: {exc.message}", file=sys.stderr)
        print("\nFor the full explanation, run:", file=sys.stderr)
        print("    python -m eepy.behaviors.validate", file=sys.stderr)
        return 2
    except ValidationError:
        # Preview is not the right place to explain a malformed file -- the
        # validator says it properly, with the behaviour and keyframe named.
        # Without this, the designer gets a six-frame pydantic traceback from
        # the other command the guide tells them to run.
        print(
            "There is a mistake in one of the behaviour files, so it cannot be "
            "previewed yet.",
            file=sys.stderr,
        )
        print("\nTo see exactly what and where, run:", file=sys.stderr)
        print("    python -m eepy.behaviors.validate", file=sys.stderr)
        return 2

    if args.list:
        print(f"{len(library.behaviors)} behaviours:\n")
        for ladder, levels in library.ladders().items():
            print(f"  ladder {ladder}:")
            for level in levels:
                behavior = library.get_behavior(ladder, level)
                print(f"    {level}  {behavior.name}")
        loose = sorted(n for n, b in library.behaviors.items() if b.ladder is None)
        if loose:
            print("\n  not on a ladder:")
            for name in loose:
                print(f"       {name}")
        return 0

    if args.ladder is not None:
        if args.level is None:
            print("--ladder also needs --level", file=sys.stderr)
            return 2
        try:
            name = library.get_behavior(args.ladder, args.level).name
        except KeyError as exc:
            print(str(exc).strip('"'), file=sys.stderr)
            return 2
    elif args.name:
        name = args.name
        if name not in library.behaviors:
            match = difflib.get_close_matches(name, library.behaviors, n=1, cutoff=0.5)
            print(f'There is no behaviour called "{name}".', file=sys.stderr)
            if match:
                print(f'Did you mean "{match[0]}"?', file=sys.stderr)
            else:
                print("Run with --list to see them all.", file=sys.stderr)
            return 2
    else:
        parser.print_help()
        return 2

    print(preview(library, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
