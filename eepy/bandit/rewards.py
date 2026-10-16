"""Rewards for the bandit (A22): every nudge as (context at decision time, action, reward).

    python -m eepy.bandit.rewards all --out data/rewards.csv       # every recorded session
    python -m eepy.bandit.rewards latest P03                        # some sessions
    python -m eepy.bandit.rewards --synthetic "work 3m, sleepy 8m"  # simulated faces, the real HMM
    python -m eepy.bandit.rewards --scenario "drowsy 6m, snoozed, drowsy 10m"   # scripted, with replies
    python -m eepy.bandit.rewards --dataset test                    # the synthetic users (A17)

The OUTCOME of an escalation nudge is decided by the escalation itself (eepy/interaction/engine.py:
nudge_outcome); this module only prices it (config/rewards.yaml), discounts successes the user was
already heading for (the belief trend before the decision), and pairs it with the context logged at
the decision. Nudges the operator played by hand (wizard_nudge) are judged here, with the same
rules and reaction windows, and flagged source=wizard.

Online, the Wizard panel feeds every logged event to a RewardTracker, which logs a `nudge_reward`
event per nudge; offline, this tool feeds the SAME tracker the same logged events in time order,
so a session gives the same rewards both ways. Design: docs/A22_REWARDS.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .context import snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "rewards.yaml"
REPLY_MARKERS = {"user_accepted": "accepted", "user_snoozed": "snoozed", "user_dismissed": "dismissed"}


class RewardConfigError(ValueError):
    pass


# --- the config --------------------------------------------------------------------------


@dataclass(frozen=True)
class RewardConfig:
    refocus_best: float = 1.0
    refocus_worst: float = 0.4
    bedtime_success: float = 1.0
    break_success: float = 1.0
    accepted: float = 0.6
    snoozed: float = 0.1
    no_reaction: float = -0.1
    dismissed: float = -0.5
    interrupted_focus: float = -1.0
    full_discount_at: float = 0.3
    max_discount: float = 0.75
    rules_pending_discount: float = 0.5
    include_wizard: bool = True
    path: str = "config/rewards.yaml"


def load_config(path: Optional[Path] = None) -> RewardConfig:
    import yaml

    path = Path(path) if path is not None else CONFIG_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RewardConfigError(f"{path}: {exc}") from None
    o, r, w = raw.get("outcomes") or {}, raw.get("recovery") or {}, raw.get("wizard") or {}
    rs = o.get("refocus_success") or {}
    try:
        cfg = RewardConfig(
            refocus_best=float(rs.get("best", 1.0)), refocus_worst=float(rs.get("worst", 0.4)),
            bedtime_success=float(o.get("bedtime_success", 1.0)), break_success=float(o.get("break_success", 1.0)),
            accepted=float(o.get("accepted", 0.6)), snoozed=float(o.get("snoozed", 0.1)),
            no_reaction=float(o.get("no_reaction", -0.1)), dismissed=float(o.get("dismissed", -0.5)),
            interrupted_focus=float(o.get("interrupted_focus", -1.0)),
            full_discount_at=float(r.get("full_discount_at", 0.3)), max_discount=float(r.get("max_discount", 0.75)),
            rules_pending_discount=float(r.get("rules_pending_discount", 0.5)),
            include_wizard=bool(w.get("include", True)),
            path=str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path))
    except (TypeError, ValueError) as exc:
        raise RewardConfigError(f"{path}: every reward must be a number ({exc})") from None
    if not 0 < cfg.full_discount_at <= 1 or not 0 <= cfg.max_discount <= 1 or not 0 <= cfg.rules_pending_discount <= 1:
        raise RewardConfigError(f"{path}: recovery: full_discount_at must be in (0, 1], the discounts in [0, 1]")
    if cfg.refocus_worst > cfg.refocus_best:
        raise RewardConfigError(f"{path}: refocus_success: worst must not be above best")
    return cfg


# --- the price of one outcome ---------------------------------------------------------------


def _how(outcome: str, ladder: str, detail: str, how: Optional[str]) -> Optional[str]:
    """How a success happened; sessions from before A22 only have the detail text."""
    if outcome != "success":
        return None
    if how:
        return how
    d = detail.lower()
    if "said ok" in d:
        return "reply"
    if "break" in d:
        return "break"
    if ladder == "bedtime" or "bed" in d or "goodnight" in d:
        return "bed"
    return "focus"


def discount(cfg: RewardConfig, context: Dict[str, Any]) -> float:
    """How much of a refocus success's credit goes: the user was already heading back to focus."""
    trend = context.get("focused_trend_20s")
    if trend is not None:
        return round(cfg.max_discount * min(1.0, max(0.0, float(trend)) / cfg.full_discount_at), 3)
    if context.get("estimator") != "hmm" and context.get("pending") == "focused":
        return cfg.rules_pending_discount
    return 0.0


