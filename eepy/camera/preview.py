"""See what EEPY's camera sees:  python -m eepy.camera.preview

    python -m eepy.camera.preview                   # the camera EEPY would use
    python -m eepy.camera.preview --camera laptop   # force the laptop camera
    python -m eepy.camera.preview --camera robot    # the robot's own camera
    python -m eepy.camera.preview --index 1         # another laptop camera

With the LAPTOP camera it shows the whole webcam picture and, in BLUE, the box
the robot sees: only what is inside the box goes to perception. Sit so your
face is well inside it. With the Wizard panel running, the box follows the
robot's head. A small copy of the robot's view sits in the corner.

With the ROBOT camera it shows the robot's picture as it is.

FACE FEATURES (eepy/perception, needs MediaPipe -- .venv-vision): the face's
landmarks, eye and mouth openness, head angles, and a flash when a blink, yawn
or nod is detected. L toggles the landmarks; --no-face turns it all off. With
the optional action units installed: a bar per AU (A toggles), with the device,
rate and time per result. GAZE: a small map of the work area with the current
gaze, ON WORK / AWAY and the mode (G toggles). What the values mean:
eepy/perception/README.md and eepy/perception/gaze/README.md.

Press Q or Esc (or close the window) to stop. Nothing is recorded or saved.
Run it in .venv-vision.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, List, Optional

WINDOW = "EEPY camera preview  (Q to quit)"
BLUE = (255, 0, 0)          # OpenCV colours are BGR: this is blue, as in BURTRAM
WHITE, BLACK, AMBER = (255, 255, 255), (0, 0, 0), (0, 165, 255)


def _text(cv2: Any, image: Any, lines: List[str], x: int, y: int, color: Any = WHITE,
          scale: float = 0.6) -> None:
    """Readable text on any background: dark outline, light fill."""
    for i, line in enumerate(lines):
        pos = (x, y + int(i * 26 * scale / 0.6))
        cv2.putText(image, line, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, BLACK, 4, cv2.LINE_AA)
        cv2.putText(image, line, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def _waiting_screen(cv2: Any, np: Any, camera: Any, seconds: float) -> Any:
    image = np.full((540, 960, 3), 40, dtype=np.uint8)
    lines = [f"Waiting for the {camera.describe()}...  ({seconds:.0f} s)", ""]
    if camera.error:
        lines += ["Problem:"] + camera.error.splitlines() + ["", "It keeps trying by itself. Fix the problem above,",
                                                                "or press Q to quit."]
    _text(cv2, image, lines, 24, 40, AMBER if camera.error else WHITE, 0.55)
    return image


def _draw(cv2: Any, frame: Any, fps: float, camera: Any) -> Any:
    from eepy.logs.clock import monotonic

    latency_ms = max(0.0, (monotonic() - frame.t) * 1000)
    if frame.full is None:                       # robot camera: the picture itself
        image = frame.image.copy()
        lines = [f"robot camera  {frame.width}x{frame.height}",
                 f"{fps:4.1f} fps   latency {latency_ms:4.0f} ms"]
        _text(cv2, image, lines, 12, 28)
        return image

    image = frame.full.copy()
    box = frame.box
    cv2.rectangle(image, (box.x, box.y), (box.x + box.w, box.y + box.h), BLUE, 3)
    _text(cv2, image, ["what the robot sees"], max(box.x + 6, 6), max(box.y + 24, 24), WHITE, 0.55)

    # Small copy of the robot's view, top-right: exactly what perception gets.
    height, width = image.shape[:2]
    inset_w = max(80, width // 4)
    inset_h = max(2, int(inset_w * frame.height / frame.width))
    inset = cv2.resize(frame.image, (inset_w, inset_h))
    x0, y0 = width - inset_w - 10, 10
    image[y0:y0 + inset_h, x0:x0 + inset_w] = inset
    cv2.rectangle(image, (x0 - 1, y0 - 1), (x0 + inset_w, y0 + inset_h), BLUE, 2)

    head = ("blue box follows the robot's head" if camera.head_known
            else "head position unknown: box centred (start the Wizard panel to move it)")
    lines = [f"laptop camera {width}x{height}  ->  robot view {frame.width}x{frame.height}",
             f"{fps:4.1f} fps   latency {latency_ms:4.0f} ms",
             head]
    _text(cv2, image, lines, 12, 28)
    if not box.inside(width, height):
        _text(cv2, image, ["part of the robot's view is outside the laptop camera (black)"],
              12, height - 16, AMBER)
    return image


def _face_pipeline(camera: Any, profile: Optional[str] = None, aus: bool = True,
                   user: Optional[str] = None) -> Any:
    """(FacePipeline, FaceOverlay), or (None, None) where MediaPipe is missing.
    Action units are added when installed (and the profile allows)."""
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        print("  (Face features need MediaPipe -- run this in .venv-vision. Showing the camera only.)")
        return None, None
    from eepy.perception import FacePipeline, default_extractors, load_config
    from eepy.perception.overlay import FaceOverlay

    config = load_config()
    extractors = default_extractors(config)
    au = None
    if aus:
        from eepy.perception.aus import ActionUnitExtractor
        from eepy.perception.aus import load_config as load_au_config

        au = ActionUnitExtractor(load_au_config(profile=profile), config)
        extractors.append(au)
    from eepy.perception.gaze import GazeExtractor, resolve_user
    from eepy.perception.gaze import load_config as load_gaze_config

    gaze = GazeExtractor(load_gaze_config(), config, user=resolve_user(user))
    extractors.append(gaze)
    from eepy.perception.baseline import StoredBaselineProvider

    baseline = StoredBaselineProvider(config, user=gaze.user, source=camera.name)
    pipeline = FacePipeline(camera, config, extractors=extractors, baseline=baseline)
    # The user's state (A13), live, with its reason. Shown only: nothing is recorded.
    from eepy.state.config import RulesConfigError
    from eepy.state.estimate import make_estimator
    from eepy.state.runner import StateFromPipeline, StateTracker

    try:
        estimator = make_estimator("rules")
        tracker = StateTracker(estimator, gap_seconds=estimator.config.gap_seconds)
        pipeline.subscribe(StateFromPipeline(tracker, config.windows))
    except RulesConfigError as exc:
        print(f"  (No user state: {exc})")
        tracker = None
    return pipeline, FaceOverlay(aus=au, gaze=gaze, baseline=baseline, state=tracker)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.camera.preview",
                                     description="Show what EEPY's camera sees. Nothing is recorded.")
    parser.add_argument("--camera", choices=("auto", "robot", "laptop", "fake"),
                        help="which camera (default: camera.source in config/eepy.yaml)")
    parser.add_argument("--index", type=int, help="laptop camera number (see python -m eepy.camera.list)")
    parser.add_argument("--no-face", action="store_true", help="camera only, no face features")
    parser.add_argument("--no-aus", action="store_true", help="face features without action units")
    parser.add_argument("--profile", help="machine profile for action units (default: EEPY_PROFILE, else laptop)")
    parser.add_argument("--user", help="whose gaze calibration to use (default: the running session's "
                                       "participant, else 'default')")
    parser.add_argument("--seconds", type=float, default=0,
                        help="stop by itself after this many seconds (default: run until Q)")
    args = parser.parse_args(argv)

    import logging

    from . import open_camera
    from .config import CameraConfigError
    from .errors import CameraError
    from .laptop import import_cv2

    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    try:
        cv2 = import_cv2()
        import numpy as np
        camera = open_camera(args.camera, keep_full=True,
                             **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    try:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    except cv2.error:
        print("\n  This OpenCV cannot open windows (opencv-python-headless is installed).\n"
              "      pip uninstall -y opencv-python-headless && pip install opencv-python", file=sys.stderr)
        return 2

    try:
        pipeline, overlay = (None, None) if args.no_face else \
            _face_pipeline(camera, args.profile, aus=not args.no_aus, user=args.user)
    except CameraConfigError as exc:      # e.g. an unknown --profile
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    print(f"\n  Opening the {camera.describe()}... Press Q or Esc in the window to stop.")
    (pipeline or camera).start()
    started = time.monotonic()
    frame, result, fps, last_shown, sized = None, None, 0.0, None, False
    try:
        while True:
            if pipeline is not None and pipeline.error:
                print(f"\n  Face features are off: {pipeline.error}", file=sys.stderr)
                pipeline, overlay = None, None
            if pipeline is not None:
                newest_result = pipeline.wait_for_result(newer_than=result, timeout=0.1)
                newest = newest_result.observation.frame if newest_result is not None else None
                if newest_result is not None:
                    result = newest_result
                    overlay.note(result)
            else:
                newest = camera.wait_for_frame(newer_than=frame, timeout=0.1)
            if newest is not None:
                now = time.monotonic()
                if last_shown is not None:
                    fps = 0.85 * fps + 0.15 / max(now - last_shown, 1e-3) if fps else 1 / max(now - last_shown, 1e-3)
                frame, last_shown = newest, now
                image = _draw(cv2, frame, fps, camera)
                if overlay is not None and result is not None:
                    offset = (frame.box.x, frame.box.y) if frame.full is not None and frame.box else (0, 0)
                    overlay.draw(cv2, image, result, offset)
                if not sized:
                    cv2.resizeWindow(WINDOW, min(image.shape[1], 1280), min(image.shape[0], 800))
                    sized = True
                cv2.imshow(WINDOW, image)
            elif frame is None or camera.status == "reconnecting":
                cv2.imshow(WINDOW, _waiting_screen(cv2, np, camera, time.monotonic() - started))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if overlay is not None:
                overlay.key(key)
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break   # the window was closed
            if args.seconds and time.monotonic() - started > args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if pipeline is not None:
            pipeline.stop()
        camera.stop()
        cv2.destroyAllWindows()
        cv2.waitKey(1)
    if frame is None and camera.error:
        print(f"\n  No picture arrived. The last problem was:\n  {camera.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
