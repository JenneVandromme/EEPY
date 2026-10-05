"""Calibrate the work area for one person and camera:  python -m eepy.perception.calibrate_gaze

    python -m eepy.perception.calibrate_gaze                 # you, this laptop's camera
    python -m eepy.perception.calibrate_gaze --user P03      # for participant P03
    python -m eepy.perception.calibrate_gaze --camera robot  # with the robot's camera
    python -m eepy.perception.calibrate_gaze --setup lab     # a particular desk

The screen goes black and shows dots: look at each one (centre, then the four
corners) until it turns green. Then it asks you to look at your notes and,
last, away to the side -- for those you cannot watch the screen, so it counts
down, records, and BEEPS when you may look back. About one minute.

It saves the person's work area to data/calibration/<user>/<setup>.json (never
committed; angles only, no pictures). Run it again whenever the camera, screen
or notes move. Q or Esc stops without saving; R repeats the current point.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, List, Optional

WINDOW = "EEPY gaze calibration  (Q: stop, R: repeat point)"
SETTLE_SECONDS = 1.2        # let the eyes arrive before recording


def _beep() -> None:
    print("\a", end="", flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.perception.calibrate_gaze",
                                     description="Calibrate where this person's work area is.")
    parser.add_argument("--user", help="whose calibration (default: the running session's participant, "
                                       "else 'default')")
    parser.add_argument("--setup", help="label for this desk/camera position, e.g. lab")
    parser.add_argument("--camera", choices=("auto", "robot", "laptop", "fake"))
    parser.add_argument("--index", type=int, help="laptop camera number")
    parser.add_argument("--no-save", action="store_true", help="show the result but do not save it")
    args = parser.parse_args(argv)

    import logging

    from eepy.camera import CameraConfigError, CameraError, open_camera
    from eepy.camera.laptop import import_cv2

    from .config import load
    from .gaze import CalibrationStore, GazeCalibration, GazeEstimator, resolve_user, setup_name
    from .gaze import load_config as load_gaze_config
    from .pipeline import FacePipeline

    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    try:
        cv2 = import_cv2()
        import numpy as np
        config, gaze_config = load(), load_gaze_config()
        camera = open_camera(args.camera, **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    except ImportError:
        print("\n  Run this in .venv-vision (it needs MediaPipe and OpenCV).", file=sys.stderr)
        return 2

    user = resolve_user(args.user)
    setup = setup_name(camera.name, args.setup)
    store = CalibrationStore()
    print(f"\n  Gaze calibration for user '{user}', camera setup '{setup}'.\n"
          f"  Sit as you normally work. The screen goes black: look at each dot.")
    pipeline = FacePipeline(camera, config).start()
    estimator = GazeEstimator(gaze_config)
    calibration = GazeCalibration(gaze_config)

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.waitKey(300)
    try:
        _, _, width, height = cv2.getWindowImageRect(WINDOW)
    except Exception:  # noqa: BLE001
        width, height = 0, 0
    if width < 400 or height < 300:
        width, height = 1280, 800

    result = None
    retries = 0
    try:
        while not calibration.done:
            step = calibration.step
            calibration.redo()
            on_screen = step.target is not None
            prepare = SETTLE_SECONDS if on_screen else 3.0
            started = time.monotonic()
            recording_from = started + prepare
            until = recording_from + step.seconds
            if not on_screen:
                _beep()
            repeat = False
            while time.monotonic() < until:
                now = time.monotonic()
                newest = pipeline.wait_for_result(newer_than=result, timeout=0.05)
                if newest is not None:
                    result = newest
                    if now >= recording_from:
                        closure = result.values.get("eye_closure")
                        calibration.add(estimator.estimate(result.observation, closure))
                canvas = _draw(cv2, np, width, height, step, calibration, now - started, prepare,
                               result, on_screen)
                cv2.imshow(WINDOW, canvas)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q"), 27):
                    print("\n  Stopped: nothing saved.")
                    return 1
                if key in (ord("r"), ord("R")):
                    repeat = True
                    break
            if repeat:
                continue
            if not on_screen:
                _beep()
            ok, why = calibration.step_quality()
            if not ok and retries < 2:
                retries += 1
                print(f"  {step.key}: {why} -- once more.")
                continue
            retries = 0
            calibration.next()
        try:
            section = calibration.finish()
        except ValueError as exc:
            print(f"\n  {exc}", file=sys.stderr)
            return 1
        _show_result(cv2, np, width, height, section)
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        cv2.waitKey(1)

    _report(section)
    if args.no_save:
        print("  (--no-save: not saved)\n")
        return 0
    path = store.save_section(user, setup, "gaze", dict(section, created=time.strftime("%Y-%m-%d %H:%M")))
    print(f"  Saved: {path}\n  The preview, self-test and recording use it for user '{user}' "
          f"with the {camera.name} camera.\n")
    return 0


def _draw(cv2: Any, np: Any, width: int, height: int, step: Any, calibration: Any, elapsed: float,
          prepare: float, result: Any, on_screen: bool) -> Any:
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    number = f"{calibration.index + 1}/{len(calibration.steps)}"
    recording = elapsed >= prepare
    face = result is not None and result.observation.present
    if on_screen:
        x, y = int(step.target[0] * width), int(step.target[1] * height)
        colour = (80, 220, 80) if recording else (255, 255, 255)
        if not recording:                                   # a ring that closes in on the dot
            radius = int(12 + 60 * max(0.0, 1 - elapsed / prepare))
            cv2.circle(canvas, (x, y), radius, (160, 160, 160), 2)
        cv2.circle(canvas, (x, y), 12, colour, -1)
        cv2.circle(canvas, (x, y), 3, (0, 0, 0), -1)
        text_y = height // 2 + 80 if step.target[1] < 0.5 else height // 2 - 80
        _centered(cv2, canvas, f"{number}  {step.instruction}", text_y, 0.8)
    else:
        left = max(0.0, prepare - elapsed)
        _centered(cv2, canvas, f"{number}  {step.instruction}", height // 2 - 40, 1.0)
        message = (f"recording in {left:.0f}... keep looking until the BEEP" if not recording
                   else "recording... keep looking until the BEEP")
        _centered(cv2, canvas, message, height // 2 + 20, 0.8, (80, 220, 80) if recording else (200, 200, 200))
    if not face:
        _centered(cv2, canvas, "no face found -- sit in view of the camera", height - 40, 0.7, (0, 165, 255))
    return canvas


def _centered(cv2: Any, canvas: Any, text: str, y: int, scale: float, colour: Any = (230, 230, 230)) -> None:
    (w, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.putText(canvas, text, ((canvas.shape[1] - w) // 2, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, 2,
                cv2.LINE_AA)


def _show_result(cv2: Any, np: Any, width: int, height: int, section: dict) -> None:
    """The learned zones on a map, for 4 seconds (or until a key)."""
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    zones = section["zones"]["gaze"]
    yaw_range, pitch_range = (-50.0, 50.0), (-40.0, 60.0)
    x0, y0, w, h = width // 2 - 300, height // 2 - 220, 600, 440

    def px(yaw: float, pitch: float) -> tuple:
        return (int(x0 + (-yaw - yaw_range[0]) / (yaw_range[1] - yaw_range[0]) * w),   # your left on the left
                int(y0 + (pitch - pitch_range[0]) / (pitch_range[1] - pitch_range[0]) * h))

    cv2.rectangle(canvas, (x0, y0), (x0 + w, y0 + h), (60, 60, 60), 1)
    for name, colour in (("screen", (255, 140, 40)), ("notes", (80, 220, 80))):
        rect = zones.get(name)
        if rect:
            cv2.rectangle(canvas, px(rect["yaw_min"], rect["pitch_min"]), px(rect["yaw_max"], rect["pitch_max"]),
                          colour, 2)
            cv2.putText(canvas, name, px(rect["yaw_min"], rect["pitch_min"]), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        colour, 2)
    for key, point in section["points"].items():
        if point["gaze"]:
            cv2.circle(canvas, px(*point["gaze"]), 5, (255, 255, 255), -1)
    _centered(cv2, canvas, "Your work area, as you see it. Done!", y0 - 30, 0.9)
    cv2.imshow(WINDOW, canvas)
    cv2.waitKey(4000)


def _report(section: dict) -> None:
    print("\n  Calibration points (gaze yaw, pitch in degrees; iris usable):")
    for key, point in section["points"].items():
        gaze = point["gaze"]
        where = f"{gaze[0]:+6.1f}, {gaze[1]:+6.1f}" if gaze else "   no face"
        print(f"    {key:20} {where}   iris {point['iris_share']:.0%}")
    screen, notes = section["zones"]["gaze"]["screen"], section["zones"]["gaze"]["notes"]
    print(f"\n  Screen zone: yaw {screen['yaw_min']:+.0f} .. {screen['yaw_max']:+.0f}, "
          f"pitch {screen['pitch_min']:+.0f} .. {screen['pitch_max']:+.0f}")
    if notes:
        print(f"  Notes zone:  yaw {notes['yaw_min']:+.0f} .. {notes['yaw_max']:+.0f}, "
              f"pitch {notes['pitch_min']:+.0f} .. {notes['pitch_max']:+.0f}")
    for warning in section["warnings"]:
        print(f"  ! {warning}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
