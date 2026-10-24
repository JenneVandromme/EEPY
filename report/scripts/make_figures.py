"""Makes every data figure of the report from the JSON that run_eval.py / trace.py wrote.

    .venv-vision/bin/python report/scripts/make_figures.py report/data report/figures
"""
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

# Reference palette (dataviz skill), light mode; slots 1-3 validate all-pairs.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
ABSENT = "#c9c8c2"

plt.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8, "legend.fontsize": 7,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.edgecolor": MUTED, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02, "pdf.fonttype": 42,
})


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf")
    fig.savefig(OUT / f"{name}.png", dpi=220)
    plt.close(fig)
    print("wrote", name)


# ---------------------------------------------------------------- 1. belief trace of one simulated user
def belief_trace(user="U036_test_mixed"):
    d = json.loads((DATA / f"trace_{user}.json").read_text())
    h = d["hmm"]
    t = np.array(h["t"]) / 60
    fam = {"focused": [], "distracted": [], "sleepy": []}
    for p in h["probs"]:
        p = p or {}
        fam["focused"].append(p.get("focused", np.nan))
        fam["distracted"].append(p.get("distracted", np.nan))
        fam["sleepy"].append((p.get("drowsy", 0) + p.get("very_drowsy", 0)) if p else np.nan)
    colour = {"focused": BLUE, "distracted": ORANGE, "drowsy": AQUA, "very_drowsy": AQUA, "absent": ABSENT}

    fig, (ax0, ax1, ax2) = plt.subplots(3, 1, figsize=(7.0, 2.9), sharex=True,
                                        gridspec_kw={"height_ratios": [0.35, 2.2, 0.75], "hspace": 0.12})
    for a, b, s in d["truth"]:
        ax0.axvspan(a / 60, b / 60, color=colour[s], lw=0)
        if b - a > 50 and s != "focused":
            ax0.text((a + b) / 120, 0.5, s, ha="center", va="center", fontsize=6.5, color="white")
    ax0.set_yticks([]); ax0.grid(False)
    ax0.set_ylabel("truth", rotation=0, ha="right", va="center")
    for s in ("left", "bottom"):
        ax0.spines[s].set_visible(False)

    names = {"focused": "P(focused)", "distracted": "P(distracted)", "sleepy": "P(drowsy + very drowsy)"}
    for k, c in (("focused", BLUE), ("distracted", ORANGE), ("sleepy", AQUA)):
        ax1.plot(t, fam[k], color=c, lw=1.2, label=names[k])
    ax1.axhline(0.70, color=INK2, lw=0.8, ls=(0, (4, 3)))
    ax1.text(t[-1], 0.72, "act threshold 70 %", ha="right", va="bottom", fontsize=6.5, color=INK2)
    ax1.set_ylim(-0.02, 1.02); ax1.set_ylabel("HMM belief")
    ax1.legend(loc="center left", bbox_to_anchor=(0.0, 0.62), frameon=False, ncol=1)

    for row, (kind, lab) in enumerate((("rules", "rules"), ("hmm", "HMM + belief"))):
        for ts, ladder, level in d[kind]["nudges"]:
            c = ORANGE if ladder == "refocus" else AQUA
            ax2.plot(ts / 60, 1 - row, marker="v" if ladder == "refocus" else "o", ms=5.5, color=c,
                     mec="white", mew=0.8, ls="none")
    ax2.set_yticks([1, 0]); ax2.set_yticklabels(["rules", "HMM + belief"])
    ax2.set_ylim(-0.6, 1.6); ax2.set_xlabel("minutes into the simulated session")
    ax2.plot([], [], marker="v", color=ORANGE, ls="none", label="refocus nudge")
    ax2.plot([], [], marker="o", color=AQUA, ls="none", label="bedtime nudge")
    ax2.legend(loc="center left", bbox_to_anchor=(0.33, 0.5), frameon=False, ncol=1, handletextpad=0.3)
    ax2.grid(axis="y", visible=False)
    save(fig, "fig_belief_trace")


