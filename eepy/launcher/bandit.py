"""The launcher's Bandit tab (A24): what the bandit learned per participant, reset, and the
smoke simulation. Runs python -m eepy.bandit / eepy.bandit.smoke in .venv-robot (the same
commands you would type) and hands the JSON to the page. Standard library only.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .parts import REPO, venv_bin

PARTICIPANT = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _run(args: List[str], timeout: float = 600.0) -> Dict[str, Any]:
    cmd = [str(venv_bin(".venv-robot", "python")), "-m"] + args
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
        return {"ok": True, "command": shown, **json.loads(done.stdout.strip().splitlines()[-1])}
    except (ValueError, IndexError):
        return {"ok": False, "message": "no answer came back", "command": shown}


def command(body: Dict[str, Any]) -> List[str]:
    kind = body.get("kind")
    if kind == "list":
        return ["eepy.bandit", "list", "--json"]
    if kind in ("show", "reset"):
        who = str(body.get("participant") or "")
        if not PARTICIPANT.match(who):
            raise ValueError("pick a participant (letters, digits, _ or -)")
        return ["eepy.bandit", kind, who, "--json"] + (["--yes"] if kind == "reset" else [])
    if kind == "smoke":
        sessions = int(body["sessions"]) if body.get("sessions") not in (None, "") else 30
        if not 1 <= sessions <= 300:
            raise ValueError("evenings per persona: 1 to 300")
        args = ["eepy.bandit.smoke", "--sessions", str(sessions), "--json"]
        if body.get("persona"):
            if not PARTICIPANT.match(str(body["persona"])):
                raise ValueError("unknown persona")
            args += ["--persona", str(body["persona"])]
        return args
    raise ValueError(f"unknown kind: {kind!r}")


def run(body: Dict[str, Any]) -> Dict[str, Any]:
    return _run(command(body))
