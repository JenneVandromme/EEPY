"""The evidence catalog (A20): every kind of evidence EEPY can give as a reason, defined once.

    python -m eepy.explanations.catalog            # print docs/EXPLANATION_CATALOG.md
    python -m eepy.explanations.catalog --write    # write it
    python -m eepy.explanations.catalog --check    # is the file up to date? (exit 1 if not)

Each evidence type groups the features that measure the SAME thing -- the HMM's correlation
groups (config/state_hmm.yaml `groups:`), so one long eye closure is one reason, not six.
A type says which HMM features and which feature inputs (the rules' features) it covers,
what it means in plain words, the placeholders a spoken line can use, and a default
sentence per tone, used until the designers write their own `explain_<type>` lines in
behaviors/lines.yaml.

Only evidence the model really uses is here: EEPY has no phone detector, so "on the phone"
shows up as looking away. Python 3.9, standard library only (the vision process imports it).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "EXPLANATION_CATALOG.md"
LINE_PREFIX = "explain_"            # a designer's line for a type: explain_eye_closure, ...
TONES = ("hedged", "direct")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class Placeholder:
    name: str
    unit: str
    meaning: str
    example: str


@dataclass(frozen=True)
class Template:
    """A default sentence. `lead`: the feature inputs it suits best (the one that moved the
    belief most comes first); a sentence is only used when all its placeholders have a value."""

    text: str
    lead: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceType:
    id: str
    title: str
    meaning: str                         # plain words, for designers and the doc
    supports: str                        # the state this evidence usually points to
    features: Tuple[str, ...]            # HMM feature ids (config/state_hmm.yaml)
    inputs: Tuple[str, ...]              # feature inputs: what the rules test, and the values
    placeholders: Tuple[Placeholder, ...]
    templates: Dict[str, Tuple[Template, ...]]      # tone -> sentences, best first
    for_phrase: str                      # in a contrast, when it pointed TO the decided state
    against_phrase: str                  # ... when it pointed AWAY from the runner-up
    example: Dict[str, Any] = field(default_factory=dict)   # example values (feature inputs)

    @property
    def line_id(self) -> str:
        return LINE_PREFIX + self.id

    @property
    def placeholder_names(self) -> Tuple[str, ...]:
        return tuple(p.name for p in self.placeholders)


MINUTES = Placeholder("minutes", "minutes", "how long the evidence has been building up for this decision "
                      "(at least 1)", "3")


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


CATALOG: Tuple[EvidenceType, ...] = (
    EvidenceType(
        id="eye_closure",
        title="Eyes closing",
        meaning="The eyes are closed more, or for longer, than when alert: the share of time closed "
                "(PERCLOS), long or slow blinks, one very long closure (a microsleep), the eyes shut "
                "right now, and the eyes-closed action unit (AU43). They all measure the same closing "
                "eyes, so they count as one reason.",
        supports="drowsy",
        features=("perclos", "blink_duration", "long_blinks", "microsleep", "eyes_closed_now", "au_eyes_closed"),
        inputs=("perclos_60s", "blink_mean_ms_rel_60s", "blink_mean_ms_60s", "long_blinks_60s",
                "blink_max_ms_60s", "eyes_closed_now_s", "AU43_active_60s"),
        placeholders=(
            Placeholder("closed_pct", "%", "share of the last minute the eyes were closed", "34"),
            Placeholder("times_normal", "x", "blinks this many times as long as the person's own normal "
                        "(only when calibrated, and at least 1.2)", "1.8"),
            Placeholder("long_blinks", "count", "long blinks in the last minute", "4"),
            Placeholder("long_blinks_text", "words", '"one long blink", "4 long blinks"', "4 long blinks"),
            Placeholder("longest_s", "seconds", "the longest single closure in the last minute "
                        "(only from 1 s)", "1.6"),
            Placeholder("closed_now_s", "seconds", "how long the eyes have been shut right now "
                        "(only from 1 s)", "2.0"),
            MINUTES),
        templates={
            "hedged": (
                Template("It looks like your eyes were closed about {closed_pct}% of the last minute.", ("perclos_60s", "AU43_active_60s")),
                Template("Your blinks seem about {times_normal} times longer than usual.", ("blink_mean_ms_rel_60s",)),
                Template("I noticed {long_blinks_text} in the last minute.", ("long_blinks_60s",)),
                Template("Your eyes seemed to stay shut for {longest_s} seconds at one point.", ("blink_max_ms_60s",)),
                Template("Your eyes seem to be closing a lot."),
            ),
            "direct": (
                Template("Your eyes were closed {closed_pct}% of the last minute.", ("perclos_60s", "AU43_active_60s")),
                Template("Your blinks are {times_normal} times longer than your normal.", ("blink_mean_ms_rel_60s",)),
                Template("That was {long_blinks_text} in the last minute.", ("long_blinks_60s",)),
                Template("Your eyes stayed shut for {longest_s} seconds.", ("blink_max_ms_60s", "eyes_closed_now_s")),
                Template("Your eyes keep closing."),
            ),
        },
        for_phrase="your eyes were closing more",
        against_phrase="your eyes stayed open",
        example={"perclos_60s": 0.34, "blink_mean_ms_rel_60s": 1.8, "long_blinks_60s": 4, "blink_max_ms_60s": 1600},
    ),
    EvidenceType(
        id="yawning",
        title="Yawning",
        meaning="Yawns, and the jaw-drop action unit (AU26) that a yawn mostly is: one reason.",
        supports="drowsy",
        features=("yawns", "au_jaw_drop"),
        inputs=("yawns_60s", "AU26_active_60s"),
        placeholders=(
            Placeholder("yawns", "count", "yawns in the last minute", "2"),
            Placeholder("yawns_times", "words", '"once", "twice", "3 times"', "twice"),
            Placeholder("jaw_pct", "%", "share of the last minute the jaw-drop action unit was active", "12"),
            MINUTES),
        templates={
            "hedged": (Template("I think I saw you yawn {yawns_times} in the last minute.", ("yawns_60s",)),
                       Template("You seem to be yawning.")),
            "direct": (Template("You've yawned {yawns_times} in the last minute.", ("yawns_60s",)),
                       Template("You're yawning.")),
        },
        for_phrase="you yawned",
        against_phrase="you didn't yawn",
        example={"yawns_60s": 2, "AU26_active_60s": 0.12},
    ),
    EvidenceType(
        id="nodding",
        title="Nodding off",
        meaning="The head dropping forward and jerking back up: nodding off.",
        supports="drowsy",
        features=("nods",),
        inputs=("nods_60s",),
        placeholders=(
            Placeholder("nods", "count", "nods in the last minute", "1"),
            Placeholder("nods_times", "words", '"once", "twice", "3 times"', "once"),
            MINUTES),
        templates={
            "hedged": (Template("It looked like your head dropped {nods_times} just now.", ("nods_60s",)),
                       Template("Your head seems to be dropping.")),
            "direct": (Template("Your head dropped {nods_times} in the last minute.", ("nods_60s",)),
                       Template("You're nodding off.")),
        },
        for_phrase="your head was dropping",
        against_phrase="your head stayed up",
        example={"nods_60s": 1},
    ),
    EvidenceType(
        id="looking_away",
        title="Looking away from the work",
        meaning="The gaze is off the screen and the notes: the share of the last minute, and how long "
                "right now. Looking at a phone in the lap shows up here too (EEPY has no phone detector). "
                "When the gaze stays ON the work, the same evidence speaks against distraction.",
        supports="distracted",
        features=("gaze_off", "away_now"),
        inputs=("gaze_off_ratio_60s", "away_current_s", "away_episodes_60s"),
        placeholders=(
            Placeholder("away_pct", "%", "share of the last minute looking away from the work", "45"),
            Placeholder("away_seconds", "seconds", "how long they have been looking away right now (from 10 s; shorter, the minute's share says more)", "12"),
            Placeholder("away_for", "words", 'the same in words: "12 seconds", "about 2 minutes"', "12 seconds"),
            Placeholder("away_count", "count", "how many separate times they looked away in the last minute "
                        "(background detail: the model does not weigh the count itself; from 2)", "4"),
            Placeholder("away_times", "words", '"twice", "4 times" (from 2)', "4 times"),
            Placeholder("on_work_pct", "%", "share of the last minute looking at the work", "55"),
            MINUTES),
        templates={
            "hedged": (Template("You seem to have been looking away for {away_for}.", ("away_current_s",)),
                       Template("It looks like you were looking away about {away_pct}% of the last minute.", ("gaze_off_ratio_60s",)),
                       Template("Your eyes seem to be off your work a lot.")),
            "direct": (Template("You've been looking away for {away_for}.", ("away_current_s",)),
                       Template("You were looking away {away_pct}% of the last minute.", ("gaze_off_ratio_60s",)),
                       Template("You keep looking away from your work.")),
        },
        for_phrase="you were looking away from your work",
        against_phrase="you kept looking at your work",
        example={"gaze_off_ratio_60s": 0.45, "away_current_s": 12, "away_episodes_60s": 4},
    ),
)

BY_ID: Dict[str, EvidenceType] = {t.id: t for t in CATALOG}
TYPE_OF_FEATURE: Dict[str, str] = {f: t.id for t in CATALOG for f in t.features}
TYPE_OF_INPUT: Dict[str, str] = {i: t.id for t in CATALOG for i in t.inputs}
STATE_NAME = {"focused": "focused", "distracted": "distracted", "drowsy": "drowsy",
              "very_drowsy": "very drowsy", "sleepy": "drowsy"}


def type_of_feature(feature_id: str) -> str:
    """The evidence type of an HMM feature; a feature outside the catalog is its own type."""
    return TYPE_OF_FEATURE.get(feature_id, feature_id)


def type_of_input(name: Optional[str]) -> Optional[str]:
    """The evidence type of a feature input (a rule's feature), or None."""
    return TYPE_OF_INPUT.get(name) if name else None


# --- filling in -------------------------------------------------------------------------


def _num(values: Mapping[str, Any], key: str) -> Optional[float]:
    v = values.get(key)
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def placeholder_values(type_id: str, values: Mapping[str, Any], window_s: float = 60.0) -> Dict[str, str]:
    """The placeholders of one type, filled from feature values (inputs -> numbers). A
    placeholder without a meaningful value is left out, so a sentence needing it is skipped."""
    out: Dict[str, str] = {"minutes": str(max(1, int(round(window_s / 60.0))))}
    g = lambda k: _num(values, k)          # noqa: E731
    if type_id == "eye_closure":
        p = g("perclos_60s")
        if p is None and g("AU43_active_60s") is not None:
            p = g("AU43_active_60s")
        if p is not None and p >= 0.01:
            out["closed_pct"] = str(int(round(p * 100)))
        r = g("blink_mean_ms_rel_60s")
        if r is not None and r >= 1.2:
            out["times_normal"] = f"{r:.1f}".rstrip("0").rstrip(".")
        n = g("long_blinks_60s")
        if n is not None and n >= 1:
            out["long_blinks"] = str(int(n))
            out["long_blinks_text"] = "one long blink" if int(n) == 1 else f"{int(n)} long blinks"
        m = g("blink_max_ms_60s")
        if m is not None and m >= 1000:
            out["longest_s"] = f"{m / 1000:.1f}"
        c = g("eyes_closed_now_s")
        if c is not None and c >= 1.0:
            out["closed_now_s"] = f"{c:.1f}"
    elif type_id == "yawning":
        n = g("yawns_60s")
        if n is not None and n >= 1:
            out["yawns"], out["yawns_times"] = str(int(n)), _times(int(n))
        j = g("AU26_active_60s")
        if j is not None and j >= 0.01:
            out["jaw_pct"] = str(int(round(j * 100)))
    elif type_id == "nodding":
        n = g("nods_60s")
        if n is not None and n >= 1:
            out["nods"], out["nods_times"] = str(int(n)), _times(int(n))
    elif type_id == "looking_away":
        a = g("gaze_off_ratio_60s")
        if a is not None:
            if a >= 0.01:
                out["away_pct"] = str(int(round(a * 100)))
            out["on_work_pct"] = str(int(round((1 - a) * 100)))
        s = g("away_current_s")
        if s is not None and s >= 10:      # a short current look says less than the minute's share
            out["away_seconds"] = str(int(round(s)))
            m = int(round(s / 60))
            out["away_for"] = (f"{int(round(s))} seconds" if s < 60 else "about a minute" if m <= 1
                               else f"about {m} minutes")
        n = g("away_episodes_60s")
        if n is not None and n >= 2:
            out["away_count"], out["away_times"] = str(int(n)), _times(int(n))
    return out


def fill(text: str, values: Mapping[str, str]) -> Optional[str]:
    """The text with its placeholders filled, or None when one has no value."""
    names = PLACEHOLDER.findall(text)
    if any(n not in values for n in names):
        return None
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)


