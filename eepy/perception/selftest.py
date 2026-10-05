"""Guided self-test of the face features:  python -m eepy.perception.selftest

Asks you to look normally, blink 5 times, yawn once, nod off once and look
away -- and, when the optional action units (AUs) are installed, to frown,
squint, raise your eyebrows, close your eyes for 3 seconds and open your mouth
wide. It reports what was detected: the quick acceptance test on each laptop
and on the robot's camera (--camera robot). It also measures your normal eye
and mouth openness (useful when tuning config/eepy.yaml) and how long each
frame takes. Nothing is recorded or saved.

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
    au: Dict[str, List[float]] = field(default_factory=dict)        # AU -> fresh values seen
    au_active_s: Dict[str, float] = field(default_factory=dict)     # AU -> seconds "on"
    eyes_shut_au43: List[bool] = field(default_factory=list)        # AU43 on, while eyes shut (A9)
    last_t: Optional[float] = None

    def count(self, kind: str) -> int:
        return self.events.get(kind, 0)

    def peak(self, unit: str) -> Optional[float]:
        values = self.au.get(unit)
        return max(values) if values else None

    def typical(self, unit: str) -> Optional[float]:
        values = self.au.get(unit)
        return statistics.median(values) if values else None


def _ok(cond: bool, good: str, bad: str) -> str:
    return ("PASS  " + good) if cond else ("CHECK " + bad)


def _steps(look_away_deg: float) -> List[Step]:
    return [
        Step("Look normally", "Sit as you normally would and look at the screen.", 6,
             "face found most of the time", lambda t: False,
             lambda t: _ok(bool(t.frames) and t.faces / t.frames >= 0.9,
                           f"face found in {t.faces}/{t.frames} frames",
                           f"face found in only {t.faces}/{t.frames} frames: light, distance, or the blue box?")),
        Step("Blink", "Blink 5 times, calmly, about one second apart.", 12,
             "5 blinks", lambda t: t.count("blink") >= 5,
             lambda t: _ok(4 <= t.count("blink") <= 6, f"{t.count('blink')} blinks",
                           f"{t.count('blink')} blinks detected, expected 5")),
        Step("Yawn", "Yawn once: open your mouth wide and hold it for about 3 seconds.", 12,
             "1 yawn", lambda t: t.count("yawn") >= 1,
             lambda t: _ok(t.count("yawn") == 1, "1 yawn", f"{t.count('yawn')} yawns detected, expected 1")),
        Step("Nod off", "Let your head drop forward (chin to chest), then lift it back up quickly.", 10,
             "1 nod", lambda t: t.count("nod") >= 1,
             lambda t: _ok(t.count("nod") >= 1,
                           f"{t.count('nod')} nod(s), dropped {max(t.nod_depth or [0]):.0f} deg (chin down = +)",
                           "no nod detected")),
        Step("Look away", "Turn your head far to YOUR LEFT for 3 seconds, then look back.", 10,
             f"head turned > {look_away_deg:.0f} deg (yaw +)", lambda t: False,
             lambda t: _ok(bool(t.yaw) and max(t.yaw) > look_away_deg or t.count("face_lost") > 0,
                           (f"yaw reached {max(t.yaw):+.0f} deg" if t.yaw else "face lost") +
                           (" -- sign correct (left = +)" if t.yaw and max(t.yaw) > abs(min(t.yaw)) else ""),
                           f"yaw only reached {max(t.yaw or [0]):+.0f} deg"
                           + (f" (but {min(t.yaw):+.0f} the other way: is the sign wrong?)"
                              if t.yaw and min(t.yaw) < -look_away_deg else ""))),
    ]


def _au_steps(units: List[str], threshold: float, neutral: Callable[[str], Optional[float]]) -> List[Step]:
    """Action-unit steps: PASS when the AU's peak is 'on' AND clearly above how it
    looked during "Look normally"."""

    def judge(unit_list: List[str]) -> Callable[[Tally], str]:
        def verdict(t: Tally) -> str:
            parts, good = [], True
            for unit in unit_list:
                peak, base = t.peak(unit), neutral(unit)
                if peak is None:
                    parts.append(f"{unit}: no fresh results")
                    good = False
                    continue
                rose = peak >= threshold and (base is None or peak - base >= 0.2)
                good = good and rose
                parts.append(f"{unit} peak {peak:.2f} (normal {base:.2f})" if base is not None
                             else f"{unit} peak {peak:.2f}")
            return _ok(good, ", ".join(parts), ", ".join(parts) + " -- did not clearly rise")
        return verdict

    def held(unit_list: List[str], seconds: float = 1.0) -> Callable[[Tally], bool]:
        return lambda t: all(t.au_active_s.get(u, 0.0) >= seconds for u in unit_list)

    candidates = [
        ("Frown", "Frown: pull your eyebrows down and together. Hold it ~3 seconds.", ["AU04"]),
        ("Squint", "Squint: tighten your eyelids as if in bright sun. Hold it ~3 seconds.", ["AU07"]),
        ("Raise eyebrows", "Raise your eyebrows as high as you can. Hold it ~3 seconds.", ["AU01", "AU02"]),
        ("Eyes closed", "Close your eyes for 3 seconds, then open them again.", ["AU43"]),
        ("Mouth wide", "Open your mouth wide, as if saying 'aah'. Hold it ~3 seconds.", ["AU26"]),
    ]
    steps = []
    for title, instruction, unit_list in candidates:
        present = [u for u in unit_list if u in units]
        if not present:
            continue
        seconds = 10 if title == "Eyes closed" else 8
        done = (lambda t: False) if title == "Eyes closed" else held(present)
        steps.append(Step(title, instruction, seconds, f"{'/'.join(present)} on", done, judge(present)))
    return steps


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.perception.selftest",
                                     description="Guided test of blink, yawn, nod, look-away and action units.")
    parser.add_argument("--camera", choices=("auto", "robot", "laptop", "fake"))
    parser.add_argument("--index", type=int, help="laptop camera number")
    parser.add_argument("--profile", help="machine profile for action units (default: EEPY_PROFILE, else laptop)")
    parser.add_argument("--no-aus", action="store_true", help="skip the action-unit steps")
    parser.add_argument("--fast", action="store_true", help="2-second steps (checks that it runs)")
    args = parser.parse_args(argv)

    import logging

    from eepy.camera import CameraConfigError, CameraError, open_camera
    from eepy.camera.laptop import import_cv2

    from .config import load
    from .extractors import default_extractors
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
    au = None
    try:
        config = load()
        extractors = default_extractors(config)
        if not args.no_aus:
            from .aus import ActionUnitExtractor
            from .aus import load_config as load_au_config

            au = ActionUnitExtractor(load_au_config(profile=args.profile), config)
            extractors.append(au)
        camera = open_camera(args.camera, **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2

    pipeline = FacePipeline(camera, config, extractors=extractors).start()
    overlay = FaceOverlay(aus=au)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    steps = _steps(config.get("", "look_away_deg"))
    tallies: List[Tally] = []
    print(f"\n  Face self-test on the {camera.describe()}. Follow the instructions in the window.")
    result = None
    quit_early = False
    added_au_steps = False
    if au is not None and au.status in ("starting", "loading"):
        # Step 1 also measures how the AUs look on a NEUTRAL face (the "normal"
        # each AU step is compared with), so the AU model must be ready first.
        print("  Loading the action-unit model...")
        deadline = time.monotonic() + 90
        while au.status in ("starting", "loading") and time.monotonic() < deadline:
            time.sleep(0.2)
    base = len(steps)
    planned = base + (len(_au_steps(list(au.units), 0.5, lambda u: None))
                      if au is not None and au.status != "off" else 0)
    try:
        index = 0
        while True:
            if index == base and au is not None and not added_au_steps:
                added_au_steps = True
                deadline = time.monotonic() + 60
                while au.status in ("starting", "loading") and time.monotonic() < deadline:
                    time.sleep(0.2)            # the AU model may still be loading
                if au.status != "running":
                    break                      # AUs off: the report says why
                steps += _au_steps(list(au.units), au.au.active_threshold, tallies[0].typical)
                continue
            if index >= len(steps):
                break
            step = steps[index]
            tally = Tally()
            title = f"{index + 1}/{max(planned, len(steps))}  {step.title}"
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
                        _count(tally, result, au)
                        if finished_at is None and step.done(tally):
                            finished_at = now
                image = _screen(cv2, result, overlay, step, title, active, phase_end - now, camera)
                cv2.imshow(WINDOW, image)
                key = cv2.waitKey(1) & 0xFF
                overlay.key(key)
                if key in (ord("q"), ord("Q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    quit_early = True
                    break
                if key == ord(" "):
                    break
            tallies.append(tally)
            index += 1
            if quit_early:
                break
        stats = pipeline.stats()
        au_info = _au_info(au)
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        cv2.waitKey(1)
    _report(steps, tallies, stats, config, au_info)
    return 0


def _count(tally: Tally, result: Any, au: Any) -> None:
    values = result.values
    tally.frames += 1
    dt = 0.0 if tally.last_t is None else min(result.t - tally.last_t, 0.5)
    tally.last_t = result.t
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
    if au is None:
        return
    threshold = au.au.active_threshold
    for unit in au.units:
        value = values.get(f"au_{unit}")       # None when stale or missing
        if value is None:
            continue
        tally.au.setdefault(unit, []).append(value)
        if value >= threshold:
            tally.au_active_s[unit] = tally.au_active_s.get(unit, 0.0) + dt
    closure, au43 = values.get("eye_closure"), values.get("au_AU43")
    if closure is not None and au43 is not None and closure >= 0.8:
        tally.eyes_shut_au43.append(au43 >= threshold)


def _au_info(au: Any) -> Optional[Dict[str, Any]]:
    if au is None:
        return None
    return {"status": au.status, "reason": au.reason, "profile": au.au.profile.name,
            "device": getattr(au.detector, "device", None), "rate": au.achieved_rate(),
            "infer_ms": au.infer_ms(), "max_rate": au.au.profile.max_rate}


def _screen(cv2: Any, result: Any, overlay: Any, step: Step, title: str, active: bool, left: float,
            camera: Any) -> Any:
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
    heading = f"{title}   " + (f"{left:.0f} s left" if active else f"get ready... {left:.0f}")
    cv2.putText(image, heading, (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (80, 220, 80) if active else (0, 200, 255), 2, cv2.LINE_AA)
    cv2.putText(image, step.instruction, (14, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return image


def _report(steps: List[Step], tallies: List[Tally], stats: Dict[str, Any], config: Any,
            au_info: Optional[Dict[str, Any]]) -> None:
    print("\n  Results")
    print("  " + "-" * 78)
    for number, (step, tally) in enumerate(zip(steps, tallies), start=1):
        print(f"  {number:>2}. {step.title:16} expected {step.expected:26} {step.verdict(tally)}")
    if len(tallies) < len(steps):
        print("  (stopped early)")
    first = tallies[0] if tallies else Tally()
    print("  " + "-" * 78)
    print(f"  Face features: {stats.get('process_ms')} ms per frame (median), {stats.get('process_ms_p95')} ms "
          f"(95 %), at {stats.get('fps')} frames per second.")
    if stats.get("fps") and stats["fps"] < 0.9 * config.get("", "min_blink_fps"):
        print(f"  NOTE: below {config.get('', 'min_blink_fps'):.0f} fps, blinks are easily missed.")
    if au_info is not None:
        if au_info["status"] == "running":
            print(f"  Action units: {au_info['device']}, profile {au_info['profile']}, "
                  f"{au_info['rate']:.1f} results/s (max {au_info['max_rate']:g}), "
                  f"{au_info['infer_ms']} ms per result.")
        else:
            print(f"  Action units: {au_info['status']} -- {au_info['reason']}")
    eyes = next((t for s, t in zip(steps, tallies) if s.title == "Eyes closed"), None)
    if eyes is not None and eyes.eyes_shut_au43:
        agree = sum(eyes.eyes_shut_au43) / len(eyes.eyes_shut_au43)
        print(f"  Cross-check: while the eye features said 'shut', AU43 agreed in {agree:.0%} of frames.")
    if first.ear:
        ear_open = statistics.median(first.ear)
        print(f"\n  Your normal eye openness (EAR): {ear_open:.3f}   (config default eyes.ear_open: "
              f"{config.get('eyes', 'ear_open'):.2f})")
        if first.mar and first.yaw and first.pitch:
            print(f"  Your closed-mouth MAR: {statistics.median(first.mar):.3f}   "
                  f"head straight ahead: yaw {statistics.median(first.yaw):+.0f}, "
                  f"pitch {statistics.median(first.pitch):+.0f} deg")
        if abs(ear_open - config.get("eyes", "ear_open")) > 0.05:
            print("  -> Your eyes differ from the default: per-person calibration (A12) will handle this.")
    if len(tallies) > 1 and tallies[1].blink_ms:
        print(f"  Blink durations: {', '.join(f'{ms:.0f}' for ms in tallies[1].blink_ms)} ms")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
