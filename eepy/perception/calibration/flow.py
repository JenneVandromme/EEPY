"""The combined session-start calibration, as a state machine (~2 minutes):

    1. face     the face check (face_check.py): in view, inside the blue box, close
                enough, lit well enough; held for 3 s
    2. gaze     A11's gaze zones: dots on the screen, then notes, then away
    3. baseline ~60 s of normal work (baseline.py), extended up to 30 s if needed;
                one automatic restart if it fails, then give up
    4. done     sections ready to save: {"gaze": ..., "baseline": ...}

Feed it every PerceptionResult with tick(); read `stage`, `instruction`,
`progress` and `gaze_step` to draw; take spoken lines from `pop_lines()` (line IDs
in behaviors/lines.yaml -- the designers write the words -- plus "beep" when the
person may look back at the screen). It knows nothing about
windows, speech engines or the panel, so the command-line tool and the vision
process (triggered by the panel) run the same flow.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..config import PerceptionConfig
from ..gaze.calibration import GazeCalibration
from ..gaze.config import GazeConfig
from ..gaze.estimate import GazeEstimator
from .baseline import BaselineCollector, Verdict
from .face_check import FaceCheck

PARTS = ("face", "gaze", "baseline")
SETTLE_SECONDS = 1.2         # gaze: let the eyes arrive at a dot before recording
OFF_SCREEN_PREPARE = 3.0     # gaze: countdown before notes / looking away
REPEAT_PROBLEM_SECONDS = 8.0


class SessionCalibration:
    def __init__(self, parts: Sequence[str] = PARTS, config: Optional[PerceptionConfig] = None,
                 gaze_config: Optional[GazeConfig] = None, baseline_seconds: float = 60.0,
                 max_extra: float = 30.0) -> None:
        unknown = [p for p in parts if p not in PARTS]
        if unknown or not parts:
            raise ValueError(f"unknown calibration part(s) {unknown}; use {', '.join(PARTS)}")
        self.parts = [p for p in PARTS if p in parts]
        self.config = config or PerceptionConfig()
        self.gaze_config = gaze_config or GazeConfig()
        self.baseline_seconds, self.max_extra = baseline_seconds, max_extra
        self.stage = "start"
        self.instruction = ""
        self.message = ""                     # a problem or result, for the person
        self.progress = 0.0                   # 0-1 within the current stage
        self.result: Dict[str, Any] = {}      # {"gaze": section, "baseline": section}
        self.checks: List[Dict[str, Any]] = []
        self.failed = False
        self.restarts = 0
        self._lines: List[str] = ["calibration_intro"]
        self._stage_t: Optional[float] = None
        self._said_problem: Dict[str, float] = {}
        self._face = FaceCheck()
        self._gaze: Optional[GazeCalibration] = None
        self._estimator = GazeEstimator(self.gaze_config)
        self._step_t: Optional[float] = None
        self._phase = "idle"                  # gaze point: "prepare" or "record"
        self._collector: Optional[BaselineCollector] = None
        self._next_stage(None)

    # --- reading the state ------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self.stage in ("done", "failed")

    @property
    def gaze_step(self) -> Any:
        """The current gaze step (with .target for a dot on screen), or None."""
        return self._gaze.step if self.stage == "gaze" and self._gaze and not self._gaze.done else None

    @property
    def recording(self) -> bool:
        """Gaze stage: recording the current point (draw the dot green)."""
        return self._step_phase() == "record"

    def pop_lines(self) -> List[str]:
        lines, self._lines = self._lines, []
        return lines

    def stage_number(self) -> str:
        return f"{self.parts.index(self.stage) + 1}/{len(self.parts)}" if self.stage in self.parts else ""

    # --- driving ------------------------------------------------------------------------------

    def tick(self, result: Any, now: float) -> None:
        """Feed one result; `now` is the clock the stage timers run on (seconds)."""
        if self.done:
            return
        if self._stage_t is None:
            self._stage_t = now
        if self.stage == "face":
            self._tick_face(result, now)
        elif self.stage == "gaze":
            self._tick_gaze(result, now)
        elif self.stage == "baseline":
            self._tick_baseline(result, now)

    def skip_step(self) -> None:
        """Gaze: repeat nothing, move on (e.g. a key press)."""
        if self.stage == "gaze" and self._gaze is not None and not self._gaze.done:
            self._gaze.next()

    # --- stages -------------------------------------------------------------------------------

    def _next_stage(self, now: Optional[float]) -> None:
        order = self.parts + ["done"]
        index = order.index(self.stage) + 1 if self.stage in order else 0
        self.stage = order[index]
        self._stage_t, self.progress = now, 0.0
        if self.stage == "face":
            self.instruction = "Sit the way you normally work, facing the screen."
            self._lines.append("calibration_face_check")
        elif self.stage == "gaze":
            self._gaze = GazeCalibration(self.gaze_config)
            self._step_t = None
            self.instruction = "Look at each dot until it turns green."
            self._lines.append("calibration_look_dot")
        elif self.stage == "baseline":
            self._collector = BaselineCollector(self.config, self.baseline_seconds, self.max_extra)
            self.instruction = "Now just work as usual for about a minute."
            self._lines.append("calibration_baseline_start")
        elif self.stage == "done":
            self.instruction = "Calibration done."
            self._lines.append("calibration_done")

    def _tick_face(self, result: Any, now: float) -> None:
        ok = self._face.add(result)
        self.message = self._face.message
        self.progress = 1.0 if ok else 0.0
        problem = self._face.problem
        if problem is not None and now - self._stage_t > 2.0:      # give them a moment first
            last = self._said_problem.get(problem, -1e9)
            if now - last >= REPEAT_PROBLEM_SECONDS:
                self._said_problem[problem] = now
                self._lines.append(problem)
        if ok:
            self.message = ""
            self._next_stage(now)

    def _step_phase(self) -> str:
        if self._step_t is None or self._gaze is None or self._gaze.done:
            return "idle"
        return self._phase

    def _tick_gaze(self, result: Any, now: float) -> None:
        cal = self._gaze
        assert cal is not None
        if cal.done:
            try:
                self.result["gaze"] = cal.finish()
            except ValueError as exc:
                return self._fail(str(exc))
            return self._next_stage(now)
        step = cal.step
        on_screen = step.target is not None
        if self._step_t is None:                                # a new point begins
            self._step_t, self._phase = now, "prepare"
            cal.redo()
            if not on_screen:
                self._lines.append("calibration_look_notes" if step.group == "notes" else "calibration_look_away")
        prepare = SETTLE_SECONDS if on_screen else OFF_SCREEN_PREPARE
        elapsed = now - self._step_t
        self.instruction = step.instruction
        if elapsed < prepare:
            self._phase = "prepare"
            return
        self._phase = "record"
        closure = result.values.get("eye_closure")
        cal.add(self._estimator.estimate(result.observation, closure))
        self.progress = (cal.index + min(1.0, (elapsed - prepare) / step.seconds)) / len(cal.steps)
        if elapsed >= prepare + step.seconds:
            if not on_screen:
                self._lines.append("beep")                     # "you may look back now"
            ok, why = cal.step_quality()
            if not ok:
                self.message = f"{why} -- once more."
                self._step_t = None                             # repeat this point
                return
            self.message = ""
            cal.next()
            self._step_t = None

    def _tick_baseline(self, result: Any, now: float) -> None:
        collector = self._collector
        assert collector is not None
        collector.add(result)
        self.progress = min(1.0, collector.elapsed / collector.target)
        verdict: Verdict = collector.evaluate()
        if verdict.action == "collect":
            return
        self.checks = verdict.checks
        if verdict.action == "extend":
            self.message = verdict.message
            self._lines.append("calibration_baseline_extend")
            return
        if verdict.action == "fail":
            if self.restarts == 0:
                self.restarts += 1
                self.message = verdict.message + " Starting the minute again."
                self._lines.append("calibration_baseline_extend")
                self._collector = BaselineCollector(self.config, self.baseline_seconds, self.max_extra)
                return
            return self._fail(verdict.message)
        self.result["baseline"] = collector.section(verdict.checks)
        self.message = ""
        self._next_stage(now)

    def _fail(self, message: str) -> None:
        self.stage, self.failed, self.message = "failed", True, message
        self.instruction = "Calibration did not succeed."
        self._lines.append("calibration_failed")
