"""
Sanity check for the robot/ environment: connects to a Reachy Mini daemon
(simulator or real robot) and plays a small, safe greeting movement.

If this runs without errors and you see the robot (or the simulator
viewer) move, your robot/ setup is working. See the README for how to
start the daemon first.

Usage:
    python robot/test_connection.py                       # local daemon
    python robot/test_connection.py --robot-host 192.168.1.50   # wireless
"""

import argparse

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

import connection

parser = argparse.ArgumentParser(description=__doc__)
connection.add_arg(parser)
args = parser.parse_args()

with ReachyMini(**connection.connection_kwargs(args)) as mini:
    print("Connected! Playing a small greeting movement...")

    mini.goto_target(
        head=create_head_pose(z=10, mm=True),
        antennas=np.deg2rad([0, 0]),
        body_yaw=np.deg2rad(0),
        duration=1.5,
        method="minjerk",
    )
    mini.goto_target(
        head=create_head_pose(yaw=25, z=10, mm=True),
        antennas=np.deg2rad([30, -30]),
        body_yaw=np.deg2rad(0),
        duration=1.5,
        method="minjerk",
    )
    mini.goto_target(
        head=create_head_pose(z=10, mm=True),
        antennas=np.deg2rad([0, 0]),
        body_yaw=np.deg2rad(0),
        duration=1.5,
        method="minjerk",
    )

    print("Done. If you saw it move, the connection works.")
