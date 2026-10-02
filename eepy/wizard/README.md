# How the Wizard-of-Oz panel works

A local web page that lets a teammate "be" EEPY's brain: they watch the student
and trigger behaviours by hand. It serves three purposes — the Stage 0 demo,
the backup if the autonomous system fails during the final demo, and pilot
sessions where the operator's observations become the labels the HMM learns
from.

> **Running a session?** You want
> [docs/WIZARD_PANEL_GUIDE.md](../../docs/WIZARD_PANEL_GUIDE.md) instead. This
> page is about how the panel is built and why.

---

## What it does

```
 laptop browser ─┐                  ┌──────────── one Python process (lab desktop) ────────────┐
 laptop browser ─┼─ HTTP buttons ──►│  Wizard  ──► BehaviorPlayer ──► backend ──► robot / sim  │
 desktop browser─┘◄─ live updates ──│    │              │ events                                 │
                     (SSE)          │    └──► Sessions ◄┘──► logs/sessions/P03_….jsonl          │
                                    └──────────────────────────────────────────────────────────┘
```

1. **Buttons are generated, not written.** The panel reads the behaviour
   library and `config/wizard_panel.yaml` and builds every button from them.
   A behaviour added to `behaviors/` appears after Reload.
2. **A press is a small HTTP request.** The server hands it to the
   `BehaviorPlayer`, which already handles interrupting whatever is playing.
3. **Everything that happens is recorded**: the operator's commands, the
   robot's events and the operator's observations, all stamped on one clock.
   During a session, it all goes into that participant's log file.
4. **The page is kept up to date by the server** over Server-Sent Events, so
   every open browser shows the same thing.

## The pieces

| File | Job |
|---|---|
| `__main__.py` | `python -m eepy.wizard` — starts everything, prints the addresses to open. |
| `server.py` | `Wizard` (owns the player, session and state — no web code) and the web routes. |
| `config.py` | Reads and checks `config/wizard_panel.yaml`; builds the buttons from it. |
| `sessions.py` | Session start/end, and the log format, behind a `SessionWriter` interface. |
| `static/index.html` | The whole page: one file, plain HTML, CSS and JavaScript. |
| `config/wizard_panel.yaml` | Keys, markers, default intensity, idle — editable by designers. |

## The main decisions, and why

**FastAPI + one plain HTML file + Server-Sent Events.** Everything needed was
already installed with the robot SDK, there is no build step, and the page is a
single file a designer can open and read. NiceGUI was the alternative; it would
have been a new dependency, and it builds the page from Python, which is harder
for designers to follow.

**The panel runs in the same process as the player.** This is the decision
the study depends on. An operator's "yawn" and the robot's `behavior_started`
are stamped by the *same* `time.monotonic()` clock, so they can be lined up to
the millisecond when fitting the HMM. A separate server would have its own clock.

**All state lives on the server.** The session, the idle setting, the live log
and the last error are all kept on the desktop. Refreshing the page, or
opening it on a second laptop, loses nothing and changes nothing.

**STOP never waits.** It takes no lock and needs no connection check. If there is
a player, STOP reaches it.

**The panel starts even when the robot doesn't.** If the robot is unreachable, or
drops out later, the panel stays up: the status turns red, Reconnect tries
again, and annotations keep being recorded.

**Keys live in a config file, not in code.** Ladder keys are bound by ladder
and *level*, not by name, so renaming a behaviour doesn't lose its key. A new
behaviour with no key gets a free one automatically, with a reminder to add it
to the config.

**A command is logged only once the player accepts it,** but it is stamped with
the moment the button was pressed.

## The session log

One JSON Lines file per session, named `P03_2026-10-06_14-03-12.jsonl`. Every
line carries `t` (the shared monotonic clock), `wall` (the same moment as a
readable time), `session`, `kind` and `source`.

- `source` is who caused it: `wizard` (the operator), `idle`, `system`, and later
  `auto` for the autonomous system — so wizard-triggered and autonomous
  behaviours can be compared.
- `kind` is what happened: the player's event kinds plus `wizard_command`,
  `annotation`, `note`, `session_start`, `session_end`, `connection`, `reload`.
- `wall` is derived from `t` through a single anchor taken when the panel
  starts, so the two can never disagree about the order of events.
- **Each line is written to disk immediately**, so a crash loses nothing that
  was already logged.
- **The writing sits behind `SessionWriter`**, so the logging module (A7) can
  replace it.

