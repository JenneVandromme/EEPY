"""How the escalation reaches the robot.

PlayerActuator   the panel's BehaviorPlayer: every behaviour is logged with source "auto"
SimActuator      the simulation: virtual time, the real library's behaviours and their
                 real durations, nothing moves
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("eepy.interaction")
SPEECH_SECONDS = 2.5        # the simulation's guess for a spoken line


class PlayerActuator:
    """`player` is a BehaviorPlayer, or a function returning the current one (the panel
    replaces its player on Reconnect). `library` (optional) answers lookups while no
    player is connected."""

    def __init__(self, player: Any, on_error: Optional[Callable[[str], None]] = None,
                 library: Optional[Callable[[], Any]] = None) -> None:
        self._player = player if callable(player) and not hasattr(player, "play") else (lambda: player)
        self._library = library
        self.on_error = on_error

    @property
    def player(self) -> Any:
        player = self._player()
        if player is None or getattr(player, "error", None) is not None:
            raise RuntimeError("the robot is not connected")
        return player

    def _lib(self) -> Any:
        player = self._player()
        if player is not None:
            return player.library
        if self._library is not None:
            return self._library()
        raise RuntimeError("no behaviours loaded")

    def _safe(self, what: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- the panel must keep running
            message = f"The escalation could not {what}: {exc}"
            log.warning(message)
            if self.on_error is not None:
                self.on_error(message)
            return None

    def busy(self) -> bool:
        player = self._player()
        if player is None or getattr(player, "error", None) is not None:
            return True                      # no robot: nothing can be played
        return bool(player.is_playing())

    def has_behavior(self, name: str) -> bool:
        return name in self._lib().behaviors

    def level_behavior(self, ladder: str, level: int) -> str:
        return self._lib().get_behavior(ladder, level).name

    def play_level(self, ladder: str, level: int, intensity: float, tone: str,
                   context: Dict[str, Any]) -> str:
        name = self.level_behavior(ladder, level)
        self._safe(f"play {name}", lambda: self.player.play_level(
            ladder, level, intensity, tone=tone, context=context, source="auto"))
        return name

    def play(self, name: str) -> None:
        self._safe(f"play {name}", lambda: self.player.play(name, source="auto"))

    def set_idle(self, name: Optional[str]) -> None:
        self._safe("change the idle", lambda: self.player.set_idle(name))

    def say(self, line_id: str, tone: str, context: Dict[str, Any]) -> None:
        self._safe(f"say {line_id}", lambda: self.player.say(line_id, tone=tone, context=context,
                                                             source="auto"))


class SimActuator:
    """Virtual robot: busy for each behaviour's real length (from behaviors/)."""

    def __init__(self, library: Any, clock: Callable[[], float]) -> None:
        self.library = library
        self.clock = clock
        self.busy_until = 0.0
        self.idle: Optional[str] = None
        self.calls: List[Tuple[float, str, str]] = []      # (t, kind, what)
        self._lengths: Dict[str, float] = {}

    def length(self, name: str) -> float:
        if name not in self._lengths:
            behavior = self.library.behaviors[name]
            steps = self.library.resolve(behavior)
            spoken = sum(SPEECH_SECONDS for s in steps if s.say)
            self._lengths[name] = max((s.end_s for s in steps), default=0.0) + spoken
        return self._lengths[name]

    def busy(self) -> bool:
        return self.clock() < self.busy_until

    def has_behavior(self, name: str) -> bool:
        return name in self.library.behaviors

    def level_behavior(self, ladder: str, level: int) -> str:
        return self.library.get_behavior(ladder, level).name

    def _run(self, kind: str, name: str, seconds: float) -> None:
        now = self.clock()
        self.busy_until = max(self.busy_until, now) + seconds
        self.calls.append((now, kind, name))

    def play_level(self, ladder: str, level: int, intensity: float, tone: str,
                   context: Dict[str, Any]) -> str:
        name = self.level_behavior(ladder, level)
        self._run("play", f"{name} ({tone})", self.length(name))
        return name

    def play(self, name: str) -> None:
        self._run("play", name, self.length(name))

    def set_idle(self, name: Optional[str]) -> None:
        if name != self.idle:
            self.idle = name
            self.calls.append((self.clock(), "idle", name or "none"))

    def say(self, line_id: str, tone: str, context: Dict[str, Any]) -> None:
        self._run("say", line_id, SPEECH_SECONDS)
