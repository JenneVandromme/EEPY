"""Text-to-speech engines: text in, audio bytes out.

    piper    local neural voices. Fast (about 50x real time), offline, and on
             every platform the team uses. The default.
    system   the operating system's own voice: `say` on macOS, SAPI on Windows,
             espeak on Linux. Lower quality, but always there -- the fallback.
    google   Google Translate's speech endpoint. Online, no key, one voice per
             language. Unofficial: it can change or block without warning.
    gemini   Gemini's speech model. Online, needs GEMINI_API_KEY (the same key
             robot/conversation.py uses), quota-limited.

Every engine produces a complete audio file as bytes (WAV, or MP3 for google),
so clips can be cached on disk and uploaded to the robot like any other sound.

Engines are synchronous and may be slow. The Speaker only ever calls them from
its own thread.
"""

from __future__ import annotations

import base64
import io
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
VOICES_DIR = REPO_ROOT / ".cache" / "voices"
ENGINE_NAMES = ("piper", "system", "google", "gemini")


class EngineError(Exception):
    """An engine could not produce audio. The message is for humans."""


class Engine:
    """Base class. Subclasses set name, voice, and implement _synthesize."""

    name = "engine"
    ext = "wav"

    def __init__(self, voice: Optional[str] = None) -> None:
        self.voice = voice

    @property
    def key(self) -> str:
        """Identifies the sound of this engine: used in cache file names."""
        return f"{self.name}:{self.voice or 'default'}"

    @property
    def label(self) -> str:
        return f"{self.name} / {self.voice or 'default voice'}"

    def unavailable(self) -> Optional[str]:
        """None if the engine can be used; otherwise why not, for humans."""
        return None

    def warm_up(self) -> None:
        """Load whatever the first synthesis would otherwise load. Best effort."""

    def synthesize(self, text: str) -> bytes:
        text = " ".join(text.split())
        if not text:
            raise EngineError("there is nothing to say")
        reason = self.unavailable()
        if reason:
            raise EngineError(reason)
        try:
            data = self._synthesize(text)
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any engine failure is reported the same way
            raise EngineError(f"{self.label} failed: {type(exc).__name__}: {exc}") from exc
        if not data or len(data) < 64:
            raise EngineError(f"{self.label} produced no audio")
        return data

    def _synthesize(self, text: str) -> bytes:
        raise NotImplementedError


# --- piper ---------------------------------------------------------------------


class PiperEngine(Engine):
    """Piper neural TTS. The voice model is a .onnx file in .cache/voices/."""

    name = "piper"

    def __init__(self, voice: str = "en_US-lessac-medium", voices_dir: Path = VOICES_DIR) -> None:
        super().__init__(voice)
        self.voices_dir = Path(voices_dir)
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_path(self) -> Path:
        return self.voices_dir / f"{self.voice}.onnx"

    def unavailable(self) -> Optional[str]:
        try:
            import piper  # noqa: F401
        except ImportError:
            return ("Piper is not installed. Activate .venv-robot and run: "
                    "pip install -r requirements-robot.txt")
        if not self.model_path.is_file() or not self.model_path.with_suffix(".onnx.json").is_file():
            return (f'the Piper voice "{self.voice}" is not downloaded yet. Run: '
                    f"python -m eepy.speech.voices --download {self.voice}")
        return None

    def warm_up(self) -> None:
        """Load the voice model now (about 1 s), not on the first live sentence."""
        if self.unavailable() is None:
            with self._lock:
                self._load()

    def _load(self):
        if self._model is None:
            from piper import PiperVoice

            self._model = PiperVoice.load(str(self.model_path))
        return self._model

    def _synthesize(self, text: str) -> bytes:
        with self._lock:     # one model, one inference at a time
            self._load()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as clip:
                self._model.synthesize_wav(text, clip)
            return buffer.getvalue()


# --- the operating system's own voice ----------------------------------------------


class SystemEngine(Engine):
    """macOS `say`, Windows SAPI (through PowerShell), or Linux espeak."""

    name = "system"

    def __init__(self, voice: Optional[str] = None) -> None:
        super().__init__(voice)
        self.os = platform.system()

    def unavailable(self) -> Optional[str]:
        if self.os == "Darwin":
            return None if shutil.which("say") else "the macOS `say` command is missing"
        if self.os == "Windows":
            return None if shutil.which("powershell") else "PowerShell is missing"
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            return None
        return "no system voice: install espeak-ng (sudo apt install espeak-ng)"

    def _synthesize(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "speech.wav"
            if self.os == "Darwin":
                cmd = ["say", "-o", str(out), "--data-format=LEI16@22050"]
                if self.voice:
                    cmd += ["-v", self.voice]
                subprocess.run(cmd + ["--", text], check=True, capture_output=True, timeout=30)
            elif self.os == "Windows":
                # Text and voice go in through the environment, never through
                # the command line, so no quoting can break or inject anything.
                script = ("Add-Type -AssemblyName System.Speech; "
                          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                          "if ($env:EEPY_VOICE) { $s.SelectVoice($env:EEPY_VOICE) }; "
                          "$s.SetOutputToWaveFile($env:EEPY_OUT); "
                          "$s.Speak($env:EEPY_TEXT); $s.Dispose()")
                env = {**os.environ, "EEPY_TEXT": text, "EEPY_OUT": str(out),
                       "EEPY_VOICE": self.voice or ""}
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               check=True, capture_output=True, timeout=30, env=env)
            else:
                exe = shutil.which("espeak-ng") or shutil.which("espeak")
                cmd = [exe, "-w", str(out)] + (["-v", self.voice] if self.voice else [])
                subprocess.run(cmd + ["--", text], check=True, capture_output=True, timeout=30)
            return out.read_bytes()