def price(cfg: RewardConfig, outcome: str, ladder: str, how: Optional[str], back_after: Optional[float],
          window: Optional[float], context: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], float, str]:
    """(reward, raw reward before the discount, discount, which rule). Censored: (None, None, 0, ...)."""
    if outcome == "censored":
        return None, None, 0.0, "censored"
    if outcome == "success":
        if how == "bed":
            raw, rule = cfg.bedtime_success, "bedtime_success"
        elif how == "break":
            raw, rule = cfg.break_success, "break_success"
        elif how == "reply":
            raw, rule = cfg.accepted, "accepted"
        else:
            share = 1.0 if not window or back_after is None else min(1.0, max(0.0, back_after / window))
            raw, rule = cfg.refocus_best - (cfg.refocus_best - cfg.refocus_worst) * share, "refocus_success"
        d = discount(cfg, context) if ladder == "refocus" else 0.0
        return round(raw * (1.0 - d), 4), round(raw, 4), d, rule
    value = {"snoozed": cfg.snoozed, "no_reaction": cfg.no_reaction, "dismissed": cfg.dismissed,
             "interrupted_focus": cfg.interrupted_focus}.get(outcome)
    if value is None:
        raise ValueError(f"unknown outcome {outcome!r}")
    return value, value, 0.0, outcome


# --- the tracker ---------------------------------------------------------------------------


@dataclass
class Pending:
    decision_id: str
    source: str                      # auto (a policy chose it) | wizard (the operator played it)
    t: float
    t_wall: Optional[str]
    ladder: str
    level: int
    behavior: Optional[str]
    tone: Optional[str]
    intensity: Optional[float]
    policy: Optional[str]
    propensity: Optional[float]
    window: Optional[float]
    context: Dict[str, Any]          # the snapshot: decision-time information only


@dataclass
class RewardEvent:
    session: Optional[str]
    decision_id: str
    source: str
    t: float
    t_session: Optional[float]
    ladder: str
    level: int
    behavior: Optional[str]
    tone: Optional[str]
    intensity: Optional[float]
    policy: Optional[str]
    propensity: Optional[float]
    outcome: str
    how: Optional[str]
    latency: Optional[float]         # success by focus: seconds until the focus began; else until the outcome
    window: Optional[float]
    rule: str
    raw_reward: Optional[float]
    discount: float
    reward: Optional[float]          # None: censored -- excluded from training
    included: bool
    detail: str
    context: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def row(self) -> Dict[str, Any]:
        """One flat row of the training table."""
        out = {k: v for k, v in self.as_dict().items() if k != "context"}
        out.update({f"ctx_{k}": v for k, v in self.context.items()})
        return out


