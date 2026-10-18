"""Normalised features: the raw values relative to THIS person's baseline (A12).

The HMM uses these, so different faces, postures and camera setups become
comparable. Each is a ratio or a difference -- not a z-score: 60 seconds of
baseline gives a reliable centre but a noisy spread, and dividing by a noisy
spread would amplify it (the spreads P10/P90 are stored with the calibration,
so z-scores can still be computed later).

    ear_rel           EAR / their open EAR        1 = as open as usual, 0.5 = half
    mar_rel           MAR - their resting MAR     0 = mouth as at rest (a ratio would
                                                  explode: resting MAR is ~0)
    head_yaw_rel ...  angle - their neutral angle 0 = their usual posture; also
                                                  absorbs camera height and mounting
    face_size_rel     size / their usual size     >1 = leaning in, <1 = further away
    au_<AU>_rel       (p - rest) / (1 - rest)     0 = their resting face, 1 = maximal
                                                  (preview and windows only; aus.csv
                                                  keeps the raw probabilities)
    calibrated        1 = their own baseline, 0 = defaults (flagged, not hidden)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .baseline import Baseline
from .extractors import Extractor, Output
from .observation import FaceObservation


class NormalizedExtractor(Extractor):
    name = "normalized"
    columns = ("ear_rel", "mar_rel", "head_yaw_rel", "head_pitch_rel", "head_roll_rel",
               "face_size_rel", "calibrated")

    def update(self, obs: FaceObservation, baseline: Baseline) -> Output:
        return Output({})                      # everything happens in derive()

    def derive(self, values: Dict[str, Optional[float]], baseline: Baseline) -> Dict[str, Any]:
        out: Dict[str, Any] = {c: None for c in self.columns}
        out["calibrated"] = 1 if baseline.calibrated else 0

        def get(name: str) -> Optional[float]:
            value = values.get(name)
            return None if value is None else float(value)

        ear, mar, size = get("ear"), get("mar"), get("face_size")
        if ear is not None and baseline.ear_open > 1e-6:
            out["ear_rel"] = round(ear / baseline.ear_open, 4)
        if mar is not None:
            out["mar_rel"] = round(mar - baseline.mar_closed, 4)
        for axis, zero in (("yaw", baseline.yaw0), ("pitch", baseline.pitch0), ("roll", baseline.roll0)):
            angle = get(f"head_{axis}")
            if angle is not None:
                out[f"head_{axis}_rel"] = round(angle - zero, 2)
        if size is not None and baseline.face_size0:
            out["face_size_rel"] = round(size / baseline.face_size0, 4)
        for key, value in values.items():
            if key.startswith("au_AU") and value is not None:
                unit = key[3:]
                rest = baseline.au_rest.get(unit, 0.0)
                out[f"au_{unit}_rel"] = round(max(0.0, min(1.0, (value - rest) / max(1e-6, 1 - rest))), 4)
        return out
