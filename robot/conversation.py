"""
Talk to EEPY: press Enter, speak (or type), and it answers out loud through
the robot's speaker while nodding along. Speech-to-text, the reply, and
text-to-speech all go through the Gemini API.

This is a *building block*, not the finished feature — it's a straight
"can we talk to the robot at all" loop, ported from a similar script in
the BURTRAM thesis repo (which used it for open-ended conversation and an
HRI deduction game). EEPY's actual use is different: the robot should
mostly speak *proactively*, when the vision/fatigue side decides it's time
for a nudge, not wait for someone to press Enter first. That trigger logic
doesn't exist yet — it's an open design question for the team (see the
README). What this file gives you two reusable pieces for that later work:

  - setup() / Robot / Voice — connect once, get a robot + a voice.
  - say_and_nod(robot, voice, text) — speak one line and nod, no back-and-
    forth needed. A future fatigue-nudge script can import this directly
    instead of going through the interactive loop below.

Usage:
    # put GEMINI_API_KEY in .env (see .env.example), then:
    source .venv-robot/bin/activate            # Windows: .venv-robot\\Scripts\\Activate.ps1
    python robot/conversation.py               # talk via the robot's mic
    python robot/conversation.py --text         # type instead (no mic needed)
    python robot/conversation.py --robot-host 192.168.1.50   # wireless robot

No robot connected? It still runs — Gemini replies out loud is skipped and
you just see the text, useful for testing the API key alone.

Ctrl+C to quit — the robot goes back to sleep on the way out.

Quota note: the Gemini API free tier is separate from any consumer "Gemini
Advanced/Pro" subscription and is stingy on the newest flash models (~20
requests/day). This defaults to a keyless voice (--tts google, only one
Gemini call per turn) and the roomier flash-lite model. For a real demo,
enable billing on the API key's project in AI Studio.
"""

import argparse
import base64
import io
import json
import os
import sys
import threading
import time
import wave

import requests
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = (
    "You are EEPY, a small friendly desk robot with a round head and two "
    "little antennas. You sit next to someone while they study or work and "
    "care about them not overdoing it. Someone is talking to you. Reply the "
    "way you would say it out loud: one or two short sentences, warm, a bit "
    "playful, never a wall of text. No emoji, no stage directions, no "
    "markdown — just what you would say."
)

REPLY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "transcript": {"type": "STRING", "description": "what the person said, verbatim"},
        "reply": {"type": "STRING", "description": "EEPY's spoken answer"},
    },
    "required": ["transcript", "reply"],
}

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_CHAT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"


