"""What any policy may play right now (A23): the bandit's action space and its safety rules.

    allowed = allowed_actions(context)          # [Action.wait(...), Action("play", "refocus", 1, ...), ...]

The engine (engine.py) decides WHEN a nudge may happen -- onset, the guards (minimum gap, per
hour, after calibration), cooldowns, A19's evidence -- and only then asks the policy WHAT to play.
This module says which "what"s are allowed at that moment; the policy must pick inside the set
(the engine checks, and falls back to the fixed escalation if it does not). "Wait" is always in it.

An action is (ladder, level, tone). The rules, all in config/escalation.yaml `actions:`:
  - the ladder must match the state: distracted -> refocus, drowsy / very drowsy -> bedtime;
    focused, absent, unknown -> wait only. Ambiguous (A19): only the gentle option, once.
  - the first nudge of an episode at most `max_start_level` (A19's switch carry-over and the
    restart after a cooldown may start higher); afterwards at most `max_step_up` above the last
  - no level they already ignored this episode (a restart after a cooldown is a fresh round)
  - the break offer only after `break_after_work` seconds of work; goodnight only via the
    wind-down timer, or early when very drowsy, sure, calibrated, after an ignored spoken nudge
  - the direct tone only when calibrated and sure (confidence.direct); ambiguous: hedged
  - a guard blocking (context.blocked): wait only
Ordered from least to most intrusive (the designers' ranking when it exists, else the level).
Docs: docs/A23_ALLOWED_ACTIONS.md.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import EscalationConfig
from .policy import Action, NudgeContext

Key = Tuple[Optional[str], Optional[int], str]


def key(action: Action) -> Key:
    """What makes two actions the same choice (intensity is scaled by the engine, not chosen)."""
    return (action.ladder, action.level, action.tone) if action.kind == "play" else (None, None, "wait")


def label(action: Action) -> str:
    """ "refocus 2 hedged" / "wait": for logs and the launcher."""
    return "wait" if action.kind != "play" else f"{action.ladder} {action.level} {action.tone}"


def allowed_actions(context: NudgeContext, config: Optional[EscalationConfig] = None) -> List[Action]:
    """Exactly the actions a policy may choose from now; the first is always wait."""
    if config is None:
        from .config import load
        config = load()
    wait = Action.wait("always allowed")
    if context.blocked:
        return [wait]
    ladder = context.ladder
    if ladder not in config.ladders:
        return [wait]
    state = context.state
    if context.ambiguous:
        if state in ("absent", "unknown"):
            return [wait]
    elif config.role_for(state) != ladder:
        return [wait]                       # e.g. no bedtime action while distracted, nothing while focused
    a, lad = config.actions, config.ladders[ladder]
    conf = context.confidence

    if context.ambiguous:
        if context.level is not None:
            return [wait]                   # A19: one gentle nudge, then wait until it is clear
        levels = [context.start_level or lad.start_level]
    else:
        if context.level is None:
            cap = a.max_start_level.get("very_drowsy" if ladder == "bedtime" and state == "very_drowsy" else ladder,
                                        lad.top)
            if context.restarting:
                cap = max(cap, lad.restart_level)
            if context.start_level is not None:
                cap = max(cap, context.start_level)
        else:
            cap = context.level + a.max_step_up
        levels = [lv for lv in lad.levels if lv <= cap]
        if a.no_ignored_levels:
            levels = [lv for lv in levels if lv not in context.ignored_levels]
        if ladder == "refocus":
            worked = context.since_break if context.since_break is not None else context.session_seconds
            if worked < a.break_after_work:
                levels = [lv for lv in levels if lv != config.break_offer_level]
        if ladder == "bedtime" and a.goodnight_early and state == "very_drowsy" and conf is not None \
                and conf >= a.goodnight_confidence and (context.calibrated or not a.goodnight_needs_calibration) \
                and (not a.goodnight_after_spoken or any(lv >= a.spoken_from for lv in context.ignored_levels)):
            levels.append(config.goodnight_level)
    sure = conf is None or conf >= config.direct_confidence       # the rules give no confidence
    direct_ok = sure and (context.calibrated or not a.direct_needs_calibration)
    if context.ambiguous:
        tones = [a.ambiguous_tone] if a.ambiguous_tone == "hedged" or direct_ok else ["hedged"]
    else:
        tones = ["hedged", "direct"] if direct_ok else ["hedged"]
    plays: List[Action] = []
    for level in sorted(set(levels), key=lambda lv: (rank(config, ladder, lv), lv)):
        for tone in tones:
            plays.append(Action("play", ladder, level, lad.intensity.get(level, 1.0), tone, reason="allowed",
                                extra={"rank": rank(config, ladder, level) + (0.5 if tone == "direct" else 0.0),
                                       "most_intrusive": most_intrusive(config, ladder, level)}))
    return [wait] + plays


def rank(config: EscalationConfig, ladder: str, level: int) -> float:
    """How intrusive (higher = more): the designers' ranking (B8) if given, else the level."""
    return config.actions.intrusiveness.get(f"{ladder}:{level}", float(level))


def most_intrusive(config: EscalationConfig, ladder: str, level: int) -> bool:
    return level >= config.actions.most_intrusive_from.get(ladder, 99)


def describe(actions: Iterable[Action]) -> Dict[str, Any]:
    """The allowed set for a log line: the choices, and which of them are the most intrusive."""
    actions = list(actions)
    return {"allowed": [label(x) for x in actions],
            "most_intrusive": [label(x) for x in actions if x.extra.get("most_intrusive")]}


def contains(actions: Iterable[Action], action: Action) -> bool:
    return key(action) in {key(x) for x in actions}
