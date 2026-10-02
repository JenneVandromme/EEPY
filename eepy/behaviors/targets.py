"""Where "look_at: user" actually points.

Behaviour files name gaze targets -- user, notes, screen, away -- and never
contain angles for them. Something has to turn a name into a direction at play
time, and that something is a TargetProvider.

For now that is StaticTargetProvider, which reads the fixed angles from
behaviors/_blocks.yaml. Later, face tracking can supply a provider whose "user"
follows the real person. The player only ever calls `get(name)`, so swapping
providers needs no change to the player or to any behaviour file.

Targets are looked up once, when a behaviour starts -- not continuously while it
plays. That keeps every behaviour fully safety-checked before the first frame
is sent. A tracking provider just has to be current at that moment.
"""

from __future__ import annotations

from typing import Callable, Protocol

from .schema import HeadPose


class TargetProvider(Protocol):
    """Anything that can say where a named gaze target is."""

    def get(self, name: str) -> HeadPose:
        """The head pose that looks at `name`. Raise KeyError if unknown."""
        ...


class StaticTargetProvider:
    """Fixed directions, from _blocks.yaml, with optional per-desk overrides.

    `library` is a callable rather than a Library so that the provider always
    sees the CURRENT library after the player reloads the YAML -- edit a gaze
    target in _blocks.yaml and the next behaviour uses it.

        StaticTargetProvider(lambda: player.library,
                             overrides={"user": HeadPose(yaw=15, pitch=-14)})
    """

    def __init__(
        self,
        library: Callable[[], object],
        overrides: dict[str, HeadPose] | None = None,
    ) -> None:
        self._library = library
        self._overrides = dict(overrides or {})

    def get(self, name: str) -> HeadPose:
        if name in self._overrides:
            return self._overrides[name]
        targets = getattr(self._library(), "gaze_targets", {})
        if name not in targets:
            known = ", ".join(sorted(set(targets) | set(self._overrides)))
            raise KeyError(f'no gaze target called "{name}" (known: {known})')
        return targets[name]

    def set(self, name: str, pose: HeadPose) -> None:
        """Override one target at runtime, e.g. once the user is located."""
        self._overrides[name] = pose
