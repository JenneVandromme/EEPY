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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="  %(message)s")

    from eepy.camera import CameraConfigError, CameraError, open_camera
    from eepy.logs.features import FeatureStream, ProcessEventLog

    from .config import load
    from .extractors import all_columns, default_extractors
    from .pipeline import FacePipeline

    try:
        config = load()
        events = ProcessEventLog("vision")
        camera = open_camera(args.camera, events=events,
                             **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    extractors = default_extractors(config)
    features = FeatureStream(all_columns(extractors) + ["process_ms"], rate_hz=camera.config.fps)
    pipeline = FacePipeline(camera, config, extractors=extractors, features=features,
                            events=events).start()
    print(f"\n  Face perception running on the {camera.describe()}. It records only while the\n"
          "  Wizard panel runs a session. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(5)
            if pipeline.error:
                print(f"\n  {pipeline.error}", file=sys.stderr)
                return 1
            stats, latest = pipeline.stats(), pipeline.get_latest()
            face = "face" if latest and latest.observation.present else "no face"
            recording = f"recording into {features.session}" if features.session else "not recording (no session)"
            print(f"  {stats['fps'] or 0:4.1f} fps   {stats['process_ms'] or 0:5.1f} ms/frame   "
                  f"{face:8}  {camera.status}   {recording}")
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        features.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
