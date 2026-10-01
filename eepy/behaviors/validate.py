"""Check every behaviour file, and explain any problem in plain language.

    python -m eepy.behaviors.validate            # check everything
    python -m eepy.behaviors.validate --strict   # treat warnings as failures
    python -m eepy.behaviors.validate --no-reach # skip the reachability check

Exit code 0 means the files are good. Anything else means at least one error,
and nothing should be merged until it is fixed.

What gets checked:
  * YAML is readable, with no duplicate keys
  * every field has the right type, and no unknown fields are present
  * every angle, height and time is inside the robot's real limits
  * every pose is one the robot can physically reach (see reach.py -- this is
    the check that per-axis ranges cannot do)
  * every reference points at something that exists: blocks, gaze targets,
    line IDs, sound files
  * no two behaviours share a name
  * each ladder runs 0, 1, 2, ... with no gaps and no repeats
  * movements are not absurdly fast (a warning, not an error)
"""

from __future__ import annotations

# First, before any third-party import, so a missing package gets a readable
# message instead of a traceback.
from ._deps import require as _require

_require()

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from pydantic import ValidationError

from . import limits, reach
from .loader import (
    BEHAVIORS_DIR,
    SOUNDS_DIR,
    BehaviorFileError,
    Library,
    Step,
    behavior_files,
    read_yaml,
)
from .schema import (
    Behavior,
    Block,
    HeadPose,
    Keyframe,
    Line,
)

Severity = Literal["error", "warning"]

# Unicode where the terminal supports it, ASCII where it does not.
_TICK, _CROSS, _WARN, _ARROW = "✓", "✗", "!", "→"


@dataclass
class Problem:
    """One thing wrong, described so a non-programmer can act on it."""

    severity: Severity
    path: Path
    where: str
    message: str
    hint: str | None = None
    line: int | None = None

    def render(self, root: Path) -> str:
        try:
            shown = self.path.relative_to(root)
        except ValueError:
            shown = self.path
        mark = _CROSS if self.severity == "error" else _WARN
        head = f"  {mark} {shown}"
        if self.line is not None:
            head += f", line {self.line}"
        out = [head]
        if self.where:
            out.append(f"      in {self.where}")
        out.append(f"      {self.message}")
        if self.hint:
            out.append(f"      {_ARROW} {self.hint}")
        return "\n".join(out)


# --- Translating pydantic's errors into English ---------------------------


def _suggest(word: str, options: Iterable[str]) -> str | None:
    """'Did you mean X?' for a misspelled field or reference."""
    matches = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return matches[0] if matches else None


_UNION_TAGS = ("int", "float", "str", "bool", "list", "dict", "none")


def _strip_union_tag(loc: tuple) -> tuple:
    """Drop pydantic's trailing union-branch marker from an error location.

    A field typed `Literal["once","forever"] | StrictInt` reports one error per
    branch, each with a tag like "literal['once','forever']" or "int" tacked
    on the end. Those are pydantic internals; a designer needs the field name.
    """
    if loc and isinstance(loc[-1], str):
        tail = loc[-1]
        if (
            tail in _UNION_TAGS
            or tail.startswith(("literal[", "function-", "constrained-", "lax-", "json-"))
        ):
            return loc[:-1]
    return loc