def default_sentence(type_id: str, tone: str, values: Mapping[str, str], lead: Optional[str] = None) -> Optional[str]:
    """The default sentence: the first one that can be filled, preferring sentences that suit
    the lead feature input (the one that moved the belief most)."""
    t = BY_ID.get(type_id)
    if t is None:
        return None
    pool = t.templates.get(tone) or t.templates["hedged"]
    ordered = sorted(pool, key=lambda tp: 0 if lead and lead in tp.lead else 1)
    for template in ordered:
        text = fill(template.text, values)
        if text is not None:
            return text
    return None


# --- the doc --------------------------------------------------------------------------


def render_markdown() -> str:
    lines = [
        "# The evidence catalog (A20)",
        "",
        "<!-- Generated by `python -m eepy.explanations.catalog --write` from eepy/explanations/catalog.py."
        " Do not edit by hand: change the catalog and regenerate. -->",
        "",
        "Every reason EEPY can give for a nudge is one of these **evidence types**. Each type groups the",
        "features that measure the same thing, so an explanation never says the same thing twice.",
        "How the reasons are chosen: [A20_EXPLANATIONS.md](A20_EXPLANATIONS.md).",
        "",
        "## Writing explanation lines (task C6)",
        "",
        "EEPY says the explanation as one extra sentence right after the nudge's own line, in the same",
        "tone (hedged when unsure, direct when sure), about the **top** evidence type of that decision.",
        "To write your own sentences for a type, add a line to `behaviors/lines.yaml` called",
        "`explain_<type>` (for example `explain_eye_closure`), with `hedged` and `direct` variants like",
        "any other line. A wording is only used when all its placeholders have a value, so mix wordings",
        "with and without numbers. Until a type has its own line, EEPY uses the default sentences below.",
        "Only the placeholders of that type are allowed in its line (the validator checks).",
        "",
        "```yaml",
        "  explain_eye_closure:",
        "    description: Said after a nudge when closing eyes were the main reason.",
        "    variants:",
        "      hedged:",
        '        - "Your eyes seem to be closing a lot."',
        '        - "It looks like your eyes were closed {closed_pct}% of the last minute."',
        "      direct:",
        '        - "Your eyes were closed {closed_pct}% of the last minute."',
        "```",
        "",
        "Values come from the last minute of measurements (the windows the model uses), and",
        "`{minutes}` is how long the evidence built up for this decision. Only blink length is",
        "measured against the person's own normal (`{times_normal}`); the other values are absolute.",
        "",
        "| Type | Usually points to | Placeholders |",
        "|---|---|---|",
    ]
    for t in CATALOG:
        lines.append(f"| [`{t.id}`](#{t.id.replace('_', '_')}) | {t.supports} | "
                     + ", ".join(f"`{{{p}}}`" for p in t.placeholder_names) + " |")
    for t in CATALOG:
        ex = placeholder_values(t.id, t.example, 180)
        lines += ["", f"## {t.id}", "", f"**{t.title}.** {t.meaning}", "",
                  f"- **Usually points to:** {t.supports}",
                  f"- **HMM features:** " + ", ".join(f"`{f}`" for f in t.features),
                  f"- **Feature inputs:** " + ", ".join(f"`{i}`" for i in t.inputs),
                  f"- **In a contrast:** \"{t.for_phrase}\" (for the decided state) / "
                  f"\"{t.against_phrase}\" (against the runner-up)",
                  f"- **Your line:** `{t.line_id}` in behaviors/lines.yaml", "",
                  "| Placeholder | Unit | Meaning | Example |", "|---|---|---|---|"]
        for p in t.placeholders:
            lines.append(f"| `{{{p.name}}}` | {p.unit} | {p.meaning} | {p.example} |")
        lines += ["", "Default sentences (the first one that can be filled is used; sentences that suit the "
                  "feature that moved the belief most come first):", ""]
        for tone in TONES:
            for tp in t.templates[tone]:
                filled = fill(tp.text, ex)
                lines.append(f"- *{tone}:* \"{tp.text}\"" + (f" → \"{filled}\"" if filled and filled != tp.text else ""))
    lines += ["", "## Not in the catalog", "",
              "- **Phone use.** EEPY has no phone detector; looking at a phone shows up as `looking_away`.",
              "- **Time and model limits** (`dynamics` in the logged Explanation): how much of the belief's "
              "change came from the model's own drift over time and its probability floor rather than from "
              "evidence. Logged for the faithfulness check, never spoken.", ""]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.explanations.catalog",
                                     description="The evidence catalog as Markdown (docs/EXPLANATION_CATALOG.md).")
    parser.add_argument("--write", action="store_true", help=f"write {DOC.relative_to(REPO_ROOT)}")
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is out of date")
    args = parser.parse_args(argv)
    text = render_markdown()
    if args.check:
        current = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
        if current != text:
            print(f"  {DOC.relative_to(REPO_ROOT)} is out of date: run python -m eepy.explanations.catalog --write")
            return 1
        print("  up to date")
        return 0
    if args.write:
        DOC.write_text(text, encoding="utf-8")
        print(f"  Written: {DOC.relative_to(REPO_ROOT)}")
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
