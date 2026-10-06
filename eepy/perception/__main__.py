"""The vision process:  python -m eepy.perception

Runs the camera and the face pipeline, and records into the session the Wizard
panel is running (features.csv, events.vision.jsonl). Outside a session it
still runs, but records nothing. Start it next to the panel, in .venv-vision.
Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.perception",
                                     description="Run face perception and record it into the panel's session.")
    parser.add_argument("--camera", choices=("auto", "robot", "laptop", "fake"),
                        help="which camera (default: camera.source in config/eepy.yaml)")
    parser.add_argument("--index", type=int, help="laptop camera number")
    parser.add_argument("--profile", help="machine profile for action units: laptop, demo-desktop, "
                                          "... (default: EEPY_PROFILE in .env, else laptop)")
    parser.add_argument("--no-aus", action="store_true", help="no action units, even if installed")
    parser.add_argument("--user", help="whose calibration (default: each session's participant)")
    parser.add_argument("--setup", help="camera-setup label of the gaze calibration (e.g. lab)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="  %(message)s")

    from eepy.camera import CameraConfigError, CameraError, open_camera
    from eepy.logs.features import FeatureStream, ProcessEventLog

    from .aus import ActionUnitExtractor
    from .aus import load_config as load_au_config
    from .config import load
    from .extractors import all_columns, default_extractors
    from .pipeline import FacePipeline

    try:
        config = load()
        au_config = load_au_config(profile=args.profile)
        events = ProcessEventLog("vision")
        # keep_full: the calibration's face check shows the blue box in the whole picture.
        camera = open_camera(args.camera, events=events, keep_full=True,
                             **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:   # also a wrong --profile
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    extractors = default_extractors(config)
    au_features = None
    if not args.no_aus:
        # One row per AU result, at its own rate, in the session's aus.csv.
        au_features = FeatureStream(list(au_config.units) + ["infer_ms"],
                                    rate_hz=au_config.profile.max_rate, name="aus")
        extractors.append(ActionUnitExtractor(au_config, config, features=au_features))
    from .gaze import GazeExtractor, resolve_user
    from .gaze import load_config as load_gaze_config

    gaze = GazeExtractor(load_gaze_config(), config, user=resolve_user(args.user), setup=args.setup)
    extractors.append(gaze)
    features = FeatureStream(all_columns(extractors) + ["process_ms"], rate_hz=camera.config.fps)
    # Each participant's own baseline (A12), following the session like the gaze zones.
    from .baseline import StoredBaselineProvider
    from .gaze import CalibrationStore

    store = CalibrationStore()
    baseline = StoredBaselineProvider(config, store=store, user=gaze.user, source=camera.name,
                                      setup=args.setup)
    pipeline = FacePipeline(camera, config, extractors=extractors, features=features,
                            events=events, baseline=baseline)
    # Real "look at user": where the face is goes to the panel (if one runs).
    from .gaze.look_at import UserTargetPublisher

    gaze_config = gaze.gaze
    publisher = UserTargetPublisher(camera.config.relay_port, gaze_config.get("robot_lens_fov_deg"),
                                    camera.config.robot.fov_deg, gaze_config.get("user_report_rate"))
    pipeline.subscribe(publisher)
    pipeline.start()
    # The Wizard panel's "Calibrate" step runs here (this process has the camera).
    from .calibration.remote import CommandHandler

    commands = CommandHandler(pipeline, camera, store, gaze, baseline, events, setup_label=args.setup)
    print(f"\n  Face perception running on the {camera.describe()} (profile {au_config.profile.name}). "
          "It records\n  only while the Wizard panel runs a session. Ctrl+C to stop.\n")
    next_status = time.monotonic() + 5
    try:
        while True:
            time.sleep(0.5)
            if pipeline.error:
                print(f"\n  {pipeline.error}", file=sys.stderr)
                return 1
            done = commands.tick()                 # the panel asked to calibrate / reuse / skip?
            if done:
                print(f"  Calibration: {done}")
            if time.monotonic() < next_status:
                continue
            next_status = time.monotonic() + 5
            if not args.user:          # follow the session: each participant's own calibration
                gaze.user = resolve_user(None)
                baseline.use(gaze.user, camera.name)
            stats, latest = pipeline.stats(), pipeline.get_latest()
            face = "face" if latest and latest.observation.present else "no face"
            recording = f"recording into {features.session}" if features.session else "not recording (no session)"
            aus = next((e for e in extractors if isinstance(e, ActionUnitExtractor)), None)
            au_text = "AUs off" if aus is None or aus.status == "off" else \
                f"AUs {aus.status} {aus.achieved_rate():.0f}/s"
            zone = (latest.values.get("gaze_zone") if latest else None) or "-"
            calib = "" if gaze.zones.calibrated and baseline.current().calibrated else " (uncalibrated)"
            print(f"  {stats['fps'] or 0:4.1f} fps   {stats['process_ms'] or 0:5.1f} ms/frame   "
                  f"{face:8}  gaze {zone}{calib:15} {au_text:18} {camera.status}   {recording}")
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        publisher.close()
        features.close()
        if au_features is not None:
            au_features.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
