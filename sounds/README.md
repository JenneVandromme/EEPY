# sounds/

Every sound EEPY can play. A behaviour refers to a file by its plain
filename, e.g.

```yaml
- head: { pitch: 4 }
  duration: 0.5
  sound: questioning.wav
```

No folders, no paths — just the filename, and the file sits directly in here.

## Files the behaviours currently expect

| File | Used by | What it should sound like |
|---|---|---|
| `questioning.wav` | refocus level 2 | A short rising "hmm?". Wordless, curious, under a second. |
| `yawn.wav` | bedtime levels 1, 2 | A soft yawn. Should be contagious, not comedic. |
| `soft_chime.wav` | bedtime level 2 | One gentle tone. Noticeable but not an alarm. |
| `goodnight.wav` | bedtime level 5 | A short settling-down sound. Think a sigh, not a jingle. |

> **The four files in here right now are silent placeholders.** They are real,
> valid WAV files so that the validator and CI pass, but they make no sound.
> Replace them with the real thing — same filename, and nothing else needs to
> change.

If you add a *new* sound, put the file here **and** reference it from a
behaviour. The validator fails if a behaviour names a file that is not here,
which is deliberate: a missing sound should never reach the robot.

## Format

The robot's audio player accepts:

```
.wav  .mp3  .ogg  .oga  .opus  .flac  .m4a  .aac
```

Prefer **`.wav`, mono, 22050 Hz or 44100 Hz, 16-bit**. It is uncompressed, so
it starts instantly — an MP3 can take a moment to decode, and for a sound
meant to land on the same beat as a movement that delay is audible.

Keep them **short**. Under two seconds for anything on a ladder: the sound
should land inside the movement, not outrun it.

## A note on timing

The robot plays sound through GStreamer, which adds a small, constant delay
before audio reaches the speaker. The SDK exposes an `audio_lead_s` parameter
to start the audio slightly *before* the motion to compensate. The behaviour
player will deal with that; it is not something to correct for by padding
silence onto the front of your file.

## Sounds already built into the robot

The SDK ships its own: `confused1`, `count`, `dance1`, `go_sleep`,
`impatient1`, `wake_up`. They are not in this folder and cannot be referenced
with `sound:`. Two of them play by themselves — `wake_up.wav` during
`wake_up()`, and `go_sleep.wav` during `goto_sleep()`, which is what bedtime
level 5 hands over to. That is why bedtime level 5 has no `sound:` of its own.