def _describe_location(loc: tuple, raw: dict) -> str:
    """Turn pydantic's ('behaviors', 0, 'keyframes', 2, 'head') into English.

    Uses the raw YAML to recover the behaviour's name, because "behaviour
    'refocus_curious', keyframe 3" is something you can search the file for,
    and "behaviors[0].keyframes[2]" is not.
    """
    parts: list[str] = []
    cursor: object = raw
    i = 0
    while i < len(loc):
        key = loc[i]
        if key == "behaviors" and isinstance(cursor, dict):
            cursor = cursor.get("behaviors")
            i += 1
            if i < len(loc) and isinstance(loc[i], int) and isinstance(cursor, list):
                index = loc[i]
                item = cursor[index] if index < len(cursor) else None
                name = item.get("name") if isinstance(item, dict) else None
                parts.append(
                    f'behaviour "{name}"' if name else f"behaviour #{index + 1}"
                )
                cursor = item
                i += 1
            continue
        if key == "keyframes":
            i += 1
            if i < len(loc) and isinstance(loc[i], int):
                parts.append(f"keyframe {loc[i] + 1}")
                if isinstance(cursor, dict):
                    frames = cursor.get("keyframes")
                    if isinstance(frames, list) and loc[i] < len(frames):
                        cursor = frames[loc[i]]
                i += 1
            continue
        if key in ("blocks", "lines", "gaze_targets"):
            label = {"blocks": "block", "lines": "line", "gaze_targets": "gaze target"}[key]
            i += 1
            if i < len(loc):
                parts.append(f'{label} "{loc[i]}"')
                if isinstance(cursor, dict):
                    group = cursor.get(key)
                    if isinstance(group, dict):
                        cursor = group.get(loc[i])
                i += 1
            continue
        # Anything else is a plain field name. Union-branch markers are
        # stripped before we get here (_strip_union_tag), so a name is a name --
        # an earlier prefix test on "int" silently swallowed "intensity".
        if isinstance(key, str):
            parts.append(f"'{key}'")
        i += 1
    return ", ".join(parts)


def _friendly(error: dict, raw: dict) -> tuple[str, str | None]:
    """(message, hint) for one pydantic error, written for a designer."""
    kind = error["type"]
    field = error["loc"][-1] if error["loc"] else ""
    given = error.get("input")

    if kind == "extra_forbidden":
        # Find the sibling fields that WOULD be valid, to suggest a fix.
        parent = error["loc"][:-1]
        known = _known_fields_at(parent, raw)
        hint = None
        if isinstance(field, str):
            match = _suggest(field, known) if known else None
            hint = (
                f'Did you mean "{match}"?'
                if match
                else (f"Fields allowed here: {', '.join(sorted(known))}" if known else None)
            )
        return (f'"{field}" is not something I recognise here.', hint)

    if kind == "missing":
        return (f"this is missing its \"{field}\".", "Add it, then validate again.")

    if kind == "string_pattern_mismatch":
        return (
            f'"{given}" is not a valid name.',
            "Names use lower-case letters, numbers and underscores only, and "
            "start with a letter -- like refocus_curious.",
        )

    if kind in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
        ctx = error.get("ctx") or {}
        bound = ctx.get("gt", ctx.get("ge", ctx.get("lt", ctx.get("le"))))
        too = "small" if kind.startswith("greater") else "big"
        word = {
            "greater_than": "more than",
            "greater_than_equal": "at least",
            "less_than": "less than",
            "less_than_equal": "at most",
        }[kind]
        hint = f"It must be {word} {bound}." if bound is not None else None
        return (f'"{field}" is {given}, which is too {too}.', hint)

    if kind in ("float_parsing", "int_parsing", "float_type", "int_type"):
        if isinstance(given, bool):
            extra = (
                " For 'loop:', write once, forever, or a number."
                if field == "loop"
                else ""
            )
            return (
                f'"{field}" reads as {given}, not a value I can use.',
                "YAML turns yes, no, on and off into true/false. Put quotes "
                'around text ("no") or write a number.' + extra,
            )
        return (f'"{field}" should be a number, but it is {given!r}.', None)

    if kind in ("string_type",):
        if isinstance(given, bool):
            return (
                f'"{field}" should be text, but YAML read it as {given}.',
                'Put it in quotes -- yes/no/on/off become true/false otherwise.',
            )
        return (f'"{field}" should be text, but it is {given!r}.', None)

    if kind == "literal_error":
        allowed = error["msg"].split("be ")[-1]
        hint = None
        if isinstance(field, str) and isinstance(given, str):
            options = [o.strip().strip("'\"") for o in allowed.replace(" or ", ", ").split(",")]
            match = _suggest(given, options)
            hint = f'Did you mean "{match}"?' if match else f"Allowed: {allowed}."
        return (f'"{field}" is {given!r}, which is not one of the allowed values.', hint)

    if kind in ("too_short", "list_type"):
        return (f'"{field}" needs at least one entry.', None)

    if kind == "string_too_short":
        return (f'"{field}" is empty.', "Write something here, even a few words.")

    if kind == "value_error":
        # Our own model_validator messages, already written for designers.
        return (str(error["msg"]).replace("Value error, ", ""), None)

    return (f"{error['msg']} (field: {field})", None)