# --------------------------------------------------------------------------
# Robot: playback + gestures over the daemon REST API (no reachy_mini import
# needed for this part, so it's fine to call from a numpy>=2.2.5 process)
# --------------------------------------------------------------------------
class Robot:
    def __init__(self, host: str, enable_motion: bool = True, startup_wait: float = 6.0):
        self.base = f"http://{host}:8000"
        self.motion = enable_motion
        self.startup_wait = startup_wait
        self.ok = False

    def _post(self, path: str, timeout: float = 10.0, **kw):
        return requests.post(self.base + path, timeout=timeout, **kw)

    def connect(self) -> bool:
        try:
            self.ok = requests.get(self.base + "/api/daemon/status", timeout=4).ok
            if self.ok:
                self._post("/api/media/acquire", timeout=5)
        except requests.RequestException:
            self.ok = False
        return self.ok

    def wait_ready(self) -> None:
        """Right after power-on the robot runs its own routine — the little
        head-tilt gimmick — and the daemon is either unreachable or reports a
        state other than 'running'. Sending motion during that makes it
        glitch, so block until the daemon says 'running', then pause
        `startup_wait` seconds for the motion to finish settling."""
        if not self.ok:
            return
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                d = requests.get(self.base + "/api/daemon/status", timeout=4).json()
            except (requests.RequestException, ValueError):
                d = {}
            if d.get("state") == "running":
                break
            print(f"  waiting for the robot daemon (state={d.get('state')!r})...")
            time.sleep(2)
        else:
            print("  daemon never reported 'running' — continuing anyway.")
        if self.startup_wait > 0:
            print(f"  giving it {self.startup_wait:.0f}s to finish starting up "
                  f"(--startup-wait to change)...")
            time.sleep(self.startup_wait)

    def set_volume(self, level: int) -> None:
        if not self.ok:
            return
        try:
            r = self._post("/api/volume/set", timeout=5, json={"volume": int(level)})
            got = r.json().get("volume") if r.ok else None
            print(f"Speaker volume set to {got if got is not None else level}.")
        except (requests.RequestException, ValueError) as e:
            print(f"  (couldn't set volume: {e})")

    def say(self, audio: bytes) -> float:
        """Upload an audio clip (wav or mp3) and play it. Returns its
        duration in seconds (0 if it can't be determined, e.g. mp3)."""
        ext, mime = _audio_kind(audio)
        dur = _wav_duration(audio) if ext == "wav" else 0.0
        if not self.ok:
            return dur
        try:
            name = f"reply_{int(time.time() * 1000)}.{ext}"
            self._post("/api/media/sounds/upload", timeout=15,
                       files={"file": (name, audio, mime)})
            self._post("/api/media/play_sound", timeout=10, json={"file": name})
        except requests.RequestException as e:
            print(f"  (couldn't play on the robot: {e})")
        return dur

    def _goto(self, *, pitch=0.0, yaw=0.0, roll=0.0, antennas=None, duration=0.5):
        if not (self.motion and self.ok):
            return
        body = {"head_pose": {"roll": roll, "pitch": pitch, "yaw": yaw},
                "duration": duration, "interpolation": "minjerk"}
        if antennas is not None:
            body["antennas"] = list(antennas)
        try:
            self._post("/api/move/goto", timeout=5, json=body)
        except requests.RequestException:
            pass

    def wake(self):
        if not self.ok:
            return
        try:
            # The daemon starts (and lands here after goto_sleep) with the
            # motors DISABLED — every move command silently accepted but
            # ignored. Enable them before anything else.
            self._post("/api/motors/set_mode/enabled", timeout=5)
            if self.motion:
                self._post("/api/move/play/wake_up", timeout=5)
                time.sleep(4.0)
                self.neutral()
                time.sleep(0.8)
        except requests.RequestException:
            pass

    def listening_pose(self):
        self._goto(pitch=-0.12, roll=0.10, antennas=(0.5, -0.5), duration=0.6)

    def nod_while_speaking(self, seconds: float):
        if not (self.motion and self.ok):
            time.sleep(max(seconds, 0.2))
            return
        end = time.time() + max(seconds, 0.4)
        down = True
        while time.time() < end:
            self._goto(pitch=0.10 if down else -0.02,
                       antennas=(0.25, -0.25) if down else (-0.15, 0.15),
                       duration=0.45)
            down = not down
            time.sleep(0.5)

    def neutral(self):
        self._goto(pitch=0.0, yaw=0.0, roll=0.0, antennas=(0.0, 0.0), duration=0.7)

    def sleep(self):
        if self.motion and self.ok:
            try:
                self._post("/api/move/play/goto_sleep", timeout=5)
            except requests.RequestException:
                pass


def _wav_duration(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        return 0.0


def _audio_kind(data: bytes) -> tuple[str, str]:
    if data[:4] == b"RIFF":
        return "wav", "audio/wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3", "audio/mpeg"
    return "wav", "audio/wav"


def _pcm_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Robot microphone (needs the reachy_mini SDK, hence this lives in robot/,
# the numpy>=2.2.5 environment)
# --------------------------------------------------------------------------
class RobotMic:
    """The robot's on-board mic, over the SDK's audio stream. A background
    thread keeps the stream drained; start()/stop() bracket the slice we
    actually keep."""

    def __init__(self, host: str):
        import numpy as np
        from reachy_mini import ReachyMini
        self._np = np
        self._cm = ReachyMini(media_backend="default", host=host, port=8000,
                              connection_mode="network")
        self.mini = self._cm.__enter__()
        self.mini.media.start_recording()
        self._buf: list = []
        self._collecting = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        while not self._stop.is_set():
            s = self.mini.media.get_audio_sample()
            if s is None:
                time.sleep(0.005)
            elif self._collecting:
                self._buf.append(self._np.asarray(s, dtype=self._np.float32))

    def start(self):
        self._buf = []
        self._collecting = True

    def stop(self) -> bytes:
        self._collecting = False
        time.sleep(0.05)
        if not self._buf:
            raise RuntimeError("the robot microphone returned no audio")
        a = self._np.concatenate(self._buf, axis=0)
        mono = a[:, 0] if a.ndim == 2 else a
        pcm16 = (self._np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        return _pcm_to_wav(pcm16, 16000)

    def close(self):
        self._stop.set()
        try:
            self.mini.media.stop_recording()
        except Exception:
            pass
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
def _gemini_post(model: str, body: dict, api_key: str, timeout: float = 60.0) -> dict:
    """POST to a Gemini model, retrying transient failures (429/500/503,
    dropped connections). Always raises RuntimeError on give-up."""
    url = f"{GEMINI_BASE}/{model}:generateContent"
    headers = {"x-goog-api-key": api_key}
    last = None
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            last = None
            if attempt < 3:
                wait = 2.0 * (attempt + 1)
                print(f"  (network problem talking to Gemini: {e.__class__.__name__}; "
                      f"retrying in {wait:.0f}s...)")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini API unreachable after retries: {e}") from e
        if r.ok:
            return r.json()
        last = r
        if r.status_code in (429, 500, 503) and attempt < 3:
            wait = _retry_delay(r) or 1.5 * (attempt + 1)
            if wait > 30:  # a long wait means a daily quota, not a transient blip
                break
            print(f"  (Gemini busy/rate-limited, waiting {wait:.0f}s...)")
            time.sleep(wait)
            continue
        break
    try:
        msg = last.json()["error"]["message"]
    except (ValueError, KeyError, AttributeError):
        msg = last.text[:300] if last is not None else "no response"
    raise RuntimeError(f"Gemini API {getattr(last, 'status_code', '?')}: {msg}")


def _retry_delay(r: requests.Response) -> float:
    try:
        for d in r.json()["error"].get("details", []):
            if d.get("@type", "").endswith("RetryInfo"):
                return float(d["retryDelay"].rstrip("s"))
    except (ValueError, KeyError, AttributeError):
        pass
    return 0.0


def user_turn_parts(audio_wav: bytes | None, text: str | None) -> list:
    if audio_wav is not None:
        return [
            {"inlineData": {"mimeType": "audio/wav",
                            "data": base64.b64encode(audio_wav).decode()}},
            {"text": "The person just said this to you (audio above). "
                     "Transcribe it, then reply."},
        ]
    return [{"text": text}]


def gemini_json(api_key: str, model: str, system_prompt: str, contents: list,
                 schema: dict, temperature: float = 0.9) -> dict:
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"responseMimeType": "application/json",
                             "responseSchema": schema, "temperature": temperature},
    }
    data = _gemini_post(model, body, api_key)
    return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])


