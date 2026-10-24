"""(.venv-robot) Is the HMM's confidence calibrated? Family-level confidence vs. being right, on the
simulated test users (scored seconds only, same grace times as the accuracy)."""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from eepy.state.dataset import GRACE, _segment, dataset_dir, truth_of, users_of
from eepy.state.estimate import make_estimator
from eepy.state.replay import run, session_features
from eepy.state.runner import estimator_gap, panel_view
FAM = {"focused": "focused", "distracted": "distracted", "drowsy": "sleepy", "very_drowsy": "sleepy"}
ds, out = dataset_dir("v1"), {}
for split in ("test", "test_hard"):
    pairs = []
    for u in users_of(ds, [split]):
        f = ds / u["folder"]; truth = truth_of(f)
        est = make_estimator("hmm")
        for e in run(session_features(f), est, estimator_gap(est)):
            v = panel_view(e); p = v.get("probs")
            seg = _segment(truth, e.t - 1000.0)
            if not p or seg is None or seg[2] not in FAM or e.t - 1000.0 - seg[0] < GRACE[seg[2]] or v["state"] in ("absent", "unknown"):
                continue
            fam = {"focused": p["focused"], "distracted": p["distracted"], "sleepy": p["drowsy"] + p["very_drowsy"]}
            k = max(fam, key=fam.get)
            pairs.append((fam[k], k == FAM[seg[2]]))
    bins = [(0.4, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.97), (0.97, 1.001)]
    rows, ece = [], 0.0
    for a, b in bins:
        sel = [(c, ok) for c, ok in pairs if a <= c < b]
        if sel:
            conf = sum(c for c, _ in sel) / len(sel); acc = sum(ok for _, ok in sel) / len(sel)
            ece += len(sel) / len(pairs) * abs(conf - acc)
            rows.append({"bin": f"{a:.2f}-{min(b,1):.2f}", "n": len(sel), "confidence": round(conf, 3), "accuracy": round(acc, 3)})
    out[split] = {"ece": round(ece, 4), "n": len(pairs), "bins": rows}
    print(split, out[split])
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
