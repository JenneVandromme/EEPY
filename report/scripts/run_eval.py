"""Re-runs every evaluation used in the report and dumps JSON (run with .venv-robot from the repo root)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
what = sys.argv[2:] or ["state", "nudges", "bandit", "xai"]


def dump(name, obj):
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))
    print("wrote", name, flush=True)


if "state" in what:
    from eepy.state.dataset import dataset_dir, run_model, users_of
    ds = dataset_dir("v1")
    res = {}
    for split in ("test", "test_hard"):
        folders = [ds / u["folder"] for u in users_of(ds, [split])]
        res[split] = {"n_users": len(folders)}
        for spec in ("rules", "hmm"):
            res[split][spec] = run_model(spec, folders)
    dump("state_eval", res)

if "nudges" in what:
    from eepy.state.dataset import dataset_dir, run_nudges, users_of
    ds = dataset_dir("v1")
    res = {}
    for split in ("test", "test_hard"):
        folders = [ds / u["folder"] for u in users_of(ds, [split])]
        res[split] = {**run_nudges("rules", folders), **run_nudges("hmm", folders)}
    dump("nudge_eval", res)

if "bandit" in what:
    from eepy.bandit.smoke import run
    seeds = [1, 2, 3, 4, 5]
    res = {}
    for s in seeds:
        t = time.time()
        res[s] = run(sessions=100, seed=s)
        print("seed", s, round(time.time() - t), "s", flush=True)
        dump("bandit_smoke", res)

if "xai" in what:
    from eepy.explanations.check import run_check, _decisions_from_log
    from eepy.logs.check import resolve
    from eepy.logs.features import read_prior
    from eepy.logs.session import DATA_DIR
    from eepy.state.hmm_params import load
    from eepy.state.hmm_prior import resolve as resolve_prior
    from eepy.state.replay import session_features
    folder = resolve("2026-10-15_P99_hmm_11", DATA_DIR)
    feats = session_features(folder)
    rows = run_check(feats, _decisions_from_log(folder), prior=resolve_prior(read_prior(folder), load()))
    dump("xai_check", {"session": folder.name, "rows": rows})
