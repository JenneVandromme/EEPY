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


def _second_user(kind: str, i: int, rng: random.Random, u: Dict[str, Any]) -> Dict[str, Any]:
    """One second of a simulated user with their own habits (see dataset.py's user profiles).
    Kinds: work, phone, sleepy (drowsy), very_sleepy (very drowsy), leave."""
    d: Dict[str, Any] = dict(present=True, judged=True, closed=0.0, blinks=[], yawn=False, nod=False, away=False)

    def blink(mean: float, sd: float, scale: float = 0.6) -> None:
        ms = max(60.0, rng.gauss(mean, sd))
        d["blinks"].append(ms)
        d["closed"] = min(1.0, ms / 1000 * scale)

    def closure(lo: float, hi: float) -> None:
        ms = rng.uniform(lo, hi)
        d["blinks"].append(ms)
        d["closed"] = min(1.0, ms / 1000)

    if kind == "work":
        d["judged"] = rng.random() < u["judged_p"]
        if d["judged"] and rng.random() < u["blink_p"]:
            blink(u["blink_ms"], u["blink_ms"] * 0.18)
        if d["judged"] and rng.random() < u["lid_noise"]:          # lowered lids while reading
            d["closed"] = max(d["closed"], rng.uniform(0.5, 1.0))
        if (i + u["glance_offset"]) % u["glance_every"] < u["glance_len"]:
            d["away"], d["judged"] = True, False
        d["yawn"] = rng.random() < u["yawn_work"] / 60
        d["nod"] = rng.random() < u["nod_work"] / 60
    elif kind == "phone":
        d.update(judged=False, away=True)
        if rng.random() < u["phone_look_back"]:                     # a quick look back at the work
            d.update(away=False, judged=rng.random() < 0.5)
    elif kind in ("sleepy", "very_sleepy"):
        very = kind == "very_sleepy"
        d["judged"] = rng.random() < (u["judged_p"] * (0.75 if very else 0.9))
        if d["judged"]:
            r = rng.random()
            if very and r < u["micro_p"]:
                closure(1500, 3500)
            elif r < u["long_p"] * (1.3 if very else 1.0):
                closure(550, 1400)
            elif r < u["long_p"] + u["blink_p"]:
                blink(u["blink_ms"] * u["slow_factor"], u["blink_ms"] * 0.3)
        d["yawn"] = rng.random() < u["yawn_sleepy"] * (1.3 if very else 1.0) / 60
        d["nod"] = rng.random() < u["nod_sleepy"] * (2.5 if very else 1.0) / 60
        if rng.random() < 0.03:
            d["away"], d["judged"] = True, False
    elif kind == "leave":
        d.update(present=False, judged=False, away=True)
    return d


