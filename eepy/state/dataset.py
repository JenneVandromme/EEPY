"""A synthetic dataset to train and compare state-estimator versions on.

    python -m eepy.state.dataset build                       # data/synthetic/v1: 48 simulated users
    python -m eepy.state.dataset train                       # fit HMM numbers on the train users
    python -m eepy.state.dataset eval                        # rules vs shipped HMM vs trained HMM, on test
    python -m eepy.state.dataset eval --model hmm:my.yaml    # any parameter file

Every simulated user has their own habits (how often the eyes are judged, blink length,
glances, lowered lids while reading, how drowsiness shows, camera speed, calibrated or
not, action units or not) and a persona that decides the course of a 20-40 minute
session (work, phone, drowsy, very drowsy, leaving). Each user is a normal session folder
with a second-by-second ground truth (truth.csv), so replay and the explanation viewer work
on it too.

Splits: train (fit numbers), test (same kind of users, never trained on) and test_hard
(users with more extreme habits: is a version robust, or only good at the average user?).

Honest limit: the users come from a simulator written with the same ideas as the model.
A version that wins here is better *under these assumptions*; real recordings stay the
judge. Use it to compare versions quickly and to catch regressions. Guide:
docs/SYNTHETIC_DATASET.md.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .synthetic import write_session

VERSION = "eepy.synthetic/1"
STATES5 = ("focused", "distracted", "drowsy", "very_drowsy", "absent")
TRUTH = {"work": "focused", "phone": "distracted", "sleepy": "drowsy", "very_sleepy": "very_drowsy",
         "leave": "absent"}
FAMILY = {"focused": "focused", "distracted": "distracted", "drowsy": "sleepy", "very_drowsy": "sleepy",
          "absent": "absent", "unknown": "unknown"}
# Seconds after a change of behaviour that are not scored: any estimator needs time to see it.
GRACE = {"focused": 20, "distracted": 15, "drowsy": 60, "very_drowsy": 45, "absent": 20}
PERSONAS = ("diligent", "distractible", "tired", "mixed")


# --- the users ------------------------------------------------------------------------------------


def user_profile(rng: random.Random, hard: bool = False) -> Dict[str, Any]:
    """One simulated user's habits. hard: wider, more extreme ranges (for test_hard)."""
    u = lambda lo, hi, hlo=None, hhi=None: rng.uniform(hlo if hard and hlo is not None else lo,  # noqa: E731
                                                       hhi if hard and hhi is not None else hi)
    return {
        "judged_p": u(0.55, 0.95, 0.35, 0.95),     # share of work seconds with the eyes judged
        "blink_p": u(0.15, 0.35),                  # blinks per judged second
        "blink_ms": u(120, 220, 100, 260),         # this person's usual blink
        "lid_noise": u(0.0, 0.04, 0.0, 0.10),      # judged seconds that read as (half) closed while reading
        "glance_every": rng.randint(10, 30) if not hard else rng.randint(6, 30),
        "glance_len": rng.randint(1, 3) if not hard else rng.randint(1, 4),
        "glance_offset": rng.randint(0, 29),
        "yawn_work": u(0.0, 0.08, 0.0, 0.25),      # per minute while focused (boredom, tiredness)
        "nod_work": u(0.0, 0.05, 0.0, 0.15),       # quick looks down that read as nods
        "phone_look_back": u(0.0, 0.15, 0.0, 0.3),
        "long_p": u(0.15, 0.35, 0.06, 0.35),       # long closures per judged second when drowsy
        "micro_p": u(0.08, 0.25, 0.04, 0.25),      # >= 1.5 s closures when very drowsy
        "slow_factor": u(1.3, 2.2, 1.1, 2.4),      # how much slower blinks get when drowsy
        "yawn_sleepy": u(0.3, 1.5, 0.0, 1.5),
        "nod_sleepy": u(0.2, 1.0, 0.0, 1.2),
        "au26_rate": u(0.01, 0.03, 0.005, 0.06),
        "fps": rng.choice([15.0] * 8 + [12.0, 10.0, 8.0] + ([6.0] if hard else [])),
        "calibrated": rng.random() < (0.5 if hard else 0.8),
        "aus": rng.random() < 0.6,
    }


