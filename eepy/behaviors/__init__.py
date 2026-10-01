"""Reading, checking and previewing the behaviour files in behaviors/.

Nothing here controls the robot. This package turns YAML into typed Python
objects, checks it against the robot's real limits, and prints what a behaviour
would do. Playing a behaviour is a separate job, still to be built.

    schema.py    the file format, as pydantic models -- the source of truth
    loader.py    reads behaviors/*.yaml and resolves blocks into flat timelines
    limits.py    the robot's limits, read from robot/safety_limits.py
    reach.py     "can the head actually hold this pose?", via the real IK
    validate.py  python -m eepy.behaviors.validate
    preview.py   python -m eepy.behaviors.preview <name>
"""