def _known_fields_at(loc: tuple, raw: dict) -> set[str]:
    """Which field names are legal at this location in the file."""
    from .schema import Antennas, HeadPose, Keyframe, Line

    tail = [part for part in loc if isinstance(part, str)]
    for model, marker in (
        (HeadPose, "head"),
        (Antennas, "antennas"),
        (Line, "lines"),
    ):
        if tail and tail[-1] == marker:
            return set(model.model_fields)
    if "keyframes" in loc:
        return set(Keyframe.model_fields)
    if "behaviors" in loc:
        return set(Behavior.model_fields)
    if "blocks" in loc:
        return set(Block.model_fields)
    return set()


# --- The checks ------------------------------------------------------------


class Validator:
    """Runs every check and collects Problems. Never raises on bad input."""

    def __init__(
        self,
        directory: Path = BEHAVIORS_DIR,
        sounds: Path = SOUNDS_DIR,
        check_reach: bool = True,
    ) -> None:
        self.directory = directory
        self.sounds = sounds
        self.check_reach = check_reach
        self.problems: list[Problem] = []
        self.library = Library()
        self.raw: dict[Path, dict] = {}
        # name -> list of files that define it, for duplicate detection
        self.seen_names: dict[str, list[Path]] = {}

    # -- helpers ----------------------------------------------------------

    def error(self, path: Path, where: str, message: str, hint: str | None = None,
              line: int | None = None) -> None:
        self.problems.append(Problem("error", path, where, message, hint, line))

    def warn(self, path: Path, where: str, message: str, hint: str | None = None) -> None:
        self.problems.append(Problem("warning", path, where, message, hint))

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "warning"]

    # -- stage 1: parse and type-check ------------------------------------
    #
    # Each behaviour, block and line is validated on its own, not as one big
    # document. If they were validated together, a single typo would make
    # pydantic reject the whole file and hide every other problem in it --
    # so a designer would fix one thing, re-run, find the next, and repeat.
    # Validating item by item means one run reports everything.

    @staticmethod
    def _yaml_hint(message: str) -> str | None:
        """A fix aimed at the specific YAML complaint, not a generic one."""
        lowered = message.lower()
        if "\\t" in message or "\t" in message or re.search(r"\btabs?\b", lowered):
            return (
                "There is a tab character here. YAML only accepts spaces -- "
                "replace the tab with two spaces."
            )
        if "mapping values are not allowed" in lowered:
            return (
                'Some text contains a colon, which YAML reads as a new field. '
                'Put the whole text in quotes: "Back to it: really?"'
            )
        if "appears twice" in lowered:
            return None  # the message already says exactly what to do
        if "could not find expected" in lowered or "expected" in lowered:
            return (
                "Something is indented inconsistently. Every keyframe under "
                "'keyframes:' starts with a dash at the same depth."
            )
        if "is empty" in lowered:
            return "Delete the file, or add a 'behaviors:' list to it."
        return (
            "Check the indentation, and that every line of text is in quotes."
        )

    def _read(self, path: Path) -> dict | None:
        try:
            raw = read_yaml(path)
        except BehaviorFileError as exc:
            self.error(path, "", exc.message,
                       self._yaml_hint(exc.message), exc.line)
            return None
        self.raw[path] = raw
        return raw

    def _report_validation(self, path: Path, exc: ValidationError,
                           raw: dict, prefix: tuple = ()) -> None:
        # A union-typed field reports one error per branch. Collapse them to a
        # single message per field, keeping the most explanatory branch.
        chosen: dict[tuple, dict] = {}
        for err in exc.errors():
            err = dict(err)
            err["loc"] = _strip_union_tag(prefix + tuple(err["loc"]))
            key = err["loc"]
            if key not in chosen:
                chosen[key] = err
                continue
            # Prefer the branch that can explain a YAML bool, since that is the
            # trap designers actually fall into.
            if isinstance(err.get("input"), bool) and err["type"].endswith(
                ("_parsing", "_type")
            ):
                chosen[key] = err
        for err in chosen.values():
            message, hint = _friendly(err, raw)
            self.error(path, _describe_location(err["loc"], raw), message, hint)

    def _load_behavior_file(self, path: Path) -> None:
        raw = self._read(path)
        if raw is None:
            return
        items = raw.get("behaviors")
        if items is None:
            self.error(path, "", "this file has no 'behaviors:' list.",
                       "A behaviour file starts with 'behaviors:' and then a "
                       "dash for each behaviour.")
            return
        if not isinstance(items, list):
            self.error(path, "'behaviors'", "'behaviors:' should be a list.",
                       "Put a dash in front of each behaviour's 'name:'.")
            return
        if not items:
            self.error(path, "'behaviors'", "this file's 'behaviors:' list is empty.")
            return
        # The loader's BehaviorFile model forbids extra keys, so a stray
        # top-level key makes load_library() raise even though every behaviour
        # in the file is fine. If the validator let that through, CI would be
        # green on a file the player cannot read.
        for key in sorted(set(raw) - {"behaviors"}):
            match = _suggest(key, ("behaviors",))
            self.error(
                path, f"'{key}'",
                f'"{key}" does not belong at the top of a behaviour file.',
                f'Did you mean "{match}"?' if match else
                "A behaviour file holds one top-level key: 'behaviors:'. "
                "Anything describing a single behaviour goes indented under it.",
            )

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                self.error(path, f"behaviour #{index + 1}",
                           "this entry is not a behaviour.",
                           "It should start with 'name:' on the same line as "
                           "the dash, or indented under it.")
                continue
            try:
                behavior = Behavior.model_validate(item)
            except ValidationError as exc:
                self._report_validation(path, exc, raw, prefix=("behaviors", index))
                continue
            self.seen_names.setdefault(behavior.name, []).append(path)
            if behavior.name not in self.library.behaviors:
                self.library.behaviors[behavior.name] = behavior
                self.library.sources[behavior.name] = path

    def _load_blocks_file(self, path: Path) -> None:
        raw = self._read(path)
        if raw is None:
            return
        for key, model, store in (
            ("gaze_targets", HeadPose, self.library.gaze_targets),
            ("blocks", Block, self.library.blocks),
        ):
            group = raw.get(key, {})
            if group is None:
                continue
            if not isinstance(group, dict):
                self.error(path, f"'{key}'",
                           f"'{key}:' should be a list of named entries.")
                continue
            for name, item in group.items():
                try:
                    store[name] = model.model_validate(item)
                except ValidationError as exc:
                    self._report_validation(path, exc, raw, prefix=(key, name))
        unknown = set(raw) - {"gaze_targets", "blocks"}
        for key in sorted(unknown):
            match = _suggest(key, ("gaze_targets", "blocks"))
            self.error(path, f"'{key}'", f'"{key}" does not belong in _blocks.yaml.',
                       f'Did you mean "{match}"?' if match else
                       "This file holds only 'gaze_targets:' and 'blocks:'.")

    def _load_lines_file(self, path: Path) -> None:
        raw = self._read(path)
        if raw is None:
            return
        group = raw.get("lines")
        if group is None:
            self.error(path, "", "lines.yaml has no 'lines:' section.")
            return
        if not isinstance(group, dict):
            self.error(path, "'lines'", "'lines:' should be a list of named entries.")
            return
        for name, item in group.items():
            try:
                self.library.lines[name] = Line.model_validate(item)
            except ValidationError as exc:
                self._report_validation(path, exc, raw, prefix=("lines", name))

    def load(self) -> None:
        if not self.directory.is_dir():
            self.error(self.directory, "", "there is no behaviors/ folder here.")
            return

        blocks_path = self.directory / "_blocks.yaml"
        if blocks_path.is_file():
            self._load_blocks_file(blocks_path)
        else:
            self.error(blocks_path, "", "_blocks.yaml is missing.",
                       "It holds the shared building blocks and gaze targets.")

        lines_path = self.directory / "lines.yaml"
        if lines_path.is_file():
            self._load_lines_file(lines_path)
        else:
            self.error(lines_path, "", "lines.yaml is missing.",
                       "It holds everything the robot can say.")

        files = behavior_files(self.directory)
        if not files:
            self.error(self.directory, "", "there are no behaviour files.",
                       "Add something like behaviors/refocus.yaml.")
        for path in files:
            self._load_behavior_file(path)

    # -- stage 2: duplicate names -----------------------------------------

    def check_duplicate_names(self) -> None:
        for name, paths in sorted(self.seen_names.items()):
            if len(paths) > 1:
                files = sorted({p.name for p in paths})
                where = (
                    f"{len(paths)} times in {files[0]}"
                    if len(files) == 1
                    else "in " + " and ".join(files)
                )
                self.error(
                    paths[-1],
                    f'behaviour "{name}"',
                    f'the name "{name}" is used {where}.',
                    "Every behaviour needs its own name -- the decision logic "
                    "looks them up by name.",
                )

    # -- stage 3: references ----------------------------------------------

    def _check_keyframe_refs(self, path: Path, where: str, keyframes) -> None:
        for index, keyframe in enumerate(keyframes):
            spot = f"{where}, keyframe {index + 1}"
            if keyframe.use is not None and keyframe.use not in self.library.blocks:
                match = _suggest(keyframe.use, self.library.blocks)
                self.error(
                    path, spot,
                    f'there is no building block called "{keyframe.use}".',
                    f'Did you mean "{match}"?' if match else
                    f"Blocks available: {', '.join(sorted(self.library.blocks)) or 'none'}.",
                )
            if keyframe.look_at is not None and keyframe.look_at not in self.library.gaze_targets:
                match = _suggest(keyframe.look_at, self.library.gaze_targets)
                self.error(
                    path, spot,
                    f'there is no gaze target called "{keyframe.look_at}".',
                    f'Did you mean "{match}"?' if match else
                    f"Targets in _blocks.yaml: {', '.join(sorted(self.library.gaze_targets)) or 'none'}.",
                )
            if keyframe.say is not None and keyframe.say not in self.library.lines:
                match = _suggest(keyframe.say, self.library.lines)
                self.error(
                    path, spot,
                    f'there is no line called "{keyframe.say}" in lines.yaml.',
                    f'Did you mean "{match}"?' if match else
                    "Add it to behaviors/lines.yaml first.",
                )
            if keyframe.sound is not None:
                self._check_sound(path, spot, keyframe.sound)

    def _check_sound(self, path: Path, where: str, sound: str) -> None:
        allowed = (".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac")
        if not sound.lower().endswith(allowed):
            self.error(
                path, where,
                f'"{sound}" is not a sound format the robot can play.',
                f"Use one of: {', '.join(allowed)}.",
            )
            return
        if "/" in sound or "\\" in sound:
            self.error(path, where, f'"{sound}" should be a plain filename.',
                       "Put the file directly in sounds/ and name it here.")
            return
        if not (self.sounds / sound).is_file():
            existing = [p.name for p in self.sounds.glob("*") if p.is_file()]
            match = _suggest(sound, existing)
            self.error(
                path, where,
                f'there is no sounds/{sound}.',
                f'Did you mean "{match}"?' if match else
                f"Add the file to sounds/. Present: {', '.join(sorted(existing)) or 'nothing'}.",
            )

    def check_references(self) -> None:
        for name, behavior in sorted(self.library.behaviors.items()):
            path = self.library.sources[name]
            self._check_keyframe_refs(path, f'behaviour "{name}"', behavior.keyframes)
        blocks_path = self.directory / "_blocks.yaml"
        for name, block in sorted(self.library.blocks.items()):
            self._check_keyframe_refs(blocks_path, f'block "{name}"', block.keyframes)
            for index, keyframe in enumerate(block.keyframes):
                if keyframe.use is not None:
                    self.error(
                        blocks_path, f'block "{name}", keyframe {index + 1}',
                        "a building block cannot use another building block.",
                        "Keep blocks flat -- write the keyframes out here instead.",
                    )

    def check_lines(self) -> None:
        path = self.directory / "lines.yaml"
        for name, line in sorted(self.library.lines.items()):
            for tone in ("hedged", "direct"):
                for index, text in enumerate(getattr(line.variants, tone)):
                    spot = f'line "{name}", {tone} variant {index + 1}'
                    if not text.strip():
                        self.error(path, spot, "this variant is empty.")
                    for placeholder in re.findall(r"\{([^}]*)\}", text):
                        if placeholder not in limits.ALLOWED_PLACEHOLDERS:
                            match = _suggest(placeholder, limits.ALLOWED_PLACEHOLDERS)
                            self.error(
                                path, spot,
                                f'"{{{placeholder}}}" is not a placeholder I know.',
                                f'Did you mean "{{{match}}}"?' if match else
                                f"Allowed: {', '.join('{' + p + '}' for p in limits.ALLOWED_PLACEHOLDERS)}.",
                            )

    # -- stage 4: ladders --------------------------------------------------

    def check_ladders(self) -> None:
        rungs: dict[str, dict[int, list[str]]] = {}
        for name, behavior in self.library.behaviors.items():
            if behavior.ladder is None or behavior.level is None:
                continue
            rungs.setdefault(behavior.ladder, {}).setdefault(behavior.level, []).append(name)

        for ladder, levels in sorted(rungs.items()):
            path = self.library.sources[next(iter(levels.values()))[0]]
            for level, names in sorted(levels.items()):
                if len(names) > 1:
                    self.error(
                        path, f'ladder "{ladder}", level {level}',
                        f"level {level} is claimed by {len(names)} behaviours "
                        f"({', '.join(sorted(names))}).",
                        "The decision logic picks one behaviour per rung, so "
                        "each level must be used exactly once.",
                    )
            present = sorted(levels)
            if present and present[0] != 0:
                self.error(
                    path, f'ladder "{ladder}"',
                    f"this ladder starts at level {present[0]}, not 0.",
                    "Ladders start at 0, the gentlest rung.",
                )
            missing = [n for n in range(present[0], present[-1] + 1) if n not in levels] if present else []
            if missing:
                self.error(
                    path, f'ladder "{ladder}"',
                    f"level(s) {', '.join(map(str, missing))} are missing "
                    f"(it jumps from {present[0]} to {present[-1]}).",
                    "The decision logic escalates one rung at a time, so it "
                    "would have nothing to play at that level.",
                )

    # -- stage 5: the resolved timeline ------------------------------------

    def check_motion(self) -> None:
        """Range, reachability and speed checks on fully resolved timelines.

        Blocks are resolved too, as little stand-alone behaviours, so an
        unused block is still checked.
        """
        if self.check_reach and not reach.available():
            self.warn(
                self.directory, "",
                "the reachability check was skipped: reachy_mini_rust_kinematics "
                "is not installed.",
                "Ranges were still checked, but per-axis ranges alone let "
                "through poses the robot cannot physically reach. "
                "Install it with: pip install reachy_mini_rust_kinematics",
            )

        for name, behavior in sorted(self.library.behaviors.items()):
            path = self.library.sources[name]
            self._check_steps(path, f'behaviour "{name}"', behavior)

        blocks_path = self.directory / "_blocks.yaml"
        for name, block in sorted(self.library.blocks.items()):
            # A stand-in behaviour that simply USES the block, rather than one
            # holding a copy of its keyframes. That way the block is resolved by
            # exactly the code the player will run -- an earlier version
            # re-implemented the relative-offset rule here and rejected blocks
            # that were perfectly legal in use. It also no longer mutates the
            # loaded models in place.
            stand_in = Behavior(
                name=f"block_{name}",
                description=block.description,
                keyframes=[Keyframe(use=name)],
            )
            self._check_steps(blocks_path, f'block "{name}"', stand_in)

    def _check_steps(self, path: Path, where: str, behavior: Behavior) -> None:
        try:
            steps = self.library.resolve(behavior)
        except KeyError:
            # A dangling reference; already reported by check_references.
            return

        previous: Step | None = None
        for step in steps:
            spot = f"{where}, keyframe {step.source.split('[')[-1].rstrip(']')}"
            in_range = self._check_ranges(path, spot, step)
            # Only ask about reachability once the values are individually
            # legal. Otherwise an out-of-range pitch draws a second error whose
            # explanation ("each value is inside its own limit, but not all
            # together") flatly contradicts the first one.
            if in_range and step.action is None:
                self._check_reachable(path, spot, step)
            if previous is not None:
                self._check_speed(path, spot, previous, step)
            previous = step

    def _check_ranges(self, path: Path, where: str, step: Step) -> bool:
        """Check every hard limit. Returns True if the step is within them."""
        before = len(self.problems)
        checks = (
            ("head pitch", step.head_pitch, limits.HEAD_PITCH_LIMIT_DEG, "degrees"),
            ("head roll", step.head_roll, limits.HEAD_ROLL_LIMIT_DEG, "degrees"),
            ("head yaw", step.head_yaw, limits.HEAD_YAW_LIMIT_DEG, "degrees"),
            ("body yaw", step.body_yaw, limits.BODY_YAW_LIMIT_DEG, "degrees"),
            ("right antenna", step.antenna_right, limits.ANTENNA_LIMIT_DEG, "degrees"),
            ("left antenna", step.antenna_left, limits.ANTENNA_LIMIT_DEG, "degrees"),
        )
        for label, value, limit, unit in checks:
            if abs(value) > limit + 1e-9:
                self.error(
                    path, where,
                    f"{label} works out to {value:.1f} {unit}, past the "
                    f"limit of {limit:.0f}.",
                    f"Keep it between -{limit:.0f} and {limit:.0f}.",
                )
        if not (limits.HEAD_Z_MIN_MM - 1e-9 <= step.head_z <= limits.HEAD_Z_MAX_MM + 1e-9):
            self.error(
                path, where,
                f"head height works out to {step.head_z:.1f} mm, outside "
                f"{limits.HEAD_Z_MIN_MM:.0f} to {limits.HEAD_Z_MAX_MM:.0f} mm.",
                "The head sinks further than it rises -- negative values have "
                "much more room than positive ones.",
            )
        delta = abs(step.head_yaw - step.body_yaw)
        if delta > limits.HEAD_BODY_YAW_DELTA_LIMIT_DEG + 1e-9:
            self.error(
                path, where,
                f"the head is turned {delta:.1f} degrees away from the body, "
                f"more than the {limits.HEAD_BODY_YAW_DELTA_LIMIT_DEG:.0f} "
                "degrees the neck allows.",
                "Turn the body too: set body_yaw closer to the head's yaw.",
            )
        return len(self.problems) == before

    def _check_reachable(self, path: Path, where: str, step: Step) -> None:
        if not self.check_reach or not reach.available():
            return
        if reach.is_reachable(
            yaw=step.head_yaw, pitch=step.head_pitch, roll=step.head_roll,
            z_mm=step.head_z, body_yaw=step.body_yaw,
        ):
            return
        self.error(
            path, where,
            "the robot cannot physically reach this pose "
            f"(yaw {step.head_yaw:.0f}, pitch {step.head_pitch:.0f}, "
            f"roll {step.head_roll:.0f}, height {step.head_z:.0f} mm).",
            "Each value is inside its own limit, but not all together -- the "
            "head is on six short legs, and tilting it uses up the same travel "
            "as raising it. Try a smaller tilt, or bring the height closer "
            "to 0.",
        )

    def _check_speed(self, path: Path, where: str, previous: Step, step: Step) -> None:
        if step.duration <= 0:
            return
        head_move = max(
            abs(step.head_yaw - previous.head_yaw),
            abs(step.head_pitch - previous.head_pitch),
            abs(step.head_roll - previous.head_roll),
        )
        antenna_move = max(
            abs(step.antenna_right - previous.antenna_right),
            abs(step.antenna_left - previous.antenna_left),
        )
        body_move = abs(step.body_yaw - previous.body_yaw)
        for label, move, cap in (
            ("the head", head_move, limits.ADVISORY_HEAD_SPEED_DEG_PER_S),
            ("an antenna", antenna_move, limits.ADVISORY_ANTENNA_SPEED_DEG_PER_S),
            ("the body", body_move, limits.ADVISORY_BODY_SPEED_DEG_PER_S),
        ):
            speed = move / step.duration
            if speed > cap:
                self.warn(
                    path, where,
                    f"{label} moves {move:.0f} degrees in {step.duration:.2f} s "
                    f"({speed:.0f} deg/s), which will look like a twitch.",
                    f"Above about {cap:.0f} deg/s the movement stops reading as "
                    f"a gesture. Try a longer duration.",
                )

    # -- run everything ----------------------------------------------------

    def run(self) -> None:
        self.load()
        self.check_duplicate_names()
        if not self.library.behaviors and not self.library.blocks:
            return
        self.check_references()
        self.check_lines()
        self.check_ladders()
        # Motion checks resolve each timeline. A behaviour with a dangling
        # reference cannot be resolved, so it is skipped individually (its
        # reference error is already reported) rather than skipping them all.
        self.check_motion()