def persona_phases(name: str, minutes: float, rng: random.Random) -> List[Tuple[str, int]]:
    """The course of one session: (behaviour, seconds)."""
    total = int(minutes * 60)
    out: List[Tuple[str, int]] = []
    left = total

    def add(kind: str, seconds: float) -> None:
        nonlocal left
        n = int(max(8, min(seconds, left)))
        if left > 0:
            out.append((kind, min(n, left)))
            left -= min(n, left)

    add("work", rng.uniform(60, 180))
    while left > 0:
        frac = 1 - left / total
        if name == "diligent":
            add("work", rng.uniform(300, 720)); add("phone", rng.uniform(10, 45))
            if rng.random() < 0.15:
                add("leave", rng.uniform(60, 300))
        elif name == "distractible":
            add("work", rng.uniform(90, 240)); add("phone", rng.uniform(20, 120))
        elif name == "tired":
            add("work", rng.uniform(180, 420) * (1 - 0.5 * frac))
            add("sleepy", rng.uniform(60, 150) * (1 + frac))
            if frac > 0.4 and rng.random() < 0.6:
                add("very_sleepy", rng.uniform(30, 120))
            if frac > 0.85:
                add("leave", left)
        else:
            add("work", rng.uniform(120, 360))
            kind = rng.choice(["phone", "phone", "sleepy", "leave"])
            if kind == "sleepy":
                add("sleepy", rng.uniform(60, 180))
                if rng.random() < 0.4:
                    add("very_sleepy", rng.uniform(20, 90))
            else:
                add(kind, rng.uniform(20, 120) if kind == "phone" else rng.uniform(60, 240))
    return out


def dataset_dir(name: str, data: Optional[Path] = None) -> Path:
    from eepy.logs.session import DATA_DIR

    root = Path(data) if data is not None else DATA_DIR.parent
    return root / "synthetic" / name


def build(out: Path, users: int = 48, minutes: Tuple[float, float] = (20, 40), seed: int = 1,
          hard_users: int = 12, say: Any = print) -> Dict[str, Any]:
    """Write the dataset: `users` normal users (70 % train, 30 % test) plus `hard_users`."""
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {"format": VERSION, "seed": seed, "created": time.strftime("%Y-%m-%d %H:%M"),
                                "users": []}
    n_train = int(round(users * 0.7))
    plan = [("train" if k < n_train else "test", False) for k in range(users)] + [("test_hard", True)] * hard_users
    for k, (split, hard) in enumerate(plan):
        urng = random.Random(rng.randrange(10 ** 9))
        profile = user_profile(urng, hard)
        persona = PERSONAS[k % len(PERSONAS)]
        phases = persona_phases(persona, urng.uniform(*minutes), urng)
        uid = f"U{k + 1:03d}"
        folder = out / f"{uid}_{split}_{persona}"
        write_session(folder, phases, seed=urng.randrange(10 ** 9), aus=profile["aus"], persona=persona,
                      profile=profile, calibrated=profile["calibrated"])
        manifest["users"].append({"id": uid, "split": split, "persona": persona, "folder": folder.name,
                                  "seconds": sum(n for _, n in phases), "profile": profile})
        if say:
            say(f"  {uid} {split:9} {persona:12} {sum(n for _, n in phases) / 60:4.0f} min")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def load_manifest(folder: Path) -> Dict[str, Any]:
    path = folder / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"No dataset in {folder}. Build one: python -m eepy.state.dataset build")
    return json.loads(path.read_text(encoding="utf-8"))


def truth_of(folder: Path) -> List[Tuple[float, float, str]]:
    with open(folder / "truth.csv", encoding="utf-8") as f:
        return [(float(r["from_s"]), float(r["to_s"]), TRUTH[r["behaviour"]]) for r in csv.DictReader(f)]


def users_of(ds: Path, splits: Iterable[str]) -> List[Dict[str, Any]]:
    wanted = set(splits)
    return [u for u in load_manifest(ds)["users"] if "all" in wanted or u["split"] in wanted]


# --- evaluation -----------------------------------------------------------------------------------


def _segment(truth: Sequence[Tuple[float, float, str]], t: float) -> Optional[Tuple[float, float, str]]:
    for seg in truth:
        if seg[0] <= t < seg[1]:
            return seg
    return None


