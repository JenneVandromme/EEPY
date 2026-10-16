# TODO

Open problems we decided to park, with enough detail to pick them up cold.

## Keep the participant's face in the robot camera's view (aim the resting position)

**Status:** parked on 2026-10-13. Code exists but is **switched off**
(`aim.enabled: false` in `config/wizard_panel.yaml`). It has not been seen working on the real robot.

### The problem

- The Reachy Mini sits on the desk with its head at about the participant's **chest height**.
- Its resting position ("neutral") looks **straight ahead** (yaw 0, pitch 0). So the robot's
  camera, which sits in its head, mostly sees the participant's chest; the face is at the top
  edge of the picture or above it.
- Every behaviour is written in absolute angles from that straight-ahead neutral, the idle's
  slow "breathing" included. When the robot moves, the face easily leaves the picture.
- Workarounds today: put the robot far away, or tilt it so it faces down/up. Neither is good.

### What we want

The robot's resting direction should point at the participant's **face**: centred, with some
tolerance (about 8°), not tracking every small movement. All its existing movements (idle,
nods, glances, the ladders) then play **around that direction**, so the face stays in view
when the robot moves.

### What was built (switched off, in the repo)

- `Library.resolve(..., base=HeadPose)` (`eepy/behaviors/loader.py`): written absolute head
  angles become offsets from a base; `intensity` scales toward it; `look_at` targets (notes,
  screen, away) stay room directions. Without a base the output is identical to before (tested
  on all 13 behaviours).
- `BehaviorPlayer.set_base()` / `check_base()` (`eepy/behaviors/player.py`): plans every
  behaviour around the base, neutral and Esc return to it, `close()` returns to true neutral;
  `check_base` runs every behaviour's safety checks from a candidate base.
- The panel (`eepy/wizard/server.py`, `Wizard.aim`, `_maybe_auto_aim`): reads where the face is
  from the vision program's `USER` reports (`TrackingTargetProvider`), moves the base there (in
  up to 3 rounds), never further than every behaviour can still play safely from, saves it with
  the calibration (`robot_base` section), restores it on Reuse, aims before Calibrate, and aims
  by itself when the face stays off-centre for 2 s while no behaviour plays. Logged as
  `robot_base`. A button "Aim at participant" on the calibration card. Settings: `aim:` in
  `config/wizard_panel.yaml`. Tests: `tests/test_aim.py` (all pass, on the dry backend).

### Why it did not work on the robot (what we know)

1. **The first robot test never aimed.** In session `2026-10-13_P99_wizard` there is no
   `robot_base` event: the first version only aimed when Calibrate or "Aim at participant" was
   pressed, and neither was. That session also ran before the code was finished (the panel only
   loads new code when it is restarted).
2. **The automatic version (aims without a button) was never tried on the robot.**
3. **Not verified: do the face reports arrive at all with the robot camera?** The aim depends on
   the vision program sending `USER {"yaw", "pitch"}` over the camera relay. With the robot
   camera this path has never been checked on the real robot. Check first: the panel's
   `/api/state` → `status.camera.tracking_user` must be `true` while a face is in view.
4. **Physical limit: about 13° up.** From a resting direction more than ~13° up (less when the
   participant also sits to the side), `bedtime_attentive` (bedtime level 2: looks at the user,
   then a yawn that tips the head back 14°) would go past the neck's limits. The aim stops
   there, so with the robot at chest height the face may still be above the centre. Fix options:
   raise the robot (a book under it), or make that yawn smaller / relative so behaviours leave
   more room upward.
5. **The camera moves with the head.** Gaze zones (screen / notes / away) are calibrated from
   one camera view, and perception does **not** correct for the robot's head pose. Any change of
   the resting direction after calibrating shifts the zones and can break "looking away"
   detection (and so the distraction nudges). This is why it aims before Calibrate and only
   re-aims when the face has clearly moved. Related: test H1 (does the robot's own movement cause
   false "looked away" / "absent"?) has not been done.

### Next steps when we pick it up

1. Restart the panel with the robot, start `python -m eepy.perception`, sit in view, and check
   `tracking_user` (point 3). If it is false, fix the USER path first.
2. Set `aim.enabled: true` (it aims by itself: `auto: true`), start a session, and watch the
   calibration card's aim line and the `robot_base` events (`python -m eepy.logs.summary latest`).
3. If the face is still above the centre at the limit: raise the robot, or rework
   `bedtime_attentive` so behaviours leave more room upward.
4. Longer term: make perception correct gaze (and nod detection) for the robot's head pose, so the
   robot may re-aim freely without breaking the calibration.

## Open ends from A20–A24 (2026-10-15)

- **The designers' intrusiveness ranking (B8)** is not in the repo. Until it is, the level order
  stands in for it: `actions.intrusiveness` in `config/escalation.yaml` (A23) and the prior means
  in `config/bandit.yaml` (A24) should follow it once it exists.
- **Explanation lines (C6):** the designers write `explain_<type>` lines in `behaviors/lines.yaml`
  (docs/EXPLANATION_CATALOG.md). Until then EEPY uses its default sentences. Be careful with a
  numeric nodding sentence: a false head-drop detection would be spoken as fact.
- **A "no nudge" baseline for the rewards:** a user who would have recovered anyway still credits
  the nudge when the belief trend before the decision didn't show it (A22). Waiting has a fixed
  value in the bandit (A24) because it has no outcome. A randomised "not yet" or a learned
  baseline would fix both.
- **Personas from D10:** the smoke simulation's personas (`eepy/bandit/personas.yaml`) are made
  up; D10's personas.csv should replace them.
- **Off-policy evaluation:** propensities are logged with every decision, but nothing uses them
  yet (A25/A26).
- **Real data:** every reward, decision and bandit number so far comes from test sessions (P99)
  and simulations. P99_hmm_5 contains about a minute of fake-camera rows (17:54–17:55 on
  2026-10-15): leave it out of any analysis.