class RewardTracker:
    """Pairs each nudge with its outcome and prices it; emits a RewardEvent when the outcome is
    known. Feed it the session's logged events (on_record) and, for the operator's nudges, the
    user state (on_state) -- live from the panel, or offline from the logs, in time order.

    Overlap: at most one open nudge counts at a time -- the newest. The escalation already closes
    its open nudge as censored when the operator plays something; an operator nudge still open
    when another nudge starts is closed here as censored."""

    def __init__(self, config: Optional[RewardConfig] = None, escalation: Any = None,
                 emit: Optional[Callable[[RewardEvent], None]] = None) -> None:
        self.config = config or RewardConfig()
        self.escalation = escalation
        self.emit = emit
        self.events: List[RewardEvent] = []
        self._lock = threading.RLock()
        self.reset()

    def reset(self, session: Optional[str] = None, t0: Optional[float] = None) -> None:
        self.session, self.t0 = session, t0
        self.pending: Dict[str, Pending] = {}
        self.wizard: Optional[Pending] = None          # the operator's open nudge (judged here)
        self.state: Optional[str] = None
        self.state_since: Optional[float] = None
        self._wizard_count = 0

    # -- input ----------------------------------------------------------------------------

    def on_record(self, record: Dict[str, Any]) -> None:
        with self._lock:
            kind, t = record.get("type"), record.get("t_mono")
            if t is None or kind == "nudge_reward":
                return
            t = float(t)
            if kind == "session_start":
                self.reset(record.get("session") or getattr(self, "default_session", None), t)
            elif kind == "nudge_decision":
                self._close_wizard("censored", t, "a new nudge started (the escalation's)")
                p = self._pending("auto", record, t)
                self.pending[p.decision_id] = p
            elif kind == "nudge_outcome":
                p = self.pending.pop(str(record.get("decision_id")), None)
                if p is not None:
                    self._finish(p, record["outcome"], t, str(record.get("detail") or ""),
                                 record.get("how"), record.get("back_after"), record.get("seconds"))
            elif kind == "wizard_nudge":
                self._close_wizard("censored", t, "the operator played another nudge")
                if self.config.include_wizard:
                    self._wizard_count += 1
                    self.wizard = self._pending("wizard", dict(record, decision_id=f"w{self._wizard_count}"), t)
                    self._judge(t)
            elif kind == "annotation" and record.get("marker") in REPLY_MARKERS:
                self._reply(REPLY_MARKERS[record["marker"]], t)
            elif kind == "session_end":
                self.finish(t)

    def on_state(self, t: float, state: Optional[str]) -> None:
        """The user state at t (each change; repeats are fine)."""
        with self._lock:
            if state != self.state:
                self.state, self.state_since = state, t
            self._judge(t)

    def tick(self, t: float) -> None:
        with self._lock:
            self._judge(t)

    def finish(self, t: float) -> None:
        """The session ended: the operator's open nudge is decided, and an escalation nudge
        without an outcome (older logs) is counted as censored."""
        with self._lock:
            self._judge(t)
            if self.wizard is not None:
                if self.wizard.ladder == "bedtime" and self.state == "absent" and self.state_since is not None:
                    self._close_wizard("success", t, "the session ended after they left (went to bed)", how="bed",
                                       back_after=self.state_since - self.wizard.t)
                elif self.wizard.ladder == "bedtime":
                    self._close_wizard("censored", t, "the session ended while they were still at the desk")
                else:
                    self._close_wizard("censored", t, "the session ended")
            for p in list(self.pending.values()):
                self._finish(p, "censored", t, "no outcome logged before the session ended", None, None, None)
            self.pending.clear()

    # -- inside ---------------------------------------------------------------------------

    def _window(self, ladder: str, level: int) -> Optional[float]:
        try:
            return self.escalation.ladders[ladder].reaction.get(level)
        except (AttributeError, KeyError):
            return None

    def _pending(self, source: str, record: Dict[str, Any], t: float) -> Pending:
        ladder, level = str(record.get("ladder")), int(record.get("level") or 0)
        window = record.get("reaction_window", self._window(ladder, level))
        return Pending(str(record.get("decision_id")), source, t, record.get("t_wall"), ladder, level,
                       record.get("behavior"), record.get("tone"), record.get("intensity"),
                       record.get("policy") if source == "auto" else None,
                       record.get("propensity") if source == "auto" else None, window,
                       snapshot(record.get("context") or {}, level=level, t_wall=record.get("t_wall")))

    def _finish(self, p: Pending, outcome: str, t: float, detail: str, how: Optional[str],
                back_after: Optional[float], seconds: Optional[float]) -> None:
        how = _how(outcome, p.ladder, detail, how)
        if outcome == "success" and how == "focus" and back_after is None:
            match = re.search(r"after (\d+) s", detail)            # older logs: in the detail text
            back_after = float(match.group(1)) if match else None
        latency = back_after if back_after is not None else (seconds if seconds is not None else t - p.t)
        reward, raw, d, rule = price(self.config, outcome, p.ladder, how, back_after, p.window, p.context)
        event = RewardEvent(
            session=self.session, decision_id=p.decision_id, source=p.source, t=round(p.t, 3),
            t_session=None if self.t0 is None else round(p.t - self.t0, 1), ladder=p.ladder, level=p.level,
            behavior=p.behavior, tone=p.tone, intensity=p.intensity, policy=p.policy, propensity=p.propensity,
            outcome=outcome, how=how, latency=None if latency is None else round(float(latency), 1),
            window=p.window, rule=rule, raw_reward=raw, discount=d, reward=reward, included=reward is not None,
            detail=detail, context=p.context)
        self.events.append(event)
        if self.emit is not None:
            self.emit(event)

    def _close_wizard(self, outcome: str, t: float, detail: str, how: Optional[str] = None,
                      back_after: Optional[float] = None) -> None:
        o = self.wizard
        if o is None:
            return
        self.wizard = None
        self._finish(o, outcome, t, detail, how, back_after, round(t - o.t, 1))

    def _reply(self, kind: str, t: float) -> None:
        o = self.wizard
        if o is None:
            return                                   # the escalation handles replies to its own nudges
        if kind == "accepted":
            esc = self.escalation
            if o.ladder == "bedtime" and esc is not None and o.level == esc.goodnight_level:
                self._close_wizard("success", t, "goodnight confirmed", how="bed")
            elif o.ladder == "refocus" and esc is not None and o.level == esc.break_offer_level:
                self._close_wizard("success", t, "accepted the break", how="break")
            else:
                self._close_wizard("success", t, "said OK", how="reply")
        else:
            self._close_wizard(kind, t, f"the participant {kind}")

    def _judge(self, now: float) -> None:
        """The operator's open nudge, with the escalation's own rules (engine._judge_open). Times
        are where the outcome HAPPENED (not when this ran), so online and offline agree."""
        o = self.wizard
        if o is None:
            return
        esc = self.escalation
        reset_after = getattr(esc, "reset_after", 10.0)
        false_alarm = getattr(esc, "false_alarm_seconds", 5.0)
        state, since = self.state, self.state_since
        if state == "focused" and since is not None:
            if since <= o.t + false_alarm:
                self._close_wizard("interrupted_focus", max(o.t, since), "already focused when the nudge came")
                return
            if o.ladder == "refocus" and now - since >= reset_after:
                self._close_wizard("success", since + reset_after, f"back to focused after {since - o.t:.0f} s",
                                   how="focus", back_after=since - o.t)
                return
        if state == "absent" and since is not None:
            if o.ladder == "refocus" and esc is not None and o.level == esc.break_offer_level \
                    and since - o.t <= esc.break_absent_within:
                self._close_wizard("success", since, "took the break", how="break")
                return
            bed = getattr(esc, "went_to_bed_after", 300.0)
            if o.ladder == "bedtime" and now - since >= bed:
                self._close_wizard("success", since + bed, "left after a bedtime nudge (went to bed)", how="bed",
                                   back_after=since - o.t)
                return
        if o.window is not None and now - o.t >= o.window:
            self._close_wizard("no_reaction", o.t + o.window, f"no reaction within {o.window:.0f} s")