def report(validator: Validator, root: Path, strict: bool) -> int:
    """Print the result. Returns the process exit code."""
    problems = validator.problems
    if not problems:
        counts = (
            f"{len(validator.library.behaviors)} behaviours, "
            f"{len(validator.library.blocks)} blocks, "
            f"{len(validator.library.lines)} lines"
        )
        print(f"{_TICK} All behaviour files are valid ({counts}).")
        for ladder, levels in validator.library.ladders().items():
            print(f"    ladder {ladder}: levels {levels[0]}-{levels[-1]} complete")
        return 0

    by_file: dict[Path, list[Problem]] = {}
    for problem in problems:
        by_file.setdefault(problem.path, []).append(problem)

    print()
    for path in sorted(by_file, key=str):
        for problem in by_file[path]:
            print(problem.render(root))
            print()

    errors, warnings = validator.errors, validator.warnings
    parts = []
    if errors:
        parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
    if warnings:
        parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    mark = _CROSS if errors else _WARN
    print(f"{mark} " + " and ".join(parts) + ".")
    if errors:
        print("  Nothing will be merged until the errors are fixed.")
        return 1
    if strict:
        print("  Running with --strict, so warnings count as failures.")
        return 1
    print("  No errors, so this is good to merge. The warnings are advice.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eepy.behaviors.validate",
        description="Check EEPY's behaviour files and explain any problems.",
    )
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures too")
    parser.add_argument("--no-reach", action="store_true",
                        help="skip the physical reachability check")
    parser.add_argument("--behaviors-dir", type=Path, default=BEHAVIORS_DIR,
                        help="where the YAML files live")
    parser.add_argument("--sounds-dir", type=Path, default=SOUNDS_DIR,
                        help="where the sound files live")
    args = parser.parse_args(argv)

    validator = Validator(
        directory=args.behaviors_dir,
        sounds=args.sounds_dir,
        check_reach=not args.no_reach,
    )
    validator.run()
    root = args.behaviors_dir.resolve().parent
    return report(validator, root, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
