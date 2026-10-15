"""The launcher: one page that starts, watches and stops EEPY's programs.

    python3 launch.py            # or: python3 -m eepy.launcher

It starts the SAME commands you would type in separate terminals (the simulator,
the Wizard panel, the vision program), each in its own virtualenv, and shows their
status, their output, and the panel and the live HMM page in tabs. The terminal
commands keep working as a fallback: the Developer tab shows each one, ready to
copy. A program you started in a terminal yourself shows as "running in a
terminal"; the launcher leaves it alone.

Python 3.9, standard library only: it runs from the system python3, outside both
virtualenvs.
"""
