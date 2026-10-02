# How the behaviour system works

This folder turns the YAML files in `behaviors/` into movement on the Reachy
Mini — in the simulator, on the real robot, or as a dry run with no robot at
all. This page is the overview: what the pieces are, why they are built the way
they are, and what got in the way along the way.

> **Editing behaviours?** You want [docs/BEHAVIORS_GUIDE.md](../../docs/BEHAVIORS_GUIDE.md)
> instead. This page is about the machinery underneath.

---

## What it does

```
behaviors/*.yaml  ──►  validate  ──►  resolve  ──►  trajectory  ──►  player  ──►  backend
 (designers)          (is it safe?)  (expand blocks,  (pose at any    (50 Hz,       (sim / real
                                      inherit values)  moment in time)  safety-checked) / dry)
```

1. **Designers write behaviours** in degrees, millimetres and seconds.
2. **The validator** checks them before they can be merged — on every pull
   request, and every time a file is saved in watch mode.
3. **The loader** expands building blocks and fills in values each keyframe
   leaves out, giving a flat list of absolute poses.
4. **The trajectory** turns that list into motion you can sample at any instant,
   using the SDK's own easing curves.
5. **The player** samples it 50 times a second, checks every pose, and sends it.
6. **A backend** delivers it: the MuJoCo simulator, the real robot, or nothing.

## The pieces

| File | Job |
|---|---|
| `schema.py` | The file format, as pydantic models. The single source of truth. |
| `loader.py` | Reads the YAML; expands blocks into a flat timeline (`resolve`). |
| `validate.py` | `python -m eepy.behaviors.validate` — explains problems in plain English. |
| `preview.py` | `python -m eepy.behaviors.preview` — the timeline as a table, no robot. |
| `play.py` | `python -m eepy.behaviors.play` — plays a behaviour; `--watch` replays on save. |
| `player.py` | `BehaviorPlayer`, the API everything else plugs into (WoZ panel, escalation, …). |
| `backends.py` | `DryRunBackend`, `SimBackend`, `RealRobotBackend`. |
| `trajectory.py` | Steps → motion over time. Shared by the player **and** the validator. |
| `safety.py` | The safety rules, written once, used by both validator and player. |
| `easing.py` | The SDK's four easing curves, plus how fast each one peaks. |
| `reach.py` | "Can the head physically hold this pose?" — via the real inverse kinematics. |
| `limits.py` | Joint limits, read from `robot/safety_limits.py` rather than copied. |
| `pose.py` | One full robot pose in human units. |
| `targets.py` | Where "look_at: user" points (`TargetProvider`). |
| `events.py` | What the player reports as it happens, for logging. |

## The main decisions, and why

**YAML, edited by hand.** Readable without Python, comments survive, diffs
review well in a pull request. Its traps — `no` silently becoming `false`,
duplicate keys silently overwriting each other, tabs — are caught by a custom
loader and explained by the validator.

**Validation means "it will play", not "the numbers are in range".** The
validator uses the player's own trajectory and safety code. If a file passes,
the player can perform it.

**Nothing is ever clamped.** A value out of range is an error in the validator
and a refusal in the player — never a quiet fix. The one thing the player is
allowed to change is *how long* a move takes, never *where* it goes (see
roadblock 8).

**The player streams poses itself** rather than asking the SDK to interpolate
(see roadblock 4). One control thread owns the robot; sounds, speech and event
delivery each have their own thread, so nothing slow can ever stall motion.

**Three backends behind one interface**, defaulting to `dry`, so nobody moves a
robot by accident. Sim and real refuse to run against each other.

**Hooks for what comes next.** Speech (A5) plugs into `speech_callback`.
Logging (A7) subscribes to the event bus. Face tracking replaces the
`TargetProvider`. None of them needs to touch the player.

---

## Roadblocks, and how we got around them

**1. Per-axis limits don't mean the robot can reach a pose.**
The head sits on a Stewart platform — six short legs — so tilting the head and
raising it compete for the same leg travel. `pitch: 35` is fine, `z: 18` is
fine, both together are impossible. Measured against the SDK's own inverse
kinematics, **51%** of poses inside the documented safety limits are
unreachable. *Fix:* the validator and player ask the real IK solver whether
each pose is reachable. It takes about 13 µs per pose, so checking every frame
is effectively free.