def evaluate(estimates: Sequence[Any], truth: Sequence[Tuple[float, float, str]], t0: float = 1000.0) -> Dict[str, Any]:
    """Second-by-second and per-episode scores of one estimator on one user."""
    conf = {a: {b: 0 for b in STATES5 + ("unknown",)} for a in STATES5}
    seconds_work, false_alarm, changes, last = 0, 0, 0, None
    for e in estimates:
        t = e.t - t0
        if last is not None and e.state != last:
            changes += 1
        last = e.state
        seg = _segment(truth, t)
        if seg is None or t - seg[0] < GRACE[seg[2]]:
            continue
        conf[seg[2]][e.state] += 1
        if seg[2] == "focused":
            seconds_work += 1
            false_alarm += e.state in ("distracted", "drowsy", "very_drowsy")
    episodes: Dict[str, List[Optional[float]]] = {s: [] for s in ("distracted", "drowsy", "very_drowsy", "absent")}
    recovery: List[Optional[float]] = []
    for k, (a, b, state) in enumerate(truth):
        inside = [e for e in estimates if a <= e.t - t0 < b]
        if state in episodes:
            want = {"distracted": ("distracted",), "drowsy": ("drowsy", "very_drowsy"),
                    "very_drowsy": ("very_drowsy",), "absent": ("absent",)}[state]
            hit = next((e.t - t0 - a for e in inside if e.state in want), None)
            episodes[state].append(hit)
        if state == "focused" and k > 0 and truth[k - 1][2] in ("drowsy", "very_drowsy"):
            back = next((e.t - t0 - a for e in inside if e.state == "focused"), None)
            recovery.append(back if back is not None else b - a)      # never: the whole stretch
    return {"confusion": conf, "work_s": seconds_work, "false_alarm_s": false_alarm, "changes": changes,
            "seconds": len(estimates), "episodes": episodes, "recovery": recovery}