# ---------------------------------------------------------------- 2. confusion matrices
def confusion():
    d = json.loads((DATA / "state_eval.json").read_text())["test"]
    rows = ["focused", "distracted", "drowsy", "very_drowsy", "absent"]
    cols = rows + ["unknown"]
    lab = ["focused", "distracted", "drowsy", "very dr.", "absent", "unknown"]
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.25))
    for ax, (spec, title) in zip(axes, (("rules", "Rules (A13)"), ("hmm", "HMM v3 (A16)"))):
        c = d[spec]["confusion"]
        m = np.array([[c[r][k] for k in cols] for r in rows], float)
        n = m / m.sum(1, keepdims=True)
        ax.imshow(n, cmap="Blues", vmin=0, vmax=1, aspect="auto")
        for i in range(len(rows)):
            for j in range(len(cols)):
                if n[i, j] >= 0.005:
                    ax.text(j, i, f"{n[i, j]:.2f}", ha="center", va="center", fontsize=6.5,
                            color="white" if n[i, j] > 0.55 else INK)
        ax.set_xticks(range(len(cols))); ax.set_xticklabels(lab, rotation=30, ha="right")
        ax.set_yticks(range(len(rows))); ax.set_yticklabels(lab[:5])
        ax.grid(False)
        acc = d[spec]["accuracy"]; bal = d[spec]["balanced_accuracy"]
        ax.set_title(f"{title}: accuracy {acc:.1%}, balanced {bal:.1%}", color=INK)
        ax.set_xlabel("estimated state")
    axes[0].set_ylabel("true state")
    fig.tight_layout(w_pad=1.5)
    save(fig, "fig_confusion")


# ---------------------------------------------------------------- 3. bandit learning curves
PNAME = {"glance_responder": "Glance responder", "needs_words": "Needs words",
         "break_taker": "Break taker", "late_sleeper": "Late sleeper"}


def bandit():
    d = json.loads((DATA / "bandit_smoke.json").read_text())
    seeds = sorted(d, key=int)
    personas = [p["persona"] for p in d[seeds[0]]["personas"]]
    fig, axes = plt.subplots(1, 4, figsize=(7.0, 1.9), sharey=False)
    summary = []
    for ax, name in zip(axes, personas):
        diffs, fx_tot, bd_tot, found, buckets, fx_n, bd_n = [], [], [], 0, 0, [], []
        for s in seeds:
            p = next(x for x in d[s]["personas"] if x["persona"] == name)
            fx, bd = np.array(p["fixed"]["curve"]), np.array(p["bandit"]["curve"])
            diffs.append(np.cumsum(bd - fx))
            fx_tot.append(fx.sum()); bd_tot.append(bd.sum())
            fx_n.append(p["fixed"]["nudges"]); bd_n.append(p["bandit"]["nudges"])
            for b in p["learned"].values():
                if b["found"] is not None:
                    buckets += 1; found += bool(b["found"])
        D = np.array(diffs); x = np.arange(1, D.shape[1] + 1)
        ax.fill_between(x, D.min(0), D.max(0), color=BLUE, alpha=0.18, lw=0)
        ax.plot(x, D.mean(0), color=BLUE, lw=1.4)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.set_title(PNAME.get(name, name), color=INK)
        ax.set_xlabel("evening")
        ax.set_xlim(1, D.shape[1])
        summary.append({"persona": name, "fixed_total": np.mean(fx_tot), "bandit_total": np.mean(bd_tot),
                        "gain_pct": 100 * (np.mean(bd_tot) - np.mean(fx_tot)) / abs(np.mean(fx_tot)),
                        "gain_min": 100 * min((b - f) / abs(f) for b, f in zip(bd_tot, fx_tot)),
                        "gain_max": 100 * max((b - f) / abs(f) for b, f in zip(bd_tot, fx_tot)),
                        "found": found, "buckets": buckets,
                        "fixed_nudges": np.mean(fx_n), "bandit_nudges": np.mean(bd_n),
                        "even_at": int(np.argmax(D.mean(0) > 0) + 1) if (D.mean(0) > 0).any() else None,
                        "diff_30": float(D.mean(0)[29]), "diff_end": float(D.mean(0)[-1])})
    axes[0].set_ylabel("cumulative reward,\nbandit $-$ fixed")
    fig.tight_layout(w_pad=0.8)
    save(fig, "fig_bandit_curves")
    (DATA / "bandit_summary.json").write_text(json.dumps(summary, indent=1))
    for r in summary:
        print({k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()})


