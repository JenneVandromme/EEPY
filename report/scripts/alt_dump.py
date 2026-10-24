"""Step 1/3 of the learned-alternative experiment (.venv-robot): one row per second per simulated
user with the window features and the true state, for train / test / test_hard.

    .venv-robot/bin/python report/scripts/alt_dump.py report/data/alt_features.csv
"""
import csv
import sys

sys.path.insert(0, ".")
from eepy.state.dataset import _segment, dataset_dir, truth_of, users_of
from eepy.state.replay import session_features

ds = dataset_dir("v1")
rows, keys = [], set()
for split in ("train", "test", "test_hard"):
    for u in users_of(ds, [split]):
        folder = ds / u["folder"]
        truth = truth_of(folder)
        for f in session_features(folder):
            seg = _segment(truth, f["t"] - 1000.0)
            if seg is None:
                continue
            row = {k: v for k, v in f.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            row.update(user=u["folder"], split=split, truth=seg[2], since=round(f["t"] - 1000.0 - seg[0], 3))
            keys |= row.keys()
            rows.append(row)
cols = ["user", "split", "truth", "since", "t"] + sorted(keys - {"user", "split", "truth", "since", "t"})
with open(sys.argv[1], "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
print(len(rows), "rows,", len(cols), "columns")