def simulate(phases: List[Tuple[str, int]], seed: int = 1, rate: float = 1.0,
             extra: Optional[Dict[int, Dict[str, Any]]] = None, aus: bool = False, t0: float = 1000.0,
             calibrated: bool = True, parts: Optional[List[Any]] = None,
             profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Features for the phases (kind, seconds), sampled `rate` times a second. `extra` overrides
    single seconds (index from 0), e.g. {70: {"yawn": True}}."""
    rng = random.Random(seed)
    if profile is None:
        secs = [_second(kind, i, rng) for kind, n in phases for i in range(n)]
    else:
        secs = [_second_user(kind, i, rng, profile) for kind, n in phases for i in range(n)]
    prof = profile or {}
    if profile is not None:                       # a long closure keeps the eyes shut for its whole length
        for k, sec in enumerate(secs):
            longest = max(sec["blinks"], default=0.0)
            for j in range(1, int(longest // 1000)):        # a d-second closure: floor(d) shut seconds
                if k + j < len(secs) and secs[k + j]["present"]:
                    secs[k + j].update(closed=1.0, judged=True, away=False, blinks=[])
    base_ms, fps = prof.get("blink_ms", 160.0), prof.get("fps", 15.0)
    au_rng = random.Random(seed + 1000)          # its own stream: the same behaviour with or without AUs
    for s in secs:
        s["au26"] = s["present"] and au_rng.random() < prof.get("au26_rate", 0.017)
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
                face["blink_mean_ms_rel" + sfx] = round(sum(blinks) / len(blinks) / base_ms, 3) if blinks else None
            face["long_blinks" + sfx] = sum(1 for b in blinks if b > 500)
            face["yawns" + sfx] = sum(1 for s in win if s["yawn"])
            face["nods" + sfx] = sum(1 for s in win if s["nod"])
            face["face_absent_ratio" + sfx] = round(1 - len(present) / n, 4)
            face["fps" + sfx] = float(fps)
            face["blink_confidence" + sfx] = "ok" if fps >= 10 else "low"
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
        if parts is not None:                     # the window summaries as the vision process logs them
            parts.append((round(t0 + t, 6), dict(face), dict(gaze), dict(au) if aus else None))
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


# --- simulated users: whole sessions as ordinary session folders -----------------------------------

PERSONAS = {
    "diligent": "works steadily; a short phone check every ~10 minutes",
    "distractible": "picks up the phone every 2-4 minutes, for 20-90 s",
    "tired": "works, then drowsiness comes in growing episodes; leaves near the end",
    "mixed": "a bit of everything: phone, drowsy spells, a break away from the desk",
}
MARK = {"phone": "phone_picked_up", "sleepy": "eyes_closing", "very_sleepy": "eyes_closing", "leave": "user_went_to_bed"}


def persona_phases(name: str, minutes: float, seed: int = 1) -> List[Tuple[str, int]]:
    """A random but reproducible sequence of (behaviour, seconds) for a persona."""
    rng = random.Random(seed)
    total, out = int(minutes * 60), []
    left = total

    def add(kind: str, seconds: float) -> None:
        nonlocal left
        n = int(max(5, min(seconds, left)))
        if n > 0 and left > 0:
            out.append((kind, n))
            left -= n

    add("work", 60)
    while left > 0:
        frac = 1 - left / total
        if name == "diligent":
            add("work", rng.uniform(420, 780)); add("phone", rng.uniform(15, 40))
        elif name == "distractible":
            add("work", rng.uniform(120, 240)); add("phone", rng.uniform(20, 90))
        elif name == "tired":
            add("work", rng.uniform(240, 480) * (1 - 0.6 * frac))
            add("sleepy", rng.uniform(40, 90) * (1 + 2 * frac))
            if frac > 0.85:
                add("leave", left)
        else:
            kind = rng.choice(["phone", "sleepy", "phone", "leave"])
            add("work", rng.uniform(150, 360))
            add(kind, rng.uniform(30, 120) if kind != "leave" else rng.uniform(60, 180))
    return out


def write_session(folder: Any, phases: List[Tuple[str, int]], seed: int = 1, aus: bool = True,
                  persona: str = "custom", profile: Optional[Dict[str, Any]] = None, calibrated: bool = True) -> Any:
    """Write a simulated session as a normal session folder (meta.json, events.jsonl with a marker
    at every behaviour change, events.vision.jsonl with the window summaries, truth.csv), so
    replay, explain and the tests treat it like a recording."""
    import json
    from pathlib import Path

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    t0 = 1000.0
    parts: List[Any] = []
    simulate(phases, seed=seed, aus=aus, t0=t0, parts=parts, profile=profile, calibrated=calibrated)
    with open(folder / "events.vision.jsonl", "w", encoding="utf-8") as f:
        seq = 0
        for t, face, gaze, au in parts:
            for kind, fields in (("face_window", face), ("gaze_window", gaze), ("au_window", au)):
                if fields is None:
                    continue
                seq += 1
                f.write(json.dumps({"v": 1, "seq": seq, "t_mono": t, "type": kind, "source": "perception",
                                    **{k: v for k, v in fields.items() if v is not None}}) + "\n")
    lines = [{"v": 1, "seq": 1, "t_mono": t0, "type": "session_start", "source": "wizard", "participant": "SIM",
              "mode": "hmm"}]
    truth, start, prev = [], 0, "work"
    for kind, n in phases:
        if kind != prev:
            marker = MARK.get(kind) or ("back_on_task" if prev == "phone" else None)
            if marker:
                lines.append({"v": 1, "seq": len(lines) + 1, "t_mono": t0 + start, "type": "annotation",
                              "source": "wizard", "marker": marker})
        truth.append((start, start + n, kind))
        start, prev = start + n, kind
    with open(folder / "events.jsonl", "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in lines) + "\n")
    with open(folder / "truth.csv", "w", encoding="utf-8") as f:
        f.write("from_s,to_s,behaviour\n" + "".join(f"{a},{b},{k}\n" for a, b, k in truth))
    (folder / "meta.json").write_text(json.dumps({"session": folder.name, "participant": "SIM", "mode": "hmm",
                                                  "simulated": {"persona": persona, "seed": seed}}, indent=1))
    return folder


TRUTH_STATES = {"work": ("focused",), "phone": ("distracted",), "sleepy": ("drowsy", "very_drowsy"),
                "very_sleepy": ("drowsy", "very_drowsy"), "leave": ("absent",)}


def score(folder: Any, params: Any = None) -> Dict[str, Any]:
    """How the HMM and the rules match the simulated truth, second by second, with allowance for
    reaction time: the first `grace` seconds of each behaviour are not counted."""
    import csv
    from pathlib import Path

    from .estimate import make_estimator
    from .replay import run, session_features
    from .runner import estimator_gap

    folder = Path(folder)
    feats = session_features(folder)
    with open(folder / "truth.csv", encoding="utf-8") as f:
        truth = [(float(r["from_s"]), float(r["to_s"]), r["behaviour"]) for r in csv.DictReader(f)]
    grace = {"work": 20, "phone": 15, "sleepy": 60, "very_sleepy": 60, "leave": 20}
    out: Dict[str, Any] = {}
    for name in ("rules", "hmm"):
        est = make_estimator(name, params=params) if name == "hmm" and params is not None else make_estimator(name)
        estimates = run(feats, est, estimator_gap(est))
        per: Dict[str, List[int]] = {}
        false_alarm = 0.0
        for e in estimates:
            t = e.t - 1000.0
            seg = next((s for s in truth if s[0] <= t < s[1]), None)
            if seg is None or t - seg[0] < grace[seg[2]]:
                continue
            hit = e.state in TRUTH_STATES[seg[2]]
            per.setdefault(seg[2], [0, 0])
            per[seg[2]][0] += hit
            per[seg[2]][1] += 1
            if seg[2] == "work" and e.state in ("distracted", "drowsy", "very_drowsy"):
                false_alarm += 1
        work_min = per.get("work", [0, 1])[1] / 60.0
        out[name] = {"per": {k: round(a / b, 3) for k, (a, b) in per.items()},
                     "false_alarm_s_per_hour": round(false_alarm / max(work_min, 1e-9) * 60, 1)}
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="python -m eepy.state.synthetic",
                                     description="Simulated users: write whole sessions to test the HMM on.")
    parser.add_argument("--persona", choices=sorted(PERSONAS), default="mixed")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--no-aus", action="store_true", help="without action units")
    parser.add_argument("--data", help="the data folder (default data/)")
    parser.add_argument("--all", action="store_true", help="every persona, then a score table")
    args = parser.parse_args(argv)
    from eepy.logs.session import DATA_DIR

    sessions = Path(args.data) / "sessions" if args.data else DATA_DIR
    names = sorted(PERSONAS) if args.all else [args.persona]
    print()
    for name in names:
        folder = sessions / f"2026-01-01_SIM-{name}-{args.seed}_hmm"
        write_session(folder, persona_phases(name, args.minutes, args.seed), seed=args.seed,
                      aus=not args.no_aus, persona=name)
        result = score(folder)
        print(f"  {folder.name}: {PERSONAS[name]}")
        for est in ("rules", "hmm"):
            r = result[est]
            per = "  ".join(f"{k} {v:.0%}" for k, v in sorted(r["per"].items()))
            print(f"    {est:5}  right per behaviour: {per}   false alarms while working: "
                  f"{r['false_alarm_s_per_hour']:.0f} s/hour")
    print("\n  Look at one: python -m eepy.state.explain SIM-<persona> --open"
          "\n  (simulated: shows how the model reacts, not how a real person looks)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