def gemini_converse(api_key: str, model: str, history: list,
                     audio_wav: bytes | None, text: str | None) -> tuple[str, str]:
    contents = history + [{"role": "user", "parts": user_turn_parts(audio_wav, text)}]
    parsed = gemini_json(api_key, model, SYSTEM_PROMPT, contents, REPLY_SCHEMA)
    transcript = (parsed.get("transcript") or "").strip()
    reply = (parsed.get("reply") or "").strip()
    if text is not None and not transcript:
        transcript = text
    return transcript, reply


def gemini_tts(api_key: str, model: str, text: str, voice: str) -> bytes:
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    data = _gemini_post(model, body, api_key)
    part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    rate = 24000
    for chunk in part.get("mimeType", "").split(";"):
        if chunk.strip().startswith("rate="):
            rate = int(chunk.split("=")[1])
    return _pcm_to_wav(base64.b64decode(part["data"]), rate)


def google_translate_tts(text: str, lang: str) -> bytes:
    """Keyless fallback TTS. mp3 bytes; the robot accepts mp3."""
    clip = text[:200]
    r = requests.get(
        "https://translate.google.com/translate_tts",
        params={"ie": "UTF-8", "client": "tw-ob", "tl": lang, "q": clip,
                "total": 1, "idx": 0, "textlen": len(clip)},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
    )
    r.raise_for_status()
    return r.content


class Voice:
    """Turns text into audio bytes. 'google' is keyless (no Gemini quota);
    'gemini' sounds better but is a second API call. Falls back to google
    permanently once a Gemini TTS call fails."""

    def __init__(self, api_key: str, engine: str = "google",
                 model: str = DEFAULT_TTS_MODEL, voice: str = "Kore", lang: str = "en"):
        self.api_key, self.model, self.voice, self.lang = api_key, model, voice, lang
        self._use_gemini = engine == "gemini"

    def synth(self, text: str) -> bytes | None:
        if self._use_gemini:
            try:
                return gemini_tts(self.api_key, self.model, text, self.voice)
            except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
                print(f"  (Gemini TTS unavailable, falling back to google: {e})")
                self._use_gemini = False
        try:
            return google_translate_tts(text, self.lang)
        except requests.RequestException as e:
            print(f"  (no audio this turn: {e})")
            return None


def say_and_nod(robot: "Robot", voice: "Voice", text: str) -> None:
    """Speak `text` on the robot and nod along for roughly its length. The
    one function a future proactive fatigue-nudge script needs — no
    interactive loop required, just setup() then this."""
    wav = voice.synth(text)
    if wav:
        dur = robot.say(wav)
        dur = dur or max(1.5, len(text) * 0.06)  # mp3 has no easy duration
        robot.nod_while_speaking(dur)
    robot.neutral()


