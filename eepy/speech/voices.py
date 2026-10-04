"""Hear one line in every voice, to choose the robot's voice (task C3b).

    python -m eepy.speech.voices --line refocus_check
    python -m eepy.speech.voices --line refocus_3          # what refocus level 3 says
    python -m eepy.speech.voices --text "Shall we take a break?"
    python -m eepy.speech.voices --list                    # what can be tried
    python -m eepy.speech.voices --download en_GB-alba-medium
    python -m eepy.speech.voices --download suggested      # all of them (~60 MB each)

Plays the line once per voice, one after another, through this computer's
speakers, showing each voice's name as it plays. Ctrl+C stops. At the end it
prints exactly what to put in config/eepy.yaml for the one you liked.

Which voices are tried:
    piper    every Piper voice downloaded into .cache/voices/
    system   a shortlist of this computer's voices (--all-system for every one)
    google   if this computer is online
    gemini   if GEMINI_API_KEY is set in .env
Narrow it down with --engines piper,system.
"""

from __future__ import annotations

from eepy.behaviors._deps import require as _require

_require()

import argparse
import platform
import re
import sys
import time
from pathlib import Path

from eepy.behaviors.loader import load_library
from eepy.behaviors.play import load_env_file

from . import audio
from .download import SUGGESTED, DownloadError, download, downloaded
from .engines import (ENGINE_NAMES, EngineError, GeminiEngine, GoogleEngine, PiperEngine,
                      SystemEngine, system_voices)
from .lines import LineSelector, SpeechError, placeholders
from .outputs import DesktopOutput

# A shortlist of the system voices that suit a desk companion, per OS. The
# rest are still available with --all-system.
SYSTEM_SHORTLIST = {
    "Darwin": ["Samantha", "Daniel", "Karen", "Moira", "Tessa", "Rishi",
               "Flo (English (US))", "Reed (English (US))", "Fred"],
}
GEMINI_VOICES = ["Kore", "Puck", "Charon", "Aoede", "Leda"]
SAMPLE_VALUES = {"minutes": 40, "evidence": "you've been still for a while"}


def _line_text(spec: str, variant: str) -> tuple[str, str]:
    """(label, text) for --line: a line ID, or <ladder>_<level> for what that rung says."""
    library = load_library()
    selector = LineSelector(lambda: library.lines)
    line_id = spec
    match = re.fullmatch(r"([a-z]+)_(\d+)", spec)
    if spec not in library.lines and match and match.group(1) in library.ladders():
        ladder, level = match.group(1), int(match.group(2))
        try:
            behavior = library.get_behavior(ladder, level)
        except KeyError as exc:
            raise SpeechError(str(exc).strip('"')) from None
        said = [step.say for step in library.resolve(behavior) if step.say]
        if not said:
            raise SpeechError(f"{ladder} level {level} ({behavior.name}) does not say anything. "
                              "Try a level that has a spoken line, or --line <line id>.")
        line_id = said[0]
        print(f"  {spec} is {behavior.name}, which says the line \"{line_id}\".")
    pool = selector.wordings(line_id, variant)
    # A wording without placeholders if there is one; otherwise fill in samples.
    text = next((w for w in pool if not placeholders(w)), None)
    if text is None:
        text = selector.pick(line_id, variant, **SAMPLE_VALUES)
    return f"{line_id} ({variant})", text


def _candidates(engines: set[str], all_system: bool) -> list:
    out = []
    if "piper" in engines:
        out += [PiperEngine(v) for v in downloaded()]
    if "system" in engines:
        available = system_voices()
        shortlist = SYSTEM_SHORTLIST.get(platform.system())
        names = available if all_system or not shortlist else [v for v in shortlist if v in available]
        out += [SystemEngine(v) for v in names] or [SystemEngine(None)]
    if "google" in engines:
        out.append(GoogleEngine("en"))
    if "gemini" in engines:
        out += [GeminiEngine(v) for v in GEMINI_VOICES]
    return out


def _config_snippet(engine) -> str:
    voice = engine.voice if engine.voice else "null"
    return f"  speech:\n    engine: {engine.name}\n    voice: {voice}"


