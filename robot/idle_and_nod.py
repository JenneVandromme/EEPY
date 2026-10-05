"""
Idle & Approving Nod motions for EEPY (Reachy Mini).

Designed for the "Idle + Approving Nod" task:
1. Calm "studying along" idle motion:
   - Gentle, subtle breathing cycle (slow vertical rise and fall of the head).
   - "Studying along": gentle glance down toward notes/desk, subtle page scan,
     and quiet companion presence without being distracting.
2. Small approving nod:
   - Looks up to make eye contact when the user returns to work.
   - A warm, encouraging nod (affirmation).
   - A subtle, friendly antenna perk.
   - Smooth return to neutral.

Safety:
Every head pose and body yaw target is checked and clamped through
`safety_limits.py`'s `enforce()` function, and antenna angles are checked
through `safety_limits.clamp_antennas()`. This guarantees the robot never
exceeds its physical mechanical limits or neck-twist constraints.

Usage:
    python robot/idle_and_nod.py                    # Play idle then approving nod
    python robot/idle_and_nod.py --motion idle      # Play only the idle motion
    python robot/idle_and_nod.py --motion nod       # Play only the approving nod
    python robot/idle_and_nod.py --loop             # Loop idle continuously (Ctrl+C to stop)
    python robot/idle_and_nod.py --robot-host <ip>  # Wireless robot
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

# Ensure robot directory is on sys.path for direct script execution
_ROBOT_DIR = Path(__file__).resolve().parent
if str(_ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROBOT_DIR))

import connection
import safety_limits

# --- Resting / Default Poses ------------------------------------------------
# Antennas rest at -10 deg (right) and +10 deg (left), mirrored for symmetry.
NEUTRAL_ANTENNAS_DEG = (-10.0, 10.0)


def move_safely(
    mini: ReachyMini,
    head_pose: np.ndarray,
    antennas_deg: tuple[float, float] | list[float] | None = None,
    body_yaw_deg: float | None = None,
    current_body_yaw_rad: float = 0.0,
    duration: float = 2.0,
    method: str = "minjerk",
) -> float:
    """Send a target pose to Reachy Mini, strictly clamped by safety_limits.py.

    Args:
        mini: Connected ReachyMini instance.
        head_pose: 4x4 pose matrix (e.g. from create_head_pose).
        antennas_deg: (right_deg, left_deg) angles in degrees, or None.
        body_yaw_deg: Target body yaw in degrees, or None.
        current_body_yaw_rad: Current body yaw in radians (for delta check).
        duration: Duration of movement in seconds.
        method: Interpolation curve ("minjerk", "ease_in_out", etc.).

    Returns:
        float: The updated body yaw in radians.
    """
    body_target_rad = np.deg2rad(body_yaw_deg) if body_yaw_deg is not None else None

    # Enforce joint rotation limits & head-vs-body delta constraint
    safe_head, safe_body = safety_limits.enforce(
        head_pose=head_pose,
        body_yaw_target=body_target_rad,
        current_body_yaw=current_body_yaw_rad,
    )

    kwargs: dict[str, Any] = {
        "head": safe_head,
        "duration": duration,
        "method": method,
        "body_yaw": safe_body,
    }

    if safe_body is not None:
        current_body_yaw_rad = safe_body

    if antennas_deg is not None:
        right_rad = np.deg2rad(antennas_deg[0])
        left_rad = np.deg2rad(antennas_deg[1])
        safe_right, safe_left = safety_limits.clamp_antennas(right_rad, left_rad)
        kwargs["antennas"] = [safe_right, safe_left]

    mini.goto_target(**kwargs)
    return current_body_yaw_rad


def play_studying_idle(
    mini: ReachyMini,
    cycles: int = 2,
    current_body_yaw_rad: float = 0.0,
) -> float:
    """Play the calm 'studying along' idle motion.

    Choreography:
      1. Gentle breathing: the head subtly rises (+3mm, pitch -1.5 deg) and
         settles back down, repeated smoothly.
      2. Studying along: head tilts down (pitch +16 deg) toward notes on the desk,
         takes a tiny reading shift (yaw +4 deg -> -2 deg), holds focus,
         and softly returns to neutral.
    """
    print("  Playing calm 'studying along' idle motion...")

    for c in range(cycles):
        # Breathe in: slight gentle rise and tiny back-tilt (felt, not watched)
        pose_in = create_head_pose(pitch=-2, z=3, mm=True)
        current_body_yaw_rad = move_safely(
            mini,
            head_pose=pose_in,
            antennas_deg=NEUTRAL_ANTENNAS_DEG,
            current_body_yaw_rad=current_body_yaw_rad,
            duration=2.2,
            method="minjerk",
        )

        # Breathe out: settle gently back to neutral baseline
        pose_out = create_head_pose(pitch=0, z=0, mm=True)
        current_body_yaw_rad = move_safely(
            mini,
            head_pose=pose_out,
            antennas_deg=NEUTRAL_ANTENNAS_DEG,
            current_body_yaw_rad=current_body_yaw_rad,
            duration=2.6,
            method="minjerk",
        )

    # Glance down at notes / desk surface as if reading along
    pose_look_notes = create_head_pose(pitch=16, yaw=3, z=0, mm=True)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_look_notes,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=1.4,
        method="minjerk",
    )

    # Subtle scan across the page
    pose_scan = create_head_pose(pitch=16, yaw=-3, z=0, mm=True)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_scan,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=1.2,
        method="minjerk",
    )
    time.sleep(0.8)  # Hold focus on reading

    # Smoothly return to level neutral rest pose
    pose_neutral = create_head_pose(pitch=0, yaw=0, roll=0, z=0, mm=True)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_neutral,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=1.5,
        method="minjerk",
    )

    return current_body_yaw_rad


def play_approving_nod(
    mini: ReachyMini,
    current_body_yaw_rad: float = 0.0,
) -> float:
    """Play the small approving nod when the user gets back to work.

    Choreography:
      1. Glance up at user: establishes eye contact (pitch -12 deg).
      2. Approving nod: dips chin down warmly (pitch +6 deg) and returns up.
      3. Antenna perk: subtle cheerful outward perk (-25 deg, +25 deg) and
         settles back to resting (-10 deg, +10 deg).
      4. Return to neutral: ready to resume quiet companion idle.
    """
    print("  Playing small approving nod...")

    # 1. Look up at the student's face
    pose_glance = create_head_pose(pitch=-12, yaw=0, roll=0, z=0, mm=True)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_glance,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=1.0,
        method="minjerk",
    )
    time.sleep(0.4)  # Hold eye contact briefly

    # 2. Approving nod (chin dips down warmly)
    pose_nod_down = create_head_pose(pitch=6, yaw=0, roll=0, z=0, mm=True)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_nod_down,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=0.5,
        method="minjerk",
    )

    # Return head back up
    pose_nod_up = create_head_pose(pitch=-10, yaw=0, roll=0, z=0, mm=True)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_nod_up,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=0.6,
        method="minjerk",
    )

    # 3. Friendly antenna perk (encouraging cheer)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_nod_up,
        antennas_deg=(-25.0, 25.0),
        current_body_yaw_rad=current_body_yaw_rad,
        duration=0.4,
        method="minjerk",
    )

    # Antennas settle back to resting position
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_nod_up,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=0.5,
        method="minjerk",
    )

    # 4. Smoothly return to level neutral rest pose
    pose_neutral = create_head_pose(pitch=0, yaw=0, roll=0, z=0, mm=True)
    current_body_yaw_rad = move_safely(
        mini,
        head_pose=pose_neutral,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=1.2,
        method="minjerk",
    )

    return current_body_yaw_rad


def return_to_neutral(
    mini: ReachyMini,
    current_body_yaw_rad: float = 0.0,
) -> float:
    """Safely return the robot to forward-facing, level neutral pose."""
    pose_neutral = create_head_pose(pitch=0, yaw=0, roll=0, z=0, mm=True)
    return move_safely(
        mini,
        head_pose=pose_neutral,
        antennas_deg=NEUTRAL_ANTENNAS_DEG,
        body_yaw_deg=0.0,
        current_body_yaw_rad=current_body_yaw_rad,
        duration=1.5,
        method="minjerk",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Play the calm studying idle motion and approving nod on Reachy Mini."
    )
    connection.add_arg(parser)
    parser.add_argument(
        "--motion",
        choices=["all", "idle", "nod"],
        default="all",
        help="Which motion to play: 'idle' (studying along), 'nod' (approving nod), or 'all' (default: all)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=2,
        help="Number of breathing cycles per idle session (default: 2)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Loop the idle motion continuously until stopped with Ctrl+C",
    )
    args = parser.parse_args()

    print("Connecting to Reachy Mini...")
    try:
        mini_ctx = ReachyMini(**connection.connection_kwargs(args))
    except (ConnectionError, Exception) as exc:
        print(f"\n[!] Could not connect to Reachy Mini: {exc}\n", file=sys.stderr)
        print(
            "Please make sure the Reachy Mini daemon is running:\n"
            "  1. Start the simulator in a separate terminal:\n"
            "     .venv-robot\\Scripts\\Activate.ps1\n"
            "     reachy-mini-daemon --sim\n"
            "  2. Or if using a real robot over USB:\n"
            "     reachy-mini-daemon\n"
            "  3. Or if using wireless:\n"
            "     python robot/idle_and_nod.py --robot-host <ip>\n",
            file=sys.stderr,
        )
        return 1

    with mini_ctx as mini:
        print("Connected! Initializing safe neutral pose...")
        current_body_yaw = return_to_neutral(mini, 0.0)

        try:
            if args.loop:
                print("Looping idle motion (press Ctrl+C to stop safely)...")
                while True:
                    current_body_yaw = play_studying_idle(
                        mini,
                        cycles=args.cycles,
                        current_body_yaw_rad=current_body_yaw,
                    )
                    time.sleep(1.0)
            elif args.motion == "idle":
                current_body_yaw = play_studying_idle(
                    mini,
                    cycles=args.cycles,
                    current_body_yaw_rad=current_body_yaw,
                )
            elif args.motion == "nod":
                current_body_yaw = play_approving_nod(
                    mini,
                    current_body_yaw_rad=current_body_yaw,
                )
            else:  # "all"
                current_body_yaw = play_studying_idle(
                    mini,
                    cycles=args.cycles,
                    current_body_yaw_rad=current_body_yaw,
                )
                print("Pausing before approving nod...")
                time.sleep(1.5)
                current_body_yaw = play_approving_nod(
                    mini,
                    current_body_yaw_rad=current_body_yaw,
                )

            print("Done! Robot returned to safe neutral pose.")
            return 0

        except KeyboardInterrupt:
            print("\nInterrupted by user. Safely returning to neutral pose...")
            return_to_neutral(mini, current_body_yaw)
            print("Safe neutral reached. Goodbye!")
            return 0


if __name__ == "__main__":
    sys.exit(main())
