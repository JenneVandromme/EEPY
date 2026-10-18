"""The back end of the /v5 page (try/launcher-ui). Standard library only, like the rest of the launcher.

Three things the old page did not need:

* **The panel, through the launcher.** The page runs on the launcher's port, the Wizard panel on its
  own (8081), so the page cannot call the panel itself. ``panel()`` passes a fixed list of the
  panel's own calls through, unchanged (play, say, annotate, session start/end, calibration, ...).
  Nothing in the panel changes.
* **The robot's volume**, from the Reachy daemon's ``/api/volume`` (the simulator's daemon on this
  computer, or the real robot's).
* **Reading recorded sessions** (``data/sessions/<date>_<participant>_<mode>/``): ``events.jsonl``
  and ``state.csv``, as described in docs/DATA_GUIDE.md. Read only; nothing is written.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .parts import DAEMON_PORT, REPO, Settings, robot_host

SESSIONS = REPO / "data" / "sessions"

# The panel's own API (eepy/wizard/server.py create_app): only these are passed through.
PANEL_GET = {"state"}
PANEL_POST = {"play", "stop", "idle", "say", "annotate", "autonomy", "note", "session/start", "session/end",
              "calibration", "reload", "reconnect", "aim"}
SLOW = {"session/end": 25.0, "session/start": 15.0, "reconnect": 20.0, "reload": 15.0}

FOLDER = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
PARTICIPANT = re.compile(r"_(P[0-9]{2,3})_")
DESK_STATES = ("focused", "tired", "distracted", "drowsy", "very_drowsy")    # at the desk (not absent / unknown)


# --- the panel ------------------------------------------------------------------------

def _message(raw: bytes) -> str:
    """The panel's error message (WizardError: {"message"}; FastAPI validation: {"detail": [...]})."""
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return ""
    if isinstance(data, dict):
        if data.get("message"):
            return str(data["message"])
        detail = data.get("detail")
        if isinstance(detail, list) and detail:
            return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
        if detail:
            return str(detail)
    return ""


def _call(url: str, method: str, body: Optional[dict], timeout: float) -> Tuple[int, Dict[str, Any]]:
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read() or b"{}")
        return response.status, payload if isinstance(payload, dict) else {"ok": True, "data": payload}


