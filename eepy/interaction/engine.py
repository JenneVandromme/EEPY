"""The EscalationEngine: WHEN EEPY nudges, and what came of each nudge.

    engine = EscalationEngine(config, FixedEscalation(config), actuator, log)
    engine.session_started(now)
    engine.on_state(estimate, now)      # the state estimate (A13), about once a second
    engine.tick(now)                    # regularly: timers
    engine.reply("accepted", now)       # the panel's buttons (later: speech, A28)

Shared by every policy: onset delays, reaction windows, cooldowns, guards, the
break and wind-down flows, absent handling, playing, and the outcome of every
nudge. The policy (policy.py) only chooses WHAT to play when a nudge is due.

Times are seconds on one clock, passed in by the caller: the shared monotonic
clock in the panel, a virtual one in the simulation and the tests. Thread-safe.
See eepy/interaction/README.md for the rules in plain words.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol

from .config import EscalationConfig
from .policy import Action, NudgeContext, NudgePolicy, NudgeRecord

STALE_SECONDS = 3.0          # no estimate for this long: the state is unknown
OUTCOMES = ("success", "no_reaction", "dismissed", "snoozed", "interrupted_focus", "censored")
REPLIES = ("accepted", "snoozed", "dismissed")
Log = Callable[..., Any]     # log(event_type, t, **fields)


class Actuator(Protocol):
    """What the engine needs from the robot side (the BehaviorPlayer in the panel)."""

    def busy(self) -> bool: ...
    def has_behavior(self, name: str) -> bool: ...
    def level_behavior(self, ladder: str, level: int) -> str: ...
    def play_level(self, ladder: str, level: int, intensity: float, tone: str,
                   context: Dict[str, Any]) -> str: ...
    def play(self, name: str) -> None: ...
    def set_idle(self, name: Optional[str]) -> None: ...
    def say(self, line_id: str, tone: str, context: Dict[str, Any]) -> None: ...


@dataclass
class OpenNudge:
    """A nudge whose outcome is not decided yet."""

    decision_id: int
    ladder: str
    level: int
    t: float
    window: Optional[float]          # reaction window; None: open until something happens
    frozen: float = 0.0              # seconds the window stood still (absent / unknown)
    freeze_start: Optional[float] = None
    accepted: bool = False

    def elapsed(self, now: float) -> float:
        frozen = self.frozen + (now - self.freeze_start if self.freeze_start is not None else 0.0)
        return now - self.t - frozen


@dataclass
class Episode:
    """One climb of a ladder, from the first nudge until a reset."""

    ladder: str
    started: float
    level: Optional[int] = None        # the last level played
    max_level: int = 0
    last_outcome: Optional[str] = None
    restarting: bool = False
    cooldown_until: Optional[float] = None
    quiet_until: Optional[float] = None    # after "accepted": give them the window
    any_success: bool = False


class EscalationEngine:
    def __init__(self, config: EscalationConfig, policy: NudgePolicy, actuator: Actuator,
                 log: Optional[Log] = None, idle: Optional[str] = "study_buddy_idle") -> None:
        self.config = config
        self.policy = policy
        self.actuator = actuator
        self._log_fn = log
        self.idle = idle
        self._lock = threading.RLock()
        self.active = False
        self.paused = False
        self._reset_all(0.0)

    # --- inputs ----------------------------------------------------------------------------

    def session_started(self, now: float) -> None:
        with self._lock:
            self._reset_all(now)
            self.active = True
            self.session_start = now

    def session_ended(self, now: float, reason: str = "session ended") -> None:
        with self._lock:
            if not self.active:
                return
            if self.open is not None:
                if self.open.ladder == "bedtime":
                    self._close("success", now, "the session ended after bedtime nudges")
                else:
                    self._close("censored", now, reason)
            self.active = False

    def on_state(self, estimate: Dict[str, Any], now: float) -> None:
        with self._lock:
            self.estimate = dict(estimate)
            self.estimate_t = now
            self._step(now)

    def tick(self, now: float) -> None:
        with self._lock:
            self._step(now)

    def calibration(self, state: str, now: float) -> None:
        """The panel's calibration progress: no nudges while it runs, and a grace
        period after it ends (whatever the result)."""
        with self._lock:
            if state == "running":
                self.holding = "calibrating"
            elif state in ("done", "reused", "skipped", "failed", "stopped"):
                self.holding = None
                self.calibrated_at = now

    def set_paused(self, paused: bool, now: float) -> None:
        """The operator's "Pause autonomy". Resuming starts afresh."""
        with self._lock:
            if paused == self.paused:
                return
            self.paused = paused
            if paused:
                if self.open is not None:
                    self._close("censored", now, "the operator paused autonomy")
                self._event("escalation", now, what="autonomy_paused")
            else:
                self._event("escalation", now, what="autonomy_resumed")
                self.episode = None
                if self.flow != "goodnight":          # after goodnight it stays goodnight
                    self.flow, self.break_until, self.wind_until = "normal", None, None
                self.role = "none"

    def operator_played(self, name: str, now: float) -> None:
        """The operator played something by hand: it counts for the minimum gap, and
        a running nudge's outcome can no longer be credited to the robot."""
        with self._lock:
            self.last_play_t = now
            if self.open is not None and self.open.window is not None:
                self._close("censored", now, f"the operator played {name}")

    def reply(self, kind: str, now: float) -> str:
        """The participant's reply: the panel's buttons now, speech later (A28).
        Returns what happened, in a few words for the panel."""
        if kind not in REPLIES:
            raise ValueError(f"a reply is one of {REPLIES}, not {kind!r}")
        with self._lock:
            return self._reply(kind, now)

    # --- reading -----------------------------------------------------------------------------

    def status(self, now: float) -> Dict[str, Any]:
        """For the panel: role, ladder and level, what comes next and when."""
        with self._lock:
            ep = self.episode
            out: Dict[str, Any] = {
                "active": self.active, "paused": self.paused, "policy": self.policy.name,
                "state": self._state, "role": self.role, "flow": self.flow,
                "ladder": ep.ladder if ep else None, "level": ep.level if ep else None,
                "next": self.next_text, "next_in": None if self.next_at is None
                else max(0.0, round(self.next_at - now, 1)),
                "nudges": len(self.nudges), "nudges_last_hour": self._last_hour(now),
                "last_outcome": self.last_outcome,
                "act_confidence": {"act": dict(self.config.act_confidence),
                                   "direct": self.config.direct_confidence},
                "open": None if self.open is None else
                {"decision_id": self.open.decision_id, "ladder": self.open.ladder, "level": self.open.level},
            }
            if self.flow == "wind_down" and self.wind_until is not None:
                out["wind_down_left"] = max(0.0, round(self.wind_until - now))
                out["snoozes_left"] = self.snoozes_left
            if self.flow == "break" and self.break_until is not None:
                out["break_left"] = max(0.0, round(self.break_until - now))
            return out

    # --- internals ---------------------------------------------------------------------------

    def _reset_all(self, now: float) -> None:
        self.session_start = now
        self.estimate: Dict[str, Any] = {}
        self.estimate_t: Optional[float] = None
        self._state = "unknown"
        self.state_since = now
        self.role_state_since: Optional[float] = None     # when the state's ROLE began
        self.role = "none"
        self.flow = "normal"                  # normal | break | wind_down | goodnight | went_to_bed
        self.episode: Optional[Episode] = None
        self.open: Optional[OpenNudge] = None
        self.nudges: List[NudgeRecord] = []
        self._hour: Deque[float] = deque()
        self.last_play_t: Optional[float] = None
        self.last_nod_t: Optional[float] = None
        self.calibrated_at: Optional[float] = None
        self.holding: Optional[str] = None
        self.last_break_t: Optional[float] = None
        self.break_offer_t: Optional[float] = None
        self.break_until: Optional[float] = None
        self.break_left_desk = False
        self.wind_until: Optional[float] = None
        self.snoozes_left = 0
        self.reminder_said = False
        self.last_outcome: Optional[str] = None
        self.next_text = "waiting for the session"
        self.next_at: Optional[float] = None
        self._decisions = 0

    def _event(self, event_type: str, t: float, **fields: Any) -> None:
        if self._log_fn is not None:
            self._log_fn(event_type, t, **fields)

    def _wait(self, text: str, at: Optional[float] = None) -> None:
        self.next_text, self.next_at = text, at

    # -- the state ------------------------------------------------------------------------

    def _effective_state(self, now: float) -> str:
        if self.estimate_t is None or now - self.estimate_t > STALE_SECONDS:
            return "unknown"
        return str(self.estimate.get("state") or "unknown")

    def _track_state(self, now: float) -> None:
        state = self._effective_state(now)
        if state == self._state:
            return
        old_role = self.config.role_for(self._state)
        self._state, self.state_since = state, now
        new_role = self.config.role_for(state)
        if new_role != old_role:
            self.role_state_since = now
        # Freeze an open reaction window while there is nobody / no data to judge.
        if self.open is not None:
            frozen_now = state in ("absent", "unknown")
            if frozen_now and self.open.freeze_start is None:
                self.open.freeze_start = now
            elif not frozen_now and self.open.freeze_start is not None:
                self.open.frozen += now - self.open.freeze_start
                self.open.freeze_start = None

    def _seconds_in_state(self, now: float) -> float:
        return now - self.state_since

    # -- one step -----------------------------------------------------------------------

    def _step(self, now: float) -> None:
        if not self.active:
            self._wait("autonomy starts with the session")
            return
        self._track_state(now)
        self._judge_open(now)
        if self.paused:
            self._wait("autonomy paused: the operator is in control")
            return
        if self.holding:
            self._wait(f"waiting: {self.holding}")
            return
        if self.flow == "goodnight":
            self._wait("goodnight: end the session when they are gone")
            return
        if self.flow == "break":
            self._break(now)
            return
        if self.flow == "went_to_bed":
            if self._state not in ("absent", "unknown") and self._seconds_in_state(now) >= self.config.reset_after:
                self.flow = "normal"
                self._event("escalation", now, what="back_after_bed")
            else:
                self._wait("they left after the bedtime nudges (went to bed?)")
                return
        if self.flow == "wind_down":
            self._wind_down(now)
            return
        role = self.config.role_for(self._state)
        if role == "study_buddy":
            self._focused(now)
        elif role == "paused":
            self._absent(now)
        elif role in self.config.ladders:
            self._ladder_role(role, now)
        else:
            self._wait("state unknown: doing nothing")

    # -- focused / absent -----------------------------------------------------------------

    def _focused(self, now: float) -> None:
        if self._seconds_in_state(now) < self.config.reset_after and self.episode is not None:
            self._wait("focused again? resetting the ladder in",
                       self.state_since + self.config.reset_after)
            return
        if self.episode is not None:
            worked = self.episode.any_success
            self._event("escalation", now, what="episode_reset", ladder=self.episode.ladder,
                        level=self.episode.level, reason="back to focused")
            self.episode = None
            self._nod(now, worked)
        self._set_role("study_buddy", now)
        self._wait("focused: never interrupted")

    def _nod(self, now: float, worked: bool) -> None:
        name = self.config.approving_nod
        if not name or (self.config.nod_only_after_success and not worked):
            return
        if self.last_nod_t is not None and now - self.last_nod_t < self.config.nod_min_interval:
            return
        if not self.actuator.has_behavior(name) or self.actuator.busy():
            return
        self.actuator.play(name)
        self.last_nod_t = now
        self._event("escalation", now, what="approving_nod", behavior=name)

    def _absent(self, now: float) -> None:
        cfg = self.config
        away = self._seconds_in_state(now)
        ep = self.episode
        # Bedtime: leaving after the spoken bedtime nudges = went to bed.
        if ep is not None and ep.ladder == "bedtime" and ep.max_level >= cfg.went_to_bed_level:
            if away >= cfg.went_to_bed_after:
                if self.open is not None:
                    self._close("success", now, "left after the bedtime nudges (went to bed)")
                self._event("escalation", now, what="went_to_bed", level=ep.max_level)
                self.episode = None
                self.flow = "went_to_bed"
                self.actuator.set_idle(None)
                self._wait("they left after the bedtime nudges (went to bed?)")
                return
            self._wait("absent after bedtime nudges: went to bed? in", self.state_since + cfg.went_to_bed_after)
            return
        if ep is not None and away >= cfg.absent_reset_after:
            if self.open is not None:
                self._close("censored", now, "left the desk")
            self._event("escalation", now, what="episode_reset", ladder=ep.ladder, level=ep.level,
                        reason="absent")
            self.episode = None
        self._set_role("paused", now)
        self._wait("absent: not talking to an empty chair")

    # -- the ladders ----------------------------------------------------------------------

    def _ladder_role(self, role: str, now: float) -> None:
        cfg = self.config
        ep = self.episode
        if ep is not None and ep.ladder != role:            # e.g. distracted -> drowsy
            if self.open is not None:
                self._close("censored", now, f"the state changed to {self._state}")
            self._event("escalation", now, what="episode_reset", ladder=ep.ladder, level=ep.level,
                        reason=f"now {self._state}")
            self.episode = ep = None
        since = self.role_state_since if self.role_state_since is not None else self.state_since
        if ep is None:
            onset = cfg.onset.get(self._state, 0.0)
            if now - since < onset:
                self._wait(f"{self._state.replace('_', ' ')}: first nudge if it lasts, in", since + onset)
                return
            unsure = self._unsure(role)
            if unsure is not None:             # no episode, no role switch until the estimate is sure
                self._wait(unsure)
                return
            self.episode = ep = Episode(role, now)
            self._set_role(role, now)
        self._maybe_nudge(ep, now, since)

    def _maybe_nudge(self, ep: Episode, now: float, since: float) -> None:
        ladder = self.config.ladders[ep.ladder]
        if self.open is not None:
            if self.open.window is not None:
                left = self.open.window - self.open.elapsed(now)
                self._wait(f"waiting for a reaction to level {self.open.level}; next level in", now + max(0.0, left))
            return
        if ep.cooldown_until is not None:
            if now < ep.cooldown_until:
                self._wait("top of the ladder: pausing, then starting again in", ep.cooldown_until)
                return
            ep.cooldown_until, ep.level, ep.restarting, ep.last_outcome = None, None, True, None
        if ep.quiet_until is not None and now < ep.quiet_until:
            self._wait("they said OK: giving them time, next nudge at the earliest in", ep.quiet_until)
            return
        blocked = self._guard(now)
        if blocked is not None:
            text, until = blocked
            self._wait(text, until)
            return
        context = self._context(ep, now, since)
        action = self.policy.decide(context)
        if action.kind != "play":
            if action.reason == "top of the ladder" or (ep.level is not None and ep.level >= ladder.top):
                ep.cooldown_until = now + ladder.cooldown
                self._event("escalation", now, what="cooldown_started", ladder=ep.ladder,
                            seconds=ladder.cooldown, restart_level=ladder.restart_level)
                self._wait("top of the ladder: pausing, then starting again in", ep.cooldown_until)
            else:
                self._wait(f"policy: {action.reason}")
            return
        self._play(action, context, now)

    def _guard(self, now: float) -> Optional[tuple]:
        cfg = self.config
        start = max(self.session_start, self.calibrated_at or self.session_start)
        if now - start < cfg.after_calibration:
            return "settling in after the calibration; nudges allowed in", start + cfg.after_calibration
        if self.last_play_t is not None and now - self.last_play_t < cfg.min_gap:
            return "minimum gap between nudges", self.last_play_t + cfg.min_gap
        if self._last_hour(now) >= cfg.max_per_hour:
            return f"{cfg.max_per_hour} nudges in the last hour: waiting until", self._hour[0] + 3600
        if self.actuator.busy():
            return "the robot is still moving", None
        unsure = self._unsure(self.episode.ladder if self.episode is not None else self.config.role_for(self._state))
        if unsure is not None:
            return unsure, None
        if self.estimate.get("pending") == "focused":
            return "they seem to be getting back to work", None
        return None

    def _unsure(self, ladder: str) -> Optional[str]:
        """Why not to act yet: the estimate is less sure than config/escalation.yaml's
        confidence.act for this ladder (HMM only: the rules give no confidence)."""
        conf = self.estimate.get("confidence")
        need = self.config.act_confidence.get(ladder)
        if conf is not None and need is not None and conf < need:
            return f"not sure enough yet: {self._state.replace('_', ' ')} {conf:.0%} (acts at {need:.0%})"
        return None

    def _last_hour(self, now: float) -> int:
        while self._hour and now - self._hour[0] >= 3600:
            self._hour.popleft()
        return len(self._hour)

    def _context(self, ep: Episode, now: float, since: float) -> NudgeContext:
        def ago(t: Optional[float]) -> Optional[float]:
            return None if t is None else now - t

        return NudgeContext(
            now=now, estimate=dict(self.estimate), state_seconds=now - since, role=self.role,
            ladder=ep.ladder, level=ep.level, last_outcome=ep.last_outcome, restarting=ep.restarting,
            nudges_session=len(self.nudges), nudges_last_hour=self._last_hour(now),
            since_last_nudge=ago(self.nudges[-1].t if self.nudges else None),
            since_break=ago(self.last_break_t), since_calibration=ago(self.calibrated_at),
            session_seconds=now - self.session_start, history=tuple(self.nudges))

    def _evidence_text(self) -> str:
        """{evidence} in a spoken line. The HMM gives a short `say` ("" when it has no clear
        reason -- never its long sentence); the rules give their reason."""
        say = self.estimate.get("say")
        return say if say is not None else (self.estimate.get("reason") or "")

    def _play(self, action: Action, context: NudgeContext, now: float, count: bool = True,
              policy_name: Optional[str] = None, window: Optional[float] = -1.0) -> None:
        cfg = self.config
        ep = self.episode
        ladder, level = str(action.ladder), int(action.level or 0)
        self._decisions += 1
        decision_id = self._decisions
        minutes = {("refocus", cfg.break_offer_level): cfg.break_seconds,
                   ("bedtime", cfg.wind_down_offer_level): cfg.wind_down_seconds}.get(
                       (ladder, level), now - self.session_start)
        reaction = cfg.ladders[ladder].reaction.get(level) if window == -1.0 else window
        behavior = self.actuator.level_behavior(ladder, level)
        self._event("nudge_decision", now, decision_id=decision_id, policy=policy_name or self.policy.name,
                    ladder=ladder, level=level, behavior=behavior, tone=action.tone,
                    intensity=action.intensity, propensity=action.propensity, reason=action.reason,
                    reaction_window=reaction, context=context.summary())
        self.actuator.play_level(ladder, level, action.intensity, action.tone,
                                 {"minutes": max(1, int(round(minutes / 60))),
                                  "evidence": self._evidence_text()})
        self.open = OpenNudge(decision_id, ladder, level, now, reaction)
        if self._state in ("absent", "unknown"):
            self.open.freeze_start = now
        self.nudges.append(NudgeRecord(decision_id, now, ladder, level))
        self.last_play_t = now
        if count:
            self._hour.append(now)
        if ep is not None:
            ep.level, ep.max_level, ep.restarting = level, max(ep.max_level, level), False
        if ladder == "refocus" and level == cfg.break_offer_level:
            self.break_offer_t = now
        if ladder == "bedtime" and level == cfg.wind_down_offer_level:
            self._start_wind_down(now)
        self._wait(f"played {ladder} level {level}; waiting for a reaction")

    # -- outcomes -------------------------------------------------------------------------

    def _close(self, outcome: str, now: float, detail: str) -> None:
        o = self.open
        if o is None:
            return
        assert outcome in OUTCOMES, outcome
        self.open = None
        self.last_outcome = outcome
        self._event("nudge_outcome", now, decision_id=o.decision_id, outcome=outcome,
                    seconds=round(now - o.t, 1), ladder=o.ladder, level=o.level,
                    policy=self.policy.name, state=self._state, detail=detail)
        self.nudges = [NudgeRecord(r.decision_id, r.t, r.ladder, r.level, outcome)
                       if r.decision_id == o.decision_id else r for r in self.nudges]
        ep = self.episode
        if ep is not None and ep.ladder == o.ladder:
            ep.last_outcome = outcome
            if outcome == "success":
                ep.any_success = True
            if outcome in ("dismissed", "snoozed"):
                ep.cooldown_until = now + self.config.ladders[o.ladder].cooldown
                self._event("escalation", now, what="cooldown_started", ladder=o.ladder,
                            seconds=self.config.ladders[o.ladder].cooldown,
                            restart_level=self.config.ladders[o.ladder].restart_level, reason=outcome)

    def _judge_open(self, now: float) -> None:
        o = self.open
        if o is None:
            return
        cfg = self.config
        state = self._state
        if o.window is None:                         # goodnight: confirmed, or they leave
            if state == "absent" and self._seconds_in_state(now) >= cfg.went_to_bed_after:
                self._close("success", now, "left after the goodnight (went to bed)")
            return
        if state == "focused":
            if o.t < self.state_since <= o.t + cfg.false_alarm_seconds:
                # A13 needs 5 s of focus before it says "focused": they were already back.
                self._close("interrupted_focus", now, "already back on task when the nudge came")
                return
            if o.ladder == "refocus":
                if now - self.state_since >= cfg.reset_after:
                    self._close("success", now, f"back to focused after {self.state_since - o.t:.0f} s")
                return                               # focused, but not long enough yet
            # Bedtime: being alert again is not a bedtime result; the window, a reply or
            # leaving decides (finishing the work during the wind-down is expected).
        if o.ladder == "refocus" and o.level == cfg.break_offer_level and state == "absent":
            if now - o.t <= cfg.break_absent_within:
                self._close("success", now, "took the break")
                self._start_break(now, left=True)
                return
        if o.window is not None and o.elapsed(now) >= o.window:
            if cfg.role_for(state) == o.ladder:
                detail = f"still {state.replace('_', ' ')} after {o.window:.0f} s"
            else:
                detail = f"no answer within {o.window:.0f} s (now {state.replace('_', ' ')})"
            self._close("no_reaction", now, detail)

    # -- replies --------------------------------------------------------------------------

    def _reply(self, kind: str, now: float) -> str:
        cfg = self.config
        o = self.open
        self._event("escalation", now, what="reply", reply=kind,
                    decision_id=o.decision_id if o else None)
        if self.flow == "wind_down" and kind in ("snoozed", "dismissed"):
            if kind == "dismissed":
                if o is not None and o.level == cfg.wind_down_offer_level:
                    self._close("dismissed", now, "said no to the wind-down")
                self.flow, self.wind_until = "normal", None
                self._event("escalation", now, what="wind_down_cancelled")
                ep = self.episode
                if ep is not None and ep.cooldown_until is None:
                    ep.cooldown_until = now + cfg.ladders["bedtime"].cooldown
                return "wind-down cancelled; bedtime nudges pause, then start again"
            if self.snoozes_left <= 0:
                return "no snoozes left: goodnight when the timer ends"
            self.snoozes_left -= 1
            self.wind_until = now + cfg.wind_down_snooze_seconds
            self.reminder_said = False
            if o is not None and o.level == cfg.wind_down_offer_level:
                self._close("snoozed", now, "asked for more time")
            self._event("escalation", now, what="wind_down_snoozed", seconds=cfg.wind_down_snooze_seconds)
            return f"snoozed: goodnight in {cfg.wind_down_snooze_seconds / 60:.0f} minutes"
        if o is None:
            return "noted (no nudge waiting for an answer)"
        if kind == "accepted":
            if o.ladder == "bedtime" and o.level == cfg.goodnight_level:
                self._close("success", now, "goodnight confirmed")
                return "goodnight confirmed"
            if o.ladder == "refocus" and o.level == cfg.break_offer_level:
                self._close("success", now, "accepted the break")
                self._start_break(now, left=False)
                return f"break: no nudges for {cfg.break_seconds / 60:.0f} minutes"
            self._close("success", now, "said OK")
            if self.episode is not None:
                self.episode.quiet_until = now + (o.window or 0.0)
            if o.ladder == "bedtime" and o.level == cfg.wind_down_offer_level:
                return f"deal: goodnight in {max(0, (self.wind_until or now) - now) / 60:.0f} minutes"
            return "noted: giving them time"
        self._close(kind, now, f"the participant {kind}")
        return f"{kind}: this ladder pauses, then starts again lower"

    # -- break ----------------------------------------------------------------------------

    def _start_break(self, now: float, left: bool) -> None:
        self.flow = "break"
        self.break_until = now + self.config.break_seconds
        self.break_left_desk = left
        self.episode = None
        self._set_role("paused", now)
        self.actuator.set_idle(self.idle)            # quiet presence during the break
        self._event("escalation", now, what="break_started", seconds=self.config.break_seconds)

    def _break(self, now: float) -> None:
        cfg = self.config
        here = self._state not in ("absent", "unknown")
        if self._state == "absent":
            self.break_left_desk = True
        if self.break_left_desk and here and self._seconds_in_state(now) >= 2:
            if cfg.break_welcome_line and not self.actuator.busy():
                self.actuator.say(cfg.break_welcome_line, "hedged", {})
                self._event("escalation", now, what="welcome_back", line=cfg.break_welcome_line)
            self._end_break(now, "back")
            return
        if self.break_until is not None and now >= self.break_until and here and not self.break_left_desk:
            self._end_break(now, "break time over")
            return
        self._wait("break: no nudges; welcome back when they return. Break time left:", self.break_until)

    def _end_break(self, now: float, why: str) -> None:
        self.flow, self.break_until, self.last_break_t = "normal", None, now
        self.role_state_since = now
        self._event("escalation", now, what="break_ended", reason=why)

    # -- wind-down ------------------------------------------------------------------------

    def _start_wind_down(self, now: float) -> None:
        cfg = self.config
        self.flow = "wind_down"
        self.wind_until = now + cfg.wind_down_seconds
        self.snoozes_left = cfg.wind_down_snoozes
        self.reminder_said = False
        self._event("escalation", now, what="wind_down_started", seconds=cfg.wind_down_seconds)

    def _wind_down(self, now: float) -> None:
        cfg = self.config
        assert self.wind_until is not None
        present = self._state not in ("absent", "unknown")
        if self._state == "absent" and self._seconds_in_state(now) >= cfg.went_to_bed_after:
            if self.open is not None:
                self._close("success", now, "left during the wind-down (went to bed)")
            self._event("escalation", now, what="went_to_bed", level=cfg.wind_down_offer_level)
            self.flow, self.episode = "went_to_bed", None
            self.actuator.set_idle(None)
            return
        if (cfg.wind_down_reminder_line and not self.reminder_said and present
                and now >= self.wind_until - cfg.wind_down_reminder_before and not self.actuator.busy()):
            self.actuator.say(cfg.wind_down_reminder_line, "hedged",
                              {"minutes": max(1, int(round(cfg.wind_down_reminder_before / 60)))})
            self.reminder_said = True
            self._event("escalation", now, what="wind_down_reminder", line=cfg.wind_down_reminder_line)
        if now < self.wind_until:
            self._wait("wind-down: goodnight in", self.wind_until)
            return
        if not present:
            self._wait("wind-down over: goodnight when they are back in view")
            return
        if self.actuator.busy():
            self._wait("wind-down over: goodnight after the current movement")
            return
        if self.open is not None:                    # the offer's window may still be open
            self._close("no_reaction", now, "no answer to the wind-down offer")
        action = Action("play", "bedtime", cfg.goodnight_level, 1.0,
                        "direct" if self.estimate.get("calibrated") else "hedged",
                        reason="the wind-down timer ran out")
        ep = self.episode or Episode("bedtime", now)
        self.episode = ep
        self._play(action, self._context(ep, now, self.state_since), now, count=False,
                   policy_name="wind_down_timer", window=None)
        self.flow = "goodnight"
        self.actuator.set_idle(None)
        self._event("escalation", now, what="goodnight")
        self._wait("goodnight: end the session when they are gone")

    # -- roles ----------------------------------------------------------------------------

    def _set_role(self, role: str, now: float) -> None:
        if role == self.role:
            return
        old, self.role = self.role, role
        self._event("role_changed", now, **{"from": old, "to": role, "state": self._state})
        switch = self.config.role_switch.get(role)
        if switch and self.actuator.has_behavior(switch) and not self.actuator.busy():
            self.actuator.play(switch)
        if role == "study_buddy":
            self.actuator.set_idle(self.idle)
        elif role in self.config.ladders:
            self.actuator.set_idle(self.actuator.level_behavior(role, 0))
