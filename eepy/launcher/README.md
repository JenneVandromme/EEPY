# The launcher

```bash
python3 launch.py                 # opens http://127.0.0.1:8090 (the new page; the old one is at /v1)
python3 launch.py --port 8091     # if 8090 is taken
python3 launch.py --no-browser
```

One page to start, watch and stop EEPY's programs. It needs no virtualenv (Python
3.9+, standard library only): it starts each program in its own one, exactly as
you would in separate terminals.

| Tab | What it is |
|---|---|
| **Overview** | **Start all** / **Stop all**, and for each program a status light, Start / Stop / Restart and its last lines of output. With the real robot, whether the robot answers. |
| **Wizard panel** | The panel itself (http://127.0.0.1:8081). Click inside it once before using its keyboard shortcuts, or use **Pop out**. |
| **Live HMM** | The vision program's live page (http://127.0.0.1:8770): the HMM the panel acts on, and the robot's decisions. |
| **Decisions** | Every nudge decision with the allowed set next to it (A23): scripted states, a reactive user, simulated faces or a recorded session. Tick **bandit** to let the bandit choose (A24), with its draws. See [docs/A23_ALLOWED_ACTIONS.md](../../docs/A23_ALLOWED_ACTIONS.md). |
| **Bandit** | What the contextual bandit learned per participant (bars per situation, **Reset…**), and the **smoke simulation**: bandit vs fixed escalation on four simulated personas. See [docs/A24_BANDIT.md](../../docs/A24_BANDIT.md). |
| **Rewards** | What every nudge was worth to the bandit (A22): **Live** for the running session, **Recorded** sessions → a report and a CSV training table, **Simulated** runs (a reactive user, simulated faces, scripted replies). See [docs/A22_REWARDS.md](../../docs/A22_REWARDS.md). |
| **Developer** | Robot (dry run / simulator / real), mode (Wizard / Rules / HMM / **HMM + bandit**), camera, ports, extra arguments; all output; the terminal commands. Clicking the mode chip in the header jumps here. |

**Start all** goes in order and waits for each program to answer before starting the
next: the simulator (only with the simulator), the panel, then vision (which uses the
camera). If one fails, it stops there and says so; its output is on its card.

**Stop** sends Ctrl+C, like in a terminal: the panel ends the session and puts the robot
back to neutral. Whatever is still running after a few seconds is killed. Ctrl+C in the
launcher's own terminal stops everything it started.

The settings are saved in `.launcher.json` (per computer, not in git).

## Testing each feature from here

Most of these need no camera and no robot. The whole system, step by step:
[docs/HOW_EEPY_DECIDES.md](../../docs/HOW_EEPY_DECIDES.md).

| Feature | Where | What to do | What you should see |
|---|---|---|---|
| everything starts | Overview | Developer: Robot *Dry run*, Camera *fake* → **Start all** | the panel and vision cards turn green |
| the state from your face (A13/A16) | Live HMM | Camera *laptop* → **Start all** | your belief moving as you look away or close your eyes |
| when it nudges (A14/A19) | Wizard panel | Mode *HMM*, start a session, look away for a minute | the autonomy row and ladder view climbing (the first nudge after 3 minutes) |
| why it nudged (A20) | Wizard panel | after a nudge | "Why #… said: …" with the top evidence; `--explanations off` in the panel's extra arguments → "would say" |
| what may be played (A23) | Decisions | `takes_the_break` → **Show decisions**; then `focused 4m, distracted 8m` | each choice inside its allowed set; no break offer in the second |
| what nudges were worth (A22) | Rewards | *Simulated* → **Run**; *Recorded* → **Build table** | rewards per action, censored share, "worth a look" |
| the bandit (A24) | Bandit | *Smoke simulation* → **Run** | per persona: bandit vs fixed, and whether it found the best nudge |
| the bandit's choices | Decisions | *Reactive user*, tick **bandit**, learned on 20 evenings | its draws next to the allowed set |
| the bandit live | Developer → Mode *HMM + bandit* → **Start all** → Wizard panel | start a session as e.g. P99, get distracted | the Bandit row's bars under the autonomy rows; afterwards Bandit tab → P99 → **Show** |

## Status lights

| Light | Meaning |
|---|---|
| green | running, and answering on its port |
| amber, blinking | starting (running, not answering yet) or stopping |
| purple | running, but **not started by the launcher** (in a terminal): it leaves it alone |
| red | stopped by itself (crashed): see its output |
| grey | stopped |

## Fallback: separate terminals

The launcher starts the same programs you can start by hand, so if it misbehaves, stop
it and use terminals. The **Developer** tab shows the commands for the current settings.
For the simulator in hmm mode:

```bash
.venv-robot/bin/mjpython .venv-robot/bin/reachy-mini-daemon --sim                    # terminal 1
.venv-robot/bin/python -m eepy.wizard --backend sim --mode hmm --port 8081         # terminal 2
.venv-vision/bin/python -m eepy.perception --live                                  # terminal 3
```

Mixing is fine: a program started in a terminal shows as purple, and **Start all** skips it.

## Two things it guards against

- **A second panel.** Every panel holds the camera relay port (8766), whatever port its
  page is on. If another panel holds it (in a terminal, or one that did not shut down
  completely), the launcher will not start a panel, and warns at the top of the Overview:
  vision started next to it records into **that** panel's session.
- **A second camera program.** Only one program can hold the camera. Do not run
  `python -m eepy.perception.live` (or calibration) next to the launcher's vision program.

The MuJoCo simulator opens in its own window: to show it in a presentation, share the
whole screen rather than the browser tab.

## The new page (default): http://127.0.0.1:8090 (= /v5)

On the `try/launcher-ui` branch the launcher opens a new page at **/** (it sends you to **/v5**), built from the
EEPY design system. The old page described above is still there at **/v1**. The new page goes through a session
step by step:

| Screen | What it does |
|---|---|
| **Home** | A greeting, **Start a session**, **Same as last time**, and day/night (Auto / Light / Dark). |
| **Set up** | Participant code, which robot (no robot / simulator / real), who decides (the four modes), and a self-check with the install commands for anything missing. |
| **Waking up** | Starts the programs in order (like Start all), then opens the participant's session in the panel. If one does not start, it says which one and shows the end of its log. |
| **Desk check** | The panel's calibration: reuse the last one, check now (face, gaze dots full screen, one minute of work), or skip with a reason. |
| **Control panel** | What EEPY thinks right now, what it is doing (hold it back / let it continue), the ladders with their keys, lines and free text with gentle/direct tone, the robot's volume, markers, notes, the session log, and the Live HMM. **Stop EEPY** (Esc or Space) stops moves and voice; **End session** (red) asks first. |
| **Wrap up** | How the session ended, time, focus, nudges and what worked, and the time won back against the first three sessions. |
| **Analyze** | **Sessions** (every session with its states, per participant), **Session story** (focus curve, every moment with why it happened and what came of it), **What I learned** (the bandit per situation, and Forget). |
| **Advanced tools** | The programs, the settings, start/stop all, the terminal commands, and all their output. |

The page talks only to the launcher. The launcher passes a fixed list of the panel's own calls
through (`/api/v5/panel/...`), reads the robot's volume from its daemon, and reads the
recorded sessions in `data/sessions/` without writing anything (`v5api.py`, standard library
only). Nothing in the panel or the vision program changes.
