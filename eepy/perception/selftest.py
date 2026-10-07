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
    gaze: List[Any] = field(default_factory=list)                   # (t, zone, away_s)
    closure: List[float] = field(default_factory=list)              # eye_closure (calibrated)
    t0: Optional[float] = None                                      # when the step became active
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


class ShadowDetectors:
    """A9's detectors once more, on the SAME frames but with the DEFAULT baseline,
    so the report can show what calibration changed (A12): "blinks 5 (default 3)".
    A pipeline plug-in that adds no values; it only remembers its events."""

    name = "shadow"
    columns: tuple = ()

    def __init__(self, config: Any) -> None:
        from .baseline import BaselineProvider
        from .extractors import EyeExtractor, HeadExtractor, MouthExtractor, PresenceExtractor

        self.baseline = BaselineProvider(config).current()
        self.inner = [PresenceExtractor(config), EyeExtractor(config), MouthExtractor(config),
                      HeadExtractor(config)]
        self.events: List[Any] = []            # (t, type)
        self.closure: List[Any] = []           # (t, eye_closure)

    def update(self, obs: Any, baseline: Any) -> Any:
        from .extractors import Output

        for extractor in self.inner:
            out = extractor.update(obs, self.baseline)
            self.events += [(e.t, e.type) for e in out.events]
            if out.values.get("eye_closure") is not None:
                self.closure.append((obs.t, out.values["eye_closure"]))
        return Output({})

    def count(self, kind: str, start: float, end: float) -> int:
        return sum(1 for t, k in self.events if k == kind and start <= t <= end)

    def median_closure(self, start: float, end: float) -> Optional[float]:
        xs = [c for t, c in self.closure if start <= t <= end]
        return statistics.median(xs) if xs else None

    # The rest of the Extractor interface: nothing to add.
    def notice(self, *args: Any) -> None:
        pass

    def summary(self, now: float) -> list:
        return []

    def drain(self) -> list:
        return []

    def derive(self, values: Any, baseline: Any) -> dict:
        return {}

    def close(self) -> None:
        pass

    def reset(self) -> None:
        pass


