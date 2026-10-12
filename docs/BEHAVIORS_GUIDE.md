# Writing EEPY's behaviours

This is the guide for editing what the robot *does*. You do not need to know
Python, and you do not need the robot on your desk.

Everything lives in two folders:

```
behaviors/     what the robot does   (the files you edit)
sounds/        the sounds it plays
```

---

## Contents

1. [The idea in one minute](#the-idea-in-one-minute)
2. [Your first edit](#your-first-edit)
3. [How a behaviour is put together](#how-a-behaviour-is-put-together)
4. [Every field you can use](#every-field-you-can-use)
5. [The ranges — how far each joint goes](#the-ranges--how-far-each-joint-goes)
6. [Building blocks](#building-blocks)
7. [Words](#words)
8. [Checking your work](#checking-your-work)
9. [Common mistakes](#common-mistakes)

---

## The idea in one minute

EEPY nudges a student in **ladders**. A ladder is a set of rungs from "barely
noticeable" to "impossible to miss", and the robot climbs it only as far as it
needs to.

There are two:

| Ladder | Levels | What it is for |
|---|---|---|
| `refocus` | 0–4 | The student has drifted off their work |
| `bedtime` | 0–5 | It is late and they should sleep |

Level 0 is almost nothing. The top level says something out loud, or goes to
sleep. **Every level must exist** — the decision logic asks for "bedtime,
level 3" and there has to be something there.

In the **rules** mode the robot climbs these ladders **on its own**. It waits
for a reaction after each level, goes one level up when there is none, and
starts again once the student is back on task. Level 0 is the background
loop while in that role. The timings are in `config/escalation.yaml`, explained
in [eepy/interaction/README.md](../eepy/interaction/README.md).

**Role switches** (optional): a behaviour named `role_to_refocus`,
`role_to_bedtime` or `role_to_study_buddy` (e.g. in a new
`behaviors/roles.yaml`) is played automatically when the robot switches into
that role. None exist yet: design them if you like.

The files:

```
behaviors/
  _blocks.yaml     shared pieces every behaviour reuses  (start here to read)
  idle.yaml        quiet background presence
  refocus.yaml     the refocus ladder, levels 0-4
  bedtime.yaml     the bedtime ladder, levels 0-5
  lines.yaml       everything the robot can say
```

One file per ladder, so three of us can edit at the same time without
colliding.

---

## Your first edit

Say you want the curious head tilt to be a bit bigger. In
`behaviors/refocus.yaml`, find `refocus_curious` and the line `roll: 12`.
Change it to `roll: 18`. That is the whole edit.

Before you push, check it. The tools live in the project's environment, so
activate that first:

```bash
source .venv-robot/bin/activate        # Windows: .venv-robot\Scripts\Activate.ps1

python -m eepy.behaviors.validate                   # is it valid and reachable?
python -m eepy.behaviors.preview refocus_curious    # what will it do?
```

(Forget the first line and the tools will tell you so — they will not throw a
traceback at you.)

A check called **behaviours** also runs on every pull request, so a broken file
cannot be merged even if you forget. When it fails, its log names the file, the
behaviour and the keyframe, and usually suggests the fix — the same output the
validator gives you locally.

### Watching it move

This is the loop for tuning: edit a number, save, watch it happen in the
simulator. Two terminals, both in the EEPY folder with `.venv-robot` active.

**Terminal 1 — the simulator.** A window opens with the robot in it. Leave it
running.

```bash
mjpython $(which reachy-mini-daemon) --sim      # macOS
reachy-mini-daemon --sim                        # Linux / Windows
```

**Terminal 2 — play the behaviour, in watch mode:**

```bash
python -m eepy.behaviors.play refocus_curious --backend sim --watch
```

It plays once, then waits. Now edit `behaviors/refocus.yaml` and **save** —
it replays straight away with your change. Press **Enter** to replay without
changing anything. **Ctrl+C** returns the robot to neutral and quits.

If you save a mistake, it explains it exactly like the validator does and
keeps playing the last good version until you fix it. You cannot break the
running session by saving a typo.

Useful extras:

```bash
python -m eepy.behaviors.play --ladder bedtime --level 2 --backend sim --watch
python -m eepy.behaviors.play refocus_curious --backend sim --intensity 0.5
python -m eepy.behaviors.play refocus_verbal --backend sim --tone direct
```

Put `EEPY_BACKEND=sim` in your `.env` and you can leave off `--backend sim`.
Without any backend, the tool uses `dry`: nothing moves, and it just prints
when each keyframe, sound and line would happen.

**Two things you will notice in the simulator:**

- **The head lags a little behind what you wrote.** The simulated motors have
  weight and take time to get there, so a fast move looks softer and slightly
  later than its numbers — up to about 15 degrees behind on a quick cartoon
  flick. The real robot will do the same. Judge by eye, not by the numbers.
- **Some moves take longer than written.** If a behaviour starts while the
  robot is somewhere unusual — antennas still drooped from a bedtime nudge, say
  — a quick move from there could be dangerously fast, so the player makes it
  take longer and tells you so ("slowed down to stay under the speed limit").
  It never changes *where* the move goes, only how long it takes.

In the simulator, sounds go to your computer's own audio output. The files
in `sounds/` are still silent placeholders, so you will hear nothing until
someone replaces them with real recordings.

---

## How a behaviour is put together

A behaviour is a **list of keyframes**. Each keyframe is one pose plus how long
to take getting there. The robot slides smoothly from one to the next.

```yaml
behaviors:

  - name: refocus_curious          # its unique name
    ladder: refocus                # which ladder
    level: 2                       # which rung
    description: >
      A questioning head tilt with a small sound.
    loop: once
    keyframes:
      - use: glance_at_user        # reuse a ready-made piece
      - head: { roll: 18 }         # or write a pose yourself
        duration: 0.5
        sound: questioning.wav
      - use: return_to_neutral
```

Three things to know:

**Durations add up.** `duration` is how long to *move into* this keyframe, not
a clock time. So you can insert a keyframe without renumbering anything.

**Anything you leave out stays as it was.** If keyframe 1 sets the antennas and
keyframe 2 does not mention them, they stay where keyframe 1 put them.

**Order is top to bottom.** That is the whole timeline.

---

## Every field you can use

### On the behaviour

| Field | Required | What it does |
|---|---|---|
| `name` | yes | Unique. Lower-case letters, numbers, underscores. |
| `description` | yes | One or two sentences, for humans. |
| `ladder` | no | `refocus` or `bedtime`. Needs `level` too. |
| `level` | with `ladder` | Which rung, counting from 0. |
| `group` | no | A free label for tidiness. |
| `loop` | no | `once` (default), `forever`, or a number like `3`. |
| `intensity` | no | `0.0`–`1.0`. Shrinks every angle. `1.0` is as written. |
| `keyframes` | yes | The list below. |

### On a keyframe

A keyframe is **exactly one** of three things.

**1. A pose:**

```yaml
- head:
    yaw: -8          # + turns LEFT,  - turns RIGHT
    pitch: 6         # + chin DOWN,   - chin UP
    roll: 12         # + tips toward the LEFT ear
    z: -4            # height in mm. + rises, - sinks
  antennas:
    right: -25       # mirrored! see the warning below
    left: 25
  body_yaw: 0        # whole body turns. + is left
  duration: 0.5      # REQUIRED, seconds
  easing: cartoon    # how it accelerates, see below
  hold: 0.8          # extra seconds to sit still afterwards
  sound: questioning.wav   # a file in sounds/
  say: refocus_check       # an ID from lines.yaml
  relative: false    # see "relative" below
```

Instead of `head:` you can write **`look_at:`** with a named direction:

```yaml
- look_at: notes     # user | notes | screen | away
  duration: 0.6
```

**2. A reused block:**

```yaml
- use: nod_small
  scale: 0.6         # optional, 0.0-1.0, makes the whole block smaller
  sound: yawn.wav    # optional, plays as the block starts
  say: refocus_check # optional, same
```

You cannot put `duration:`, `easing:` or `hold:` next to `use:` — the block
already carries its own timing, so it would be unclear which one wins. To make
a block slower, change it in `_blocks.yaml`; to make it smaller, use `scale:`.

**3. A handover to the robot's own built-in routine:**

```yaml
- action: goto_sleep
  duration: 2.0
```

`goto_sleep` must be the **last** keyframe — nothing can follow it.

### `easing` — how the movement accelerates

| Value | Feels like |
|---|---|
| `minjerk` | Smooth and natural. The default; use it unless you want otherwise. |
| `ease_in_out` | Gentle start and stop. Good for slow, calm movements. |
| `linear` | Constant speed. Looks mechanical — usually a mistake. |
| `cartoon` | Snappy, with a bit of overshoot. Good for a surprised tilt. |

### `relative` — offsets instead of positions

Normally the numbers are absolute: `pitch: 10` means "chin at 10 degrees".

With `relative: true` they are **offsets from wherever the head already is**.
Offsets add up as you go, so `+8` then `-8` goes down and comes back to where
it started. That is how a nod works from any pose:

```yaml
- head: { pitch: 8 }     # 8 degrees DOWN from here
  relative: true
  duration: 0.25
```

Most blocks in `_blocks.yaml` are relative for exactly this reason.

### ⚠️ The antennas are mirrored

To make the antennas look **symmetric**, give them equal-and-opposite numbers:

```yaml
antennas: { right: -45, left: 45 }     # symmetric
antennas: { right: 45,  left: 45 }     # lopsided!
```

Their resting position is `right: -10, left: 10`, not zero. The robot's own
software offsets them slightly off vertical on purpose, because they shake
when they are perfectly upright.

Bigger mirrored numbers = folded further over. `-150 / 150` is nearly asleep.

---

## The ranges — how far each joint goes

**Write a number outside these and the check fails.** Nothing is quietly
corrected for you — a wrong number is a mistake worth seeing, not something to
paper over.

| What | Range | Notes |
|---|---|---|
| `head.yaw` | −180 … 180 | But see the neck rule below. |
| `head.pitch` | −40 … 40 | + is chin down. |
| `head.roll` | −40 … 40 | + tips toward the left ear. |
| `head.z` | −45 … 20 mm | **The head sinks much further than it rises.** |
| `antennas.right/left` | −180 … 180 | Mirrored. Rest is −10 / +10. |
| `body_yaw` | −160 … 160 | |
| `duration` | above 0, up to 60 s | |
| `hold` | 0 … 60 s | |
| `intensity`, `scale` | 0.0 … 1.0 | |

**The neck rule.** The head cannot twist more than **65°** away from the body.
So `yaw: 90` with `body_yaw: 0` fails — turn the body too:

```yaml
- head: { yaw: 90 }
  body_yaw: 60        # now the head is only 30 deg from the body
  duration: 1.0
```

### The one that surprises everyone

**Two numbers can each be fine and still be impossible together.**

The head sits on six short legs. Tilting it and raising it use up *the same*
leg travel. So:

- `pitch: 35` on its own — fine.
- `z: 18` on its own — fine.
- `pitch: 35` **and** `z: 18` together — **impossible.**

The checker knows this. It asks the robot's real geometry whether each pose
can actually be held, and says so:

```
✗ behaviors/bedtime.yaml
    in behaviour "bedtime_subtle", keyframe 2
    the robot cannot physically reach this pose (yaw 0, pitch 35, roll 0, height 18 mm).
    → Each value is inside its own limit, but not all together -- the head is
      on six short legs, and tilting it uses up the same travel as raising it.
      Try a smaller tilt, or bring the height closer to 0.
```

If you hit this: make the tilt smaller, or move `z` closer to 0. There is the
most room when `z` is between −10 and −25, where the head can tilt a long way.

---

## Building blocks

`behaviors/_blocks.yaml` holds small named movements. Reuse them instead of
copying keyframes.

(The leading underscore is not decoration: it is how the tools know this file
holds shared pieces rather than behaviours. Do not rename it, and do not start
a behaviour file's name with one.)



| Block | What it does |
|---|---|
| `nod_small`, `nod_slow` | One nod, quick or heavy. |
| `glance_at_user`, `glance_at_notes` | Look up at them, or down at the work. |
| `head_tilt_curious` | The sideways "hmm?" tilt. |
| `antenna_perk` | Antennas up and alert. |
| `antenna_droop`, `antenna_droop_deep` | Tired, and nearly asleep. |
| `breathe_in`, `breathe_out` | The two halves of the idle breathing. |
| `yawn` | A yawn, read as a stretch. |
| `head_sag` | The head sinking the way a tired head does. |
| `return_to_neutral` | Back to calm and level. |

Changing a block changes **every** behaviour that uses it. That is the point,
but tread carefully.

To add one, copy an existing block and give it a new name. Blocks cannot use
other blocks — keep them flat.

**Gaze targets** are in the same file. If the robot gets moved on the desk and
is looking slightly wrong, fix the angle once in `gaze_targets:` and every
behaviour follows.

---

## Words

`behaviors/lines.yaml`. A behaviour never contains words — it references a
line by ID with `say:`, and the words live in one place.

Every line comes in two tones:

- **`hedged`** — when the system is *not sure*. Softer, a guess, easy to brush
  off. *"Losing focus a bit?"*
- **`direct`** — when it *is* sure. Shorter, more certain. *"Back to it?"*

Each tone holds a **list**, and one is picked at random so the robot does not
repeat itself.

```yaml
  refocus_check:
    description: Level 3 of the refocus ladder.
    variants:
      hedged:
        - "Losing focus a bit?"
        - "Still with it?"
      direct:
        - "Back to it?"
```

**Always put the words in quotes.** Without them, YAML turns `no` into
`false`, and any line containing a colon breaks the file.

You can use two placeholders, filled in when the robot speaks:

- `{minutes}` — a number of minutes
- `{evidence}` — a short phrase saying why the robot spoke up

Anything else is rejected, so a typo like `{minute}` gets caught.

**Calibration lines.** The lines whose ID starts with `calibration_` (at the end of
`lines.yaml`) are what EEPY says during the two-minute calibration at the start of
a session: `calibration_intro`, `calibration_face_check`, `calibration_look_dot`,
`calibration_look_notes`, `calibration_look_away`, `calibration_baseline_start`,
`calibration_done`, and a few for problems (`calibration_too_dark`,
`calibration_too_far`, ...). Their text is a **placeholder: rewrite it freely**. Keep
the IDs. They get no button on the Wizard panel, because the calibration says them
by itself. What each one is for:
[eepy/perception/calibration/README.md](../eepy/perception/calibration/README.md).

**Escalation lines.** `break_welcome_back` is said when the student comes back
after the break the robot offered (refocus level 4). `wind_down_reminder` is said
one minute before the "15 more minutes" are up (bedtime level 4). Both are
**placeholder text**: rewrite them. When they are used:
[eepy/interaction/README.md](../eepy/interaction/README.md).

---

## Checking your work

Two places, same check: on every pull request, and locally.

**Locally** — you already have everything. The behaviour tools
live in the same environment as the robot code, so if you followed the
README's setup there is nothing extra to install:

```bash
source .venv-robot/bin/activate          # Windows: .venv-robot\Scripts\Activate.ps1
```

If you have not set that up yet, work through **"1. Setting everything up on
your computer"** in the main [README](../README.md) once — you need Step 0,
Step 1 and Step 3. You do not need a robot plugged in, and you do not need
the simulator running — checking and previewing behaviours works entirely
offline.

Then:

```bash
python -m eepy.behaviors.validate      # check every file
```

```
✓ All behaviour files are valid (13 behaviours, 13 blocks, 6 lines).
    ladder bedtime: levels 0-5 complete
    ladder refocus: levels 0-4 complete
```

To **see** a behaviour without the robot:

```bash
python -m eepy.behaviors.preview refocus_joint_attention
python -m eepy.behaviors.preview --ladder bedtime --level 5
python -m eepy.behaviors.preview --list
```

That prints the whole timeline with blocks expanded and `intensity` applied —
the actual numbers the robot will be sent:

```
time         dur        easing        head y/p/r            z mm     antennas R/L     body     sound / say
---------------------------------------------------------------------------------------------------------
 0.00- 1.30  0.80+0.5   ease_in_out      0.0  -12.0    0.0     0.0    -10.0   10.0       0.0
 1.30- 2.70  0.60+0.8   minjerk          0.0   20.0    0.0     0.0    -10.0   10.0       0.0
 2.70- 3.70  1.00       minjerk          0.0    0.0    0.0     0.0    -10.0   10.0       0.0

  ✓ every pose is physically reachable
```

### Errors and warnings

An **error** (`✗`) blocks merging. A **warning** (`!`) does not — it is advice,
usually "this movement is so fast it will look like a twitch".

---

## Common mistakes

**Indentation.** YAML cares. Use **spaces, never tabs** — the check will tell
you if a tab sneaks in. Everything under a `-` lines up with everything else
under that `-`.

**Forgetting the quotes on words.**

```yaml
- "Back to it?"          # good
- Back to it: really?    # breaks the file -- the colon starts a new field
- "no"                   # good
- no                     # becomes false
```

**Forgetting `duration`.** Every keyframe needs one. The only exception is
`use:`, which brings its own.

**Antennas the same sign.** `right: 45, left: 45` is lopsided. Mirror them.

**Expecting `z` to rise as far as it sinks.** It does not: −45 mm down, only
+20 mm up.

**A pose and a `use:` in one keyframe.** Pick one — they are separate
keyframes.

**Writing the same name twice.** Every behaviour's name must be unique across
*all* the files.

**Leaving a gap in a ladder.** 0, 1, 3 is broken — the robot would have
nothing to play at level 2.

**Referencing a sound that is not there.** The file has to land in `sounds/`
alongside the behaviour that names it.

---

## Words and sounds together

A keyframe's `say:` line is spoken when that keyframe **starts** moving, and so
is its `sound:`. The robot can only play one of them at a time, so **while it
is speaking, a sound effect is skipped** — words matter more. The validator
warns if one keyframe asks for both. Put the sound on the keyframe after the
line instead, as `bedtime_goodnight` does.

You hear the lines while tuning with `--watch` too. Add `--no-speech` to see
them as text only.

## Choosing the robot's voice

Hear one line in every available voice, one after another, with each voice's
name shown:

```bash
python -m eepy.speech.voices --line refocus_check        # a line from lines.yaml
python -m eepy.speech.voices --line refocus_3            # whatever refocus level 3 says
python -m eepy.speech.voices --text "Shall we take a break?"
```

More Piper voices — American, British, and Flemish — can be downloaded:

```bash
python -m eepy.speech.voices --list                      # what there is
python -m eepy.speech.voices --download en_GB-alba-medium
```

When you have picked one, put it in `config/eepy.yaml`, under `speech:` — the
tool prints the exact lines to copy. Restart the panel; every line is
regenerated in the new voice by itself.

## Your behaviour on the Wizard panel

Every behaviour automatically becomes a button on the Wizard-of-Oz panel — no
code needed. Behaviours on a ladder go in that ladder's row; the others go
under "Idle & basics". After adding or renaming one, press **Reload files** on
the panel.

To give a new behaviour its own shortcut key, add it to
`config/wizard_panel.yaml` (ladder rungs are bound by ladder and level, others by
name). Until you do, the panel picks a free key and shows a reminder. See
[WIZARD_PANEL_GUIDE.md](WIZARD_PANEL_GUIDE.md).

## If you get stuck

Run the validator. It names the file, the behaviour and the keyframe, and
usually suggests the fix. If the message is confusing, that is a bug in the
checker worth reporting — say so, and it can be made clearer.
