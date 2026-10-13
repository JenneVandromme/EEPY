"""Synthetic sessions: per-second behaviour -> the window features the vision process
logs, through the real assemble(). No camera.

    features = simulate(SCENARIO, seed=1, aus=True)     # one dict a second

Used by the HMM tests (tests/test_hmm.py) and by `python -m eepy.state.explain --demo`.
The behaviour per second: is a face there, are the eyes judged (looking forward), how
closed are they, blinks (ms), yawn, nod, looking away, the jaw-drop action unit.
Windows of 10 and 60 s like the vision process. Standard library only.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from .features import assemble

def _second(kind: str, i: int, rng: random.Random) -> Dict[str, Any]:
    d: Dict[str, Any] = dict(present=True, judged=True, closed=0.0, blinks=[], yawn=False, nod=False, away=False)
    if kind == "work":
        d["judged"] = rng.random() < 0.8
        if d["judged"] and rng.random() < 0.25:
            ms = rng.gauss(160, 30)
            d["blinks"].append(ms)
            d["closed"] = ms / 1000 * 0.6
        if i % 15 in (7, 8):                      # a short glance away every 15 s
            d["away"], d["judged"] = True, False
    elif kind == "phone":
        d.update(judged=False, away=True)
    elif kind == "sleepy":
        if rng.random() < 0.3:
            ms = rng.uniform(700, 1300)
            d["blinks"].append(ms)
            d["closed"] = min(1.0, ms / 1000)
        elif rng.random() < 0.2:
            ms = rng.gauss(250, 50)
            d["blinks"].append(ms)
            d["closed"] = ms / 1000 * 0.6
    elif kind == "leave":
        d.update(present=False, judged=False, away=True)
    return d


def simulate(phases: List[Tuple[str, int]], seed: int = 1, rate: float = 1.0,
             extra: Optional[Dict[int, Dict[str, Any]]] = None, aus: bool = False, t0: float = 1000.0,
             calibrated: bool = True) -> List[Dict[str, Any]]:
    """Features for the phases (kind, seconds), sampled `rate` times a second. `extra` overrides
    single seconds (index from 0), e.g. {70: {"yawn": True}}."""
    rng = random.Random(seed)
    secs = [_second(kind, i, rng) for kind, n in phases for i in range(n)]
    au_rng = random.Random(seed + 1000)          # its own stream: the same behaviour with or without AUs
    for s in secs:
        s["au26"] = s["present"] and au_rng.random() < 0.017
    for k, v in (extra or {}).items():
        secs[k].update(v)
    for k, s in enumerate(secs):                 # a yawn drops the jaw for ~5 s
        if s["yawn"]:
            for j in range(k, min(k + 5, len(secs))):
                secs[j]["au26"] = secs[j]["present"]
    out: List[Dict[str, Any]] = []
    step, t, away_since = 1.0 / rate, 1.0, None
    while t <= len(secs) + 1e-9:
        i = int(t + 1e-9) - 1
        face: Dict[str, Any] = {}
        gaze: Dict[str, Any] = {}
        au: Dict[str, Any] = {"status": "running", "rate": 10.0}
        for w in (10, 60):
            win = secs[max(0, i - w + 1): i + 1]
            n = len(win)
            present = [s for s in win if s["present"]]
            judged = [s for s in present if s["judged"]]
            blinks = [b for s in judged for b in s["blinks"]]
            sfx = f"_{w}s"
            face["perclos" + sfx] = round(sum(s["closed"] for s in judged) / len(judged), 4) if judged else None
            face["eyes_measured_s" + sfx] = float(len(judged))
            face["blink_rate" + sfx] = round(len(blinks) / (len(judged) / 60), 1) if len(judged) >= 0.1 * w else None
            face["blink_mean_ms" + sfx] = round(sum(blinks) / len(blinks), 1) if blinks else None
            face["blink_max_ms" + sfx] = round(max(blinks), 1) if blinks else None
            if calibrated:
                face["blink_mean_ms_rel" + sfx] = round(sum(blinks) / len(blinks) / 160, 3) if blinks else None
            face["long_blinks" + sfx] = sum(1 for b in blinks if b > 500)
            face["yawns" + sfx] = sum(1 for s in win if s["yawn"])
            face["nods" + sfx] = sum(1 for s in win if s["nod"])
            face["face_absent_ratio" + sfx] = round(1 - len(present) / n, 4)
            face["fps" + sfx] = 15.0
            face["blink_confidence" + sfx] = "ok"
            face["coverage_s" + sfx] = float(n)
            off = sum(1 for s in win if s["away"] or not s["present"])
            gaze["off_work_ratio" + sfx] = round(off / n, 3)
            gaze["no_face_ratio" + sfx] = round(1 - len(present) / n, 3)
            au["results" + sfx] = 10 * len(present)
            au["results_eyes" + sfx] = 10 * len(judged)
            au["AU26_active" + sfx] = round(sum(s["au26"] for s in present) / len(present), 4) if present else None
            au["AU43_active" + sfx] = (round(sum(min(1.0, 0.07 + s["closed"]) for s in judged) / len(judged), 4)
                                       if judged else None)
        cur = secs[i]
        if cur["away"] or not cur["present"]:
            away_since = i if away_since is None else away_since
            gaze["away_current_s"] = float(i - away_since + 1)
        else:
            away_since = None
            gaze["away_current_s"] = 0.0
        closed_now, j = 0.0, i
        while j >= 0 and secs[j]["judged"] and secs[j]["closed"] >= 0.99:
            closed_now += 1
            j -= 1
        face["eyes_closed_now_s"] = closed_now
        face["baseline_calibrated"] = calibrated
        gaze["calibrated"] = True
        out.append(assemble(round(t0 + t, 6), face, gaze, au if aus else None))
        t += step
    return out


SCENARIO = [("work", 40), ("phone", 20), ("work", 20), ("sleepy", 45), ("leave", 30)]


def closures(start: int, end: int) -> Dict[int, Dict[str, Any]]:
    """2-s eye closures every 8 s (for `extra`)."""
    ex: Dict[int, Dict[str, Any]] = {}
    for k in range(start, end, 8):
        ex[k] = {"closed": 1.0, "judged": True, "blinks": [2000.0], "away": False}
        ex[k + 1] = {"closed": 1.0, "judged": True, "away": False}
    return ex
