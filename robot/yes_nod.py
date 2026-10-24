"""
A friendly "yes": Reachy Mini perks its antennas up and nods three times,
each nod a little smaller than the one before, then comes back to neutral.

Standalone, in .venv-robot (like greetings.py and the other motion scripts):

    python robot/yes_nod.py                              # simulator / local daemon
    python robot/yes_nod.py --robot-host 192.168.50.60   # the wireless robot
    python robot/yes_nod.py --nods 2                     # fewer nods

Wake the robot first if it is in its sleep pose (motors on + wake_up, see the
README). Angles are degrees: pitch + = looking down, - = looking up.
Antennas: 0 = up; right negative / left positive = folded back.
"""

import argparse
import time

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

import connection
from safety_limits import HEAD_PITCH_ROLL_LIMIT_DEG, clamp_antennas

p = argparse.ArgumentParser(description="Reachy Mini nods yes.")
connection.add_arg(p)
p.add_argument("--nods", type=int, default=3, help="how many nods (default 3)")
args = p.parse_args()

DOWN, UP = 14, -5           # how far each nod goes down, and back up past neutral
SHRINK = 0.75               # each nod this much of the one before
NOD_SECONDS = 0.28          # one half of a nod (down, or up)
PERK = -12                  # antennas a touch forward: eager, agreeing (0 = straight up)


def move(mini, pitch=0.0, antennas=0.0, duration=1.0, method="minjerk"):
    """One pose. antennas: how far both are folded back, in degrees (negative = forward)."""
    pitch = float(np.clip(pitch, -HEAD_PITCH_ROLL_LIMIT_DEG, HEAD_PITCH_ROLL_LIMIT_DEG))
    r, l = clamp_antennas(np.deg2rad(-antennas), np.deg2rad(antennas))
    mini.goto_target(head=create_head_pose(pitch=pitch), antennas=[r, l], duration=duration, method=method)


with ReachyMini(**connection.connection_kwargs(args)) as mini:
    print("Robot connected: neutral.")
    move(mini, duration=1.0)
    move(mini, antennas=PERK, duration=0.4, method="cartoon")

    print("Yes!")
    size = 1.0
    for _ in range(args.nods):
        move(mini, pitch=DOWN * size, antennas=PERK, duration=NOD_SECONDS, method="ease_in_out")
        move(mini, pitch=UP * size, antennas=PERK, duration=NOD_SECONDS, method="ease_in_out")
        size *= SHRINK

    time.sleep(0.3)
    print("Back to neutral.")
    move(mini, duration=0.8)