# --------------------------------------------------------------------------
def get_user_turn(args, robot: "Robot", mic) -> tuple[bytes | None, str | None]:
    """Prompt for one user turn. Returns (audio_wav, text) — exactly one
    set. Returns (None, None) if nothing usable was captured."""
    if args.text:
        try:
            t = input("you > ").strip()
        except EOFError:
            return None, None
        return (None, t) if t else (None, None)

    input("you > (Enter to start) ")
    robot.listening_pose()
    mic.start()
    input("      listening... (Enter to stop) ")
    try:
        return mic.stop(), None
    except RuntimeError as e:
        print(f"  {e}")
        return None, None


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--robot-host", default=os.environ.get("REACHY_MINI_HOST", "localhost"),
                    help="robot daemon address: 'localhost' for a simulator or USB robot "
                         "on this machine, or an IP for a wireless robot "
                         "(default: $REACHY_MINI_HOST or localhost)")
    p.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY"),
                    help="Gemini API key (default: $GEMINI_API_KEY, e.g. from .env)")
    p.add_argument("--model", default=DEFAULT_CHAT_MODEL,
                    help=f"Gemini model for the conversation (default: {DEFAULT_CHAT_MODEL})")
    p.add_argument("--tts", choices=("google", "gemini"), default="google",
                    help="voice engine: 'google' is keyless and doesn't touch your "
                         "Gemini quota (default); 'gemini' sounds better but costs one "
                         "extra API call per reply.")
    p.add_argument("--tts-model", default=DEFAULT_TTS_MODEL,
                    help=f"Gemini TTS model, with --tts gemini (default: {DEFAULT_TTS_MODEL})")
    p.add_argument("--voice-name", default="Kore",
                    help="Gemini TTS voice: Kore, Puck, Charon, Aoede, ... (default: Kore)")
    p.add_argument("--lang", default="en", help="language for the fallback TTS (default: en)")
    p.add_argument("--text", action="store_true",
                    help="type messages instead of speaking (no microphone needed)")
    p.add_argument("--no-motion", action="store_true", help="don't move the head/antennas")
    p.add_argument("--startup-wait", type=float, default=6.0, metavar="SEC",
                    help="after connecting, wait this long for the robot's power-on "
                         "routine to settle before driving it (default: 6; use 0 if "
                         "it's already warmed up)")
    p.add_argument("--volume", type=int, default=100, metavar="0-100",
                    help="robot speaker volume (default: 100; pass -1 to leave it untouched)")


def setup(args) -> tuple["Robot", object, "Voice"]:
    """Shared startup: validate the key, connect the robot, set volume, open
    the microphone. Returns (robot, mic_or_None, voice). Exits on fatal
    problems. If the robot isn't reachable, continues voice-only (handy for
    testing the Gemini key alone before the robot is wired up)."""
    if args.volume is not None and args.volume >= 0 and not 0 <= args.volume <= 100:
        sys.exit("--volume must be between 0 and 100 (or -1 to leave it untouched)")
    if not args.api_key:
        sys.exit("No Gemini API key. Put GEMINI_API_KEY=... in .env "
                 "(see .env.example), or pass --api-key.")

    robot = Robot(args.robot_host, enable_motion=not args.no_motion,
                  startup_wait=args.startup_wait)
    if robot.connect():
        print(f"Connected to the robot at {args.robot_host}.")
        robot.wait_ready()
        if args.volume is not None and args.volume >= 0:
            robot.set_volume(args.volume)
    else:
        print(f"Robot not reachable at {args.robot_host} — voice-only, no robot.")

    mic = None
    if not args.text:
        try:
            print("Opening the robot's microphone...")
            mic = RobotMic(args.robot_host)
        except Exception as e:
            sys.exit(f"Couldn't open the robot mic ({e}).\nUse --text instead, "
                     f"or check the robot/simulator is running (see the README).")

    voice = Voice(args.api_key, args.tts, args.tts_model, args.voice_name, args.lang)
    return robot, mic, voice


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    args = p.parse_args()

    robot, mic, voice = setup(args)
    robot.wake()
    history: list = []
    prompt_hint = "type a message" if args.text else "press Enter, speak, press Enter again"
    print(f"\nReady. {prompt_hint}. Ctrl+C to quit.\n")

    try:
        while True:
            audio, user_text = get_user_turn(args, robot, mic)
            if audio is None and not user_text:
                if args.text:
                    break
                continue

            try:
                transcript, reply = gemini_converse(
                    args.api_key, args.model, history, audio, user_text)
            except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
                print(f"  Gemini error: {e}")
                robot.neutral()
                continue

            if transcript and not args.text:
                print(f"      heard: {transcript}")
            print(f"eepy> {reply}\n")

            history.append({"role": "user", "parts": [{"text": transcript or user_text or "..."}]})
            history.append({"role": "model", "parts": [{"text": reply}]})
            history[:] = history[-12:]

            say_and_nod(robot, voice, reply)
    except KeyboardInterrupt:
        print()
    finally:
        if mic is not None:
            mic.close()
        robot.neutral()
        time.sleep(0.3)
        robot.sleep()
        print("bye.")


if __name__ == "__main__":
    main()