# ---------------------------------------------------------------- 4. deletion test
def deletion():
    d = json.loads((DATA / "xai_check.json").read_text())
    rows = d["rows"]
    fig, ax = plt.subplots(figsize=(3.35, 1.85))
    x = np.arange(len(rows)); w = 0.36
    p = [r["p"] for r in rows]; q = [r["p_without_top"] for r in rows]
    ax.bar(x - w / 2 - 0.01, p, w, color=BLUE, label="P(decided state)", zorder=2)
    ax.bar(x + w / 2 + 0.01, q, w, color=ORANGE, label="without its top reason", zorder=2)
    for i, (a, b) in enumerate(zip(p, q)):
        ax.text(i + w / 2 + 0.01, b + 0.02, f"{b:.0%}", ha="center", va="bottom", fontsize=6, color=INK2)
    ax.set_xticks(x)
    ax.set_xticklabels([f"#{i + 1}\n{r['ladder']} {r['level']}\n{'away' if r['top'] == 'looking_away' else r['top']}"
                        for i, r in enumerate(rows)], fontsize=5.8)
    ax.set_ylim(0, 1.12); ax.set_yticks([0, 0.5, 1]); ax.set_yticklabels(["0", "50 %", "100 %"])
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.22), ncol=2)
    ax.grid(axis="x", visible=False)
    save(fig, "fig_deletion")


belief_trace()
confusion()
bandit()
deletion()


# ---------------------------------------------------------------- 5. data exploration (V2)
def exploration():
    import pandas as pd
    df = pd.read_csv(DATA / "alt_features.csv")
    order = ["focused", "distracted", "drowsy", "very_drowsy", "absent"]
    stats = {}
    for split, d in df.groupby("split"):
        stats[split] = {"users": int(d.user.nunique()), "hours": round(len(d) / 3600, 2),
                        "share": {s: round(float((d.truth == s).mean()), 3) for s in order}}
    (DATA / "dataset_stats.json").write_text(json.dumps(stats, indent=1))
    print(stats)
    tr = df[(df.split == "train") & (df.since >= 60)]          # windows wholly inside one behaviour
    show = [("perclos_60s", "eyes closed, share of last 60 s"), ("gaze_off_ratio_60s", "gaze off work, share of 60 s"),
            ("yawns_60s", "yawns in last 60 s"), ("nods_60s", "nods in last 60 s")]
    lab = ["focused", "distracted", "drowsy", "very dr."]
    fig, axes = plt.subplots(1, 4, figsize=(7.0, 1.7))
    for ax, (col, title) in zip(axes, show):
        data = [tr.loc[tr.truth == s, col].dropna().values for s in order[:4]]
        bp = ax.boxplot(data, widths=0.55, patch_artist=True, showfliers=False,
                        medianprops={"color": INK, "lw": 1.0}, whiskerprops={"color": MUTED},
                        capprops={"color": MUTED})
        for patch, c in zip(bp["boxes"], (BLUE, ORANGE, AQUA, AQUA)):
            patch.set(facecolor=c, alpha=0.55, edgecolor=c)
        ax.set_xticks(range(1, 5)); ax.set_xticklabels(lab, rotation=30, ha="right")
        ax.set_title(title, color=INK, fontsize=7.5)
        ax.grid(axis="x", visible=False)
    fig.tight_layout(w_pad=0.6)
    save(fig, "fig_exploration")


def confusion3():
    d = json.loads((DATA / "state_eval.json").read_text())["test"]
    rows = ["focused", "distracted", "drowsy", "very_drowsy", "absent"]
    cols = rows + ["unknown"]
    lab = ["focused", "distracted", "drowsy", "very dr.", "absent", "unknown"]
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.05))
    for ax, (spec, title) in zip(axes, (("rules", "Threshold rules"), ("hmm", "Hand-set HMM (ours)"),
                                        ("gboost", "Gradient boosting"))):
        c = d[spec]["confusion"]
        m = np.array([[c[r][k] for k in cols] for r in rows], float)
        n = m / m.sum(1, keepdims=True)
        ax.imshow(n, cmap="Blues", vmin=0, vmax=1, aspect="auto")
        for i in range(len(rows)):
            for j in range(len(cols)):
                if n[i, j] >= 0.005:
                    ax.text(j, i, f"{n[i, j]:.2f}", ha="center", va="center", fontsize=5.8,
                            color="white" if n[i, j] > 0.55 else INK)
        ax.set_xticks(range(len(cols))); ax.set_xticklabels(lab, rotation=35, ha="right", fontsize=6)
        ax.set_yticks(range(len(rows))); ax.set_yticklabels(lab[:5] if spec == "rules" else [""] * 5, fontsize=6)
        ax.grid(False)
        ax.set_title(f"{title}\nacc. {d[spec]['accuracy']:.1%}, bal. {d[spec]['balanced_accuracy']:.1%}",
                     color=INK, fontsize=7.5)
    axes[0].set_ylabel("true state")
    fig.tight_layout(w_pad=0.4)
    save(fig, "fig_confusion3")


exploration()
confusion3()
