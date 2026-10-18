"""Read behaviors/*.yaml and resolve them into flat timelines.

This is the API the future behaviour player uses. It does no robot control and
imports no robot SDK -- it turns YAML into typed Python objects and expands
building blocks into a plain list of steps.

Typical use from the player:

    from eepy.behaviors.loader import load_library

    library = load_library()
    behavior = library.get_behavior("bedtime", 3)
    for step in library.resolve(behavior):
        ...  # step.head_yaw, step.duration, step.sound, step.say, ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml

from . import limits
from .schema import (
    Behavior,
    BehaviorFile,
    Block,
    BlocksFile,
    HeadPose,
    Keyframe,
    Line,
    LinesFile,
)

# behaviors/ lives next to eepy/, at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
BEHAVIORS_DIR = REPO_ROOT / "behaviors"
SOUNDS_DIR = REPO_ROOT / "sounds"

BLOCKS_FILENAME = "_blocks.yaml"
LINES_FILENAME = "lines.yaml"


class BehaviorFileError(Exception):
    """A behaviour file could not be read at all (bad YAML, duplicate key).

    Carries the file and, when YAML tells us, the line number -- so the
    validator can point a designer straight at it.
    """

    def __init__(self, path: Path, message: str, line: int | None = None) -> None:
        self.path = path
        self.message = message
        self.line = line
        super().__init__(message)


class _NoDuplicatesLoader(yaml.SafeLoader):
    """A YAML loader that refuses duplicate keys.

    Plain PyYAML silently keeps the last of two identical keys, so writing
    'name:' twice loses the first one with no warning at all. In a file edited
    by hand that is a trap -- the edit appears to do nothing -- so we make it
    an error.
    """


def _no_duplicates(loader: yaml.Loader, node: yaml.Node) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f'the key "{key}" appears twice in the same block. '
                "Delete one of them -- otherwise only the second takes effect",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_NoDuplicatesLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def read_yaml(path: Path) -> dict:
    """Load one YAML file, with duplicate keys treated as an error."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BehaviorFileError(path, f"could not read the file: {exc}") from exc
    try:
        data = yaml.load(text, Loader=_NoDuplicatesLoader)
    except yaml.MarkedYAMLError as exc:
        line = exc.problem_mark.line + 1 if exc.problem_mark else None
        problem = exc.problem or "the file is not valid YAML"
        raise BehaviorFileError(path, problem, line) from exc
    except yaml.YAMLError as exc:
        raise BehaviorFileError(path, str(exc)) from exc
    if data is None:
        raise BehaviorFileError(path, "the file is empty")
    if not isinstance(data, dict):
        raise BehaviorFileError(
            path,
            "the file should start with a top-level key such as 'behaviors:'",
        )
    return data


# --- A resolved step ------------------------------------------------------


@dataclass
class Step:
    """One concrete movement, with every building block already expanded.

    Absolute values in designer units. Nothing here is optional or inherited:
    the resolver has already filled in whatever the YAML left out.
    """

    start_s: float
    duration: float
    easing: str
    hold: float

    head_yaw: float
    head_pitch: float
    head_roll: float
    head_z: float
    antenna_right: float
    antenna_left: float
    body_yaw: float

    sound: str | None = None
    say: str | None = None
    action: str | None = None

    # Where this step came from, for error messages and the preview table.
    source: str = ""

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration + self.hold


@dataclass
class _Pose:
    """Mutable cursor the resolver carries through a timeline."""

    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    z: float = 0.0
    antenna_right: float = limits.NEUTRAL_ANTENNA_RIGHT_DEG
    antenna_left: float = limits.NEUTRAL_ANTENNA_LEFT_DEG
    body_yaw: float = 0.0


