"""The explanation viewer: how the HMM came to its decisions, second by second.

    python -m eepy.state.explain latest              # a recorded session -> an HTML page
    python -m eepy.state.explain P03 --open          # and open it in the browser
    python -m eepy.state.explain --demo              # a simulated session (no recording needed)

It replays the HMM over the session's logged features (the same estimates as live:
replay is deterministic) and writes ONE self-contained HTML page: no internet, no
server, nothing installed. The page shows the decision pipeline

    signs  ->  belief (4 states)  ->  shown state  ->  act? (the confidence gate)  ->  robot

for any second, and a timeline of the whole session (the belief, the shown states of
the HMM and the rules, the operator's markers, the robot's nudges) to scrub through or
play in slow motion. Guide: docs/EXPLANATION_VIEWER.md.

Needs numpy (the HMM): .venv-vision or .venv-robot. The demo's robot actions need the
behaviour library (.venv-robot); elsewhere the demo shows the HMM only.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .estimate import StateEstimate
from .hmm_params import HIDDEN, HMMParams

VIEWER = Path(__file__).with_name("explain_viewer.html")
DEMO = [("work", 60), ("phone", 45), ("work", 40), ("sleepy", 150), ("leave", 40)]
DEMO_PARTS = ["work normally", "on the phone", "back to work", "acting sleepy", "left the desk"]


# --- building the data --------------------------------------------------------------------------


def _skipped(estimate: StateEstimate, ids: Sequence[str]) -> Dict[str, str]:
    """{feature id: why it was skipped} from the estimate's skipped list."""
    out: Dict[str, str] = {}
    for line in estimate.skipped:
        fid, _, why = line.partition(": ")
        if fid in ids and fid not in out:
            out[fid] = why
    return out


FAMILY_INDEX = {"focused": 0, "distracted": 1, "sleepy": 2}


def _step(e: StateEstimate, t0: float, ids: Sequence[str], rules_state: Optional[str]) -> Dict[str, Any]:
    """One second, compactly. ev: [feature index, supported family index (-1: none),
    push per family per window (3), effect this second per family (3), value text, no-news]."""
    return {
        "t": round(e.t - t0, 3),
        "p": [round(e.scores.get(s, 0.0), 4) for s in HIDDEN],
        "s": e.state, "ml": e.most_likely, "c": None if e.confidence is None else round(e.confidence, 3),
        "u": e.uncertainty, "pend": e.pending, "up": e.updated, "cal": e.calibrated,
        "ph": list(e.values["hidden"].values()) if "hidden" in e.values else None,
        "lvl": e.values.get("drowsiness_level"),
        "r": [r.text for r in e.reasons],
        "ev": [[ids.index(d["feature"]), FAMILY_INDEX.get(d["supports"], -1),
                [round(v, 2) for v in d.get("push") or [0, 0, 0]],
                [round(v, 3) for v in d.get("step") or [0, 0, 0]], d.get("show") or "", int(bool(d["quiet"]))]
               for d in e.evidence],
        "sk": _skipped(e, ids),
        "rules": rules_state,
    }


def _events(folder: Path, t0: float) -> List[Dict[str, Any]]:
    """What happened besides the state: the operator's markers and the robot's decisions."""
    from .replay import _records

    out: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("events*.jsonl")):
        for r in _records(path):
            kind, t = r.get("type"), r.get("t_mono")
            if t is None:
                continue
            item: Dict[str, Any] = {"t": round(float(t) - t0, 3)}
            if kind == "annotation" and r.get("marker"):
                item.update(kind="marker", text=str(r["marker"]).replace("_", " "))
            elif kind == "nudge_decision":
                conf = (r.get("context") or {}).get("confidence")
                item.update(kind="nudge", ladder=r.get("ladder"), level=r.get("level"), tone=r.get("tone"),
                            id=r.get("decision_id"), confidence=conf,
                            text=f"{r.get('ladder')} level {r.get('level')} ({r.get('tone')}): {r.get('behavior')}")
            elif kind == "nudge_outcome":
                item.update(kind="outcome", id=r.get("decision_id"), outcome=r.get("outcome"),
                            text=f"outcome of nudge {r.get('decision_id')}: {str(r.get('outcome')).replace('_', ' ')}")
            elif kind == "role_changed":
                item.update(kind="role", role=r.get("to"), text=f"role: {str(r.get('to')).replace('_', ' ')}")
            elif kind == "escalation":
                what = str(r.get("what"))
                if what == "waiting_for_confidence":
                    item.update(kind="gate_wait", ladder=r.get("ladder"),
                                text=f"held back: {r.get('state')} {float(r.get('confidence') or 0):.0%} "
                                     f"< {float(r.get('needed') or 0):.0%}")
                elif what == "confidence_wait_ended":
                    item.update(kind="gate_end", ladder=r.get("ladder"), text=f"gate: {r.get('reason')}")
                else:
                    item.update(kind="escalation", text=what.replace("_", " "))
            elif kind == "behavior_started" and r.get("source") == "wizard":
                item.update(kind="operator", text=f"operator played {r.get('behavior')}")
            else:
                continue
            out.append(item)
    return sorted(out, key=lambda d: d["t"])


