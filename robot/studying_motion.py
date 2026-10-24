"""
A "studying" motion: Reachy Mini looks down at something in front of it and
reads it, line by line. Each line goes from the robot's left to its right in
small hops with short stops, the way eyes jump along words, then a quick
sweep back to the left for the next line, a little lower each time. At the
end of the page a small nod, as if it got it, and back to neutral.

Standalone, in .venv-robot (like greetings.py, disappointed_no.py, idea_motion.py):

    python robot/studying_motion.py                              # simulator / local daemon
    python robot/studying_motion.py --robot-host 192.168.50.60   # the wireless robot
    python robot/studying_motion.py --lines 6                    # read a longer page
    python robot/studying_motion.py --stay                       # stay looking at the page at the end

Wake the robot first if it is in its sleep pose (motors on + wake_up, see the
README). Angles are degrees: pitch + = looking down; yaw + = the robot's own
left, - = its right (eepy/perception/gaze/look_at.py). Antennas: 0 = up,
right negative / left positive = folded back.
"""

import argparse
import time

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

import connection
from safety_limits import HEAD_PITCH_ROLL_LIMIT_DEG, clamp_antennas

p = argparse.ArgumentParser(description="Reachy Mini reads something in front of it, line by line.")
connection.add_arg(p)
p.add_argument("--lines", type=int, default=4, help="how many lines it reads (default 4)")
p.add_argument("--stay", action="store_true", help="stay looking at the page at the end (no return to neutral)")
args = p.parse_args()

LINE_LEFT, LINE_RIGHT = 18, -18     # where a line starts (robot's left) and ends (its right)
HOPS = 4                            # small jumps along one line
HOP_SECONDS, STOP_SECONDS = 0.35, 0.25
FIRST_LINE_PITCH, LINE_STEP = 16, 2.5   # looking down at the page; each line a bit lower
TILT = 4                            # a slight head tilt: curious, absorbed
ANTENNAS = 25                       # antennas a little back: calm, concentrating


def move(mini, yaw=0.0, pitch=0.0, roll=0.0, z=0.0, antennas=0.0, duration=1.0, method="minjerk"):
    """One pose. antennas: how far both are folded back, in degrees (0 = up)."""
    pitch = float(np.clip(pitch, -HEAD_PITCH_ROLL_LIMIT_DEG, HEAD_PITCH_ROLL_LIMIT_DEG))
    roll = float(np.clip(roll, -HEAD_PITCH_ROLL_LIMIT_DEG, HEAD_PITCH_ROLL_LIMIT_DEG))
    r, l = clamp_antennas(np.deg2rad(-antennas), np.deg2rad(antennas))
    mini.goto_target(
        head=create_head_pose(yaw=yaw, pitch=pitch, roll=roll, z=z, mm=True),
        antennas=[r, l],
        duration=duration,
        method=method,
    )


with ReachyMini(**connection.connection_kwargs(args)) as mini:
    print("Robot connected: looking down at the page.")
    move(mini, duration=1.0)
    move(mini, yaw=LINE_LEFT, pitch=FIRST_LINE_PITCH, roll=TILT, antennas=ANTENNAS, duration=1.5)
    time.sleep(0.6)

    for line in range(args.lines):
        pitch = FIRST_LINE_PITCH + line * LINE_STEP
        print(f"Reading line {line + 1}...")
        for hop in range(1, HOPS + 1):              # along the line, left to right, in hops
            yaw = LINE_LEFT + (LINE_RIGHT - LINE_LEFT) * hop / HOPS
            move(mini, yaw=yaw, pitch=pitch, roll=TILT, antennas=ANTENNAS, duration=HOP_SECONDS, method="ease_in_out")
            time.sleep(STOP_SECONDS)
        if line + 1 < args.lines:                   # quick sweep back to the start of the next line
            move(mini, yaw=LINE_LEFT, pitch=pitch + LINE_STEP, roll=TILT, antennas=ANTENNAS, duration=0.5)
            time.sleep(0.2)

    print("Got it.")
    last = FIRST_LINE_PITCH + (args.lines - 1) * LINE_STEP
    move(mini, pitch=last, roll=TILT, antennas=ANTENNAS, duration=0.8)          # centre on the page
    move(mini, pitch=last + 6, roll=TILT, antennas=ANTENNAS, duration=0.3)      # a small nod
    move(mini, pitch=last - 2, roll=TILT, antennas=ANTENNAS, duration=0.4)
    time.sleep(0.6)

    if not args.stay:
        print("Back to neutral.")
        move(mini, duration=1.3)