# --- offline: sessions -> the same tracker ---------------------------------------------------


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def session_stream(folder: Path) -> Tuple[List[Tuple[float, int, str, Dict[str, Any]]], Dict[str, Any]]:
    """The session's events (events.jsonl) and its user states (state_changed in
    events.vision.jsonl), merged in time order: (t, order, kind, data)."""
    events = _read_jsonl(folder / "events.jsonl")
    vision = _read_jsonl(folder / "events.vision.jsonl")
    items: List[Tuple[float, int, str, Dict[str, Any]]] = []
    for i, r in enumerate(events):
        if r.get("t_mono") is not None:
            items.append((float(r["t_mono"]), 1, "record", dict(r, _i=i)))
    for r in vision:
        if r.get("type") == "state_changed" and r.get("t_mono") is not None:
            items.append((float(r["t_mono"]), 0, "state", r))
    items.sort(key=lambda x: (x[0], x[1], x[3].get("_i", 0)))
    meta: Dict[str, Any] = {}
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    info = {"session": folder.name, "mode": meta.get("mode"), "notes": meta.get("notes") or [],
            "decisions": sum(1 for r in events if r.get("type") == "nudge_decision"),
            "wizard_nudges": sum(1 for r in events if r.get("type") == "wizard_nudge"),
            "operator_plays": sum(1 for r in events if r.get("type") == "wizard_command" and r.get("action") == "play"),
            "states": sum(1 for r in vision if r.get("type") == "state_changed"),
            "logged_rewards": [r for r in events if r.get("type") == "nudge_reward"]}
    return items, info


