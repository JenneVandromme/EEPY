"""
An "I've got an idea!" moment: Reachy Mini starts low, head down and both
antennas folded as far back as they go. Then the right antenna springs up,
then the left one, and then the head comes up, a bit above neutral, bright
and alert. It holds that for a moment and settles back to neutral.

Standalone, in .venv-robot (like greetings.py and disappointed_no.py):

    python robot/idea_motion.py                              # simulator / local daemon
    python robot/idea_motion.py --robot-host 192.168.50.60   # the wireless robot
    python robot/idea_motion.py --stay                       # stay in the "idea" pose at the end

Wake the robot first if it is in its sleep pose (motors on + wake_up, see the
README). Angles are degrees: pitch + = looking down, - = looking up.
Antennas: 0 = straight up; right negative / left positive = folded back and
down (the direction of antenna_droop in behaviors/_blocks.yaml and of the
SDK's sleep pose, which uses about 175).
"""

import argparse
import time

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

import connection
from safety_limits import HEAD_PITCH_ROLL_LIMIT_DEG, clamp_antennas

p = argparse.ArgumentParser(description="Reachy Mini gets an idea: antennas up one by one, then the head.")
connection.add_arg(p)
p.add_argument("--stay", action="store_true", help="stay in the idea pose at the end (no return to neutral)")
p.add_argument("--hold", type=float, default=1.5, help="seconds to hold the idea pose (default 1.5)")
args = p.parse_args()

FOLDED = 170        # antennas as far back as they go (the sleep pose uses ~175, the joint stops at 180)
LOW = dict(pitch=25, z=-10)      # head down and sunk a little
UP = dict(pitch=-8, z=8)         # head up, a bit above neutral: bright, "aha"


def move(mini, right=0.0, left=0.0, pitch=0.0, roll=0.0, z=0.0, duration=1.0, method="minjerk"):
    """One pose. right / left: how far each antenna is folded back, in degrees (0 = up)."""
    pitch = float(np.clip(pitch, -HEAD_PITCH_ROLL_LIMIT_DEG, HEAD_PITCH_ROLL_LIMIT_DEG))
    roll = float(np.clip(roll, -HEAD_PITCH_ROLL_LIMIT_DEG, HEAD_PITCH_ROLL_LIMIT_DEG))
    r, l = clamp_antennas(np.deg2rad(-right), np.deg2rad(left))
    mini.goto_target(
        head=create_head_pose(pitch=pitch, roll=roll, z=z, mm=True),
        antennas=[r, l],
        duration=duration,
        method=method,
    )


with ReachyMini(**connection.connection_kwargs(args)) as mini:
    print("Robot connected: head low, antennas folded back.")
    move(mini, right=FOLDED, left=FOLDED, **LOW, duration=2.0)
    time.sleep(1.0)                                   # a quiet, thinking moment

    print("Right antenna up...")
    move(mini, right=0, left=FOLDED, **LOW, duration=0.6, method="cartoon")
    time.sleep(0.4)

    print("Left antenna up...")
    move(mini, right=0, left=0, **LOW, duration=0.6, method="cartoon")
    time.sleep(0.4)

    print("Head up: idea!")
    move(mini, **UP, duration=0.8, method="cartoon")
    time.sleep(args.hold)

    if not args.stay:
        print("Back to neutral.")
        move(mini, duration=1.2)