**2. The IK solver had to run in CI without the whole SDK.**
Installing `reachy-mini` pulls in the simulator, GStreamer and more — far too
heavy for a 30-second check. *Fix:* the solver itself is a small zero-dependency
wheel (`reachy_mini_rust_kinematics`); the robot geometry it needs is a 7.4 KB
file, vendored in `data/`. Two catches: that file ships ±180° placeholder joint
limits, so the real ones come from the robot's URDF; and the repo's `.gitignore`
rule `data/` would have silently kept it out of git, so it has an exception.
Our standalone solver agrees with the SDK's on **5000 / 5000** random poses.

**3. The SDK's sleep pose can't be written in a behaviour file.**
It needs a sideways head shift (x = −21 mm) that the format deliberately
doesn't expose. *Fix:* bedtime level 5 hands over to the SDK's own
`goto_sleep()`. The player tracks that shift internally, so waking up slides
out of it instead of snapping.

**4. The SDK's `goto_target` can't be interrupted.**
It looked like the obvious way to move: the robot does the interpolation, so
Wi-Fi delays don't matter. But reading the SDK source showed that the daemon
**silently drops** a new goto while one is running, so a new behaviour would
just vanish instead of interrupting. Gotos also wait for an acknowledgement, so
chaining keyframes adds a network round trip at each one, and streamed targets
are ignored while any goto runs. *Fix:* the player streams its own poses at
50 Hz on a monotonic clock — what the SDK's own `play_move` does, but with one
command per frame instead of three. Interrupting is instant, and every frame is
checked before it leaves.

**5. Turning media off would have silenced the robot.**
The player needs no camera or microphone, so `media_backend="no_media"` seemed
right — but it tells the daemon to **release its audio hardware**, after which
the robot cannot play any sound. *Fix:* the client keeps the SDK's default
media setting and never uses it. Sounds go through the daemon's own web API
instead.

**6. Playing a sound over the network stalls whoever asks.**
The SDK's `play_sound` uploads the entire file on every call, on the calling
thread. *Fix:* each file is uploaded once (and again only if it changes),
then played by name, on the audio thread. Measured on the simulator: 45 ms for
the first play, 5 ms after.

**7. Cartoon easing goes past its own keyframes.**
`cartoon` winds up backwards and overshoots by 10%, and peaks at **5.6×** its
average speed. A keyframe at pitch 38 can briefly reach 41.8 on the way there,
so checking only the keyframes wasn't enough. *Fix:* the validator checks the
whole trajectory, using the same code the player runs.

**8. A safe behaviour can become unsafe because of where the robot already is.**
`approving_nod` ends with a quick 0.4 s antenna flick back to neutral. From
neutral it's no movement at all. Played right after a bedtime nudge, with the
antennas still drooped, it's a 140° snap peaking near **1958°/s** — five times
the limit. Refusing it would stop the robot from praising someone right after
nudging them toward bed. *Fix:* a move made too fast by the starting pose is
made to take longer, and the player logs it. Every behaviour played directly
after every other one (169 pairs) now stays within all limits.

**9. `relative:` moves didn't come back to where they started.**
A nod written as "+8 then −8" ended 8° higher than it began, because each offset
was measured from the start of the block instead of from the previous pose.
*Fix:* offsets now add up keyframe by keyframe, so nods return and the idle
breathing loops cleanly.

**10. Ctrl+C sometimes did nothing.**
A process started in the background from a script ignores Ctrl+C, and an
editor's stop button sends a different signal (SIGTERM) that Python doesn't
handle by default — either way the robot could be left mid-move. *Fix:* the
play tool installs its own handlers, so both always return the robot to
neutral before quitting.

**11. The easing curves are copied, not imported.**
Needed so CI can run without the SDK. *Guard:* a test compares our copy to the
SDK's at 1001 points per curve whenever the SDK is installed, so a change
upstream can't go unnoticed.

## Measured behaviour

| | Result |
|---|---|
| Keyframe timing vs plan | within about 15 ms |
| Frame lateness, simulator | mean 2.7 ms, p95 4.8 ms, max 16.8 ms (one frame is 20 ms) |
| Simulated head vs command | median 2.7° behind, up to 15° on fast moves — motor lag, not the player |
| Tests | 132 passing |

## Not verified yet

- **Anything on the real robot**, including the Wi-Fi timing. Run
  `python -m eepy.behaviors.play <name> --backend real --timing` to measure it.
- **Which antenna is which, and which way is "droop".** Inferred from the SDK's
  sleep pose. If it's wrong, the fix is in the player, not the YAML.
- **Sounds being audible.** The path works end to end, but the files in
  `sounds/` are still silent placeholders.
