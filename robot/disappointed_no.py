"""
A disappointed "no": Reachy Mini starts neutral with its antennas up, first
lets its antennas sink, then shakes its head no in wide, slow swings while
the head slowly sinks, as if it is let down. It ends looking at the desk,
holds that for a moment, and comes back to neutral.

Standalone, in .venv-robot (like greetings.py):

    python robot/disappointed_no.py                              # simulator / local daemon
    python robot/disappointed_no.py --robot-host 192.168.50.60   # the wireless robot
    python robot/disappointed_no.py --stay                       # stay looking down at the end

Wake the robot first if it is in its sleep pose (motors on + wake_up, see the
README). Angles are degrees: pitch + = looking down, yaw = left/right.
Antennas: 0 = up; right negative / left positive = folded back and down (the
same direction as antenna_droop in behaviors/_blocks.yaml).
"""

import argparse
import time

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

import connection
from safety_limits import HEAD_PITCH_ROLL_LIMIT_DEG, clamp_antennas

p = argparse.ArgumentParser(description="Reachy Mini shakes its head no, disappointed.")
connection.add_arg(p)
p.add_argument("--stay", action="store_true", help="stay looking down at the end (no return to neutral)")
p.add_argument("--hold", type=float, default=1.5, help="seconds to hold the disappointed pose (default 1.5)")
args = p.parse_args()


def move(mini, yaw=0.0, pitch=0.0, roll=0.0, z=0.0, droop=0.0, duration=1.0, method="minjerk"):
    """One pose. droop: 0 = antennas up, 1 = folded down like antenna_droop (160 deg)."""
    pitch = float(np.clip(pitch, -HEAD_PITCH_ROLL_LIMIT_DEG, HEAD_PITCH_ROLL_LIMIT_DEG))
    roll = float(np.clip(roll, -HEAD_PITCH_ROLL_LIMIT_DEG, HEAD_PITCH_ROLL_LIMIT_DEG))
    right, left = clamp_antennas(np.deg2rad(-160 * droop), np.deg2rad(160 * droop))
    mini.goto_target(
        head=create_head_pose(yaw=yaw, pitch=pitch, roll=roll, z=z, mm=True),
        antennas=[right, left],
        duration=duration,
        method=method,
    )


# The shake: (yaw, pitch, seconds), after the antennas are already down.
# Wide swings (35 deg each way, well inside the 65 deg head-vs-body limit)
# that get smaller and slower while the head sinks: the energy drains out of
# the "no".
SHAKE = [
    (35, 3, 0.9),
    (-35, 6, 1.1),
    (30, 9, 1.1),
    (-28, 12, 1.2),
    (23, 15, 1.2),
    (-18, 18, 1.3),
    (12, 20, 1.3),
    (-6, 22, 1.4),
]
DROOP = 1.0        # how far the antennas sink (1 = antenna_droop, fully folded)

with ReachyMini(**connection.connection_kwargs(args)) as mini:
    print("Robot connected: neutral, antennas up.")
    move(mini, duration=1.2)
    time.sleep(0.4)                                   # a beat before the reaction

    print("Antennas down...")
    move(mini, droop=DROOP, duration=2.0)
    time.sleep(0.5)

    print("No...")
    for yaw, pitch, seconds in SHAKE:
        move(mini, yaw=yaw, pitch=pitch, droop=DROOP, duration=seconds, method="ease_in_out")

    # Settle: centred, head down, a slight sag and tilt, antennas a bit lower still.
    move(mini, pitch=25, roll=-6, z=-6, droop=0.85, duration=1.8)
    time.sleep(args.hold)

    if not args.stay:
        print("Back to neutral.")
        move(mini, duration=1.5)
