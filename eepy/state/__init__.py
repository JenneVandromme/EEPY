"""The user's state, from the camera's features: focused, distracted, drowsy,
very_drowsy, absent or unknown.

    from eepy.state import make_estimator, assemble
    estimator = make_estimator("rules")          # A13; "hmm" comes with A16
    estimate = estimator.update(assemble(t, face_window, gaze_window, au_window))
    estimate.state, estimate.reason_text

Rules and thresholds: config/state_rules.yaml. Replay over a recorded session:
python -m eepy.state.replay <session>. Plain-language guide: eepy/state/README.md.
"""

from .estimate import MEANING, STATES, Reason, StateEstimate, StateEstimator, for_mode, make_estimator
from .features import assemble, catalog

__all__ = ["MEANING", "STATES", "Reason", "StateEstimate", "StateEstimator", "for_mode",
           "make_estimator", "assemble", "catalog"]