def _play(output: DesktopOutput, data: bytes, path: Path) -> None:
    path.write_bytes(data)
    output.play(path)
    started, length = time.monotonic(), audio.duration(data)
    while time.monotonic() - started < length + 3:
        finished = output.done()
        if finished is True or (finished is None and time.monotonic() - started >= length):
            break
        time.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(prog="python -m eepy.speech.voices",
                                     description="Hear a line in every available voice.")
    what = parser.add_mutually_exclusive_group()
    what.add_argument("--line", help="a line ID from lines.yaml, or e.g. refocus_3 for "
                                     "the line refocus level 3 says")
    what.add_argument("--text", help="any text instead of a line")
    parser.add_argument("--variant", choices=("hedged", "direct"), default="direct")
    parser.add_argument("--engines", default=",".join(ENGINE_NAMES),
                        help="which engines to try, e.g. piper,system (default: all)")
    parser.add_argument("--all-system", action="store_true",
                        help="every system voice, not just the shortlist")
    parser.add_argument("--list", action="store_true", help="list the voices and stop")
    parser.add_argument("--download", nargs="+", metavar="VOICE",
                        help='download Piper voices (or "suggested" for all suggested ones)')
    parser.add_argument("--no-play", action="store_true",
                        help="synthesize and time each voice, but do not play it")
    args = parser.parse_args(argv)

    if args.download:
        names = list(SUGGESTED) if args.download == ["suggested"] else args.download
        for name in names:
            try:
                download(name)
            except DownloadError as exc:
                print(f"\n  Could not download {name}: {exc}", file=sys.stderr)
                if name not in SUGGESTED:
                    print("  Suggested voices: " + ", ".join(SUGGESTED), file=sys.stderr)
                return 1
        if not (args.line or args.text or args.list):
            return 0

    engines = {e.strip() for e in args.engines.split(",") if e.strip()}
    unknown = engines - set(ENGINE_NAMES)
    if unknown:
        print(f"Unknown engine(s): {', '.join(sorted(unknown))}. Use: {', '.join(ENGINE_NAMES)}",
              file=sys.stderr)
        return 2
    candidates = _candidates(engines, args.all_system)

    if args.list or not (args.line or args.text):
        print("\n  Voices that can be tried now:")
        for engine in candidates:
            reason = engine.unavailable()
            print(f"    {'  ' if reason is None else '- '}{engine.label}"
                  + (f"   (not usable: {reason})" if reason else ""))
        missing = [v for v in SUGGESTED if v not in downloaded()]
        if missing and "piper" in engines:
            print("\n  More Piper voices you can download (~60 MB each):")
            for voice in missing:
                print(f"    {voice:36s} {SUGGESTED[voice]}")
            print("\n    python -m eepy.speech.voices --download <name>      (or: suggested)")
        if not (args.line or args.text):
            print("\n  To hear them: python -m eepy.speech.voices --line refocus_check")
        return 0

    try:
        label, text = (("your text", args.text) if args.text else _line_text(args.line, args.variant))
    except SpeechError as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2

    usable = [e for e in candidates if e.unavailable() is None]
    skipped = [e for e in candidates if e.unavailable() is not None]
    if not usable:
        print("\n  No voice can be used. Run with --list to see why.", file=sys.stderr)
        return 1
    print(f'\n  {label}: "{text}"')
    print(f"  {len(usable)} voices, one after another. Ctrl+C to stop.\n")

    output = DesktopOutput()
    if not args.no_play and output.unavailable():
        print(f"  Cannot play audio here: {output.unavailable()}", file=sys.stderr)
        return 1
    scratch = Path(__file__).resolve().parents[2] / ".cache" / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    played = []
    try:
        for number, engine in enumerate(usable, start=1):
            print(f"  [{number:2d}/{len(usable)}]  {engine.label:44s}", end="", flush=True)
            try:
                started = time.perf_counter()
                data = engine.synthesize(text)
                took = time.perf_counter() - started
            except EngineError as exc:
                print(f"failed: {exc}")
                continue
            print(f"made in {took * 1000:5.0f} ms, {audio.duration(data):.1f} s long")
            played.append(engine)
            if not args.no_play:
                _play(output, data, scratch / f"voices_sample.{engine.ext}")
                time.sleep(0.6)
    except KeyboardInterrupt:
        output.stop()
        print("\n  Stopped.")

    if skipped:
        reasons: dict[str, list[str]] = {}
        for engine in skipped:
            reasons.setdefault(f"{engine.name}: {engine.unavailable()}", []).append(engine.voice or "default")
        print("\n  Not tried:")
        for reason, voices in reasons.items():
            print(f"    {reason}  ({len(voices)} voice{'s' if len(voices) != 1 else ''})")
    if played:
        print("\n  To use a voice, put it in config/eepy.yaml. For example, the first one:\n")
        print(_config_snippet(played[0]))
        print("\n  Then restart the panel. Lines are regenerated in the new voice by themselves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
