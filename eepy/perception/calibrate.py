"""Calibrate one person for this camera:  python -m eepy.perception.calibrate

    python -m eepy.perception.calibrate --user P03          # the whole flow (~2 minutes)
    python -m eepy.perception.calibrate --only gaze         # only the screen / notes zones
    python -m eepy.perception.calibrate --only baseline     # only the minute of normal work
    python -m eepy.perception.calibrate --camera robot      # with the robot's camera

The flow: a face check (sit in view, in good light), the gaze dots (look at the
centre and corners of the screen, then your notes, then away), and one minute of
normal work. EEPY says what to do -- through the robot when the Wizard panel is
running, else this computer's voice -- and shows it on screen. Q stops.

If this person already has a recent calibration for this camera (14 days), it
asks whether to reuse it. The result is saved per person and camera in
data/calibration/ (numbers only, never committed) and logged in the running
session, if any. During a session the Wizard panel can start this flow too.
See eepy/perception/calibration/README.md.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None, prog: str = "python -m eepy.perception.calibrate") -> int:
    parser = argparse.ArgumentParser(prog=prog, description="Calibrate one person for this camera.")
    parser.add_argument("--user", help="the participant code (default: the running session's, else 'default')")
    parser.add_argument("--setup", help="label for this desk / camera position, e.g. lab")
    parser.add_argument("--camera", choices=("auto", "robot", "laptop", "fake"))
    parser.add_argument("--index", type=int, help="laptop camera number")
    parser.add_argument("--only", choices=("gaze", "baseline"),
                        help="run only this part (plus the quick face check)")
    parser.add_argument("--recalibrate", action="store_true", help="do not offer to reuse a recent one")
    parser.add_argument("--no-save", action="store_true", help="show the result but do not save it")
    parser.add_argument("--profile", help="machine profile, for action units (default: EEPY_PROFILE)")
    args = parser.parse_args(argv)

    import logging

    from eepy.camera import CameraConfigError, CameraError, open_camera
    from eepy.camera.laptop import import_cv2
    from eepy.camera.relay import RelayClient
    from eepy.logs.features import ProcessEventLog

    from .baseline import BaselineProvider
    from .calibration import log_record, recent
    from .calibration.screen import CalibrationScreen
    from .calibration.session import perform
    from .calibration.voice import Voice
    from .config import load
    from .extractors import default_extractors
    from .gaze import CalibrationStore, GazeExtractor, resolve_user, setup_name
    from .gaze import load_config as load_gaze_config
    from .pipeline import FacePipeline

    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    try:
        cv2 = import_cv2()
        import numpy as np
        config, gaze_config = load(), load_gaze_config()
        camera = open_camera(args.camera, keep_full=True,
                             **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    except ImportError:
        print("\n  Run this in .venv-vision (it needs MediaPipe and OpenCV).", file=sys.stderr)
        return 2

    user = resolve_user(args.user)
    setup = setup_name(camera.name, args.setup)
    store = CalibrationStore()
    events = ProcessEventLog("vision")
    parts = ["face", args.only] if args.only else ["face", "gaze", "baseline"]

    previous = None if (args.recalibrate or args.only) else recent(store, user, setup)
    if previous is not None:
        late = "  (taken late in the evening: they may already have been tired)" if previous["late_evening"] else ""
        print(f"\n  '{user}' already has a calibration for '{setup}' from {previous['taken_at']} "
              f"({previous['age_days']:.0f} days ago).{late}")
        answer = input("  Reuse it? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes", "j", "ja"):
            events.event("calibration", **log_record(user, setup, "reused"),
                         taken_at=previous["taken_at"], late_evening=previous["late_evening"])
            print("  Reused. Nothing else to do.\n")
            events.close()
            return 0

    extractors = default_extractors(config)
    gaze = GazeExtractor(gaze_config, config, user=user, setup=args.setup)
    extractors.append(gaze)
    try:
        from .aus import ActionUnitExtractor, aus_available
        from .aus import load_config as load_au_config

        if aus_available()[0]:
            extractors.append(ActionUnitExtractor(load_au_config(profile=args.profile), config))
    except CameraConfigError:
        pass
    # During the calibration everything is measured against the DEFAULT baseline.
    pipeline = FacePipeline(camera, config, extractors=extractors, baseline=BaselineProvider(config)).start()
    voice = Voice(RelayClient(camera.config.relay_port, timeout=0.5))
    print(f"\n  Calibrating '{user}' with camera setup '{setup}': {', '.join(parts)}. "
          "Follow the screen. Q stops.")
    screen = CalibrationScreen(cv2, np)
    try:
        outcome, flow = perform(pipeline, user=user, source=camera.name, setup_label=args.setup,
                                parts=parts, store=store, voice=voice, screen=screen, gaze=gaze,
                                events=events, save_result=not args.no_save)
    finally:
        screen.close()
        pipeline.stop()
        voice.close()
        events.close()
    return _report(outcome, flow, store, user, setup, args.no_save)


def _report(outcome: str, flow, store, user: str, setup: str, no_save: bool) -> int:
    if outcome == "stopped":
        print("\n  Stopped: nothing saved.\n")
        return 1
    if outcome == "failed":
        print(f"\n  Calibration did not succeed:\n  {flow.message}\n")
        if flow.checks:
            print("  The checks (measured values), for whoever looks into it:")
            for check in flow.checks:
                mark = "ok  " if check["ok"] else ("note" if check["kind"] == "warning" else "FAIL")
                print(f"    {mark} {check['check']:22} {check['measured']}")
            print()
        return 1
    baseline = flow.result.get("baseline")
    if baseline:
        v = baseline["values"]
        print("\n  Baseline (your normal face, over "
              f"{baseline['seconds']:.0f} s at {baseline['fps']:.0f} fps):")
        print(f"    eyes open (EAR)   {v['ear_open']}  (left {v['ear_open_left']}, right {v['ear_open_right']})"
              f"   closed {v['ear_floor']}")
        print(f"    blinks            {v['blink_rate']} per minute, {v['blink_ms']} ms")
        print(f"    mouth at rest     {v['mar_closed']}")
        print(f"    head neutral      yaw {v['yaw0']:+}  pitch {v['pitch0']:+}  roll {v['roll0']:+} deg")
        print(f"    face size         {v['face_size0']}")
        if v.get("au_rest"):
            print("    AUs at rest       " + "  ".join(f"{k} {x:.2f}" for k, x in v["au_rest"].items()))
        for check in baseline["quality"]:
            if not check["ok"]:
                print(f"    ! {check['message']}")
        if baseline["late_evening"]:
            print("    ! Taken late in the evening: this person may already be tired, so 'tired' "
                  "may look normal. Prefer calibrating earlier in the day.")
    gaze = flow.result.get("gaze")
    if gaze:
        for warning in gaze["warnings"]:
            print(f"    ! {warning}")
    if not no_save:
        print(f"\n  Saved: {store.path(user, setup)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