# macOS ships musical and joke voices alongside the real ones. Played to
# designers one after another they are noise, so they are left out unless asked.
MACOS_NOVELTY = {"Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos",
                 "Good News", "Jester", "Organ", "Superstar", "Trinoids", "Whisper",
                 "Wobble", "Zarvox"}


def system_voices(include_novelty: bool = False) -> list[str]:
    """English voices the operating system offers. Best effort; [] if unknown."""
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
            voices = []
            for line in out.splitlines():
                name, _, rest = line.partition("#")[0].rstrip().rpartition(" ")
                if rest.startswith("en_") and name.strip():
                    if include_novelty or name.strip() not in MACOS_NOVELTY:
                        voices.append(name.strip())
            return voices
        if system == "Windows":
            script = ("Add-Type -AssemblyName System.Speech; "
                      "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() "
                      "| ForEach-Object { $_.VoiceInfo.Name }")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                 capture_output=True, text=True, timeout=20).stdout
            return [v.strip() for v in out.splitlines() if v.strip()]
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if exe:
            out = subprocess.run([exe, "--voices=en"], capture_output=True, text=True, timeout=10).stdout
            return [line.split()[4] for line in out.splitlines()[1:] if len(line.split()) > 4]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


# --- cloud ------------------------------------------------------------------------------


class GoogleEngine(Engine):
    """Google Translate TTS: online, keyless, MP3. Unofficial -- may break."""

    name = "google"
    ext = "mp3"
    MAX_CHARS = 200     # the endpoint refuses longer requests

    def __init__(self, voice: str = "en") -> None:
        super().__init__(voice)        # for google the "voice" is a language code

    def _synthesize(self, text: str) -> bytes:
        import requests

        audio = b""
        for chunk in _chunks(text, self.MAX_CHARS):
            reply = requests.get(
                "https://translate.google.com/translate_tts",
                params={"ie": "UTF-8", "client": "tw-ob", "tl": self.voice, "q": chunk,
                        "total": 1, "idx": 0, "textlen": len(chunk)},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            reply.raise_for_status()
            audio += reply.content     # MP3 frames concatenate cleanly
        return audio


def _chunks(text: str, limit: int) -> list[str]:
    """Pack whole sentences into as few pieces of at most `limit` characters as
    possible; only a sentence longer than `limit` is split between words.

    Fewer pieces means fewer requests: faster, and less likely to be rate-limited.
    """
    import re

    def by_words(sentence: str) -> list[str]:
        out, current = [], ""
        for word in sentence.split():
            if current and len(current) + 1 + len(word) > limit:
                out.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        return out + ([current] if current else [])

    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        for part in (by_words(sentence) if len(sentence) > limit else [sentence]):
            if current and len(current) + 1 + len(part) > limit:
                pieces.append(current)
                current = part
            else:
                current = f"{current} {part}".strip()
    return pieces + ([current] if current else [])


class GeminiEngine(Engine):
    """Gemini TTS: online, needs GEMINI_API_KEY in .env. Same call as conversation.py."""

    name = "gemini"
    MODEL = "gemini-3.1-flash-tts-preview"

    def __init__(self, voice: str = "Kore") -> None:
        super().__init__(voice)

    def unavailable(self) -> Optional[str]:
        return None if os.environ.get("GEMINI_API_KEY") else (
            "GEMINI_API_KEY is not set (put it in .env -- see .env.example)")

    def _synthesize(self, text: str) -> bytes:
        import requests

        reply = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.MODEL}:generateContent",
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json={"contents": [{"parts": [{"text": text}]}],
                  "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                      "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}}}},
            timeout=30)
        if not reply.ok:
            raise EngineError(f"Gemini answered {reply.status_code}: {reply.text[:200]}")
        part = reply.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        rate = 24000
        for item in part.get("mimeType", "").split(";"):
            if item.strip().startswith("rate="):
                rate = int(item.split("=")[1])
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(rate)
            clip.writeframes(base64.b64decode(part["data"]))
        return buffer.getvalue()


def make_engine(name: str, voice: Optional[str] = None, voices_dir: Path = VOICES_DIR) -> Engine:
    """Build an engine by name. `voice` None means the engine's default."""
    name = (name or "").lower()
    if name == "piper":
        return PiperEngine(voice or "en_US-lessac-medium", voices_dir)
    if name == "system":
        return SystemEngine(voice)
    if name == "google":
        return GoogleEngine(voice or "en")
    if name == "gemini":
        return GeminiEngine(voice or "Kore")
    raise EngineError(f'"{name}" is not a speech engine. Use one of: {", ".join(ENGINE_NAMES)}.')
