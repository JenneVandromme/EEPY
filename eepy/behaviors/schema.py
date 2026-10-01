"""The behaviour file format, as pydantic v2 models.

This module is the single source of truth for the shape of everything under
behaviors/. The validator, the loader and the preview all read these models,
and the designers' guide (docs/BEHAVIORS_GUIDE.md) documents them.

Units, everywhere, with no exceptions:
    angles  degrees
    heights millimetres
    times   seconds
    scales  0.0 to 1.0

Nothing here imports the robot SDK, so this file loads without a robot, a
daemon or a simulator -- it needs only pydantic. That is what lets the
validator run in CI without installing reachy-mini.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from . import limits

# Names are snake_case so they are safe to use as Python identifiers, dict
# keys and filenames, and so they sort predictably in a file listing.
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]

Easing = Literal["linear", "minjerk", "ease_in_out", "cartoon"]
Action = Literal["goto_sleep", "wake_up"]
Tone = Literal["hedged", "direct"]


class Strict(BaseModel):
    """Base for every model: unknown fields are an error, not ignored.

    This is what turns a typo like "antena:" into a clear message instead of
    a behaviour that silently does nothing.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _numbers_are_finite(self) -> "Strict":
        """Reject .nan, .inf and -.inf, which YAML accepts as floats.

        These slip through every range check -- abs(nan) > 40 is False, so a
        NaN angle looks perfectly legal -- and would reach the robot as a
        garbage target.
        """
        for name, value in self:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(
                    f"'{name}' is {value}, which is not a number the robot can "
                    "use. Write an ordinary number."
                )
        return self


class HeadPose(Strict):
    """Where the head points, in degrees, plus its height in millimetres.

    Every field is optional. Anything left out keeps whatever value the
    previous keyframe had, so a keyframe that only changes pitch writes only
    pitch.
    """

    yaw: float | None = Field(
        default=None, description="+ turns left, - turns right."
    )
    pitch: float | None = Field(
        default=None, description="+ tips the chin down, - tips it up."
    )
    roll: float | None = Field(
        default=None, description="+ tips toward the left ear."
    )
    z: float | None = Field(
        default=None, description="Height in mm. + rises, - sinks."
    )

    def is_empty(self) -> bool:
        return all(v is None for v in (self.yaw, self.pitch, self.roll, self.z))


class Antennas(Strict):
    """The two antennas, in degrees.

    The antennas are mirrored: equal-and-opposite values look symmetric.
    Neutral is right -10 / left +10 (the SDK offsets them from vertical on
    purpose, to stop them shaking).
    """

    right: float | None = None
    left: float | None = None


class Keyframe(Strict):
    """One step of a behaviour.

    A keyframe is exactly one of three things:

      1. a pose to move into  -- head / look_at / antennas / body_yaw
      2. a reused block       -- use: <block name>
      3. a built-in handover  -- action: goto_sleep
    """

    # --- 1. a pose ---------------------------------------------------------
    head: HeadPose | None = None
    look_at: Name | None = Field(
        default=None,
        description="A named gaze target from _blocks.yaml. Use instead of head.",
    )
    antennas: Antennas | None = None
    body_yaw: float | None = Field(
        default=None, description="Body rotation in degrees. + turns left."
    )
    relative: bool = Field(
        default=False,
        description=(
            "True means head values are offsets from the PREVIOUS pose, not "
            "absolute positions. Offsets compose: '+8 then -8' goes down and "
            "comes back."
        ),
    )

    # --- 2. a reused block -------------------------------------------------
    use: Name | None = None
    scale: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Shrinks a reused block's angles. 1.0 is as written.",
    )

    # --- 3. a built-in handover -------------------------------------------
    action: Action | None = None

    # --- timing, shared by all three --------------------------------------
    duration: float | None = Field(
        default=None,
        gt=0.0,
        le=60.0,
        description="Seconds to move into this keyframe. Required.",
    )
    easing: Easing = "minjerk"
    hold: float = Field(
        default=0.0,
        ge=0.0,
        le=60.0,
        description="Extra seconds to sit still after arriving.",
    )

    # --- optional extras ---------------------------------------------------
    sound: str | None = Field(
        default=None, description="A filename in sounds/, e.g. yawn.wav"
    )
    say: Name | None = Field(
        default=None, description="A line ID from lines.yaml."
    )

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> "Keyframe":
        has_pose = bool(
            (self.head is not None and not self.head.is_empty())
            or self.look_at is not None
            or self.antennas is not None
            or self.body_yaw is not None
        )
        kinds = [has_pose, self.use is not None, self.action is not None]
        if sum(kinds) == 0:
            raise ValueError(
                "this keyframe does nothing. Give it a pose (head/look_at/"
                "antennas/body_yaw), or 'use:' a block, or an 'action:'"
            )
        if sum(kinds) > 1:
            raise ValueError(
                "this keyframe tries to do two things at once. A keyframe is "
                "either a pose, or 'use:' a block, or an 'action:' -- not a mix"
            )
        if self.head is not None and self.look_at is not None:
            raise ValueError(
                "'head:' and 'look_at:' both set the head. Pick one: look_at "
                "for a named direction, head for exact angles"
            )
        if self.use is None and self.scale != 1.0:
            raise ValueError("'scale:' only means something next to 'use:'")
        if self.use is not None:
            # A block carries its own timing, so overriding it here would be
            # ambiguous: does 'duration' replace the block's total, or each of
            # its keyframes? Rather than guess, say so. 'sound' and 'say' ARE
            # allowed -- they attach to the moment the block starts.
            clashes = [
                name
                for name, value in (
                    ("duration", self.duration),
                    ("hold", self.hold or None),
                    ("easing", self.easing if "easing" in self.model_fields_set else None),
                )
                if value is not None
            ]
            if clashes:
                listed = " and ".join(f"'{name}:'" for name in clashes)
                raise ValueError(
                    f"{listed} cannot go next to 'use:' -- the block "
                    f"'{self.use}' already sets its own timing. Change it "
                    "inside the block, or use 'scale:' to make the whole block "
                    "smaller. ('sound:' and 'say:' are fine here.)"
                )
        if self.use is None and self.duration is None:
            raise ValueError("every keyframe needs a 'duration:' in seconds")
        return self