@dataclass
class Library:
    """Everything under behaviors/, loaded and cross-referenced."""

    behaviors: dict[str, Behavior] = field(default_factory=dict)
    blocks: dict[str, Block] = field(default_factory=dict)
    gaze_targets: dict[str, HeadPose] = field(default_factory=dict)
    lines: dict[str, Line] = field(default_factory=dict)
    # Which file each behaviour came from, for error messages.
    sources: dict[str, Path] = field(default_factory=dict)

    # --- lookup -----------------------------------------------------------

    def get_behavior(self, ladder: str, level: int) -> Behavior:
        """The behaviour on a ladder rung.

        Raises KeyError if that rung does not exist, listing what does, so a
        caller is never left guessing.
        """
        for behavior in self.behaviors.values():
            if behavior.ladder == ladder and behavior.level == level:
                return behavior
        available = sorted(
            lvl for b in self.behaviors.values()
            if b.ladder == ladder and (lvl := b.level) is not None
        )
        if not available:
            ladders = sorted(
                {b.ladder for b in self.behaviors.values() if b.ladder}
            )
            raise KeyError(
                f"there is no ladder called {ladder!r}. Ladders: {ladders}"
            )
        raise KeyError(
            f"ladder {ladder!r} has no level {level}. It has levels {available}"
        )

    def ladders(self) -> dict[str, list[int]]:
        """Every ladder and the levels it defines, sorted."""
        out: dict[str, list[int]] = {}
        for behavior in self.behaviors.values():
            if behavior.ladder is not None and behavior.level is not None:
                out.setdefault(behavior.ladder, []).append(behavior.level)
        return {k: sorted(v) for k, v in sorted(out.items())}

    # --- resolution -------------------------------------------------------

    def resolve(
        self,
        behavior: Behavior,
        start: Any = None,
        gaze: Callable[[str], HeadPose] | None = None,
        intensity_scale: float = 1.0,
        base: HeadPose | None = None,
    ) -> list[Step]:
        """Expand a behaviour into a flat list of absolute steps.

        Building blocks are inlined, omitted values inherit from the previous
        keyframe, `relative` offsets are added to the previous pose, and
        `intensity` scales every angle.

        The three optional arguments are for the player; the validator and the
        preview leave them alone and get the same result as before.

          start            the pose the robot is in when the behaviour begins.
                           Anything with yaw/pitch/roll/z/antenna_right/
                           antenna_left/body_yaw attributes (a pose.Pose).
                           Relative keyframes and omitted values build on it.
                           Default: neutral.
          gaze             how to turn a look_at name into a head pose. The
                           player passes its TargetProvider here, so face
                           tracking can supply a live "user". Default: the
                           fixed angles in _blocks.yaml.
          intensity_scale  multiplies the behaviour's own `intensity`, for
                           play(name, intensity=0.5). Must be 0-1, like the
                           field it scales.
          base             the robot's resting direction (yaw/pitch), e.g. aimed
                           at the student's face. Absolute head angles in a
                           behaviour are then offsets from it, and intensity
                           scales toward it instead of toward 0; look_at targets
                           stay the directions they name. None (the default):
                           exactly as before, the resting direction is 0.

        One pass through a looping behaviour is returned; looping is the
        player's job, not this function's.
        """
        if not 0.0 <= intensity_scale <= 1.0:
            raise ValueError(
                f"intensity must be between 0 and 1, got {intensity_scale}"
            )
        pose = _Pose()
        if start is not None:
            for name in ("yaw", "pitch", "roll", "z", "antenna_right",
                         "antenna_left", "body_yaw"):
                setattr(pose, name, float(getattr(start, name)))
        self._gaze = gaze or (lambda name: self.gaze_targets[name])
        self._base_yaw = float(base.yaw or 0.0) if base is not None else 0.0
        self._base_pitch = float(base.pitch or 0.0) if base is not None else 0.0
        steps: list[Step] = []
        clock = 0.0
        for index, keyframe in enumerate(behavior.keyframes):
            clock = self._apply(
                keyframe,
                pose,
                steps,
                clock,
                behavior.intensity * intensity_scale,
                f"{behavior.name}[{index + 1}]",
            )
        return steps

    def _apply(
        self,
        keyframe: Keyframe,
        pose: _Pose,
        steps: list[Step],
        clock: float,
        intensity: float,
        source: str,
    ) -> float:
        """Apply one keyframe, appending to steps. Returns the new clock."""
        if keyframe.use is not None:
            block = self.blocks[keyframe.use]
            scale = intensity * keyframe.scale
            first = len(steps)
            for i, inner in enumerate(block.keyframes):
                clock = self._apply_pose(
                    inner,
                    pose,
                    steps,
                    clock,
                    scale,
                    f"{source} -> {keyframe.use}[{i + 1}]",
                    relative=inner.relative or block.relative,
                )
            # A sound or line written next to `use:` belongs to the moment the
            # block starts, which is its first step. Without this, the obvious
            # way to attach a sound to a block -- `- use: yawn` with
            # `sound: yawn.wav` -- would validate and then do nothing.
            if steps and (keyframe.sound or keyframe.say):
                if keyframe.sound:
                    steps[first].sound = keyframe.sound
                if keyframe.say:
                    steps[first].say = keyframe.say
            return clock

        return self._apply_pose(
            keyframe,
            pose,
            steps,
            clock,
            intensity,
            source,
            relative=keyframe.relative,
        )

    def _apply_pose(
        self,
        keyframe: Keyframe,
        pose: _Pose,
        steps: list[Step],
        clock: float,
        intensity: float,
        source: str,
        relative: bool,
    ) -> float:
        """Turn one pose-or-action keyframe into exactly one Step."""
        # A built-in handover has no pose of its own; it leaves the robot
        # wherever the SDK's own routine puts it.
        if keyframe.action is not None:
            step = Step(
                start_s=clock,
                duration=keyframe.duration or 0.0,
                easing=keyframe.easing,
                hold=keyframe.hold,
                head_yaw=pose.yaw,
                head_pitch=pose.pitch,
                head_roll=pose.roll,
                head_z=pose.z,
                antenna_right=pose.antenna_right,
                antenna_left=pose.antenna_left,
                body_yaw=pose.body_yaw,
                action=keyframe.action,
                sound=keyframe.sound,
                say=keyframe.say,
                source=source,
            )
            steps.append(step)
            return step.end_s

        # Resolve the head. look_at is just a named absolute pose.
        head = keyframe.head
        target = keyframe.look_at is not None
        if target:
            head = self._gaze(keyframe.look_at)
            relative = False
        # The resting direction (0 unless the player was given a base). A written
        # angle is an offset from it; a look_at target is a direction in the room,
        # so only its intensity scaling goes toward the base. With a base of 0 both
        # are exactly the old "value * intensity".
        by = getattr(self, "_base_yaw", 0.0)
        bp = getattr(self, "_base_pitch", 0.0)

        if head is not None:
            if relative:
                # Offsets from the PREVIOUS pose, so a sequence composes the way
                # it reads: a nod written "+8 then -8" goes down and comes back,
                # and "breathe in, breathe out" returns to where it started.
                # Offsets from a fixed start pose would instead leave both
                # ending somewhere they never meant to be.
                if head.yaw is not None:
                    pose.yaw += head.yaw * intensity
                if head.pitch is not None:
                    pose.pitch += head.pitch * intensity
                if head.roll is not None:
                    pose.roll += head.roll * intensity
                if head.z is not None:
                    pose.z += head.z * intensity
            else:
                # Absolute, scaled toward the resting direction (0 without a base).
                if head.yaw is not None:
                    pose.yaw = by + ((head.yaw - by) if target else head.yaw) * intensity
                if head.pitch is not None:
                    pose.pitch = bp + ((head.pitch - bp) if target else head.pitch) * intensity
                if head.roll is not None:
                    pose.roll = head.roll * intensity
                if head.z is not None:
                    pose.z = head.z * intensity

        if keyframe.antennas is not None:
            # Antennas scale toward their neutral offset, not toward zero --
            # zero is a pose the SDK deliberately avoids (it shakes there).
            if keyframe.antennas.right is not None:
                neutral = limits.NEUTRAL_ANTENNA_RIGHT_DEG
                pose.antenna_right = neutral + (
                    keyframe.antennas.right - neutral
                ) * intensity
            if keyframe.antennas.left is not None:
                neutral = limits.NEUTRAL_ANTENNA_LEFT_DEG
                pose.antenna_left = neutral + (
                    keyframe.antennas.left - neutral
                ) * intensity

        if keyframe.body_yaw is not None:
            pose.body_yaw = keyframe.body_yaw * intensity

        step = Step(
            start_s=clock,
            duration=keyframe.duration or 0.0,
            easing=keyframe.easing,
            hold=keyframe.hold,
            head_yaw=pose.yaw,
            head_pitch=pose.pitch,
            head_roll=pose.roll,
            head_z=pose.z,
            antenna_right=pose.antenna_right,
            antenna_left=pose.antenna_left,
            body_yaw=pose.body_yaw,
            sound=keyframe.sound,
            say=keyframe.say,
            source=source,
        )
        steps.append(step)
        return step.end_s


