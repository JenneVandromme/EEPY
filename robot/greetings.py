import argparse

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

import connection

p = argparse.ArgumentParser(description="Play a scripted greeting sequence on Reachy Mini.")
connection.add_arg(p)
args = p.parse_args()

with ReachyMini(**connection.connection_kwargs(args)) as mini:
    print("Robot connected!")

    mini.goto_target(
        head=create_head_pose(z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([0, 0]),           # Antennas out
        body_yaw=np.deg2rad(0),                 # Turn body
        duration=2.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )


    mini.goto_target(
        head=create_head_pose(yaw=-115, z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([45, 45]),           # Antennas out
        body_yaw=np.deg2rad(-70),                 # Turn body
        duration=2.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )

    mini.goto_target(
        head=create_head_pose(yaw= 0, z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([0, 0]),           # Antennas out
        body_yaw=np.deg2rad(0),                 # Turn body
        duration=2.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )
    mini.goto_target(
        head=create_head_pose(yaw= 135, z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([-45, -45]),           # Antennas out
        body_yaw=np.deg2rad(90),                 # Turn body
        duration=2.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )
    mini.goto_target(
        head=create_head_pose(yaw= 135, z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([-45, 90]),           # Antennas out
        body_yaw=np.deg2rad(90),                 # Turn body
        duration=1.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )
    mini.goto_target(
        head=create_head_pose(yaw= 135, z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([-45, -45]),           # Antennas out
        body_yaw=np.deg2rad(90),                 # Turn body
        duration=1.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )
    mini.goto_target(
        head=create_head_pose(yaw= 135, z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([-45, 90]),           # Antennas out
        body_yaw=np.deg2rad(90),                 # Turn body
        duration=1.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )

    mini.goto_target(
        head=create_head_pose(yaw= 135, z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([-45, -45]),           # Antennas out
        body_yaw=np.deg2rad(90),                 # Turn body
        duration=1.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )


    mini.goto_target(
        head=create_head_pose(z=10, mm=True),    # Up 10mm
        antennas=np.deg2rad([0, 0]),           # Antennas out
        body_yaw=np.deg2rad(0),                 # Turn body
        duration=3.0,                            # Take 2 seconds
        method="minjerk"                         # Smooth acceleration
    )
