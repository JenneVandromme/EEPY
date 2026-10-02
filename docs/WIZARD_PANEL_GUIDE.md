# The Wizard panel — one-page guide

You are EEPY's brain. You watch the student, and you press the buttons that
make the robot react. Everything you do is recorded, so do it the same way
every time.

## Start it (on the lab desktop)

```bash
source .venv-robot/bin/activate
python -m eepy.wizard --backend real        # or --backend sim, or --backend dry
```

It prints two addresses. Open **`http://localhost:8080`** on the desktop, or the
**`http://<numbers>:8080`** one on any laptop on the same network. Several
laptops can have it open at once — they all show the same thing.

Check the top bar: a **green dot** means the robot is connected. Red means it
is not — see "If something goes wrong".

## Run a session

1. Type the **participant ID** (e.g. `P03`) and press **Start session**. The
   bar turns red and says **RECORDING**.
2. Watch the student. Trigger behaviours, and mark what you observe (below).
3. Press **End session** when they leave. The file is saved in `logs/sessions/`.

Markers pressed with **no session running are not saved** — the panel warns
you in orange. Start the session first.

## The keys

The key for every button is printed on it. With the default settings:

| | Keys |
|---|---|
| **STOP** — robot back to neutral | **Esc** (always works, even while typing) or **Space** |
| Refocus ladder, levels 0–4 | `1` `2` `3` `4` `5` |
| Bedtime ladder, levels 0–5 | `Q` `W` `E` `R` `T` `Y` |
| Idle on / off · approving nod | `A` · `S` |
| Spoken lines | `6` `7` `8` `9` `0` `-` |
| Switch lines between hedged / direct | `Tab` |
| **I observed…** markers | `Z` phone picked up · `X` looked away · `C` back on task · `V` yawn · `B` eyes closing · `N` user replied · `M` user dismissed · `,` user went to bed |

- The **intensity slider** applies to the *next* behaviour you trigger.
- **Hedged** lines sound unsure ("Losing focus a bit?"); **direct** ones are
  confident ("Back to it?"). Pick per moment.
- **Say this…** speaks anything you type, and **Note…** adds a free-text note.
  Press Enter to send either.
- While you are typing in a box, the other keys type letters instead of doing
  things. Esc leaves the box — and stops the robot.

## Annotating — the part that matters for the study

The markers are the ground truth the HMM learns from, so:

- **Press the moment you see it**, not when you are sure. The time of the key
  press is what gets recorded, to the millisecond.
- **Mark what you observe, not what you think it means.** "looked away", not
  "lost focus".
- One press per event. Holding a key down counts once.
- If you made a mistake, add a **note** saying so ("previous yawn was a
  sneeze"). Never edit the log file.

## If something goes wrong

| What you see | What to do |
|---|---|
| Robot doing something wrong | **Esc**. Always first. |
| Red **NOT CONNECTED** | Keep annotating — it is still recorded. Fix the robot, then press **Reconnect robot**. The session carries on. |
| "Kept the last good version" after Reload | A behaviour or config file has a mistake. The red box says which file and line. The panel keeps working with the old version meanwhile. |
| STOP seems slow at bedtime level 5 | The goodnight routine cannot be interrupted. It finishes in a few seconds. |
| Page says it lost contact with its server | The panel was stopped on the desktop. Start it again; nothing recorded so far is lost. |
| Page looks stuck | Refresh it. The session keeps running on the desktop. |
| Laptop cannot open the address | Same Wi-Fi as the desktop? On the desktop, allow "Python" when macOS asks about incoming connections. |

**To stop the panel:** press **Ctrl+C** in its terminal. It ends any running
session, saves it, and returns the robot to neutral.

Anyone on the lab network can open the panel and drive the robot — there is no
login. Do not leave it running unattended on a shared network.

*Changing keys or markers:* edit `config/wizard_panel.yaml` and press **Reload
files**. New behaviours added to `behaviors/` appear after Reload too.
