"""The launcher's Decisions tab (A23): every nudge decision with the allowed set next to it.

Runs python -m eepy.interaction.decisions ... --json (in .venv-robot), the same command you
would type, and hands the rows to the page. Standard library only.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .parts import REPO, venv_bin

KINDS = {"scenario": "--scenario", "responsive": "--responsive", "synthetic": "--synthetic"}


def command(body: Dict[str, Any]) -> List[str]:
    cmd = [str(venv_bin(".venv-robot", "python")), "-m", "eepy.interaction.decisions", "--json"]
    kind = body.get("kind")
    if kind == "session":
        name = str(body.get("session") or "")
        if not name or "/" in name or name.startswith("."):
            raise ValueError("pick a session")
        return cmd + [name]
    if kind in KINDS:
        spec = str(body.get("spec") or "").strip()
        if not spec:
            raise ValueError('write a scenario first, e.g. "focused 15m, distracted 6m"')
        cmd += [KINDS[kind], spec]
        if kind == "responsive" and body.get("seed") not in (None, ""):
            cmd += ["--seed", str(int(body["seed"]))]
        if body.get("bandit"):
            cmd += ["--bandit"]
            evenings = int(body.get("evenings") or 0)
            if kind == "responsive" and 0 < evenings <= 200:
                cmd += ["--evenings", str(evenings)]
        return cmd
    raise ValueError(f"unknown kind: {kind!r}")


def run(body: Dict[str, Any], timeout: float = 300.0) -> Dict[str, Any]:
    cmd = command(body)
    shown = " ".join(shlex.quote(str(Path(w).relative_to(REPO)) if w.startswith(str(REPO)) else w)
                     for w in cmd if w != "--json")
    if not Path(cmd[0]).exists():
        return {"ok": False, "message": f"{cmd[0]} does not exist: set up .venv-robot.", "command": shown}
    try:
        done = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"still running after {timeout:.0f} s: stopped", "command": shown}
    if done.returncode != 0:
        return {"ok": False, "message": (done.stderr or done.stdout).strip()[-2000:], "command": shown}
    try:
        data = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "message": "no rows came back", "command": shown}
    return {"ok": True, "command": shown, **data}
