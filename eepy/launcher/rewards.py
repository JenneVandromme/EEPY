"""The launcher's Rewards tab (A22): live rewards, recorded sessions, simulations.

Live      the nudge_reward events of the running session (data/sessions/CURRENT)
Recorded  python -m eepy.bandit.rewards <sessions> --json --out data/rewards/<name>.csv
Simulated the same tool with --responsive / --synthetic / --scenario

It runs the same command you would type (in .venv-robot), so the terminal stays the fallback.
Standard library only.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .parts import REPO, venv_bin

SESSIONS = REPO / "data" / "sessions"
OUT_DIR = REPO / "data" / "rewards"
SIM_KINDS = {"responsive": "--responsive", "synthetic": "--synthetic", "scenario": "--scenario"}


def _lines(path: Path) -> List[Dict[str, Any]]:
    out = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def sessions() -> List[Dict[str, Any]]:
    """The recorded sessions, newest first, with how many nudges each has."""
    out = []
    if not SESSIONS.is_dir():
        return out
    folders = [p for p in SESSIONS.iterdir() if (p / "events.jsonl").exists()]
    for folder in sorted(folders, key=lambda p: (p / "events.jsonl").stat().st_mtime, reverse=True):
        try:
            text = (folder / "events.jsonl").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        mode = ""
        try:
            mode = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("mode") or ""
        except (OSError, ValueError):
            pass
        out.append({"name": folder.name, "mode": mode,
                    "nudges": text.count('"type": "nudge_decision"') + text.count('"type": "wizard_nudge"'),
                    "plays": text.count('"action": "play"')})
    return out


def live() -> Dict[str, Any]:
    """The running session's rewards so far."""
    try:
        current = json.loads((SESSIONS / "CURRENT").read_text(encoding="utf-8"))
        folder = Path(current["folder"])
    except (OSError, ValueError, KeyError):
        return {"session": None, "rows": []}
    records = _lines(folder / "events.jsonl")
    start = next((r["t_mono"] for r in records if r.get("type") == "session_start"), None)
    rows = []
    for r in records:
        if r.get("type") != "nudge_reward":
            continue
        rows.append({"t": None if start is None else round(r.get("t_decision", r["t_mono"]) - start),
                     "decision_id": r.get("decision_id"), "source": r.get("source"), "ladder": r.get("ladder"),
                     "level": r.get("level"), "tone": r.get("tone"), "outcome": r.get("outcome"),
                     "latency": r.get("latency"), "discount": r.get("discount"), "reward": r.get("reward"),
                     "detail": r.get("detail")})
    pending = sum(1 for r in records if r.get("type") in ("nudge_decision", "wizard_nudge")) - len(rows)
    return {"session": folder.name, "rows": rows, "open": max(0, pending)}


def command(body: Dict[str, Any]) -> List[str]:
    """The eepy.bandit.rewards command for a request from the page."""
    cmd = [str(venv_bin(".venv-robot", "python")), "-m", "eepy.bandit.rewards", "--json"]
    kind = body.get("kind")
    if kind == "recorded":
        names = body.get("sessions") or ["all"]
        if not isinstance(names, list) or not all(isinstance(n, str) and n and "/" not in n for n in names):
            raise ValueError("sessions: a list of session names, or [\"all\"]")
        cmd += names
        if body.get("rerun") in ("rules", "hmm"):
            cmd += ["--rerun", body["rerun"]]
        name = "all" if names == ["all"] else (names[0] if len(names) == 1 else f"{len(names)}_sessions")
    elif kind in SIM_KINDS:
        spec = str(body.get("spec") or "").strip()
        if not spec:
            raise ValueError("write a timeline first, e.g. \"focused 3m, distracted 6m\"")
        cmd += [SIM_KINDS[kind], spec]
        if kind == "responsive":
            persona = body.get("persona") or "average"
            runs = int(body.get("runs", 10) if body.get("runs") not in (None, "") else 10)
            if persona not in ("average", "responsive", "stubborn") or not 1 <= runs <= 200:
                raise ValueError("persona: average, responsive or stubborn; runs: 1 to 200")
            cmd += ["--persona", persona, "--runs", str(runs)]
        hour = body.get("start_hour")
        if hour not in (None, ""):
            cmd += ["--start-hour", str(float(hour))]
        name = f"sim_{kind}"
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    out = OUT_DIR / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{name}.csv"
    return cmd + ["--out", str(out)]


def run(body: Dict[str, Any], timeout: float = 600.0) -> Dict[str, Any]:
    cmd = command(body)
    shown = " ".join(shlex.quote(str(Path(w).relative_to(REPO)) if w.startswith(str(REPO)) else w) for w in cmd
                     if w != "--json")
    if not Path(cmd[0]).exists():
        return {"ok": False, "message": f"{cmd[0]} does not exist: set up .venv-robot (README, step 1).",
                "command": shown}
    try:
        done = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"still running after {timeout:.0f} s: stopped", "command": shown}
    if done.returncode != 0:
        return {"ok": False, "message": (done.stderr or done.stdout).strip()[-2000:] or f"exit {done.returncode}",
                "command": shown}
    try:
        report = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "message": "no report came back:\n" + done.stdout[-2000:], "command": shown}
    out: Optional[str] = report.get("out")
    if out:
        try:
            report["out"] = str(Path(out).relative_to(REPO))
        except ValueError:
            pass
    return {"ok": True, "report": report, "command": shown}
