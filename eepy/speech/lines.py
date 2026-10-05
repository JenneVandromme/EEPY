"""Choosing the words: which wording of a line, with its placeholders filled.

    selector = LineSelector(lambda: library.lines)
    text = selector.pick("refocus_break_offer", "direct", minutes=5)

Rules:

  * A wording is picked at random from the requested tone (hedged or direct),
    but never the same one twice in a row for the same line -- unless it is the
    only one that can be said.
  * A wording whose placeholders cannot all be filled is passed over. Lines
    often mix the two: "Let's take five." next to "Come back in {minutes}
    minutes?" -- without a value for minutes, the first is still fine, so it is
    used. Only when NO wording of the requested tone can be filled is it an
    error, and the error names what is missing.
  * If a line has no wordings at all in the requested tone, the other tone is
    used rather than saying nothing. (The validator allows a line to have only
    one tone.)
"""

from __future__ import annotations

import random
import re
import threading
from typing import Any, Callable, Mapping

PLACEHOLDER = re.compile(r"\{(\w+)\}")
TONES = ("hedged", "direct")


class SpeechError(Exception):
    """A request speech cannot honour. The message is for humans."""


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def fill(text: str, values: Mapping[str, Any]) -> str:
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)


class LineSelector:
    """Picks and fills wordings. Thread-safe; remembers the last wording per line."""

    def __init__(self, lines: Callable[[], Mapping[str, Any]],
                 rng: random.Random | None = None) -> None:
        self._lines = lines
        self._rng = rng or random.Random()
        self._last: dict[str, str] = {}
        self._lock = threading.Lock()

    def wordings(self, line_id: str, variant: str) -> list[str]:
        """Every wording of a line in a tone (falling back to the other tone
        only if this one has none)."""
        if variant not in TONES:
            raise SpeechError(f'variant must be "hedged" or "direct", not {variant!r}.')
        lines = self._lines()
        if line_id not in lines:
            import difflib

            match = difflib.get_close_matches(line_id, lines, n=1, cutoff=0.5)
            hint = f' Did you mean "{match[0]}"?' if match else ""
            raise SpeechError(f'There is no line called "{line_id}" in lines.yaml.{hint}')
        variants = lines[line_id].variants
        own = list(getattr(variants, variant))
        if own:
            return own
        other = "direct" if variant == "hedged" else "hedged"
        return list(getattr(variants, other))

    def pick(self, line_id: str, variant: str = "hedged", **values: Any) -> str:
        """The text to say. Raises SpeechError if nothing can be said in full."""
        pool = self.wordings(line_id, variant)
        usable = [w for w in pool if placeholders(w) <= set(values)]
        if not usable:
            needed = sorted(set().union(*(placeholders(w) for w in pool)) - set(values))
            given = ", ".join(sorted(values)) or "none"
            raise SpeechError(
                f'Cannot say "{line_id}" ({variant}): every wording needs '
                f"{' or '.join('{' + n + '}' for n in needed)}, and no value was given "
                f"(values given: {given}). For example: speak_line(\"{line_id}\", "
                f'"{variant}", {needed[0]}=...)')
        with self._lock:
            fresh = [w for w in usable if w != self._last.get(line_id)] or usable
            choice = self._rng.choice(fresh)
            self._last[line_id] = choice
        return fill(choice, values)
