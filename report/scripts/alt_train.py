"""Step 2/3 (.venv-vision): learned alternatives to the hand-set HMM, trained on the simulated
train users. Hyperparameters are chosen on a user-wise validation split of the train users
(never on test); the chosen model is refitted on all train users and predicts test / test_hard.

    .venv-vision/bin/python report/scripts/alt_train.py report/data
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATA = Path(sys.argv[1])
df = pd.read_csv(DATA / "alt_features.csv")
GRACE = {"focused": 20, "distracted": 15, "drowsy": 60, "very_drowsy": 45, "absent": 20}   # as the HMM's scoring
DROP = {"user", "split", "truth", "since", "t"}
feats = [c for c in df.columns if c not in DROP and df[c].notna().any()]

train = df[df.split == "train"]
users = sorted(train.user.unique())
rng = np.random.default_rng(1)
val_users = set(rng.choice(users, size=8, replace=False))
fit, val = train[~train.user.isin(val_users)], train[train.user.isin(val_users)]
scored = lambda d: d[d.since >= d.truth.map(GRACE)]          # noqa: E731  (score like the HMM)


def lr(c):
    return make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                         LogisticRegression(C=c, max_iter=2000, class_weight="balanced"))


def gb(lr_, leaves):
    return HistGradientBoostingClassifier(learning_rate=lr_, max_leaf_nodes=leaves, max_iter=300,
                                          class_weight="balanced", random_state=1)


grids = {"logreg": [({"C": c}, lambda c=c: lr(c)) for c in (0.01, 0.1, 1.0, 10.0)],
         "gboost": [({"learning_rate": a, "max_leaf_nodes": n}, lambda a=a, n=n: gb(a, n))
                    for a in (0.05, 0.1) for n in (15, 31, 63)]}
report = {"features": feats, "val_users": sorted(val_users), "n_fit_rows": len(fit), "n_val_rows": len(val),
          "models": {}}
preds = {}
for name, grid in grids.items():
    tried = []
    for params, make in grid:
        m = make().fit(fit[feats], fit.truth)
        v = scored(val)
        tried.append((balanced_accuracy_score(v.truth, m.predict(v[feats])), params, make))
        print(name, params, round(tried[-1][0], 4), flush=True)
    best = max(tried, key=lambda x: x[0])
    model = best[2]().fit(train[feats], train.truth)
    report["models"][name] = {"grid": [{"val_balanced_accuracy": round(s, 4), **p} for s, p, _ in tried],
                              "chosen": best[1]}
    for split in ("test", "test_hard"):
        d = df[df.split == split]
        p = model.predict(d[feats])
        for user, t, s in zip(d.user, d.t, p):
            preds.setdefault(name, {}).setdefault(user, []).append([float(t), str(s)])
    if name == "gboost":           # which inputs it leans on (permutation-free: split gain is not exposed)
        pass
(DATA / "alt_report.json").write_text(json.dumps(report, indent=1))
(DATA / "alt_predictions.json").write_text(json.dumps(preds))
print("done")
