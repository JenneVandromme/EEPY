"""Belief trace of one simulated test user: truth, rules state, HMM belief, and the nudges of both modes."""
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, ".")
from eepy.behaviors.loader import load_library
from eepy.interaction.config import load as load_escalation
from eepy.interaction.simulate import run_estimates
from eepy.state.dataset import dataset_dir, truth_of
from eepy.state.estimate import make_estimator
from eepy.state.replay import run, session_features
from eepy.state.runner import estimator_gap, panel_view

user = sys.argv[2]
folder = dataset_dir("v1") / user
feats = session_features(folder)
lib, cfg = load_library(), load_escalation()
out = {"user": user, "truth": truth_of(folder)}
for kind in ("rules", "hmm"):
    est = make_estimator(kind)
    ests = [dict(panel_view(e)) for e in run(feats, est, estimator_gap(est))]
    t0 = ests[0]["t"] - 1.0
    variant = cfg if kind == "rules" else replace(cfg, belief=replace(cfg.belief, decisions="belief"))
    rec, _, _ = run_estimates(ests, [], variant, lib)
    out[kind] = {
        "t": [e["t"] - 1000.0 for e in ests],
        "state": [e["state"] for e in ests],
        "probs": [e.get("probs") for e in ests] if kind == "hmm" else None,
        "nudges": [(t - t0, f["ladder"], f["level"]) for k, t, f in rec.events if k == "nudge_decision"],
    }
Path(sys.argv[1]).write_text(json.dumps(out))
print("ok", user, len(out["hmm"]["t"]), out["rules"]["nudges"], out["hmm"]["nudges"])
