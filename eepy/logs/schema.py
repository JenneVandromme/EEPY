"""Every event type EEPY can log, defined in one place.

If it is not in EVENT_TYPES, it cannot be logged: `log.event("anotation", ...)`
fails straight away, in the developer's face, rather than quietly creating a
new kind of row nobody will think to analyse. Each type lists the fields it
must have; extra fields are always allowed, so the data can grow without a
schema change.

Bump SCHEMA_VERSION when a change would make old files read differently
(renaming or removing a field, changing what a value means). Adding a new
type, or a new optional field, does not need a bump.

This module is standard-library only, and must stay that way: the vision
process imports it from the OTHER virtualenv, which has neither pydantic nor
pyyaml, and runs Python 3.9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Tuple

SCHEMA_VERSION = 1

# Who caused an event.
SOURCES: FrozenSet[str] = frozenset({
    "wizard",      # the operator, through the panel
    "auto",        # the autonomous decision system: rules, HMM, bandit
    "idle",        # the robot's idle loop
    "system",      # the software itself: start-up, reloads, connection, errors
    "perception",  # the vision process
})

# The experimental conditions. Only "wizard" has a controller so far.
MODES: Tuple[str, ...] = ("wizard", "rules", "hmm", "hmm_bandit")
IMPLEMENTED_MODES: FrozenSet[str] = frozenset({"wizard"})


@dataclass(frozen=True)
class EventType:
    group: str
    required: Tuple[str, ...]
    doc: str
    reserved: bool = False                  # defined now, emitted by a later task
    personal: Tuple[str, ...] = field(default=())   # fields holding what a
                                            # participant SAID -- dropped unless
                                            # privacy.store_raw_speech is on


EVENT_TYPES: Dict[str, EventType] = {
    # --- the session ---------------------------------------------------------
    "session_start": EventType("session", ("participant", "mode"),
                               "A session began. Always the first line."),
    "session_end": EventType("session", ("reason",),
                             "A session ended: operator, panel stopped, process exit."),
    "mode_not_implemented": EventType("session", ("mode",),
                                      "The chosen mode has no controller yet; manual control only."),

    # --- the robot, as reported by the BehaviorPlayer ---------------------------
    "behavior_started": EventType("robot", ("behavior",), "A behaviour began."),
    "keyframe_reached": EventType("robot", ("behavior", "index"),
                                  "A keyframe's pose was reached."),
    "behavior_finished": EventType("robot", ("behavior",), "A behaviour played to its end."),
    "behavior_interrupted": EventType("robot", ("behavior", "by"),
                                      "Something cut a behaviour short."),
    "behavior_refused": EventType("robot", ("behavior", "reason"),
                                  "A behaviour was refused: unsafe from the current pose."),
    "line_requested": EventType("robot", ("line_id", "text"),
                                "A line was handed to speech. Written by sessions recorded "
                                "before speech existed (A5); now speech_requested."),

    # --- the robot's voice (eepy/speech). All words here are the ROBOT's. --------
    "speech_requested": EventType("speech", ("line_id", "text"),
                                  "Words were chosen: a line's wording, or free text "
                                  "(line_id free_text)."),
    "speech_started": EventType("speech", ("line_id", "engine"),
                                "Audio began. latency_ms is from the request to here; "
                                "cached says whether it was pre-generated."),
    "speech_finished": EventType("speech", ("line_id",), "The clip played to the end."),
    "speech_interrupted": EventType("speech", ("line_id", "by"),
                                    "Speech was cut short: by stop, a new behaviour, newer speech."),
    "speech_failed": EventType("speech", ("line_id", "error"),
                               "Nothing could be played. The words are still in `text`, "
                               "shown in the panel instead."),
    "sound_played": EventType("robot", ("file",), "A sound was sent to the speaker."),

    # --- the operator ------------------------------------------------------------
    "wizard_command": EventType("operator", ("action",),
                                "The operator pressed something: play, stop, say, idle, ..."),
    "annotation": EventType("operator", ("marker",),
                            "The operator OBSERVED something. These are the HMM labels."),
    "note": EventType("operator", ("text",), "A free-text note from the operator."),

    # --- the software --------------------------------------------------------------
    "connection": EventType("system", ("status",),
                            "The robot link: connected, lost, failed."),
    "reload": EventType("system", ("ok",), "Behaviour files and config were reloaded."),
    "error": EventType("system", ("message",), "Something went wrong."),

    # --- reserved for later tasks: defined now so the format is settled ---------------
    "hmm_belief": EventType("model", ("probs", "most_likely"),
                            "The HMM's belief over hidden states, e.g. "
                            "{'focused': .7, 'distracted': .2, 'tired': .1}.", reserved=True),
    "bandit_decision": EventType("model", ("arm", "policy"),
                                 "The bandit chose an action. Log 'propensity' too: "
                                 "the probability it had of choosing it, needed for "
                                 "off-policy evaluation.", reserved=True),
    "bandit_reward": EventType("model", ("decision_seq", "reward"),
                               "The reward for an earlier bandit_decision, by its seq.",
                               reserved=True),
    "reply_category": EventType("dialogue", ("category",),
                                "What kind of reply the participant gave.",
                                reserved=True, personal=("text",)),
    "llm_output": EventType("dialogue", ("model", "output"),
                            "Text generated by a language model, and how long it took.",
                            reserved=True, personal=("prompt",)),
}


class SchemaError(ValueError):
    """An event that does not match its definition. A bug in the caller."""


def check_event(event_type: str, source: str, data: dict) -> None:
    """Raise SchemaError if this event cannot be logged as given."""
    if event_type not in EVENT_TYPES:
        import difflib

        match = difflib.get_close_matches(event_type, EVENT_TYPES, n=1)
        hint = f' Did you mean "{match[0]}"?' if match else ""
        raise SchemaError(f'"{event_type}" is not an event type in eepy/logs/schema.py.{hint}')
    if source not in SOURCES:
        raise SchemaError(f'source must be one of {sorted(SOURCES)}, not "{source}".')
    missing = [name for name in EVENT_TYPES[event_type].required if data.get(name) is None]
    if missing:
        raise SchemaError(f'a "{event_type}" event needs: {", ".join(missing)}.')
