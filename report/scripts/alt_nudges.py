"""(.venv-robot) The learned alternatives' states through the real escalation (timer-based, as the rules
mode: they give no calibrated belief), scored like the rules and the HMM in nudge_eval.json.

    .venv-robot/bin/python report/scripts/alt_nudges.py report/data
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from eepy.behaviors.loader import load_library
from eepy.interaction.config import load as load_escalation
from eepy.interaction.simulate import run_estimates
from eepy.state.dataset import combine_nudges, dataset_dir, score_nudges, truth_of, users_of
from eepy.state.estimate import make_estimator
from eepy.state.replay import run, session_features
from eepy.state.runner import estimator_gap, panel_view

DATA = Path(sys.argv[1])
preds = json.loads((DATA / "alt_predictions.json").read_text())
lib, cfg = load_library(), load_escalation()
ds = dataset_dir("v1")
out = json.loads((DATA / "nudge_eval.json").read_text())
for split in ("test", "test_hard"):
    res = {name: [] for name in preds}
    for u in users_of(ds, [split]):
        folder = ds / u["folder"]
        est = make_estimator("rules")
        base = [dict(panel_view(e)) for e in run(session_features(folder), est, estimator_gap(est))]
        t0, truth = base[0]["t"] - 1.0, truth_of(folder)
        for name, per_user in preds.items():
            by_t = {round(t, 3): s for t, s in per_user[u["folder"]]}
            ests, last, since = [], None, 0.0
            for b in base:
                s = by_t.get(round(b["t"], 3), b["state"])
                if s != last:
                    since, last = b["t"], s
                e = dict(b, state=s, pending=None, rules=[], reason="", fired={}, since=round(b["t"] - since, 3))
                ests.append(e)
            rec, _, _ = run_estimates(ests, [], cfg, lib)
            nudges = [(t - t0, f["ladder"]) for k, t, f in rec.events if k == "nudge_decision"]
            night = next((t - t0 for k, t, f in rec.events if k == "escalation" and f.get("what") == "goodnight"), None)
            res[name].append(score_nudges(nudges, truth, 10.0, settle=cfg.after_calibration, until=night))
    for name, r in res.items():
        out[split][name] = combine_nudges(r)
        print(split, name, out[split][name])
(DATA / "nudge_eval.json").write_text(json.dumps(out, indent=1))
