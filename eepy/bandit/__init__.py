"""The contextual bandit's data (A22) and, later, the bandit itself (A24).

    context.py   what the bandit may know at decision time (the belief trend, the snapshot)
    rewards.py   the reward function, the RewardTracker (online), and the offline tool:
                 python -m eepy.bandit.rewards <session(s)> --out table.csv

Design and reasoning: docs/A22_REWARDS.md. Standard library + PyYAML.
"""
