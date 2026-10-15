"""Guided self-test of the face features:  python -m eepy.perception.selftest

Asks you to look normally, blink 5 times, yawn once, nod off once and look
away, and reports what it detected -- the quick acceptance test on each laptop
and on the robot's camera (--camera robot). Also measures your normal eye and
mouth openness (useful when tuning config/eepy.yaml) and how long each frame
takes. Nothing is recorded or saved.

Keys in the window: SPACE skips a step, Q quits.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

WINDOW = "EEPY face self-test  (SPACE: skip step, Q: quit)"


@dataclass
class Step:
    title: str
    instruction: str
    seconds: float
    expected: str
    done: Callable[["Tally"], bool]
    verdict: Callable[["Tally"], str]


@dataclass
class Tally:
    """What happened during one step."""

    events: Dict[str, int] = field(default_factory=dict)
    frames: int = 0
    faces: int = 0
    ear: List[float] = field(default_factory=list)
    mar: List[float] = field(default_factory=list)
    yaw: List[float] = field(default_factory=list)
    pitch: List[float] = field(default_factory=list)
    blink_ms: List[float] = field(default_factory=list)
    nod_depth: List[float] = field(default_factory=list)

    def count(self, kind: str) -> int:
        return self.events.get(kind, 0)


def _steps(look_away_deg: float) -> List[Step]:
    def ok(cond: bool, good: str, bad: str) -> str:
        return ("PASS  " + good) if cond else ("CHECK " + bad)

    return [
        Step("1/5  Look normally", "Sit as you normally would and look at the screen.", 6,
             "face found most of the time", lambda t: False,
             lambda t: ok(t.frames and t.faces / t.frames >= 0.9,
                          f"face found in {t.faces}/{t.frames} frames",
                          f"face found in only {t.faces}/{t.frames} frames: light, distance, or the blue box?")),
        Step("2/5  Blink", "Blink 5 times, calmly, about one second apart.", 12,
             "5 blinks", lambda t: t.count("blink") >= 5,
             lambda t: ok(4 <= t.count("blink") <= 6, f"{t.count('blink')} blinks",
                          f"{t.count('blink')} blinks detected, expected 5")),
        Step("3/5  Yawn", "Yawn once: open your mouth wide and hold it for about 3 seconds.", 12,
             "1 yawn", lambda t: t.count("yawn") >= 1,
             lambda t: ok(t.count("yawn") == 1, "1 yawn", f"{t.count('yawn')} yawns detected, expected 1")),
        Step("4/5  Nod off", "Let your head drop forward (chin to chest), then lift it back up quickly.", 10,
             "1 nod", lambda t: t.count("nod") >= 1,
             lambda t: ok(t.count("nod") >= 1,
                          f"{t.count('nod')} nod(s), dropped {max(t.nod_depth or [0]):.0f} deg (chin down = +)",
                          "no nod detected")),
        Step("5/5  Look away", "Turn your head far to YOUR LEFT for 3 seconds, then look back.", 10,
             f"head turned > {look_away_deg:.0f} deg (yaw +)", lambda t: False,
             lambda t: ok(bool(t.yaw) and max(t.yaw) > look_away_deg or t.count("face_lost") > 0,
                          (f"yaw reached {max(t.yaw):+.0f} deg" if t.yaw else "face lost") +
                          (" -- sign correct (left = +)" if t.yaw and max(t.yaw) > abs(min(t.yaw)) else ""),
                          f"yaw only reached {max(t.yaw or [0]):+.0f} deg"
                          + (f" (but {min(t.yaw):+.0f} the other way: is the sign wrong?)"
                             if t.yaw and min(t.yaw) < -look_away_deg else ""))),
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.perception.selftest",
                                     description="Guided test of blink, yawn, nod and look-away detection.")
    parser.add_argument("--camera", choices=("auto", "robot", "laptop", "fake"))
    parser.add_argument("--index", type=int, help="laptop camera number")
    parser.add_argument("--fast", action="store_true", help="2-second steps (checks that it runs)")
    args = parser.parse_args(argv)

    import logging

    from eepy.camera import CameraConfigError, CameraError, open_camera
    from eepy.camera.laptop import import_cv2

    from .config import load
    from .overlay import FaceOverlay
    from .pipeline import FacePipeline

    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    try:
        cv2 = import_cv2()
        import mediapipe  # noqa: F401
    except ImportError:
        print("\n  The self-test needs MediaPipe: run it in .venv-vision\n"
              "      pip install -r requirements-vision.txt", file=sys.stderr)
        return 2
    except CameraError as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    try:
        config = load()
        camera = open_camera(args.camera, **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2

    pipeline = FacePipeline(camera, config).start()
    overlay = FaceOverlay()
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    steps = _steps(config.get("", "look_away_deg"))
    tallies: List[Tally] = []
    print(f"\n  Face self-test on the {camera.describe()}. Follow the instructions in the window.")
    result = None
    quit_early = False
    try:
        for step in steps:
            tally = Tally()
            phase_end = time.monotonic() + (1.0 if args.fast else 3.0)   # "get ready"
            active = False
            finished_at: Optional[float] = None
            while True:
                now = time.monotonic()
                if not active and now >= phase_end:
                    active, phase_end = True, now + (2.0 if args.fast else step.seconds)
                if active and (now >= phase_end or (finished_at and now - finished_at > 1.0)):
                    break
                if pipeline.error:
                    print(f"\n  {pipeline.error}", file=sys.stderr)
                    return 1
                newest = pipeline.wait_for_result(newer_than=result, timeout=0.1)
                if newest is not None:
                    result = newest
                    overlay.note(result)
                    if active:
                        _count(tally, result)
                        if finished_at is None and step.done(tally):
                            finished_at = now
                image = _screen(cv2, result, overlay, step, active, phase_end - now, camera)
                cv2.imshow(WINDOW, image)
                key = cv2.waitKey(1) & 0xFF
                overlay.key(key)
                if key in (ord("q"), ord("Q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    quit_early = True
                    break
                if key == ord(" "):
                    break
            tallies.append(tally)
            if quit_early:
                break
        stats = pipeline.stats()
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        cv2.waitKey(1)
    _report(steps, tallies, stats, config)
    return 0


def _count(tally: Tally, result: Any) -> None:
    values = result.values
    tally.frames += 1
    if result.observation.present:
        tally.faces += 1
        for name, bucket in (("ear", tally.ear), ("mar", tally.mar), ("head_yaw", tally.yaw),
                             ("head_pitch", tally.pitch)):
            if values.get(name) is not None:
                bucket.append(values[name])
    for event in result.events:
        tally.events[event.type] = tally.events.get(event.type, 0) + 1
        if event.type == "blink":
            tally.blink_ms.append(event.fields["duration_ms"])
        if event.type == "nod":
            tally.nod_depth.append(event.fields["depth_deg"])


def _screen(cv2: Any, result: Any, overlay: Any, step: Step, active: bool, left: float, camera: Any) -> Any:
    import numpy as np

    if result is None or result.observation.frame is None:
        image = np.full((540, 960, 3), 40, dtype=np.uint8)
        message = camera.error or "Waiting for the camera..."
        for i, line in enumerate(message.splitlines()):
            cv2.putText(image, line, (20, 200 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 1)
        return image
    frame = result.observation.frame
    image = frame.image.copy()          # exactly what perception sees
    overlay.draw(cv2, image, result)
    width = image.shape[1]
    cv2.rectangle(image, (0, 0), (width, 78), (30, 30, 30), -1)
    title = f"{step.title}   " + (f"{left:.0f} s left" if active else f"get ready... {left:.0f}")
    cv2.putText(image, title, (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (80, 220, 80) if active else (0, 200, 255), 2, cv2.LINE_AA)
    cv2.putText(image, step.instruction, (14, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return image


def _report(steps: List[Step], tallies: List[Tally], stats: Dict[str, Any], config: Any) -> None:
    print("\n  Results")
    print("  " + "-" * 74)
    for step, tally in zip(steps, tallies):
        print(f"  {step.title:20} expected {step.expected:28} {step.verdict(tally)}")
    if len(tallies) < len(steps):
        print("  (stopped early)")
    first = tallies[0] if tallies else Tally()
    print("  " + "-" * 74)
    print(f"  Processing: {stats.get('process_ms')} ms per frame (median), {stats.get('process_ms_p95')} ms "
          f"(95 %), at {stats.get('fps')} frames per second.")
    if stats.get("fps") and stats["fps"] < 0.9 * config.get("", "min_blink_fps"):
        print(f"  NOTE: below {config.get('', 'min_blink_fps'):.0f} fps, blinks are easily missed.")
    if first.ear:
        ear_open = statistics.median(first.ear)
        print(f"\n  Your normal eye openness (EAR): {ear_open:.3f}   (config default eyes.ear_open: "
              f"{config.get('eyes', 'ear_open'):.2f})")
        if first.mar and first.yaw and first.pitch:
            print(f"  Your closed-mouth MAR: {statistics.median(first.mar):.3f}   "
                  f"head straight ahead: yaw {statistics.median(first.yaw):+.0f}, "
                  f"pitch {statistics.median(first.pitch):+.0f} deg")
        if abs(ear_open - config.get("eyes", "ear_open")) > 0.05:
            print("  -> Your eyes differ from the default: consider setting eyes.ear_open to "
                  f"{ear_open:.2f} in config/eepy.yaml (until per-person calibration exists).")
    if len(tallies) > 1 and tallies[1].blink_ms:
        print(f"  Blink durations: {', '.join(f'{ms:.0f}' for ms in tallies[1].blink_ms)} ms")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