def combine(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals over users -> the summary numbers."""
    conf = {a: {b: sum(r["confusion"][a][b] for r in results) for b in STATES5 + ("unknown",)} for a in STATES5}
    recall = {a: (conf[a][a] / max(1, sum(conf[a].values()))) for a in STATES5 if sum(conf[a].values())}
    fam_hit = sum(v for a in STATES5 for b, v in conf[a].items() if FAMILY[a] == FAMILY[b])
    total = sum(sum(conf[a].values()) for a in STATES5)
    exact = sum(conf[a][a] for a in STATES5)
    work_s = sum(r["work_s"] for r in results)
    eps = {s: [d for r in results for d in r["episodes"][s]] for s in ("distracted", "drowsy", "very_drowsy", "absent")}
    rec = [d for r in results for d in r["recovery"]]

    def med(xs: List[float]) -> Optional[float]:
        return round(statistics.median(xs), 1) if xs else None

    return {
        "accuracy": round(exact / max(1, total), 4),
        "family_accuracy": round(fam_hit / max(1, total), 4),
        "balanced_accuracy": round(sum(recall.values()) / max(1, len(recall)), 4),
        "recall": {k: round(v, 3) for k, v in recall.items()},
        "false_alarm_s_per_hour": round(sum(r["false_alarm_s"] for r in results) / max(1, work_s) * 3600, 1),
        "changes_per_hour": round(sum(r["changes"] for r in results) / max(1, sum(r["seconds"] for r in results)) * 3600, 1),
        "detected": {s: round(sum(d is not None for d in v) / len(v), 3) for s, v in eps.items() if v},
        "delay_median_s": {s: med([d for d in v if d is not None]) for s, v in eps.items() if v},
        "recovery_median_s": med(rec),
        "confusion": conf,
    }


def run_model(spec: str, folders: Sequence[Path]) -> Dict[str, Any]:
    """spec: rules | hmm | hmm:PATH. Returns the combined scores."""
    from .estimate import make_estimator
    from .hmm_params import load
    from .replay import run, session_features
    from .runner import estimator_gap

    results = []
    params = None
    if spec.startswith("hmm"):
        params = load(Path(spec.split(":", 1)[1]) if ":" in spec else None)
    for folder in folders:
        est = make_estimator("hmm", params=params) if params is not None else make_estimator("rules")
        estimates = run(session_features(folder), est, estimator_gap(est))
        results.append(evaluate(estimates, truth_of(folder)))
    return combine(results)


# --- nudges: the escalation on top of the estimates (A19) -------------------------------------------

NUDGE_TOLERANCE = 10.0                                  # s around a change of behaviour (configurable)
MIN_LENGTHS = {"distracted": (45, 60, 90), "drowsy": (90, 120, 180)}
NUDGE_LADDER = {"distracted": "refocus", "drowsy": "bedtime"}


def _episodes(truth: Sequence[Tuple[float, float, str]], kind: str) -> List[Tuple[float, float]]:
    """Stretches of one kind; drowsy and very drowsy count as one drowsy episode."""
    want = ("drowsy", "very_drowsy") if kind == "drowsy" else (kind,)
    out: List[Tuple[float, float]] = []
    for a, b, state in truth:
        if state in want:
            if out and abs(out[-1][1] - a) < 1e-6:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return out


def score_nudges(nudges: Sequence[Tuple[float, str]], truth: Sequence[Tuple[float, float, str]],
                 tolerance: float = NUDGE_TOLERANCE, settle: float = 0.0,
                 until: Optional[float] = None) -> Dict[str, Any]:
    """nudges: (t from the session start, ladder). A false nudge comes while the truth is focused;
    with the tolerance, not within `tolerance` s of a change of behaviour (just refocused, or just
    starting). Episodes that end before `settle` (the escalation's settling-in time after the start)
    or start after `until` (the goodnight: the robot sleeps for the rest of the session) cannot be
    caught by design and are left out."""
    changes = [a for a, _, _ in truth[1:]]
    work_s = sum(b - a for a, b, state in truth if state == "focused")
    false = [t for t, _ in nudges if (_segment(truth, t) or (0, 0, ""))[2] == "focused"]
    false_tol = [t for t in false if all(abs(t - c) > tolerance for c in changes)]
    episodes: Dict[str, List[Tuple[float, Optional[float]]]] = {}
    for kind, ladder in NUDGE_LADDER.items():
        rows = []
        for a, b in _episodes(truth, kind):
            if b <= settle or (until is not None and a >= until):
                continue
            start = max(a, settle)
            hit = next((t - a for t, lad in nudges if lad == ladder and start <= t <= b + tolerance), None)
            rows.append((b - start, hit))
        episodes[kind] = rows
    return {"work_s": work_s, "false": len(false), "false_tol": len(false_tol), "nudges": len(nudges),
            "seconds": truth[-1][1] if truth else 0.0, "episodes": episodes}


def combine_nudges(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    work = sum(r["work_s"] for r in results)
    total = sum(r["seconds"] for r in results)
    out: Dict[str, Any] = {
        "nudges_per_hour": round(sum(r["nudges"] for r in results) / max(1, total) * 3600, 1),
        "false_per_hour": round(sum(r["false"] for r in results) / max(1, work) * 3600, 2),
        "false_per_hour_tolerant": round(sum(r["false_tol"] for r in results) / max(1, work) * 3600, 2),
        "missed": {}, "eligible": {}, "first_nudge_median_s": {}}
    for kind, lengths in MIN_LENGTHS.items():
        rows = [row for r in results for row in r["episodes"][kind]]
        for n in lengths:
            sel = [hit for length, hit in rows if length >= n]
            out["eligible"][f"{kind}>={n}"] = len(sel)
            out["missed"][f"{kind}>={n}"] = round(sum(h is None for h in sel) / len(sel), 3) if sel else None
        hits = [hit for length, hit in rows if length >= lengths[0] and hit is not None]
        out["first_nudge_median_s"][kind] = round(statistics.median(hits), 1) if hits else None
    return out


def run_nudges(spec: str, folders: Sequence[Path], tolerance: float = NUDGE_TOLERANCE,
               escalation: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """The escalation (config/escalation.yaml) over each user's estimates, in virtual time.
    rules -> one column; hmm[:PATH] -> two: A16's confidence gate and A19's belief decisions."""
    from dataclasses import replace

    from eepy.behaviors.loader import load_library
    from eepy.interaction.config import load as load_escalation
    from eepy.interaction.simulate import run_estimates

    from .estimate import make_estimator
    from .hmm_params import load
    from .replay import run, session_features
    from .runner import estimator_gap, panel_view

    library = load_library()
    config = load_escalation(escalation) if escalation else load_escalation()
    params = load(Path(spec.split(":", 1)[1]) if ":" in spec else None) if spec.startswith("hmm") else None
    if params is None:
        variants = {"rules": config}
    else:
        variants = {"gate": replace(config, belief=None)}
        if config.belief is not None:
            variants["belief"] = replace(config, belief=replace(config.belief, decisions="belief"))
    results: Dict[str, List[Dict[str, Any]]] = {k: [] for k in variants}
    for folder in folders:
        est = make_estimator("hmm", params=params) if params is not None else make_estimator("rules")
        estimates = [dict(panel_view(e)) for e in run(session_features(folder), est, estimator_gap(est))]
        if not estimates:
            continue
        t0, truth = estimates[0]["t"] - 1.0, truth_of(folder)
        for name, cfg in variants.items():
            recorder, _, _ = run_estimates(estimates, [], cfg, library)
            nudges = [(t - t0, f["ladder"]) for kind, t, f in recorder.events if kind == "nudge_decision"]
            night = next((t - t0 for kind, t, f in recorder.events
                          if kind == "escalation" and f.get("what") == "goodnight"), None)
            results[name].append(score_nudges(nudges, truth, tolerance, settle=cfg.after_calibration, until=night))
    return {k: combine_nudges(v) for k, v in results.items()}


def _nudge_table(scores: Dict[str, Dict[str, Any]], tolerance: float, say: Any = print) -> None:
    names = list(scores)
    w = max(14, max(len(n) for n in names) + 2)

    def pct(v: Optional[float]) -> str:
        return "-" if v is None else f"{v:.0%}"

    rows = [
        ("nudges per hour (all)", lambda s: f"{s['nudges_per_hour']:.1f}", "-"),
        ("false nudges / hour of work", lambda s: f"{s['false_per_hour']:.2f}", "lower"),
        (f"  ... not within {tolerance:g} s of a change", lambda s: f"{s['false_per_hour_tolerant']:.2f}", "lower"),
        *[(f"missed {k.replace('>=', ' >= ')} s", (lambda k: lambda s: pct(s["missed"].get(k)))(k), "lower")
          for kind, lengths in MIN_LENGTHS.items() for k in (f"{kind}>={n}" for n in lengths)],
        *[(f"first nudge, {kind} (median)", (lambda k: lambda s: "-" if s["first_nudge_median_s"].get(k) is None
                                             else f"{s['first_nudge_median_s'][k]:.0f} s")(kind), "lower")
          for kind in MIN_LENGTHS],
    ]
    say("  " + f"{'':38}" + "".join(f"{n:>{w}}" for n in names) + "   (better)")
    for label, fn, better in rows:
        say("  " + f"{label:38}" + "".join(f"{fn(scores[n]):>{w}}" for n in names) + f"   ({better})")
    first = scores[names[0]]["eligible"]
    say("\n  Episodes counted: " + ", ".join(f"{k.replace('>=', ' >= ')} s: {v}" for k, v in first.items()))


def _table(scores: Dict[str, Dict[str, Any]], say: Any = print) -> None:
    names = list(scores)
    w = max(14, max(len(n) for n in names) + 2)
    rows = [
        ("accuracy (5 states)", lambda s: f"{s['accuracy']:.1%}", "higher"),
        ("accuracy (families)", lambda s: f"{s['family_accuracy']:.1%}", "higher"),
        ("balanced accuracy", lambda s: f"{s['balanced_accuracy']:.1%}", "higher"),
        *[(f"  recall {k}", (lambda k: lambda s: f"{s['recall'].get(k, 0):.1%}")(k), "higher") for k in STATES5],
        ("false alarms (s/hour of work)", lambda s: f"{s['false_alarm_s_per_hour']:.0f}", "lower"),
        ("state changes per hour", lambda s: f"{s['changes_per_hour']:.0f}", "lower = calmer"),
        *[(f"  {k} episodes found", (lambda k: lambda s: f"{s['detected'].get(k, 0):.0%}")(k), "higher")
          for k in ("distracted", "drowsy", "very_drowsy", "absent")],
        *[(f"  {k}: median delay", (lambda k: lambda s: "-" if s['delay_median_s'].get(k) is None
                                     else f"{s['delay_median_s'][k]:.0f} s")(k), "lower")
          for k in ("distracted", "drowsy", "very_drowsy", "absent")],
        ("back to focused after drowsy", lambda s: "-" if s["recovery_median_s"] is None
         else f"{s['recovery_median_s']:.0f} s", "lower"),
    ]
    say("  " + f"{'':34}" + "".join(f"{n:>{w}}" for n in names) + "   (better)")
    for label, fn, better in rows:
        say("  " + f"{label:34}" + "".join(f"{fn(scores[n]):>{w}}" for n in names) + f"   ({better})")


# --- training (supervised, on the ground truth) ----------------------------------------------------


def _lag(spec: Any) -> int:
    """How long the truth must have been the same before a window value belongs to it."""
    if spec.input.endswith("_60s"):
        return 60
    if spec.input.endswith("_10s"):
        return 10
    return 3


def collect(folders: Sequence[Path], params: Any) -> Tuple[Dict[str, Dict[int, List[Any]]], Dict[str, Any]]:
    """Per feature and hidden state: the prepared observations of seconds whose window lies wholly
    inside one behaviour; and the transition counts / time per state."""
    from .hmm import HMMEstimator
    from .replay import session_features

    est = HMMEstimator(params)
    states = list(params.states)       # truth labels train the hidden states of the same name
    n = len(states)
    data: Dict[str, Dict[int, List[Any]]] = {f.id: {i: [] for i in range(n)} for f in params.features}
    counts = [[0.0] * n for _ in range(n)]
    time_in = [0.0] * n
    for folder in folders:
        truth = truth_of(folder)
        for f in session_features(folder):
            t = f["t"] - 1000.0
            seg = _segment(truth, t)
            if seg is None or seg[2] not in states:
                continue
            state = states.index(seg[2])
            obs, _ = est.extract(f)
            for o in obs:
                if t - seg[0] >= _lag(o.spec):
                    data[o.spec.id][state].append(o.x)
        prev: Optional[int] = None
        for a, b, state in truth:
            if state not in states:
                prev = None                         # leaving interrupts: no transition counted across it
                continue
            i = states.index(state)
            time_in[i] += (b - a) / 3600.0
            if prev is not None and prev != i:
                counts[prev][i] += 1
            prev = i
    return data, {"counts": counts, "hours": time_in}


def _blend(new: float, n: float, prior: float, n0: float) -> float:
    return (new * n + prior * n0) / (n + n0)


def fit(params: Any, data: Dict[str, Dict[int, List[Any]]], trans: Dict[str, Any], dataset_name: str,
        min_samples: int = 30, prior_windows: float = 10.0, prior_hours: float = 0.25) -> Any:
    """New parameters: each emission fitted on the labelled seconds (blended with the hand-set
    value as a prior, so a state with little data keeps its old numbers), and the transition
    rates from the truth's switches. Evidence times and correlations are kept."""
    from .hmm_params import parse

    states = list(params.states)
    raw = copy.deepcopy(params.raw)
    notes: List[str] = []
    for spec in params.features:
        node = raw["features"][spec.id]["params"]
        for i, state in enumerate(states):
            xs = data[spec.id][i]
            if len(xs) < min_samples:
                continue
            windows = len(xs) / max(1.0, spec.tau)           # roughly independent looks
            old = spec.params[i]
            entry = node[state]
            fam = spec.family
            if fam == "beta":
                lo, hi = spec.clip or (0.005, 0.995)
                v = [min(max(float(x), lo), hi) for x in xs]
                m = statistics.fmean(v)
                var = statistics.pvariance(v) or 1e-4
                conc = min(200.0, max(2.0, m * (1 - m) / var - 1))
                entry["mean"] = round(min(0.98, max(0.005, _blend(m, windows, old["mean"], prior_windows))), 4)
                entry["concentration"] = round(_blend(conc, windows, old["concentration"], prior_windows), 2)
            elif fam in ("student_t", "gaussian"):
                v = sorted(float(x) for x in xs)
                med = statistics.median(v)
                mad = statistics.median([abs(x - med) for x in v]) * 1.4826 or 0.1
                entry["mean"] = round(_blend(med, windows, old["mean"], prior_windows), 4)
                entry["sd"] = round(max(0.08, _blend(mad, windows, old["sd"], prior_windows)), 4)
            elif fam == "poisson":
                # Overlapping windows count each event several times, and their exposure too:
                # the ratio is still the rate per minute.
                k = sum(x[0] for x in xs)
                minutes = sum(x[1] for x in xs) or 1e-9
                entry["rate_per_minute"] = round(max(0.005, _blend(k / minutes, windows, old["rate_per_minute"],
                                                                   prior_windows)), 4)
            elif fam == "bernoulli":
                z = sum(int(x) for x in xs) / len(xs)
                entry["p"] = round(min(0.95, max(0.001, _blend(z, windows, old["p"], prior_windows))), 4)
            elif fam == "categorical":
                from .hmm_model import bin_index

                c = [0.0] * (len(spec.bins) + 1)
                for x in xs:
                    c[bin_index(float(x), spec.bins)] += 1
                tot = sum(c)
                probs = [_blend(ci / tot, windows, pi, prior_windows) for ci, pi in zip(c, old["probs"])]
                probs = [max(1e-4, p) for p in probs]
                s = sum(probs)
                probs = [round(p / s, 5) for p in probs]
                probs[-1] = round(1 - sum(probs[:-1]), 5)
                if probs[-1] <= 0:
                    continue
                entry["probs"] = probs
            entry["why"] = f"fitted: {len(xs)} s labelled {state} (synthetic {dataset_name}, train), blended with the hand-set value"
        notes.append(spec.id)
    counts, hours = trans["counts"], trans["hours"]
    for i, a in enumerate(states):
        for j, b in enumerate(states):
            if i == j or hours[i] <= 0:
                continue                             # a hidden state without its own label keeps its rates
            old = params.rates_per_hour[i][j]
            rate = (counts[i][j] + old * prior_hours) / (hours[i] + prior_hours)
            node = raw["transitions_per_hour"][a][b]
            node["value"] = round(max(0.01, rate), 3)
            node["why"] = (f"fitted: {int(counts[i][j])} switches in {hours[i]:.2f} h {a} (synthetic {dataset_name}, "
                           f"train), with the hand-set {old} as a prior")
    raw["version"] = int(raw.get("version", 1)) + 1
    raw["source"] = "fitted"
    raw["description"] = f"Fitted on the synthetic dataset {dataset_name} (train split) from version {params.version}."
    n_sec = sum(len(v) for d in data.values() for v in d.values())
    raw["fit"] = {"method": "supervised_counts", "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "parent": {"version": params.version}, "sessions": [dataset_name + "/train"], "n_sessions": 1,
                  "n_seconds": max(1, n_sec), "labels": "synthetic_truth", "log_likelihood": 0.0,
                  "fixed": ["states", "groups", "features.*.evidence_seconds", "features.*.needs"],
                  "notes": "synthetic users: compare versions with python -m eepy.state.dataset eval"}
    return parse(raw)


# --- the command ------------------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.state.dataset",
                                     description="Build, train on and evaluate a synthetic dataset of simulated users.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the simulated users")
    b.add_argument("--users", type=int, default=48, help="normal users (70 %% train, 30 %% test)")
    b.add_argument("--hard-users", type=int, default=12, help="extra test users with more extreme habits")
    b.add_argument("--minutes", type=float, nargs=2, default=(20, 40), metavar=("MIN", "MAX"))
    b.add_argument("--seed", type=int, default=1)
    t = sub.add_parser("train", help="fit HMM numbers on the train users")
    t.add_argument("--params", help="the starting parameter file (default config/state_hmm.yaml)")
    t.add_argument("--out", help="where to write (default data/synthetic/<name>/state_hmm_trained.yaml)")
    e = sub.add_parser("eval", help="score estimators on a split")
    e.add_argument("--split", default="test", help="train, test, test_hard or all (default test)")
    e.add_argument("--model", action="append",
                   help="rules, hmm (shipped) or hmm:PATH; repeat to compare (default: rules, hmm, and the trained file)")
    e.add_argument("--json", help="also write the scores as JSON")
    e.add_argument("--nudges", action="store_true",
                   help="score the escalation's nudges instead (rules, hmm with A16's gate, hmm with A19's "
                        "belief decisions): false nudges, missed episodes, time to the first nudge")
    e.add_argument("--tolerance", type=float, default=NUDGE_TOLERANCE,
                   help="--nudges: seconds around a change of behaviour where a nudge is not counted as false "
                        "(default 10)")
    e.add_argument("--escalation", help="--nudges: another escalation file (default config/escalation.yaml)")
    for p in (b, t, e):
        p.add_argument("--name", default="v1", help="the dataset's name (default v1)")
        p.add_argument("--data", help="the data folder (default data/)")
    args = parser.parse_args(argv)
    ds = dataset_dir(args.name, Path(args.data) if args.data else None)

    if args.cmd == "build":
        print(f"\n  Building {ds} ...")
        m = build(ds, args.users, tuple(args.minutes), args.seed, args.hard_users)
        hours = sum(u["seconds"] for u in m["users"]) / 3600
        splits = {s: sum(1 for u in m["users"] if u["split"] == s) for s in ("train", "test", "test_hard")}
        print(f"\n  {len(m['users'])} users, {hours:.1f} hours: " + ", ".join(f"{k} {v}" for k, v in splits.items()))
        print(f"  Next: python -m eepy.state.dataset train --name {args.name}\n")
        return 0

    from .hmm_params import HMMConfigError, load, save

    try:
        if args.cmd == "train":
            params = load(Path(args.params) if args.params else None)
            folders = [ds / u["folder"] for u in users_of(ds, ["train"])]
            print(f"\n  Training on {len(folders)} train users of {ds.name} ...")
            data, trans = collect(folders, params)
            fitted = fit(params, data, trans, ds.name)
            out = Path(args.out) if args.out else ds / "state_hmm_trained.yaml"
            save(fitted, out)
            print(f"  Written: {out}  (version {fitted.version}, source fitted)")
            print(f"  Compare: python -m eepy.state.dataset eval --name {args.name}\n")
            return 0
        models = args.model or ["rules", "hmm"] + (
            [f"hmm:{ds / 'state_hmm_trained.yaml'}"] if (ds / "state_hmm_trained.yaml").exists() else [])
        folders = [ds / u["folder"] for u in users_of(ds, [args.split])]
        if not folders:
            print(f"\n  No users in split {args.split}.", file=sys.stderr)
            return 2
        print(f"\n  {ds.name}, split {args.split}: {len(folders)} users, "
              f"{sum(1 for _ in folders)} sessions. Scoring {len(models)} model(s) ...\n")
        if args.nudges:
            nudge_scores: Dict[str, Dict[str, Any]] = {}
            for spec in (args.model or ["rules", "hmm"]):
                base = Path(spec.split(":", 1)[1]).stem if ":" in spec else spec
                for variant, result in run_nudges(spec, folders, args.tolerance,
                                                  Path(args.escalation) if args.escalation else None).items():
                    nudge_scores[base if variant == "rules" else f"{base} {variant}"] = result
            _nudge_table(nudge_scores, args.tolerance)
            print("\n  Missed: episodes of at least that length with no nudge of the matching ladder (refocus / "
                  "bedtime) before they ended (+ the tolerance); episodes inside the settling-in time after the "
                  "start or after a goodnight are left out. False: a nudge while the truth was focused.\n")
            if args.json:
                Path(args.json).write_text(json.dumps(nudge_scores, indent=1), encoding="utf-8")
            return 0
        scores: Dict[str, Dict[str, Any]] = {}
        for spec in models:
            label = "trained" if spec.endswith("state_hmm_trained.yaml") else (
                Path(spec.split(":", 1)[1]).stem if ":" in spec else spec)
            scores[label] = run_model(spec, folders)
        _table(scores)
        print(f"\n  Scored after a grace time per change ({', '.join(f'{k} {v} s' for k, v in GRACE.items())}).")
        print("  Synthetic users: a better score means better under the simulator's assumptions; check on real "
              "recordings too.\n")
        if args.json:
            Path(args.json).write_text(json.dumps(scores, indent=1), encoding="utf-8")
        return 0
    except (FileNotFoundError, HMMConfigError, ValueError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
