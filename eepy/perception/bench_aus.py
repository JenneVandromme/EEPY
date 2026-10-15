"""Benchmark the face features on THIS machine:  python -m eepy.perception.bench_aus

Prints what hardware PyTorch sees, how long MediaPipe and the AU model take per
frame on each available device, the rate that allows, and a suggested profile.
Run it on every machine that will record data -- numbers from one machine say
nothing about another. Uses a sample face (no camera needed; --camera for a
live one). Nothing is saved.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import time
from typing import Any, Dict, List, Optional


def _times(fn: Any, n: int, warm: int = 3) -> Dict[str, float]:
    for _ in range(warm):
        fn()
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    return {"median": statistics.median(samples), "p95": samples[max(0, int(len(samples) * 0.95) - 1)]}


def _sample_frame() -> Any:
    """A face on our robot-view canvas (821x462): Py-Feat's own sample picture."""
    import cv2
    import numpy as np

    import importlib.util
    from pathlib import Path

    # Find Py-Feat's sample WITHOUT importing Py-Feat (see aus/detector.py import_pyfeat).
    spec = importlib.util.find_spec("feat")
    path = None
    if spec is not None and spec.origin:
        candidate = Path(spec.origin).parent / "tests" / "data" / "single_face.jpg"
        path = candidate if candidate.is_file() else None
    canvas = np.zeros((462, 821, 3), dtype=np.uint8)
    if path is None:
        return canvas, False
    image = cv2.imread(str(path))
    height = 462
    width = min(821, int(image.shape[1] * height / image.shape[0]))
    image = cv2.resize(image, (width, height))
    x0 = (821 - width) // 2
    canvas[:, x0:x0 + width] = image
    return canvas, True


def _live_frame(camera_name: Optional[str]) -> Any:
    from eepy.camera import open_camera

    camera = open_camera(camera_name).start()
    try:
        frame = camera.wait_for_frame(timeout=10)
        if frame is None:
            raise SystemExit(f"\n  No picture from the camera: {camera.error}")
        time.sleep(1.0)
        return camera.get_latest().image.copy()
    finally:
        camera.stop()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.perception.bench_aus",
                                     description="Benchmark face features and action units on this machine.")
    parser.add_argument("--device", choices=("all", "auto", "cuda", "mps", "cpu"), default="all",
                        help="which device(s) to time (default: every one available)")
    parser.add_argument("--frames", type=int, default=30, help="timed frames per measurement")
    parser.add_argument("--camera", nargs="?", const="auto", default=None,
                        help="use a live camera frame instead of the sample face")
    args = parser.parse_args(argv)

    from .aus.config import DEFAULT_UNITS, load as load_au_config
    from .aus.detector import AUUnavailable, aus_available, load_detector
    from .aus.device import cpu_threads, nvidia_gpu_present
    from .observation import from_mediapipe
    from .pipeline import Landmarker

    print("\n  EEPY face benchmark -- numbers for THIS machine only\n  " + "-" * 60)
    print(f"  Machine   {platform.system()} {platform.release()}, {platform.machine()}, "
          f"Python {platform.python_version()}")
    au_config = load_au_config()
    print(f"  Profile   {au_config.profile.name} (from --profile/EEPY_PROFILE; max {au_config.profile.max_rate:g} AU/s)")

    if args.camera:
        frame, real_face = _live_frame(None if args.camera == "auto" else args.camera), True
    else:
        frame, real_face = _sample_frame()

    # MediaPipe (always on the CPU)
    landmarker = Landmarker()
    t = [0.0]

    def mediapipe_once() -> Any:
        t[0] += 0.07
        return landmarker.detect(frame, t[0])

    mp_time = _times(mediapipe_once, args.frames)
    from eepy.camera.frame import Frame

    observation = from_mediapipe(Frame(frame, 0.0, 1, "bench"), mediapipe_once(), 0.0)
    landmarker.close()
    print(f"  MediaPipe {mp_time['median']:6.1f} ms per frame (p95 {mp_time['p95']:.1f})"
          f"  -> up to {1000 / mp_time['median']:.0f} frames/s")
    if not observation.present:
        print("  (no face found in the picture: AU timings use a fixed box)")

    ok, why = aus_available()
    if not ok:
        print(f"\n  Action units: not available here.\n  {why}\n")
        print("  Suggested profile: laptop (AUs stay off; everything else works).\n")
        return 0
    import torch

    cuda, mps = torch.cuda.is_available(), torch.backends.mps.is_available()
    gpu = torch.cuda.get_device_name(0) if cuda else ("Apple GPU (MPS)" if mps else "none")
    print(f"  PyTorch   {torch.__version__}   CUDA {'yes' if cuda else 'no'}   MPS {'yes' if mps else 'no'}"
          f"   GPU: {gpu}")
    if not cuda and nvidia_gpu_present():
        print("  !! An NVIDIA GPU is present but PyTorch cannot use it (CPU-only PyTorch?).\n"
              "  !! See eepy/perception/aus/README.md, 'Install'.")

    devices = {"all": [d for d, on in (("cuda", cuda), ("mps", mps), ("cpu", True)) if on],
               "auto": ["auto"]}.get(args.device, [args.device])
    from eepy.camera.region import Box

    box = observation.face_box if observation.present else Box(300, 100, 220, 260)
    results: Dict[str, float] = {}
    print("\n  Action units (EEPY's path: MediaPipe's face box -> Py-Feat AU model, fp32)")
    for device in devices:
        try:
            detector = load_detector(DEFAULT_UNITS, device, "auto")
        except AUUnavailable as exc:
            print(f"    {device:5} cannot run: {exc}")
            continue
        timing = _times(lambda: detector.predict(frame, box), args.frames)
        results[detector.device] = timing["median"]
        threads = f", {cpu_threads('auto')} threads" if detector.device == "cpu" else ""
        print(f"    {detector.device:5}{threads:12} {timing['median']:7.1f} ms per face (p95 {timing['p95']:.1f})"
              f"  -> up to {1000 / timing['median']:5.1f} AU results/s")
        detector.close()

    if not results:
        return 1
    best_device = min(results, key=results.get)
    possible = 1000 / results[best_device]
    print("\n  Suggestion" + "\n  " + "-" * 60)
    if best_device == "cuda" and possible >= 15:
        print("  This machine can run AUs at the camera's full rate.\n"
              "  Put this line in your .env:   EEPY_PROFILE=demo-desktop")
    elif possible >= au_config.min_rate:
        rate = max(1, min(15, int(possible * 0.8)))
        print(f"  AUs run on {best_device} at up to ~{possible:.0f}/s.\n"
              "  Put this line in your .env:   EEPY_PROFILE=laptop"
              + (f"\n  (and consider aus.profiles.laptop.max_rate: {rate})" if rate < 10 else ""))
    else:
        print(f"  AUs would manage only ~{possible:.1f}/s here: too slow to be useful.\n"
              "  Use EEPY_PROFILE=laptop; AUs will pause themselves, everything else works.")
    if not real_face:
        print("  (Timed without a sample face: install requirements-aus.txt for the real picture.)")
    print("  Copy these numbers into the team's notes, labelled with this machine's name.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
