"""Step 3/3 (.venv-robot): score the learned alternatives with exactly the scorer the HMM and the
rules are scored with (eepy.state.dataset.evaluate / combine), and add them to state_eval.json.

    .venv-robot/bin/python report/scripts/alt_score.py report/data
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")
from eepy.state.dataset import combine, dataset_dir, evaluate, truth_of, users_of

DATA = Path(sys.argv[1])
preds = json.loads((DATA / "alt_predictions.json").read_text())
ds = dataset_dir("v1")
out = json.loads((DATA / "state_eval.json").read_text())
for split in ("test", "test_hard"):
    for name, per_user in preds.items():
        results = []
        for u in users_of(ds, [split]):
            ests = [SimpleNamespace(t=t, state=s) for t, s in per_user[u["folder"]]]
            results.append(evaluate(ests, truth_of(ds / u["folder"])))
        out[split][name] = combine(results)
        r = out[split][name]
        print(split, name, r["accuracy"], r["balanced_accuracy"], r["false_alarm_s_per_hour"],
              r["changes_per_hour"], r["recall"])
    for spec in ("rules", "hmm"):
        r = out[split][spec]
        print(split, spec, r["accuracy"], r["balanced_accuracy"], r["false_alarm_s_per_hour"], r["changes_per_hour"])
(DATA / "state_eval.json").write_text(json.dumps(out, indent=1))