def rewards_from_stream(items: Iterable[Tuple[float, int, str, Dict[str, Any]]], config: RewardConfig,
                        escalation: Any, session: Optional[str] = None) -> List[RewardEvent]:
    tracker = RewardTracker(config, escalation)
    tracker.default_session = session
    last = None
    for t, _, kind, data in items:
        tracker.tick(t)
        if kind == "state":
            tracker.on_state(t, data.get("to"))
        else:
            tracker.on_record(data)
        last = t
    if last is not None and (tracker.wizard is not None or tracker.pending):
        tracker.finish(last)
    return tracker.events


def sim_wall(t: float, t0: float = 0.0, start_hour: float = 21.0) -> str:
    """A made-up wall clock for simulations: the session starts at `start_hour`."""
    minutes = start_hour * 60 + (t - t0) / 60.0
    h, m = int(minutes // 60) % 24, int(minutes % 60)
    return f"2026-01-01T{h:02d}:{m:02d}:00.000+00:00"


def records_from_recorder(events: Sequence[Tuple[str, float, Dict[str, Any]]], t0: float,
                          start_hour: float = 21.0, session: str = "simulated") -> List[Dict[str, Any]]:
    """A simulation's engine events as logged records, with a made-up wall clock (the session
    starts at `start_hour`), so the offline path is the same."""
    out = [{"type": "session_start", "t_mono": t0, "t_wall": sim_wall(t0, t0, start_hour), "session": session}]
    for kind, t, fields in events:
        out.append(dict(fields, type=kind, t_mono=t, t_wall=sim_wall(t, t0, start_hour)))
    return out


def rerun_session(folder: Path, estimator: str = "hmm") -> List[Dict[str, Any]]:
    """For a session without escalation decisions (wizard mode, the synthetic users): its
    features through the estimator and the escalation in virtual time, as logged records."""
    from eepy.interaction.simulate import run_estimates
    from eepy.state import make_estimator
    from eepy.state.replay import run, session_features
    from eepy.state.runner import estimator_gap, panel_view

    est = make_estimator(estimator)
    estimates = [dict(panel_view(e)) for e in run(session_features(folder), est, estimator_gap(est))]
    if not estimates:
        return []
    recorder, _, _ = run_estimates(estimates, [])
    return records_from_recorder(recorder.events, estimates[0]["t"], session=folder.name)


# --- the report ------------------------------------------------------------------------------


def _band(v: Optional[float], edges: Sequence[Tuple[float, str]], none: str = "n/a") -> str:
    if v is None:
        return none
    for edge, name in edges:
        if v < edge:
            return name
    return edges[-1][1]


def report(events: Sequence[RewardEvent], infos: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    def stats(group: Sequence[RewardEvent]) -> Dict[str, Any]:
        inc = [e.reward for e in group if e.included]
        outcomes: Dict[str, int] = {}
        for e in group:
            outcomes[e.outcome] = outcomes.get(e.outcome, 0) + 1
        return {"n": len(group), "included": len(inc),
                "mean_reward": round(sum(inc) / len(inc), 3) if inc else None, "outcomes": outcomes}

    def by(key: Callable[[RewardEvent], str]) -> Dict[str, Any]:
        groups: Dict[str, List[RewardEvent]] = {}
        for e in events:
            groups.setdefault(key(e), []).append(e)
        return {k: stats(v) for k, v in sorted(groups.items())}

    censored = [e for e in events if not e.included]
    suspicious: List[str] = []
    for info in infos:
        name = info["session"]
        if info.get("operator_plays") and not info.get("wizard_nudges"):
            suspicious.append(f"{name}: {info['operator_plays']} operator play(s) logged before A22 (no "
                              "wizard_nudge event): not in the table")
        if info.get("wizard_nudges") and not info.get("states"):
            suspicious.append(f"{name}: operator nudges but no user states logged: they can only time out")
        if any("fake" in str(n).lower() for n in info.get("notes") or []):
            suspicious.append(f"{name}: its notes mention a fake camera")
        logged = {str(r.get("decision_id")): r.get("reward") for r in info.get("logged_rewards") or []}
        for e in events:
            if e.session == name and e.decision_id in logged and logged[e.decision_id] != e.reward:
                suspicious.append(f"{name} #{e.decision_id}: logged reward {logged[e.decision_id]} != recomputed "
                                  f"{e.reward} (config changed since?)")
    for e in events:
        tag = f"{e.session} #{e.decision_id}"
        if e.context.get("state") in (None, "unknown", "absent"):
            suspicious.append(f"{tag}: decided on state {e.context.get('state')!r}")
        if e.latency is not None and e.latency < 0:
            suspicious.append(f"{tag}: negative latency {e.latency}")
        if e.outcome == "success" and "the session ended after bedtime nudges" in e.detail:
            suspicious.append(f"{tag}: bedtime success only because the session ended (logged before the fix of "
                              "2026-10-16: they may still have been at the desk)")
        if e.window and e.latency is not None and e.latency > 2 * e.window and e.outcome == "success":
            suspicious.append(f"{tag}: success after {e.latency:.0f} s on a {e.window:.0f} s window (the window "
                              "pauses while absent or unknown); priced as the slowest success")
        if e.discount >= 0.5 and e.outcome == "success":
            suspicious.append(f"{tag}: success discounted {e.discount:.0%} (P(focused) was already rising)")
    return {
        "nudges": len(events), "included": len(events) - len(censored),
        "censored": len(censored), "censored_share": round(len(censored) / len(events), 3) if events else None,
        "censored_reasons": {d: sum(1 for e in censored if e.detail == d) for d in sorted({e.detail for e in censored})},
        "all": stats(events),
        "by_action": by(lambda e: f"{e.ladder} L{e.level} {e.tone or '-'}"),
        "by_source": by(lambda e: e.source),
        "by_state": by(lambda e: str(e.context.get("state"))),
        "by_confidence": by(lambda e: _band(e.context.get("confidence"),
                                            ((0.7, "< 70 %"), (0.9, "70-90 %"), (9, ">= 90 %")))),
        "by_calibrated": by(lambda e: "calibrated" if e.context.get("calibrated") else "not calibrated"),
        "by_hour": by(lambda e: _band(e.context.get("hour"), ((21, "before 21:00"), (23, "21:00-23:00"),
                                                              (25, "23:00 and later")))),
        "suspicious": sorted(set(suspicious)),
        "sessions": [{k: v for k, v in i.items() if k != "logged_rewards"} for i in infos],
    }


def print_report(rep: Dict[str, Any], title: str) -> None:
    print(f"\n  Rewards: {title}\n")
    if not rep["nudges"]:
        print("  No nudges.\n")
        return
    a = rep["all"]
    print(f"  {rep['nudges']} nudges, {rep['included']} with a reward (mean {a['mean_reward']}), "
          f"{rep['censored']} censored ({rep['censored_share']:.0%}, excluded)")
    for reason, n in rep["censored_reasons"].items():
        print(f"      censored {n}x: {reason}")
    for title2, key in (("per action", "by_action"), ("per source", "by_source"), ("per state", "by_state"),
                        ("per confidence", "by_confidence"), ("calibrated?", "by_calibrated"),
                        ("per hour", "by_hour")):
        print(f"\n  {title2}:")
        for name, s in rep[key].items():
            mean = "-" if s["mean_reward"] is None else f"{s['mean_reward']:+.2f}"
            outs = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(s["outcomes"].items()))
            print(f"    {name:22} n={s['n']:<3} mean {mean:>6}   {outs}")
    if rep["suspicious"]:
        print("\n  Worth a look:")
        for line in rep["suspicious"]:
            print(f"    - {line}")
    print()


def write_table(events: Sequence[RewardEvent], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [e.row() for e in events]
    if out.suffix == ".jsonl":
        out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return
    fields: List[str] = []
    for r in rows:
        fields += [k for k in r if k not in fields]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def resolve_sessions(names: Sequence[str], data: Optional[Path] = None) -> List[Path]:
    from eepy.logs.check import resolve
    from eepy.logs.session import DATA_DIR

    sessions = Path(data) / "sessions" if data else DATA_DIR
    if any(n == "all" for n in names):
        return sorted(p for p in sessions.iterdir() if (p / "events.jsonl").exists())
    return [resolve(n, sessions) for n in names]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.bandit.rewards",
                                     description="Build the bandit's training table (one row per nudge) and a reward report.")
    parser.add_argument("sessions", nargs="*", help='session folder names, unique parts, "latest", or "all"')
    parser.add_argument("--synthetic", help='simulated faces through the real HMM, e.g. "work 3m, sleepy 8m"')
    parser.add_argument("--scenario", help='a scripted state timeline with replies, e.g. "drowsy 6m, snoozed, drowsy 10m"')
    parser.add_argument("--responsive", metavar="TIMELINE",
                        help='a simulated user who REACTS to nudges (eepy/bandit/simuser.py; made-up numbers), '
                             'e.g. "focused 3m, distracted 6m, focused 3m, drowsy 15m"')
    parser.add_argument("--persona", default="average", help="--responsive: average, responsive or stubborn")
    parser.add_argument("--runs", type=int, default=1, help="--responsive: how many runs (seeds 1..N)")
    parser.add_argument("--start-hour", type=float, default=21.0, help="simulations: the clock time it starts")
    parser.add_argument("--dataset", metavar="SPLIT", help="the synthetic users (python -m eepy.state.dataset build): "
                                                           "train, test, test_hard or all")
    parser.add_argument("--rerun", choices=("rules", "hmm"),
                        help="re-run the escalation on sessions without logged decisions (e.g. wizard mode)")
    parser.add_argument("--config", help="another rewards file (default config/rewards.yaml)")
    parser.add_argument("--data", help="the data folder (default data/)")
    parser.add_argument("--out", help="write the training table (.csv or .jsonl)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON (for the launcher)")
    args = parser.parse_args(argv)
    if not (args.sessions or args.synthetic or args.scenario or args.dataset or args.responsive):
        parser.print_help()
        return 2
    from eepy.interaction.config import load as load_escalation

    try:
        config = load_config(Path(args.config) if args.config else None)
        escalation = load_escalation()
        events: List[RewardEvent] = []
        infos: List[Dict[str, Any]] = []
        titles: List[str] = []
        if args.sessions:
            folders = resolve_sessions(args.sessions, Path(args.data) if args.data else None)
            for folder in folders:
                items, info = session_stream(folder)
                if args.rerun and not info["decisions"]:
                    recs = rerun_session(folder, args.rerun)
                    items = [(float(r["t_mono"]), 1, "record", r) for r in recs]
                    info["rerun"] = args.rerun
                events += rewards_from_stream(items, config, escalation, folder.name)
                infos.append(info)
            titles.append(f"{len(folders)} recorded session(s)")
        if args.synthetic or args.scenario:
            from eepy.interaction.simulate import (SCENARIOS, parse_spec, run_estimates, run_scenario,
                                                   synthetic_estimates)
            if args.synthetic:
                estimates, t0 = synthetic_estimates(args.synthetic)
                recorder, _, _ = run_estimates(estimates, [])
                titles.append(f"synthetic: {args.synthetic}")
            else:
                recorder, _, _ = run_scenario(SCENARIOS.get(args.scenario) or parse_spec(args.scenario))
                t0 = 0.0
                titles.append(f"scenario: {args.scenario}")
            recs = records_from_recorder(recorder.events, t0, args.start_hour)
            events += rewards_from_stream([(float(r["t_mono"]), 1, "record", r) for r in recs], config, escalation)
        if args.responsive:
            from .simuser import run_responsive

            for seed in range(1, max(1, args.runs) + 1):
                recorder, t0 = run_responsive(args.responsive, seed=seed, persona=args.persona,
                                              start_hour=args.start_hour)
                recs = records_from_recorder(recorder.events, t0, args.start_hour, session=f"responsive-{seed}")
                events += rewards_from_stream([(float(r["t_mono"]), 1, "record", r) for r in recs], config, escalation)
            titles.append(f"reactive user ({args.persona}, {max(1, args.runs)} run(s)): {args.responsive}")
        if args.dataset:
            from eepy.state.dataset import dataset_dir, users_of

            ds = dataset_dir("v1", Path(args.data) if args.data else None)
            users = users_of(ds, [args.dataset])
            for u in users:
                folder = ds / u["folder"]
                recs = rerun_session(folder, args.rerun or "hmm")
                events += rewards_from_stream([(float(r["t_mono"]), 1, "record", r) for r in recs], config, escalation)
            titles.append(f"{len(users)} synthetic users ({args.dataset}, {args.rerun or 'hmm'})")
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2
    rep = report(events, infos)
    rep["title"] = "; ".join(titles)
    rep["config"] = config.path
    if args.out:
        write_table(events, Path(args.out))
        rep["out"] = args.out
    if args.json:
        print(json.dumps(rep, default=str))
    else:
        print_report(rep, rep["title"])
        if args.out:
            print(f"  Training table: {args.out} ({len(events)} rows)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
