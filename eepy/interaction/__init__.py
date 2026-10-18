"""What EEPY does about the participant's state (A14): the escalation.

The state estimator (eepy/state, A13) says how the participant is doing; the
EscalationEngine decides when to nudge and the NudgePolicy which ladder level.
The panel runs it in the rules mode. Plain-language guide: eepy/interaction/README.md.
Try it without a camera: python -m eepy.interaction.simulate --list
"""
