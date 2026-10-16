# eepy/bandit: rewards, the action space's learner, and simulated users

This package turns EEPY's nudges into a learning problem. It holds three tasks:

| Task | What | Doc |
|---|---|---|
| A22 | every nudge → (context at decision time, action, reward) | [docs/A22_REWARDS.md](../../docs/A22_REWARDS.md) |
| A24 | the contextual bandit (Thompson sampling) that chooses which nudge | [docs/A24_BANDIT.md](../../docs/A24_BANDIT.md) |
| (both) | a reactive simulated user with personas, for developing and checking without people | below |

The action space itself, i.e. what any policy may choose, is A23's `allowed_actions()` in
`eepy/interaction/actions.py` ([docs/A23_ALLOWED_ACTIONS.md](../../docs/A23_ALLOWED_ACTIONS.md)).
The whole pipeline: [docs/HOW_EEPY_DECIDES.md](../../docs/HOW_EEPY_DECIDES.md).

## How the pieces connect

```
 EscalationEngine (eepy/interaction)            Wizard panel (eepy/wizard/server.py)
   when a nudge is due:                             every logged event ──► RewardTracker (rewards.py)
     allowed = allowed_actions(context)                                          │ RewardEvent
     action  = policy.decide(context)  ◄── BanditPolicy (policy.py)               ▼
       (FixedEscalation, or the bandit                ▲         observe() ── nudge_reward event
        in the hmm_bandit mode)                       │                       + bandit_update event
                                                      └── Store: data/bandit/<participant>.json
```

## Files

| File | What |
|---|---|
| `rewards.py` | `price()` (an outcome's reward), `RewardTracker` (pairs nudges with outcomes, emits `RewardEvent`), the offline tool `python -m eepy.bandit.rewards`, the report |
| `context.py` | `FocusTrend` (P(focused) over the 20 s before a decision), `snapshot()` (the training context), `hour_of()` |
| `policy.py` | `BanditPolicy` (decide / observe / view), `bucket_of()`, `Store` / `MemoryStore`, the config loader |
| `linear.py` | the optional linear model (`model: linear`) |
| `simuser.py` | `run_responsive()`: a simulated user who reacts to nudges (named personas or a table), with an optional policy and an online learner |
| `personas.yaml` | four personas with hidden preferences (made up) |
| `smoke.py` | `python -m eepy.bandit.smoke`: bandit vs fixed escalation on the personas |
| `__main__.py` | `python -m eepy.bandit list / show / reset` |

Settings: `config/rewards.yaml` (A22), `config/bandit.yaml` (A24).

## Commands

```bash
python -m eepy.bandit.rewards all --out data/rewards/table.csv     # the training table + report
python -m eepy.bandit.rewards --responsive "focused 3m, distracted 6m, drowsy 15m" --runs 50
python -m eepy.bandit.smoke --sessions 30                          # bandit vs fixed on 4 personas
python -m eepy.bandit show P03                                     # what the bandit learned for P03
python -m eepy.bandit reset P03
```

All of these are also in the launcher: the **Rewards** and **Bandit** tabs.

## For developers

- **A new policy** implements `NudgePolicy.decide(context) -> Action`, picks from `context.allowed`,
  and is registered in `eepy/interaction/policy.py: make_policy()`. The engine checks every choice
  against the allowed set and falls back to the fixed escalation.
- **Learning online** in a simulation: pass `policy=` and `learner=` to `run_responsive()`. The
  learner receives each `RewardEvent` as soon as the outcome is known, as in the panel.
- **Offline = online:** the offline reward tool feeds the same `RewardTracker` the logged events in
  time order. `tests/test_wizard.py` checks that a panel session gives identical rewards both ways.
- **Tests:** `tests/test_rewards.py` (A22), `tests/test_bandit.py` (A24), `tests/test_actions.py` (A23).
