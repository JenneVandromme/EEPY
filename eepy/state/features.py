"""What the state estimators see: one flat dict of features about once a second.

ONE function builds it -- `assemble()` -- from the window summaries the vision
process already logs (face_window, gaze_window, au_window: A9-A12). Live, they
come straight from the FacePipeline; in replay, from the session's
events.vision.jsonl. Same input, same states.

Feature names are the logged names (perclos_60s, off_work_ratio_10s, ...), plus
a few worked out here:

    face_present_ratio_<w>   share of the window with a face (1 - face_absent_ratio)
    gaze_off_ratio_<w>       share of the FACE-PRESENT time the gaze was off the work:
                             looking away while at the desk, not having left it
    au_status, au_rate       the action units' state and rate (au_window's status/rate)
    <state>_points           points added up by the rules (e.g. drowsy_points)

`catalog()` lists every name with what it means: the YAML is checked against it,
and the docs show it.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

Features = Dict[str, Any]

WINDOWS = (10, 60)
# The gaze zones (eepy/perception/gaze/zones.py ZONES; a test keeps them equal --
# not imported, so the panel's environment can use this without the vision one).
ZONES = ("screen", "notes", "down_other", "away", "no_face")

PER_WINDOW = {
    # face_window (eepy/perception/windows.py)
    "perclos": "share of the face-present time with the eyes >= 80 % shut (PERCLOS)",
    "blink_rate": "blinks per minute",
    "blink_mean_ms": "mean blink duration (ms)",
    "blink_max_ms": "longest eye closure (ms) -- a microsleep is > 1500",
    "long_blinks": "blinks longer than blink.long_ms (500 ms)",
    "yawns": "yawns",
    "nods": "nods (head dropping: nodding off)",
    "look_away_ratio": "share of the time the head was turned away",
    "pitch_mean": "mean head pitch (degrees, chin down +)",
    "pitch_var": "head pitch variance",
    "face_absent_ratio": "share of the time with no face",
    "face_present_ratio": "share of the time with a face",
    "fps": "camera pictures processed per second",
    "blink_confidence": "ok, or low (camera too slow to measure blinks)",
    "coverage_s": "seconds of camera data in the window",
    "blink_rate_rel": "blink rate / the person's usual (calibrated only)",
    "blink_mean_ms_rel": "blink duration / the person's usual (calibrated only)",
    # gaze_window (eepy/perception/gaze/extractor.py)
    **{f"{zone}_ratio": f"share of the time looking at: {zone}" for zone in ZONES},
    "off_work_ratio": "share of the time NOT looking at the work (no face included)",
    "gaze_off_ratio": "share of the face-present time the gaze was off the work",
    "away_episodes": "number of looks away",
    "away_mean_s": "mean length of a look away (s)",
    "away_longest_s": "longest look away (s)",
    # au_window (eepy/perception/aus/extractor.py)
    "au_results": "action-unit results in the window",
}
UNSUFFIXED = {
    "eyes_closed_now_s": "how long the eyes have been closed right now (s)",
    "baseline_calibrated": "true when the face values are relative to this person (A12)",
    "away_current_s": "how long the person has been looking away right now (s)",
    "gaze_calibrated": "true when the gaze zones are this person's (A11)",
    "au_status": "action units: running, paused, loading, starting -- missing when off",
    "au_rate": "action-unit results per second",
}
AU_FEATURE = re.compile(r"^AU\d\d_(mean|active)_(\d+)s$")
AU_MEANING = {"mean": "mean probability", "active": "share of the time active"}


def catalog(windows: Iterable[float] = WINDOWS, point_states: Iterable[str] = ()) -> Dict[str, str]:
    """Every feature name a rule may use, with what it means."""
    out: Dict[str, str] = {}
    for seconds in sorted(set(int(w) for w in windows)):
        for name, meaning in PER_WINDOW.items():
            out[f"{name}_{seconds}s"] = f"{meaning}, last {seconds} s"
    out.update(UNSUFFIXED)
    for state in point_states:
        out[f"{state}_points"] = f"points added up by the {state} rules"
    return out


def known(name: str, names: Mapping[str, str], windows: Iterable[float] = WINDOWS) -> bool:
    match = AU_FEATURE.match(name)
    if match:
        return int(match.group(2)) in {int(w) for w in windows}
    return name in names


def _ratio_complement(value: Any) -> Optional[float]:
    return None if value is None else round(1.0 - float(value), 4)


def assemble(t: float, face: Optional[Mapping[str, Any]] = None,
             gaze: Optional[Mapping[str, Any]] = None, aus: Optional[Mapping[str, Any]] = None,
             windows: Sequence[float] = WINDOWS) -> Features:
    """The features for one moment, from the window summaries logged at that moment.
    Missing summaries (no gaze extractor, AUs off) simply leave their names out."""
    out: Features = {"t": t}
    if face:
        out.update(face)
        out["baseline_calibrated"] = bool(face.get("baseline_calibrated", False))
    if gaze:
        out.update({k: v for k, v in gaze.items() if k not in ("calibrated", "iris_blendshape_agree")})
        out["gaze_calibrated"] = bool(gaze.get("calibrated", False))
    if aus:
        for key, value in aus.items():
            if key in ("status", "rate"):
                out[f"au_{key}"] = value
            elif key.startswith("results_"):
                out[f"au_{key}"] = value
            elif key != "infer_ms":
                out[key] = value
    for seconds in sorted(set(int(w) for w in windows)):
        suffix = f"_{seconds}s"
        if face and face.get(f"face_absent_ratio{suffix}") is not None:
            out[f"face_present_ratio{suffix}"] = _ratio_complement(face[f"face_absent_ratio{suffix}"])
        off, none = (gaze or {}).get(f"off_work_ratio{suffix}"), (gaze or {}).get(f"no_face_ratio{suffix}")
        if off is not None and none is not None:
            seen = 1.0 - none
            out[f"gaze_off_ratio{suffix}"] = round(max(0.0, off - none) / seen, 4) if seen > 0.05 else None
    return out


def empty(t: float) -> Features:
    """No data at all at time t (the camera sent nothing): the estimators say unknown."""
    return {"t": t, "no_data": True}
