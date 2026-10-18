# How the robot's voice works

This package turns lines from `behaviors/lines.yaml`, and free text from the
Wizard panel, into speech — out of the robot's speaker, or this computer's.

> **Choosing a voice?** See "Choosing the robot's voice" in
> [docs/BEHAVIORS_GUIDE.md](../../docs/BEHAVIORS_GUIDE.md). This page is about
> how it is built, and why.

---

## What it does

```
 behaviour keyframe  ─┐                                   ┌──► robot speaker (via the daemon)
 panel line button   ─┼─► Speaker ─► words ─► audio clip ─┤
 panel free text     ─┘   (own thread)  │   cached? else   └──► this computer (one open stream)
                                        │   Piper, live
                          LineSelector ─┘
```

1. **Choose the words.** `speak_line("refocus_check", "direct")` picks a
   wording at random — never the same one twice in a row — and fills in
   placeholders like `{minutes}`. This happens in the caller, so a mistake
   raises at once with an explanation.
2. **Get the audio.** Fixed wordings were generated ahead of time and come from
   the cache. Free text and filled-in placeholders are made live by Piper
   (about 30–100 ms).
3. **Play it**, on the speaker's own thread: the robot's speaker when connected
   to one, otherwise this computer's.
4. **Report** every step as an event: requested, started (with latency),
   finished, interrupted, failed.

## The pieces

| File | Job |
|---|---|
| `lines.py` | `LineSelector`: which wording, never twice in a row; placeholders. |
| `engines.py` | Text → audio: `piper`, `system` (macOS `say` / Windows SAPI / espeak), `google`, `gemini`. |
| `cache.py` | Pre-generated clips, named by a hash of engine + voice + text. |
| `outputs.py` | Where audio goes: `robot`, `desktop`, `none`; `auto` picks (the real robot's speaker; with the dry run or the simulator, this computer's speakers directly: the simulator's own audio stayed silent on macOS). |
| `speaker.py` | `Speaker`: one voice at a time, never blocks, reports everything. |
| `audio.py` | How long a clip is — nothing reports when playback ends. |
| `download.py` | Piper voices, with progress and resume. |
| `voices.py` | `python -m eepy.speech.voices` — hear every voice, pick one. |

Settings live in `config/eepy.yaml` under `speech:` (engine, voice, fallback,
output, pregenerate).

## Choosing the engine — measured, not guessed

Measured on the development Mac (Apple Silicon), same sentences for each:

| | **Piper** (local, neural) | macOS `say` | Google Translate | Gemini |
|---|---|---|---|---|
| Short line | **18 ms** | 400–550 ms | 360–1270 ms | not measured (no key) |
| Long free text | **95 ms** | 410–560 ms | 1.2–1.4 s | not measured |
| Offline | **yes** | yes | no | no |
| Windows / older Mac / Linux | **yes / yes / yes** | Mac only | online | online |
| Risk | none known | none | unofficial endpoint | quota, needs a key |

**Piper is the default**, with each operating system's own voice as the
offline fallback. The cloud engines stay out of the live path: the `voices`
tool offers them, and either can be configured, but a session should not
depend on the internet. Voice *quality* is a matter of listening, not
measuring — which is what the `voices` tool is for.

## The main decisions, and why

**Words are chosen in the caller; audio happens on the speaker's thread.** A
missing `{minutes}` is the caller's mistake and should fail in their face, at
once. Synthesis and playback must never hold anyone up.

**A wording that can't be filled is skipped, not fatal.** Lines mix wordings:
"Let's take five." next to "…in {minutes} minutes?". Without a value, the first
one is fine. Only when *no* wording of the requested tone can be filled is it an
error — one that names the missing value.

**One voice at a time; the newest wins.** A new line cuts the current one;
anything still waiting is dropped. A behaviour interrupting another cuts its
line too, and STOP cuts all speech — even when nothing else is playing.

**`stop()` never blocks.** The player calls it from its 50 Hz motion loop, so
it only sets a flag; the speaker's thread cuts the audio within about 20 ms.

**Speech beats sound effects.** The robot plays one clip at a time, so while it
is speaking a keyframe's sound effect is skipped. The validator warns when a
keyframe asks for both.

**Pre-generate everything fixed; cache by content.** A clip is named after a
hash of engine, voice and text, so editing a wording regenerates exactly that
clip and switching voice regenerates all of them — no bookkeeping. On a robot,
every clip is also uploaded at connect, so the first playback doesn't wait for
Wi-Fi.

**Finished is worked out, not reported.** The robot's daemon never says when
a sound ends, so a clip counts as finished once its own length has passed (WAV
lengths are exact). On this computer, the stream reports the end directly.

---

## Roadblocks, and how we got around them

**1. The robot plays one sound at a time.**
The daemon's `play_sound` replaces whatever is playing, so a sound effect
would cut a sentence off — or a sentence a sound. *Fix:* speech wins; sound
effects are skipped while speaking; the validator warns; bedtime level 5's
sound moved to the keyframe after its words.

**2. Nothing reports when playback ends.**
Neither the daemon nor its web API says "done". *Fix:* clip length from the
file itself, plus a small allowance for the robot's audio pipeline.

**3. STOP didn't cover speech-only moments.**
The player only stopped sound when it interrupted a *behaviour*. A line spoken
from the panel, with nothing else playing, would have kept talking through
STOP. *Fix:* STOP always cuts speech.

**4. Piper's downloader looks like a hang.**
A 60 MB voice took nine minutes on the lab Wi-Fi with no output at all. *Fix:*
our own downloader, with a progress bar, resume, and a size check.

**5. The first live sentence was slow.**
With every line already cached, nothing loaded the Piper model at start-up,
so the first free text paid for it: 561 ms. *Fix:* the model is warmed up
during start-up — first live sentence now about 75 ms.

**6. Desktop playback lagged by most of a second.**
A separate player per clip (`afplay`) measured about **0.8 s** of overhead. An
in-process library (`sounddevice`) was far better — but its `play()` closes the
previous stream first, which on macOS blocks about **100 ms** on every clip but
the first. *Fix:* one audio stream, opened once and kept open, playing silence
when idle; a new clip just replaces what it reads. Measured through the panel:
**1.6–2.6 ms** from button press to audio for a cached line (was 29–119 ms),
and STOP is instant.

**7. Google's request limit.**
The endpoint refuses more than 200 characters, and an early version sent one
request per sentence. *Fix:* sentences are packed into as few requests as fit.

**8. The system voices include Apple's joke voices.**
"Bubbles", "Boing" and friends, one after another, are noise to someone
choosing a voice. *Fix:* left out of the list unless `--all-system` asks.

## Measured

| | Result |
|---|---|
| All 19 fixed wordings in `lines.yaml`, generated | 0.9 s |
| Cached line → audio (this computer) | 1.6–2.6 ms |
| Cached line → robot daemon (simulator, local) | about 25 ms |
| Free text → audio (Piper, live) | 75–110 ms |
| STOP → speech cut | 7 ms (panel key press to cut) |

## Not verified yet

- **Windows.** The SAPI voice, `winsound` and `sounddevice` paths are written
  from documentation, not run. Expect the first Windows teammate to find
  something.
- **The real robot's speaker and Wi-Fi.** Tested through the simulator's
  daemon, which is the same code path, on one machine.
- **Gemini.** No API key was available; it uses the same request as
  `robot/conversation.py`.
- **How the voices *sound*.** That is for the designers to judge.
