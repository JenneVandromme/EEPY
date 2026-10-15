"""Where speech comes out: the robot's speaker, this computer's, or nowhere.

    robot    through the robot's daemon -- the robot's speaker. In the simulator
             the sim's daemon plays it (GStreamer), which can stay silent on
             macOS while still answering "ok", so `auto` does not use it there.
             Needs a backend whose daemon has media: not dry, not --no-media.
    desktop  this computer's speakers, directly: afplay on macOS, winsound on
             Windows, aplay / paplay on Linux. Used with the dry-run backend.
    none     no audio at all; the words still appear in the panel and the log.

`auto` picks robot for the real robot when it can play sound, else desktop
(the dry run and the simulator: this computer's speakers, directly).

An output plays one clip at a time, and a new clip replaces whatever was
playing -- the robot's daemon works that way, and the desktop output copies it
so what designers hear at a desk matches what the robot does.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("eepy.speech")

OUTPUT_NAMES = ("auto", "robot", "desktop", "none")


class OutputError(Exception):
    """Audio could not be played. The message is for humans."""


class Output:
    name = "output"

    def unavailable(self) -> Optional[str]:
        return None

    def preload(self, paths: list[Path]) -> None:
        """Get clips ready ahead of time, if that helps this output."""

    def play(self, path: Path) -> None:
        """Start playing; return as soon as it has started."""
        raise NotImplementedError

    def done(self) -> Optional[bool]:
        """True once playback has ended; None if this output cannot tell."""
        return None

    def stop(self) -> None:
        """Cut playback now. Safe to call when nothing is playing."""

    def close(self) -> None:
        """Release the audio device, if this output holds one."""


class NullOutput(Output):
    name = "none"

    def play(self, path: Path) -> None:
        pass

    def done(self) -> Optional[bool]:
        return True


class RobotOutput(Output):
    """The robot's speaker, through the daemon's sound upload and playback."""

    name = "robot"

    def __init__(self, backend) -> None:
        self.backend = backend

    def unavailable(self) -> Optional[str]:
        if self.backend.name not in ("sim", "real"):
            return f"the {self.backend.name} backend has no speaker"
        if getattr(self.backend, "_no_media", False):
            return ("the daemon was started with --no-media, so it cannot play sound. "
                    "Start it without --no-media")
        return None

    def preload(self, paths: list[Path]) -> None:
        upload = getattr(self.backend, "upload_sound", None)
        if upload is None:
            return
        for path in paths:
            try:
                upload(path)
            except Exception as exc:  # noqa: BLE001 -- uploaded again on first play instead
                log.warning("could not pre-upload %s: %s", Path(path).name, exc)
                return

    def play(self, path: Path) -> None:
        try:
            self.backend.play_sound(Path(path))
        except Exception as exc:  # noqa: BLE001 -- reported to the speaker as one kind of failure
            raise OutputError(f"the robot could not play it: {exc}") from exc

    def stop(self) -> None:
        try:
            self.backend.stop_sound()
        except Exception as exc:  # noqa: BLE001 -- nothing to stop on a dead link
            log.debug("stop_sound failed: %s", exc)


class DesktopOutput(Output):
    """This computer's speakers.

    Plays WAV clips inside this process through ONE sounddevice (PortAudio)
    stream that is opened once and kept open, playing silence when idle. A new
    clip just replaces what the stream is reading, so it is heard within one
    audio buffer (a few ms). Measured alternatives, and why not:

      * a separate player per clip (afplay): ~0.8 s overhead per clip;
      * sounddevice's play(): it closes the previous stream before opening the
        next, and on macOS that close blocks ~100 ms -- on every clip but the
        first.

    The separate players remain as the fallback: when sounddevice is not
    installed, for MP3 clips (the google engine), and on Linux without PortAudio.
    """

    name = "desktop"

    def __init__(self, prefer_in_process: bool = True) -> None:
        self.os = platform.system()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._sd = _sounddevice() if prefer_in_process else None
        self._stream = _Stream(self._sd) if self._sd is not None else None
        self._in_process = False         # is the current clip playing in-process?

    def unavailable(self) -> Optional[str]:
        if self._sd is not None:
            return None
        if self.os == "Darwin":
            return None if shutil.which("afplay") else "afplay is missing"
        if self.os == "Windows":
            return None
        if shutil.which("aplay") or shutil.which("paplay"):
            return None
        return "no audio player found: pip install sounddevice, or install alsa-utils (aplay)"

    def play(self, path: Path) -> None:
        path = Path(path)
        self.stop()
        if self._sd is not None and path.suffix.lower() == ".wav":
            import wave

            import numpy as np

            with wave.open(str(path)) as clip:
                frames = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16)
                channels, rate = clip.getnchannels(), clip.getframerate()
            try:
                self._stream.play(frames.reshape(-1, channels), rate, channels)
            except Exception as exc:  # noqa: BLE001 -- no output device, device busy, ...
                raise OutputError(f"could not play on this computer: {exc}") from exc
            self._in_process = True
            return
        self._in_process = False
        if self.os == "Windows":
            if path.suffix.lower() != ".wav":
                raise OutputError("on Windows only WAV clips can be played here "
                                  "(the google engine makes MP3 -- use piper or system)")
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
            return
        if self.os == "Darwin":
            cmd = ["afplay", str(path)]
        elif path.suffix.lower() == ".mp3":
            player = shutil.which("mpg123") or shutil.which("ffplay")
            if not player:
                raise OutputError("cannot play MP3 here: install mpg123")
            cmd = [player, "-q", str(path)] if "mpg123" in player else \
                  [player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        elif shutil.which("aplay"):
            cmd = ["aplay", "-q", str(path)]
        else:
            cmd = ["paplay", str(path)]
        with self._lock:
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
            except OSError as exc:
                raise OutputError(f"could not start {cmd[0]}: {exc}") from exc

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            self._stream.close()

    def preload(self, paths: list[Path]) -> None:
        """Open the audio stream now, in the clips' format, so the first line
        does not pay ~50 ms for it."""
        if self._stream is None:
            return
        wavs = [Path(p) for p in paths if Path(p).suffix.lower() == ".wav"]
        if not wavs:
            return
        import wave

        import numpy as np

        try:
            with wave.open(str(wavs[0])) as clip:
                rate, channels = clip.getframerate(), clip.getnchannels()
            self._stream.play(np.zeros((1, channels), dtype=np.int16), rate, channels)
        except Exception as exc:  # noqa: BLE001 -- it is opened on first play instead
            log.debug("could not open the audio stream early: %s", exc)

    def done(self) -> Optional[bool]:
        if self._in_process:
            return not self._stream.playing
        if self.os == "Windows":
            return None          # winsound cannot report the end; use the clip length
        with self._lock:
            return self._proc is None or self._proc.poll() is not None

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()           # instant: the stream just goes silent
        if self.os == "Windows":
            try:
                import winsound

                winsound.PlaySound(None, 0)
            except Exception:  # noqa: BLE001 -- nothing playing
                pass
            return
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()


class _Stream:
    """One output stream, kept open; plays a clip by swapping what it reads."""

    def __init__(self, sd) -> None:
        self._sd = sd
        self._stream = None
        self._format: Optional[tuple[int, int]] = None
        self._lock = threading.Lock()
        self._clip = None
        self._pos = 0

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._clip is not None

    def play(self, frames, rate: int, channels: int) -> None:
        if self._format != (rate, channels):
            self.close()                  # a different format: reopen, once
            self._stream = self._sd.OutputStream(samplerate=rate, channels=channels,
                                                 dtype="int16", callback=self._fill,
                                                 latency="low")
            self._stream.start()
            self._format = (rate, channels)
        with self._lock:
            self._clip, self._pos = frames, 0

    def stop(self) -> None:
        with self._lock:
            self._clip = None

    def close(self) -> None:
        self.stop()
        stream, self._stream, self._format = self._stream, None, None
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 -- the device may already be gone
                pass

    def _fill(self, outdata, frames, time_info, status) -> None:
        # The audio callback: runs on PortAudio's thread, every few ms. Keep it short.
        with self._lock:
            clip = self._clip
            if clip is None:
                outdata.fill(0)
                return
            chunk = clip[self._pos:self._pos + frames]
            self._pos += frames
            if self._pos >= len(clip):
                self._clip = None
        outdata[:len(chunk)] = chunk
        outdata[len(chunk):] = 0


def _sounddevice():
    """The sounddevice module if it is installed AND can find an output, else None."""
    try:
        import sounddevice
    except (ImportError, OSError):     # OSError: Linux without libportaudio2
        return None
    try:
        sounddevice.query_devices(kind="output")
    except Exception:  # noqa: BLE001 -- no output device at all (a headless server)
        return None
    return sounddevice


def make_output(name: str, backend=None) -> Output:
    """Build an output. `auto`: the real robot if it can play sound, else desktop."""
    name = (name or "auto").lower()
    if name not in OUTPUT_NAMES:
        raise OutputError(f'"{name}" is not a speech output. Use one of: {", ".join(OUTPUT_NAMES)}.')
    if name == "none":
        return NullOutput()
    if name == "desktop":
        return DesktopOutput()
    robot = RobotOutput(backend) if backend is not None else None
    if name == "robot":
        if robot is None:
            raise OutputError("speech output is set to robot, but there is no robot backend")
        return robot
    if robot is not None and backend.name == "sim":
        # The simulator's daemon plays sound through GStreamer: on macOS that stayed silent
        # while answering "ok" (2026-10-15), so speech goes to these speakers directly.
        log.info("Speech plays on this computer (simulator).")
        return DesktopOutput()
    if robot is not None and robot.unavailable() is None:
        return robot
    if robot is not None:
        log.info("Speech plays on this computer: %s.", robot.unavailable())
    return DesktopOutput()