# --- Loading --------------------------------------------------------------


def behavior_files(directory: Path | str = BEHAVIORS_DIR) -> list[Path]:
    """Every behaviour-carrying YAML file, excluding _blocks and lines."""
    directory = Path(directory)
    return sorted(
        p
        for p in directory.glob("*.yaml")
        if p.name not in (BLOCKS_FILENAME, LINES_FILENAME)
    )


def load_library(directory: Path | str = BEHAVIORS_DIR) -> Library:
    """Load and type-check everything under behaviors/.

    Raises BehaviorFileError for unreadable YAML, and pydantic's
    ValidationError for a file whose shape is wrong. For friendly, designer-
    readable reporting of those, use `python -m eepy.behaviors.validate`
    instead of calling this directly.
    """
    directory = Path(directory)
    library = Library()

    blocks_path = directory / BLOCKS_FILENAME
    if blocks_path.is_file():
        parsed = BlocksFile.model_validate(read_yaml(blocks_path))
        library.blocks = dict(parsed.blocks)
        library.gaze_targets = dict(parsed.gaze_targets)

    lines_path = directory / LINES_FILENAME
    if lines_path.is_file():
        library.lines = dict(LinesFile.model_validate(read_yaml(lines_path)).lines)

    for path in behavior_files(directory):
        parsed = BehaviorFile.model_validate(read_yaml(path))
        for behavior in parsed.behaviors:
            library.behaviors[behavior.name] = behavior
            library.sources[behavior.name] = path

    return library


def load_behaviors(directory: Path = BEHAVIORS_DIR) -> dict[str, Behavior]:
    """Every behaviour, keyed by name. The simplest entry point."""
    return load_library(directory).behaviors


def get_behavior(
    ladder: str, level: int, directory: Path = BEHAVIORS_DIR
) -> Behavior:
    """The behaviour on one ladder rung.

    Convenience wrapper; a player that plays more than one behaviour should
    hold on to a Library instead of reloading every file each time.
    """
    return load_library(directory).get_behavior(ladder, level)


def iter_sound_references(library: Library) -> Iterator[tuple[str, str]]:
    """(behaviour or block name, sound filename) for every sound referenced."""
    for name, behavior in library.behaviors.items():
        for keyframe in behavior.keyframes:
            if keyframe.sound:
                yield name, keyframe.sound
    for name, block in library.blocks.items():
        for keyframe in block.keyframes:
            if keyframe.sound:
                yield name, keyframe.sound


__all__ = [
    "BEHAVIORS_DIR",
    "BehaviorFileError",
    "Library",
    "SOUNDS_DIR",
    "Step",
    "behavior_files",
    "get_behavior",
    "iter_sound_references",
    "load_behaviors",
    "load_library",
    "read_yaml",
]