def panel(settings: Settings, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, Dict[str, Any]]:
    """One of the panel's calls, through the launcher: (status code, JSON)."""
    allowed = PANEL_GET if method == "GET" else PANEL_POST
    if path not in allowed:
        return 404, {"ok": False, "message": f"not a panel call: {path!r}"}
    url = f"http://127.0.0.1:{settings.panel_port}/api/{path}"
    try:
        return _call(url, method, body, SLOW.get(path, 6.0))
    except urllib.error.HTTPError as exc:
        return exc.code, {"ok": False, "message": _message(exc.read()) or f"the panel said {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError):
        return 502, {"ok": False, "message": "My control panel isn't answering. Is it running?"}


# --- the robot's volume ---------------------------------------------------------------

def volume(settings: Settings, method: str, body: Optional[dict] = None) -> Tuple[int, Dict[str, Any]]:
    """The speaker volume (0-100) of the robot this setting uses. The simulator's daemon sets this
    computer's output volume; the real robot's daemon sets the robot's own speaker."""
    if settings.backend == "dry":
        return 409, {"ok": False, "message": "No robot in this setting, so there is no speaker to set."}
    host = robot_host(settings) if settings.backend == "real" else "127.0.0.1"
    base = f"http://{host}:{DAEMON_PORT}/api/volume"
    try:
        if method == "GET":
            code, data = _call(base + "/current", "GET", None, 2.0)
        else:
            level = int((body or {}).get("volume"))
            if not 0 <= level <= 100:
                raise ValueError
            code, data = _call(base + "/set", "POST", {"volume": level}, 6.0)
    except (TypeError, ValueError):
        return 400, {"ok": False, "message": "The volume goes from 0 to 100."}
    except urllib.error.HTTPError as exc:
        return exc.code, {"ok": False, "message": _message(exc.read()) or "The robot could not change its volume."}
    except (urllib.error.URLError, OSError):
        return 502, {"ok": False, "message": "My body isn't answering, so I can't reach my speaker."}
    return code, {"ok": True, "volume": data.get("volume"), "device": data.get("device"),
                  "platform": data.get("platform"), "simulator": settings.backend == "sim"}


# --- recorded sessions ----------------------------------------------------------------

def _events(folder: Path) -> List[Dict[str, Any]]:
    out = []
    try:
        lines = (folder / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue                       # the last line of a session that was killed may be cut
        if isinstance(record, dict) and isinstance(record.get("t_mono"), (int, float)):
            out.append(record)
    return out


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _states(folder: Path) -> List[Dict[str, Any]]:
    """state.csv: one row a second (t_mono, state, p_focused, ...)."""
    rows = []
    try:
        with (folder / "state.csv").open(encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                t = _float(row.get("t_mono"))
                if t is None:
                    continue
                rows.append({"t": t, "state": row.get("state") or "unknown",
                             "p_focused": _float(row.get("p_focused")), "confidence": _float(row.get("confidence")),
                             "estimator": row.get("estimator") or "rules"})
    except OSError:
        pass
    return rows


def _meta(folder: Path) -> Dict[str, Any]:
    try:
        data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clock(start_wall: Optional[str], start_mono: float, t: float) -> str:
    """t (t_mono) as the local time of day, from the session's start (t_wall at t_mono)."""
    try:
        moment = datetime.fromisoformat(str(start_wall)) + timedelta(seconds=t - start_mono)
        return moment.strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _focus(states: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How much of the time at the desk was focused (away / unknown seconds left out)."""
    desk = [r for r in states if r["state"] in DESK_STATES]
    focused = sum(1 for r in desk if r["state"] in ("focused", "tired"))     # tired is shown as focused
    return {"desk_s": len(desk), "focused_s": focused,
            "focused_share": round(focused / len(desk), 3) if desk else None}


def session_folders(root: Path = SESSIONS) -> List[Path]:
    if not root.is_dir():
        return []
    folders = [p for p in root.iterdir() if p.is_dir() and FOLDER.match(p.name) and (p / "events.jsonl").exists()]
    return sorted(folders, key=lambda p: p.name, reverse=True)       # <date>_..., newest first


def _summary(folder: Path, events: List[Dict[str, Any]], states: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta = _meta(folder)
    start = next((e for e in events if e.get("type") == "session_start"), events[0] if events else {})
    end = next((e for e in reversed(events) if e.get("type") == "session_end"), None)
    t0 = float(start.get("t_mono", 0.0))
    last = max([e["t_mono"] for e in events] + [r["t"] for r in states] + [t0])
    t1 = float(end["t_mono"]) if end else last
    found = PARTICIPANT.search(f"_{folder.name}_")
    outcomes: Dict[str, int] = {}
    for e in events:       # EEPY's own nudges, and the verdicts of the ones played by hand
        if e.get("type") == "nudge_outcome" or (e.get("type") == "nudge_reward" and e.get("source") == "wizard"):
            outcomes[str(e.get("outcome"))] = outcomes.get(str(e.get("outcome")), 0) + 1
    auto = sum(1 for e in events if e.get("type") == "nudge_decision")
    by_hand = sum(1 for e in events if e.get("type") == "wizard_nudge")
    # How it ended (the escalation's own events): they went to rest when EEPY suggested it, EEPY
    # suggested it (goodnight / wind-down) and they carried on, or the operator ended it.
    steps = [str(e.get("what")) for e in events if e.get("type") == "escalation"]
    rest = [w for w in steps if w in ("went_to_bed", "back_after_bed")]
    ending = ("rest" if rest and rest[-1] == "went_to_bed"
              else "suggested" if {"goodnight", "wind_down_started"} & set(steps) else "you")
    return {"name": folder.name, "participant": meta.get("participant") or start.get("participant") or (found.group(1) if found else ""),
            "mode": meta.get("mode") or start.get("mode") or "", "start": start.get("t_wall"),
            "date": str(start.get("t_wall") or "")[:10], "clock": _clock(start.get("t_wall"), t0, t0),
            "end_clock": _clock(start.get("t_wall"), t0, t1), "duration_s": round(max(0.0, t1 - t0)),
            "ended": end is not None, "end_reason": (end or {}).get("reason"), "ending": ending,
            "nudges": auto, "by_hand": by_hand, "outcomes": outcomes,
            "calibration": (meta.get("calibration") or {}).get("result") if isinstance(meta.get("calibration"), dict) else None,
            "has_state": bool(states), **_focus(states)}


def sessions(root: Path = SESSIONS, participant: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every recorded session, newest first, with its numbers (one line each)."""
    out = []
    for folder in session_folders(root):
        events = _events(folder)
        if not events:
            continue
        states = _states(folder)
        item = _summary(folder, events, states)
        if participant and item["participant"] != participant:
            continue
        t0 = float(next((e for e in events if e.get("type") == "session_start"), events[0])["t_mono"])
        item["strip"] = _strip(states, t0, shortest=60.0)               # a compact one, for the list
        item["marks_at"] = [round(e["t_mono"] - t0) for e in events if e.get("type") in ("nudge_decision", "wizard_nudge")]
        out.append(item)
    return out


def progress(root: Path = SESSIONS, participant: str = "") -> Dict[str, Any]:
    """Time won back: the latest session's focus against this participant's first 3 sessions
    (only sessions with a state.csv count). A before-and-after, not an experiment."""
    rows = [s for s in sessions(root, participant) if s["focused_share"] is not None]
    rows.reverse()                                                    # oldest first
    if len(rows) < 4:
        return {"participant": participant, "enough": False, "sessions": len(rows)}
    first = rows[:3]
    first_share = sum(s["focused_share"] for s in first) / 3
    latest = rows[-1]
    gain = latest["focused_share"] - first_share
    return {"participant": participant, "enough": True, "sessions": len(rows),
            "first_share": round(first_share, 3), "latest_share": latest["focused_share"], "latest": latest["name"],
            "gain_points": round(gain * 100), "minutes_more": round(gain * latest["desk_s"] / 60)}


def _curve(states: List[Dict[str, Any]], t0: float, points: int = 90) -> List[List[float]]:
    """P(focused) over the session, averaged into at most `points` steps: [[seconds, p], ...]."""
    values = [(r["t"] - t0, r["p_focused"] if r["p_focused"] is not None else (1.0 if r["state"] in ("focused", "tired") else 0.0))
              for r in states if r["state"] in DESK_STATES]
    if not values:
        return []
    size = max(1, len(values) // points)
    out = []
    for i in range(0, len(values), size):
        chunk = values[i:i + size]
        out.append([round(sum(c[0] for c in chunk) / len(chunk)), round(sum(c[1] for c in chunk) / len(chunk), 3)])
    return out


def _strip(states: List[Dict[str, Any]], t0: float, shortest: float = 15.0) -> List[Dict[str, Any]]:
    """The states as runs: [{"from", "to", "state"}], runs shorter than `shortest` merged away."""
    runs: List[Dict[str, Any]] = []
    for r in states:
        t = r["t"] - t0
        if runs and runs[-1]["state"] == r["state"]:
            runs[-1]["to"] = t + 1
        else:
            runs.append({"from": t, "to": t + 1, "state": r["state"]})
    merged: List[Dict[str, Any]] = []
    for run in runs:
        if merged and (run["to"] - run["from"] < shortest or merged[-1]["state"] == run["state"]):
            merged[-1]["to"] = run["to"]
        else:
            merged.append(dict(run))
    return [{"from": round(m["from"]), "to": round(m["to"]), "state": m["state"]} for m in merged]


def detail(root: Path = SESSIONS, name: str = "") -> Optional[Dict[str, Any]]:
    """One session's story: the numbers, the focus curve, the states, every moment EEPY (or you)
    stepped in with what came of it and why, your markers and notes."""
    if not FOLDER.match(name or ""):
        return None
    folder = root / name
    if folder not in session_folders(root):
        return None
    events, states = _events(folder), _states(folder)
    if not events:
        return None
    summary = _summary(folder, events, states)
    start = next((e for e in events if e.get("type") == "session_start"), events[0])
    t0, wall = float(start["t_mono"]), start.get("t_wall")
    outcomes = {e.get("decision_id"): e for e in events if e.get("type") == "nudge_outcome"}
    # A nudge played by hand has no decision_id of its own: its verdict is the nudge_reward
    # with decision_id "w1", "w2", ... in the order they were played (eepy/bandit/rewards.py).
    by_hand = {e.get("decision_id"): e for e in events if e.get("type") == "nudge_reward" and e.get("source") == "wizard"}
    moments, hand = [], 0
    for e in events:
        if e.get("type") not in ("nudge_decision", "wizard_nudge"):
            continue
        if e.get("type") == "nudge_decision":
            out = outcomes.get(e.get("decision_id"))
        else:
            hand += 1
            out = by_hand.get(f"w{hand}")
        context = e.get("context") if isinstance(e.get("context"), dict) else {}
        explanation = e.get("explanation") if isinstance(e.get("explanation"), dict) else {}
        moments.append({
            "t": round(e["t_mono"] - t0), "clock": _clock(wall, t0, e["t_mono"]),
            "by": "eepy" if e.get("type") == "nudge_decision" else "you",
            "ladder": e.get("ladder"), "level": e.get("level"), "behavior": e.get("behavior"), "tone": e.get("tone"),
            "reason": e.get("reason"), "outcome": (out or {}).get("outcome"),
            "seconds": (out or {}).get("seconds", (out or {}).get("latency")),
            "how": (out or {}).get("how"), "detail": (out or {}).get("detail"),
            "confidence": context.get("confidence"), "probs": context.get("probs"),
            "evidence": (context.get("evidence") or [])[:3] if isinstance(context.get("evidence"), list) else [],
            "sentence": explanation.get("sentence"), "spoken": explanation.get("spoken"),
            "contrast": (explanation.get("contrast") or {}).get("text") if isinstance(explanation.get("contrast"), dict) else None,
            "items": [{"type": i.get("type"), "share": i.get("share")} for i in (explanation.get("items") or [])[:4]
                      if isinstance(i, dict)],
            "propensity": e.get("propensity"),
        })
    marks = [{"t": round(e["t_mono"] - t0), "clock": _clock(wall, t0, e["t_mono"]), "marker": e.get("marker")}
             for e in events if e.get("type") == "annotation"]
    notes = [{"t": round(e["t_mono"] - t0), "clock": _clock(wall, t0, e["t_mono"]), "text": e.get("text")}
             for e in events if e.get("type") == "note"]
    return {**summary, "curve": _curve(states, t0), "strip": _strip(states, t0), "moments": moments,
            "marks": marks, "notes": notes}