def _thresholds(folder: Optional[Path]) -> Dict[str, Any]:
    """The confidence gate's thresholds: from the session's snapshot of config/escalation.yaml
    when it has them; else today's file, marked as such (sessions from before A16 had no gate)."""
    from eepy.interaction.config import load as load_escalation

    def read(path: Optional[Path]) -> Optional[Dict[str, Any]]:
        try:
            cfg = load_escalation(path)
        except Exception:  # noqa: BLE001 -- an old or broken snapshot: try the next source
            return None
        return {"act": dict(cfg.act_confidence), "direct": cfg.direct_confidence} if cfg.act_confidence else None

    snap = folder / "snapshot" / "config" / "escalation.yaml" if folder is not None else None
    found = read(snap) if snap is not None and snap.exists() else None
    if found is not None:
        return dict(found, source="session")
    found = read(None)
    if found is not None:
        return dict(found, source="session" if folder is None else "today")
    return {"act": {}, "direct": None, "source": "none"}


def build(estimates: Sequence[StateEstimate], params: HMMParams, t0: float,
          rules: Optional[Sequence[StateEstimate]] = None, events: Optional[List[Dict[str, Any]]] = None,
          meta: Optional[Dict[str, Any]] = None, thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the page needs, JSON-ready."""
    ids = [f.id for f in params.features]
    rules_at = {round(e.t, 3): e.state for e in (rules or [])}
    feats = [{"id": f.id, "label": f.label, "group": f.group, "family": f.family, "tau": f.tau,
              "enabled": f.enabled} for f in params.features]
    groups = {g: rho for g, rho in params.groups.items()}
    return {
        "meta": dict(meta or {}, params={"path": params.path, "version": params.version,
                                         "source": params.source},
                     states=list(HIDDEN), hidden=list(params.states), reports=list(params.reports),
                     switch=params.switch_probability,
                     thresholds=thresholds or {"act": {}, "direct": None}),
        "features": feats,
        "groups": groups,
        "rates": [list(r) for r in params.rates_per_hour],
        "steps": [_step(e, t0, ids, rules_at.get(round(e.t, 3))) for e in estimates],
        "events": events or [],
    }


def from_session(folder: Path, params: HMMParams, prior: Any = "session") -> Dict[str, Any]:
    """The explanation of a recorded session. prior: "session" (its prior.json), None, or a dict."""
    from eepy.logs.features import read_prior

    from .estimate import make_estimator
    from .hmm_prior import resolve
    from .replay import live_estimator, run, session_features, session_start
    from .runner import estimator_gap

    features = session_features(folder)
    if not features:
        raise ValueError(f"{folder.name} has no face features (events.vision.jsonl with face_window). "
                         "Was the vision program running during the session?")
    hmm = make_estimator("hmm", params=params)
    applied = resolve(read_prior(folder) if prior == "session" else prior, params)
    hmm.set_session_prior(applied)
    estimates = run(features, hmm, estimator_gap(hmm))
    rules_est = make_estimator("rules")
    rules = run(features, rules_est, estimator_gap(rules_est))
    t0 = session_start(folder) or features[0]["t"]
    meta_raw: Dict[str, Any] = {}
    try:
        meta_raw = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    notes = []
    mode = meta_raw.get("mode")
    if mode != "hmm":
        notes.append(f'This session ran in the "{mode}" mode: the robot acted on the '
                     f"{live_estimator(folder)} estimate, not on this HMM. The belief shows what the HMM "
                     "would have said; the robot's actions are what really happened.")
    if params.path != "config/state_hmm.yaml":
        notes.append(f"HMM numbers from {params.path}, not the shipped file: the belief may differ from "
                     "what the robot saw live.")
    meta = {"title": folder.name, "session": folder.name, "mode": mode, "participant": meta_raw.get("participant"),
            "prior": applied.as_dict(), "notes": notes, "demo": False, "parts": []}
    return build(estimates, params, t0, rules, _events(folder, t0), meta, _thresholds(folder))


def demo(params: HMMParams, seed: int = 1) -> Dict[str, Any]:
    """A simulated session (work, phone, back, acting sleepy, leaving) with the robot's
    escalation run in virtual time when the behaviour library is available."""
    from .estimate import make_estimator
    from .replay import run
    from .runner import estimator_gap
    from .synthetic import simulate

    t0 = 1000.0
    features = simulate(DEMO, seed=seed, aus=True, t0=t0)
    hmm = make_estimator("hmm", params=params)
    estimates = run(features, hmm, estimator_gap(hmm))
    rules_est = make_estimator("rules")
    rules = run(features, rules_est, estimator_gap(rules_est))
    events: List[Dict[str, Any]] = []
    parts, start = [], 0
    for (kind, seconds), title in zip(DEMO, DEMO_PARTS):
        parts.append({"t": start, "end": start + seconds, "text": title})
        events.append({"t": float(start), "kind": "marker", "text": title})
        start += seconds
    notes = ["A simulated session (eepy/state/synthetic.py), not a real person: the signs are made up "
             "to show how the HMM reacts."]
    thresholds = _thresholds(None)
    try:
        events += _demo_robot(estimates, t0)
        notes.append("The robot's actions are the real escalation (config/escalation.yaml) run in virtual "
                     "time, without the 3-minute settling-in period.")
    except Exception as exc:  # noqa: BLE001 -- no behaviour library here (it needs .venv-robot, Python 3.10+)
        notes.append(f"No robot actions: the behaviour library needs .venv-robot ({type(exc).__name__}: {exc}).")
    meta = {"title": "Demo: a simulated session", "session": None, "mode": "hmm", "participant": None,
            "prior": None, "notes": notes, "demo": True, "parts": parts}
    return build(estimates, params, t0, rules, sorted(events, key=lambda d: d["t"]), meta, thresholds)


def _demo_robot(estimates: Sequence[StateEstimate], t0: float) -> List[Dict[str, Any]]:
    """The real escalation engine over the demo's estimates, in virtual time."""
    import copy

    import yaml

    from eepy.behaviors.loader import load_library
    from eepy.interaction.actuator import SimActuator
    from eepy.interaction.config import DEFAULT_PATH, check_library, parse
    from eepy.interaction.engine import EscalationEngine
    from eepy.interaction.policy import FixedEscalation
    from eepy.interaction.simulate import VirtualClock

    from .runner import panel_view

    raw = copy.deepcopy(yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8")))
    raw["guards"]["after_calibration"] = 0
    config = parse(raw)
    library = load_library()
    check_library(config, library)
    clock = VirtualClock(t0)
    logged: List[Dict[str, Any]] = []

    def log(event_type: str, t: float, **fields: Any) -> None:
        logged.append({"type": event_type, "t_mono": t, **fields})

    engine = EscalationEngine(config, FixedEscalation(config), SimActuator(library, clock), log)
    engine.session_started(t0)
    for e in estimates:
        clock.now = e.t
        engine.on_state(panel_view(e), e.t)
    # Reuse the recorded-session parser on what was logged.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        path.write_text("\n".join(json.dumps(r, default=str) for r in logged), encoding="utf-8")
        return _events(Path(tmp), t0)


# --- the page -----------------------------------------------------------------------------------


def render(data: Dict[str, Any]) -> str:
    """The self-contained HTML page with the data inside."""
    page = VIEWER.read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    title = str(data["meta"].get("title") or "EEPY explanation").replace("<", "").replace("&", "and")
    return page.replace("/*__DATA__*/null", blob).replace("__TITLE__", title)


def default_out(name: str, data_dir: Optional[Path] = None) -> Path:
    from eepy.logs.session import DATA_DIR

    sessions = Path(data_dir) if data_dir is not None else DATA_DIR
    return sessions.parent / "explain" / f"{name}.html"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eepy.state.explain",
                                     description="Write a page that explains the HMM's decisions, second by second.")
    parser.add_argument("session", nargs="?", help='a session folder name, a unique part of it, or "latest"')
    parser.add_argument("--demo", action="store_true", help="a simulated session instead (no recording needed)")
    parser.add_argument("--params", help="another HMM parameter file (default config/state_hmm.yaml)")
    parser.add_argument("--prior", help="a session prior JSON file, or none (default: the session's prior.json)")
    parser.add_argument("--out", help="where to write the page (default data/explain/<session>.html)")
    parser.add_argument("--data", help="the data folder (default data/)")
    parser.add_argument("--open", action="store_true", help="open the page in the browser afterwards")
    args = parser.parse_args(argv)
    if bool(args.session) == bool(args.demo):
        print("\n  Give a session (e.g. latest) or --demo.", file=sys.stderr)
        return 2

    from .hmm_params import HMMConfigError, load

    try:
        params = load(Path(args.params) if args.params else None)
        if args.demo:
            data = demo(params)
            name = "demo"
            sessions_dir = Path(args.data) / "sessions" if args.data else None
        else:
            from eepy.logs.check import resolve as resolve_session
            from eepy.logs.session import DATA_DIR

            sessions_dir = Path(args.data) / "sessions" if args.data else DATA_DIR
            folder = resolve_session(args.session, sessions_dir)
            prior: Any = "session"
            if args.prior and args.prior.lower() == "none":
                prior = None
            elif args.prior:
                from .replay import read_prior_file

                prior = read_prior_file(Path(args.prior))
            data = from_session(folder, params, prior)
            name = folder.name
    except (FileNotFoundError, HMMConfigError, ValueError) as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else default_out(name, sessions_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    seconds = data["steps"][-1]["t"] - data["steps"][0]["t"] if data["steps"] else 0
    print(f"\n  {len(data['steps'])} steps ({seconds / 60:.1f} minutes), {len(data['events'])} events")
    print(f"  Written: {out}")
    print("  Open it in any browser (double-click). It needs no internet.\n")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
