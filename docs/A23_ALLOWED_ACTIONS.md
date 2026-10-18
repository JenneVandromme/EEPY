# A23: what the robot may do, per state

The bandit (A24) will learn which nudge to play. This page describes the fence around it: one
function, `allowed_actions(context)`, that returns exactly the actions any policy may choose from
at a moment. The bandit only ever picks inside that set, so however it learns, it can never do
something socially inappropriate. The rules are in `config/escalation.yaml` under `actions:`.

## In short

- **Two questions, two places.** *When* may EEPY nudge? That's the escalation: onset, the guards,
  cooldowns, A19's evidence. *Which* nudge? That's the policy, choosing inside the allowed set.
- **An action** is a ladder, a level and a tone (`refocus 2 hedged`). "Wait" is always allowed.
- **The engine checks every choice.** A policy that crashes, or picks something outside the set,
  is replaced by the fixed escalation for that decision, and the replacement is logged.
- **Every nudge logs its allowed set** (`nudge_decision.allowed`, `most_intrusive`,
  `chosen_in_allowed`), so you can always see what the policy could have chosen.
- **The fixed escalation stays the default** (`policy: fixed`) and is always the fallback.

## The rules

| Rule | Setting | Why |
|---|---|---|
| The ladder must match the state | `roles:` | distracted → refocus, drowsy/very drowsy → bedtime; nothing while focused, absent or unknown |
| Ambiguous: the gentle option, once | `ambiguous_tone: hedged` | A19: unclear whether distracted or drowsy → one hedged nudge (or the break offer after long work), then wait |
| First nudge at most level 2 (very drowsy 3) | `max_start_level` | we may be wrong; start wordless. A19's switch carry-over and the restart after a cooldown may start higher |
| At most +2 above the last level | `max_step_up: 2` | no jump from a glance straight to a spoken prompt |
| No level they already ignored | `no_ignored_levels` | repeating what didn't work is nagging; a restart after a cooldown is a fresh round |
| Break offer after 15 min of work | `break_after_work: 900` | offering a break after 5 minutes of work is odd; before that, L3 is the top |
| Wind-down only when drowsy | (it's bedtime L4) | the bedtime ladder only exists for drowsy states |
| Goodnight early | `goodnight_early` | otherwise only via the wind-down timer; early only when very drowsy, ≥ 90 % sure, calibrated, and after an ignored spoken bedtime nudge |
| Direct tone needs certainty | `direct_tone` | calibrated and ≥ 85 % sure (`confidence.direct`): never firm on a guess |
| A guard blocking: wait only | (A14's guards) | min gap, per hour, after calibration, cooldowns, "OK" quiet time, the robot busy |

**Intrusiveness.** The designers' ranking (B8) isn't in the repo yet, so actions are ordered by
level, hedged before direct. When B8 exists, fill in `actions.intrusiveness` (e.g. `"refocus:2": 1.5`)
and the order follows it. `most_intrusive_from` marks spoken prompts, the break, the wind-down and
goodnight as "most intrusive" in the logs and the launcher.

## What changed for the current robot

- **No break offer in the first 15 minutes** after the session start or a break: the refocus ladder
  tops out at L3 there, then cools down as before.
- Everything else the fixed escalation does already fit inside the set. A test checks every one of
  its choices across all built-in scenarios, simulated faces and the reactive user.

## The policy switch and the safety net

`policy: fixed` in `config/escalation.yaml` chooses the policy (`make_policy`). A24 will add
`bandit`. Whatever the setting, the engine:
1. computes the allowed set,
2. asks the policy (`decide(context)`; `context.allowed` holds the set),
3. if the policy raised an error (`escalation` event `policy_failed`) or chose outside the set
   (`policy_outside_allowed`), uses the fixed escalation's choice instead, and
4. if even that is outside the set, waits.

## Try it

**In the launcher** (`python3 launch.py`), the **Decisions** tab: pick *Scripted states* (e.g.
`takes_the_break`, `late_evening`, `belief_shift`, or your own timeline), a *Reactive user*, *Simulated
faces*, or *a recorded session* → **Show decisions**. Each decision shows the chosen action (filled)
inside its allowed set (orange outline = most intrusive), the levels ignored so far, and the outcome.

In a terminal:

```bash
python -m eepy.interaction.decisions --scenario takes_the_break
python -m eepy.interaction.decisions --scenario "focused 4m, distracted 8m"      # no break offer yet
python -m eepy.interaction.decisions --responsive "focused 15m, distracted 6m, drowsy 15m"
python -m eepy.interaction.decisions latest                                    # a recorded session
```

| File | What |
|---|---|
| `eepy/interaction/actions.py` | `allowed_actions`, ordering, `describe`, `contains` |
| `eepy/interaction/config.py` | the `policy:` and `actions:` sections, validated |
| `eepy/interaction/policy.py` | `NudgeContext` (`ignored_levels`, `ambiguous`, `blocked`, `allowed`), `make_policy`; the fixed escalation picks inside the set |
| `eepy/interaction/engine.py` | the set before every decision, the safety net, logging, goodnight early |
| `eepy/interaction/decisions.py` | the decision log |
| `eepy/launcher/decisions.py` | the launcher's Decisions tab |
