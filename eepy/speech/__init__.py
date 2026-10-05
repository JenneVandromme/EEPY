"""EEPY's voice: lines from lines.yaml and free text, spoken aloud.

    from eepy.speech import make_speaker
    speaker = make_speaker()                # from config/eepy.yaml, section `speech`
    speaker.start(backend)
    speaker.speak_line("refocus_check", "direct")
    speaker.speak_text("Need a hand?")

The BehaviorPlayer owns one and speaks the `say:` lines of behaviours through
it. See eepy/speech/README.md for how it works, and
`python -m eepy.speech.voices` for choosing a voice.
"""

from __future__ import annotations

from typing import Any, Optional

from .engines import ENGINE_NAMES, Engine, EngineError, SystemEngine, make_engine
from .lines import LineSelector, SpeechError
from .outputs import OUTPUT_NAMES, Output, OutputError, make_output
from .speaker import FREE_TEXT, Speaker, Utterance


def make_speaker(config: Any = None, **kwargs: Any) -> Speaker:
    """A Speaker as configured in config/eepy.yaml (or `config`, a Speech settings object).

    engine "none" gives a text-only speaker: words are shown and logged, never
    played. The fallback is the operating system's voice unless switched off.
    """
    if config is None:
        from eepy.settings import load_settings

        config = load_settings().speech
    engine: Optional[Engine] = None
    if config.engine != "none":
        engine = make_engine(config.engine, config.voice)
    fallback: Optional[Engine] = None
    if config.fallback == "system" and (engine is None or engine.name != "system"):
        fallback = SystemEngine(config.fallback_voice)
    return Speaker(engine, fallback=fallback, output=config.output,
                   prepare=config.pregenerate, **kwargs)


__all__ = ["ENGINE_NAMES", "Engine", "EngineError", "FREE_TEXT", "LineSelector", "OUTPUT_NAMES",
           "Output", "OutputError", "Speaker", "SpeechError", "Utterance", "make_engine",
           "make_output", "make_speaker"]