**Annotations are stamped when the desktop receives the key press.** That is a
few milliseconds of Wi-Fi after the press — small next to the operator's own
reaction time of roughly 300–500 ms, which is worth remembering when fitting
the HMM.

## What the panel needed from the player

Two additions, plus one small rename (details in
[`eepy/behaviors/README.md`](../behaviors/README.md)):

- **`source=`** on `play()` and `play_level()`, carried into every event that
  behaviour produces.
- **`player.say()`**, to speak a line, or free text, without playing a behaviour.
- In `keyframe_reached` events, the YAML location is now called `keyframe`, so
  that `source` always means "who triggered it".

And one on the backend, not the player: **`backend.is_connected()`**.

---

## Roadblocks, and how we got around them

**1. The panel config couldn't go next to the behaviours.**
The validator reads every `.yaml` file in `behaviors/` as a behaviour file. *Fix:*
it lives in `config/wizard_panel.yaml`, with its own checks and friendly errors.

**2. Participant logs could have been committed to git.**
Nothing excluded `logs/`. *Fix:* `logs/` is gitignored — checked with a real
file before writing any session.

**3. Spoken lines needed code that was locked inside the player.**
Choosing a wording (random, never the same as last time, placeholders filled
in) only happened inside a behaviour. *Fix:* `player.say()`. My first version
could deadlock — it took the player's lock and then called code that the
control thread also runs — so the wording memory got a lock of its own.

**4. A request the player rejected appeared in the log as if it had happened.**
A misspelled behaviour was logged as `wizard_command: play …` before the player
turned it down. In a dataset that is a trap. *Fix:* log only accepted commands,
still stamped at the moment of the press.

**5. A dropped robot was invisible while nothing was playing.**
The player only notices a lost connection when it tries to send a pose. *Fix:*
`backend.is_connected()`. The SDK has no public way to ask, so it reads the
private flag the SDK's websocket client sets when its connection dies; if a
future SDK renames it, the check falls back to "assume connected" instead of
crashing. Tested by killing the simulator under a running panel: it turned red
within **0.1 s**.

**6. Short drops reached the page but not the data.**
Drops were only checked in the background every 0.5 s, so a shorter outage
showed red on screen and left nothing in the log. *Fix:* a drop is now
recorded wherever it's first noticed — page updates, an attempted play, or the
background check.

**7. STOP froze during a reconnect — and the log gained a drop that never
happened.**
Reconnecting held a lock across the whole slow connect. The page's live updates
waited on that lock from the web server's event loop, which stalled *every*
request, STOP included. The same lock also let a background check record a
stale "lost" right after each reconnect. *Fix:* slow work (connecting,
validating) never holds a lock, and the live updates never wait on one from the
event loop. A test checks that STOP answers within half a second while a
reconnect hangs; another checks that reconnecting never logs a false drop.

**8. Space as STOP could restart the behaviour you'd just clicked.**
Browsers "click" a focused button when Space is pressed, and a button keeps
focus after you click it. *Fix:* the panel's buttons never take keyboard focus.
Space is STOP only when you're not typing; Esc is STOP always, even in a text
box.

**9. Holding a key down would fire it thirty times.**
Key repeat would have turned one "yawn" into a burst of markers. *Fix:*
repeats are ignored.

**10. No browser to test the page in.**
Chrome isn't installed on the development machine. *Fix:* the page's own
JavaScript was run in a simulated browser (jsdom) against a live panel, with
scripted key presses: every button rendered, every key behaved correctly, and
no errors were thrown.

**11. The page came up completely white in Safari.**
Another program on the same Mac — a PHP dev server — held port 8080 on IPv6
(`[::1]`), while the panel held it on IPv4. Both started without complaint.
Safari tries IPv6 first for `localhost`, so it reached the other program and
showed its empty page. *Fix:* before starting, the panel checks the port on
**both** IPv4 and IPv6, and refuses with a message naming the program in the
way. It also prints `http://127.0.0.1:8080` rather than `localhost`, which can't
be ambiguous.

## Measured

| | Result |
|---|---|
| Robot drop shown on the page (real simulator) | 0.1 s |
| STOP while a reconnect hangs for 2 s | answers in under 0.5 s |
| Tests | `python -m pytest tests/test_wizard.py` — every endpoint, the log format, config errors, robot drops, STOP during a reconnect, port conflicts. Also run by CI on every pull request. |

## Not verified yet

- **A real browser.** Open the panel once on the desktop and once on a laptop
  before relying on it.
- **The real robot and the lab network**, including the macOS firewall prompt
  the first time a laptop connects.