def _gaze_steps(calibrated: bool) -> List[Step]:
    """Gaze steps: PASS when most of the step is spent in the expected zone. The
    delays: 'seen' = first frame in that zone (includes your reaction time),
    'confirmed' = after the hysteresis (gaze.min_away/back_seconds)."""

    def judge(expected: str, on_work: bool) -> Callable[[Tally], str]:
        def verdict(t: Tally) -> str:
            if not t.gaze:
                return "CHECK no frames"
            tail = t.gaze[len(t.gaze) * 2 // 5:]            # after the first 40 %: settled
            counts: Dict[str, int] = {}
            for _, zone, _ in tail:
                counts[zone] = counts.get(zone, 0) + 1
            top = max(counts, key=counts.get)
            seen = next((g[0] for g in t.gaze if g[1] == expected), None)
            confirmed = next((g[0] for g in t.gaze if (g[2] == 0) == on_work), None)
            delay = (f"seen after {seen - t.t0:.1f} s" if seen is not None else "never seen") + \
                (f", confirmed after {confirmed - t.t0:.1f} s" if confirmed is not None else "")
            share = counts.get(expected, 0) / len(tail)
            note = "" if calibrated else " [uncalibrated]"
            return _ok(top == expected, f"{expected} {share:.0%} of the time; {delay}{note}",
                       f"mostly {top} ({counts[top] / len(tail):.0%}), expected {expected}; {delay}{note}")
        return verdict

    return [
        # Alternating on / off work, so every step is a real switch and its
        # delays mean something (screen -> away -> notes -> phone).
        Step("Gaze: screen", "Look at the MIDDLE of your screen and read something there.", 6,
             "zone screen", lambda t: False, judge("screen", True)),
        Step("Gaze: away", "Look AWAY to the side (a window, a door) and keep looking.", 6,
             "zone away", lambda t: False, judge("away", False)),
        Step("Gaze: notes", "Look at your NOTES (as if reading them).", 6,
             "zone notes", lambda t: False, judge("notes", True)),
        Step("Gaze: phone", "Look down at an imaginary PHONE in your LAP.", 6,
             "zone down_other", lambda t: False, judge("down_other", False)),
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
    parser.add_argument("--user", help="whose gaze calibration (default: 'default')")
    parser.add_argument("--fast", action="store_true", help="2-second steps (checks that it runs)")
    parser.add_argument("--states", action="store_true",
                        help="instead: the user-state scenario (A13, ~3 minutes): work, phone, "
                             "act sleepy, leave")
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
        from .gaze import GazeExtractor, resolve_user
        from .gaze import load_config as load_gaze_config

        gaze = GazeExtractor(load_gaze_config(), config, user=resolve_user(args.user))
        extractors.append(gaze)
        shadow = ShadowDetectors(config)
        extractors.append(shadow)
        if not args.no_aus:
            from .aus import ActionUnitExtractor
            from .aus import load_config as load_au_config

            au = ActionUnitExtractor(load_au_config(profile=args.profile), config)
            extractors.append(au)
        camera = open_camera(args.camera, **({"index": args.index} if args.index is not None else {}))
    except (CameraError, CameraConfigError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2

    from .baseline import StoredBaselineProvider

    baseline = StoredBaselineProvider(config, user=gaze.user, source=camera.name)
    pipeline = FacePipeline(camera, config, extractors=extractors, baseline=baseline)
    if args.states:
        return _state_scenario(cv2, pipeline, camera, config, au, gaze, baseline, args.fast)
    pipeline.start()
    overlay = FaceOverlay(aus=au, gaze=gaze, baseline=baseline)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    steps = _steps(config.get("", "look_away_deg"))
    # Calibration is loaded on the first frame; the file tells us up front.
    from .gaze import CalibrationStore, setup_name

    calibrated = CalibrationStore().section(gaze.user, setup_name(camera.name), "gaze") is not None
    steps += _gaze_steps(calibrated)
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
                    tally.t0 = result.t if result is not None else None
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
    _compare(steps, tallies, shadow, baseline.current())
    return 0


def _state_scenario(cv2: Any, pipeline: Any, camera: Any, config: Any, au: Any, gaze: Any,
                    baseline: Any, fast: bool) -> int:
    """A13: the scripted scenario -- work, phone, back, act sleepy, leave -- with the
    rules running live. Reports the state in each part and how long switches took."""
    from eepy.logs.clock import monotonic
    from eepy.state.estimate import make_estimator
    from eepy.state.runner import StateFromPipeline, StateTracker
    from eepy.state.scenario import SCENARIO, judge, report

    from .overlay import FaceOverlay

    estimator = make_estimator("rules")
    estimates: List[Any] = []
    tracker = StateTracker(estimator, gap_seconds=estimator.config.gap_seconds, listeners=[estimates.append])
    pipeline.subscribe(StateFromPipeline(tracker, config.windows))
    pipeline.start()
    overlay = FaceOverlay(aus=au, gaze=gaze, baseline=baseline, state=tracker)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    print(f"\n  User-state scenario on the {camera.describe()} (about 3 minutes). "
          "Follow the instructions in the window.")
    result = None
    timed: List[Any] = []
    started = monotonic()
    try:
        for n, phase in enumerate(SCENARIO, start=1):
            step = Step(phase.title, phase.instruction, phase.seconds, "/".join(phase.expected),
                        lambda t: False, lambda t: "")
            title = f"{n}/{len(SCENARIO)}  {phase.title}"
            start = monotonic()
            end = start + (4.0 if fast else phase.seconds)
            print("\a", end="", flush=True)                  # a beep: also for whoever left the view
            while True:
                now = monotonic()
                if now >= end:
                    break
                if pipeline.error:
                    print(f"\n  {pipeline.error}", file=sys.stderr)
                    return 1
                tracker.idle(now)
                newest = pipeline.wait_for_result(newer_than=result, timeout=0.1)
                if newest is not None:
                    result = newest
                    overlay.note(result)
                cv2.imshow(WINDOW, _screen(cv2, result, overlay, step, title, True, end - now, camera))
                key = cv2.waitKey(1) & 0xFF
                overlay.key(key)
                if key in (ord("q"), ord("Q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    timed.append((phase, start, now))
                    raise KeyboardInterrupt
                if key == ord(" "):
                    break
            timed.append((phase, start, monotonic()))
    except KeyboardInterrupt:
        print("  (stopped early)")
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
        cv2.waitKey(1)
    for line in report(judge(timed, estimates), estimates, started):
        print(line)
    print()
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
    if values.get("eye_closure") is not None:
        tally.closure.append(values["eye_closure"])
    if values.get("gaze_zone") is not None:
        tally.gaze.append((result.t, values["gaze_zone"], values.get("away_s") or 0.0))
        if tally.t0 is None:
            tally.t0 = result.t
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



def _compare(steps: List[Step], tallies: List[Tally], shadow: ShadowDetectors, baseline: Any) -> None:
    """Calibrated vs default detections on the same frames (A12)."""
    if not baseline.calibrated:
        print("  (No calibration for this person and camera: everything above used the defaults.\n"
              "   Calibrate with  python -m eepy.perception.calibrate  to compare.)\n")
        return
    print(f"  Calibrated ({baseline.label}, {baseline.taken_at}) vs. default baseline, same frames:")
    for step, tally in zip(steps, tallies):
        if not tally.gaze or tally.t0 is None:
            continue
        start, end = tally.t0, tally.gaze[-1][0]
        kind = {"Blink": "blink", "Yawn": "yawn", "Nod off": "nod"}.get(step.title)
        if kind:
            print(f"    {step.title:14} {kind}s: calibrated {tally.count(kind)}, default {shadow.count(kind, start, end)}")
        if step.title == "Look normally":
            default = shadow.median_closure(start, end)
            mine = statistics.median([e for e in tally.closure]) if tally.closure else None
            if default is not None and mine is not None:
                print(f"    {step.title:14} eyes 'closed' at rest: calibrated {mine:.0%}, default {default:.0%}"
                      "  (should be ~0 %)")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