class Block(Strict):
    """A small named movement that behaviours reuse."""

    description: str = Field(min_length=1)
    relative: bool = Field(
        default=False,
        description=(
            "True means every keyframe in this block is an offset from the "
            "previous pose, so the block works from wherever the head is."
        ),
    )
    keyframes: list[Keyframe] = Field(min_length=1)


class Behavior(Strict):
    """One named, playable behaviour."""

    name: Name
    description: str = Field(min_length=1)
    group: str | None = None

    # Ladder metadata. The decision logic selects by (ladder, level), so these
    # two always travel together: either both set, or neither.
    ladder: Name | None = None
    level: int | None = Field(default=None, ge=0, le=20)

    # StrictInt, not int: plain int would let pydantic quietly turn YAML's
    # `loop: no` into the number 0. StrictInt rejects the bool instead, which
    # lets the validator explain the yes/no trap properly.
    loop: Literal["once", "forever"] | StrictInt = Field(
        default="once",
        description="'once', 'forever', or a repeat count like 3.",
    )
    intensity: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Scales every angle toward neutral. 0.5 is half as big. Capped at "
            "1.0 so a behaviour that validates stays safe at any intensity."
        ),
    )
    keyframes: list[Keyframe] = Field(min_length=1)

    @model_validator(mode="after")
    def _ladder_and_level_together(self) -> "Behavior":
        if (self.ladder is None) != (self.level is None):
            raise ValueError(
                "'ladder:' and 'level:' go together. Set both (so the decision "
                "logic can find this behaviour), or neither (for a behaviour "
                "that is not on a ladder, like an idle)"
            )
        return self

    @model_validator(mode="after")
    def _loop_count_sane(self) -> "Behavior":
        if isinstance(self.loop, bool):
            raise ValueError(
                "'loop:' must be 'once', 'forever', or a number. It looks like "
                "you wrote yes/no -- YAML turns those into true/false"
            )
        if isinstance(self.loop, int) and not 1 <= self.loop <= 100:
            raise ValueError("a 'loop:' count must be between 1 and 100")
        return self

    @model_validator(mode="after")
    def _action_is_last(self) -> "Behavior":
        for i, kf in enumerate(self.keyframes):
            if kf.action is not None and i != len(self.keyframes) - 1:
                raise ValueError(
                    f"keyframe {i + 1} hands over to the robot's built-in "
                    f"'{kf.action}', so it must be the last keyframe -- "
                    "nothing can follow it"
                )
        return self


class LineVariants(Strict):
    """The two tones a spoken line comes in."""

    hedged: list[str] = Field(default_factory=list)
    direct: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one(self) -> "LineVariants":
        if not self.hedged and not self.direct:
            raise ValueError(
                "a line needs at least one variant under 'hedged:' or 'direct:'"
            )
        return self


class Line(Strict):
    """One thing the robot can say, in several wordings."""

    description: str = Field(min_length=1)
    variants: LineVariants


# --- Whole-file models ----------------------------------------------------


class BlocksFile(Strict):
    """behaviors/_blocks.yaml"""

    gaze_targets: dict[Name, HeadPose] = Field(default_factory=dict)
    blocks: dict[Name, Block] = Field(default_factory=dict)


class BehaviorFile(Strict):
    """Any behaviors/<name>.yaml that is not _blocks or lines."""

    behaviors: list[Behavior] = Field(min_length=1)


class LinesFile(Strict):
    """behaviors/lines.yaml"""

    lines: dict[Name, Line] = Field(min_length=1)


__all__ = [
    "Action",
    "Antennas",
    "Behavior",
    "BehaviorFile",
    "Block",
    "BlocksFile",
    "Easing",
    "HeadPose",
    "Keyframe",
    "Line",
    "LineVariants",
    "LinesFile",
    "Name",
    "Tone",
    "limits",
]
