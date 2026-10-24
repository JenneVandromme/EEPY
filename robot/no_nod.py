"""
A plain "no": Reachy Mini shakes its head left and right three times, each
shake a little smaller than the one before, then comes back to neutral.
Neutral, not sad: for the let-down version see disappointed_no.py.

Standalone, in .venv-robot (like greetings.py and the other motion scripts):

    python robot/no_nod.py                              # simulator / local daemon
    python robot/no_nod.py --robot-host 192.168.50.60   # the wireless robot
    python robot/no_nod.py --shakes 2                   # fewer shakes

Wake the robot first if it is in its sleep pose (motors on + wake_up, see the
README). Angles are degrees: yaw + = the robot's own left, - = its right.
Antennas: 0 = up; right negative / left positive = folded back.
"""

import argparse
import time

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

import connection
from safety_limits import clamp_antennas

p = argparse.ArgumentParser(description="Reachy Mini shakes its head no.")
connection.add_arg(p)
p.add_argument("--shakes", type=int, default=3, help="how many left-right shakes (default 3)")
args = p.parse_args()

SWING = 22                  # degrees to each side (well inside the 65 deg head-vs-body limit)
SHRINK = 0.75               # each shake this much of the one before
SWING_SECONDS = 0.32        # one swing to a side
ANTENNAS = 10               # antennas a touch back: firm, not sad


def move(mini, yaw=0.0, antennas=0.0, duration=1.0, method="minjerk"):
    """One pose. antennas: how far both are folded back, in degrees (0 = up)."""
    r, l = clamp_antennas(np.deg2rad(-antennas), np.deg2rad(antennas))
    mini.goto_target(head=create_head_pose(yaw=yaw), antennas=[r, l], duration=duration, method=method)


with ReachyMini(**connection.connection_kwargs(args)) as mini:
    print("Robot connected: neutral.")
    move(mini, duration=1.0)

    print("No.")
    size = 1.0
    for _ in range(args.shakes):
        move(mini, yaw=SWING * size, antennas=ANTENNAS, duration=SWING_SECONDS, method="ease_in_out")
        move(mini, yaw=-SWING * size, antennas=ANTENNAS, duration=SWING_SECONDS, method="ease_in_out")
        size *= SHRINK

    time.sleep(0.2)
    print("Back to neutral.")
    move(mini, duration=0.8)
