"""Tests for the launcher (eepy/launcher): the commands it builds, starting and stopping
programs, telling a crash from a stop, and noticing programs started in a terminal.

The "programs" here are tiny Python scripts that listen on a free port, so nothing
real (robot, camera, simulator) is started.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from eepy.launcher.__main__ import Launcher, _handler
from eepy.launcher.parts import REPO, LogBook, Part, Settings, panel_command, port_open, shell_text, \
    sim_command, vision_command

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX process groups")

# Listens on a port; ignores SIGTERM like the panel; Ctrl+C (SIGINT) ends it cleanly.
SERVER = """
import signal, socket, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen()
print("listening", flush=True)
try:
    while True: time.sleep(0.05)
except KeyboardInterrupt:
    print("bye", flush=True)
"""
STUBBORN = SERVER.replace("signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGINT, signal.SIG_IGN)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_part(name: str, logs: LogBook, script: str = SERVER, port: int = 0, grace: float = 3.0) -> Part:
    port = port or free_port()
    return Part(name, name.title(), lambda s: [sys.executable, "-c", script, str(port)], lambda s: port, logs,
                url=lambda s: f"http://127.0.0.1:{port}/", stop_grace=grace)


def wait_for(check, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


# --- settings and commands --------------------------------------------------------

def test_settings_reject_bad_values_and_keep_the_old_ones():
    settings = Settings()
    for bad in ({"backend": "robot"}, {"mode": "bandit"}, {"panel_port": 80}, {"camera": "webcam"},
                {"panel_extra": "--note 'unclosed"}):
        with pytest.raises(ValueError):
            settings.update(bad)
    assert settings == Settings()
    settings.update({"backend": "real", "panel_port": "8082", "unknown": 1})
    assert settings.backend == "real" and settings.panel_port == 8082


def test_settings_survive_a_save_and_a_broken_file(tmp_path):
    path = tmp_path / "launcher.json"
    Settings(backend="dry", mode="rules").save(path)
    assert Settings.load(path).mode == "rules"
    path.write_text("{not json", encoding="utf-8")
    assert Settings.load(path) == Settings()


def test_commands_follow_the_settings():
    settings = Settings(backend="real", mode="rules", robot_host="10.0.0.7", panel_port=8082, camera="fake",
                        live_port=8771, vision_extra="--no-aus")
    panel = panel_command(settings)
    assert panel[1:] == ["-m", "eepy.wizard", "--backend", "real", "--mode", "rules", "--port", "8082",
                         "--robot-host", "10.0.0.7"]
    assert vision_command(settings)[1:] == ["-m", "eepy.perception", "--live", "--live-port", "8771",
                                            "--no-browser", "--camera", "fake", "--no-aus"]
    assert "--robot-host" not in panel_command(Settings(backend="sim"))
    # Default camera: the robot's with the real robot (vision's "auto" would follow .env instead)
    assert vision_command(Settings(backend="real"))[-2:] == ["--camera", "robot"]
    assert "--camera" not in vision_command(Settings(backend="sim"))
    assert "--sim" in sim_command(Settings())


def test_the_fallback_command_is_what_you_type_in_the_repo_folder():
    text = shell_text(vision_command(Settings()))
    assert text.startswith(".venv-vision/bin/python -m eepy.perception --live")
    assert "--no-browser" not in text and str(REPO) not in text


def test_the_perception_program_knows_no_browser():
    from eepy.perception import __main__ as perception
    import inspect

    assert '"--no-browser"' in inspect.getsource(perception.main)


# --- one program ------------------------------------------------------------------

def test_start_then_stop_with_ctrl_c_even_when_sigterm_is_ignored():
    logs = LogBook()
    part = fake_part("panel", logs)
    settings = Settings()
    assert part.state(settings) == "stopped"
    assert "started" in part.start(settings)
    assert wait_for(lambda: part.state(settings) == "running")
    assert "already running" in part.start(settings)
    assert "stopped" in part.stop()
    assert part.state(settings) == "stopped"          # not "crashed": we stopped it on purpose
    assert wait_for(lambda: any(t == "bye" for _, p, t in logs.lines if p == "panel"))


def test_a_program_that_ignores_ctrl_c_is_killed_after_the_grace_period():
    part = fake_part("sim", LogBook(), script=STUBBORN, grace=0.5)
    settings = Settings()
    part.start(settings)
    assert wait_for(lambda: part.state(settings) == "running")
    part.stop()
    assert not part.running() and part.state(settings) == "stopped"


def test_a_program_that_dies_by_itself_is_a_crash():
    logs = LogBook()
    port = free_port()
    part = Part("vision", "Vision", lambda s: [sys.executable, "-c", "print('oops'); raise SystemExit(3)"],
                lambda s: port, logs)
    settings = Settings()
    part.start(settings)
    assert wait_for(lambda: part.state(settings) == "crashed")
    assert part.status(settings)["exit_code"] == 3
    assert wait_for(lambda: any(t == "oops" for _, _, t in logs.lines))


def test_a_program_started_in_a_terminal_is_left_alone():
    with socket.socket() as other:                     # "the panel, started in a terminal"
        other.bind(("127.0.0.1", 0))
        other.listen()
        part = fake_part("panel", LogBook(), port=other.getsockname()[1])
        settings = Settings()
        assert part.state(settings) == "external"
        assert "already in use" in part.start(settings)
        assert not part.running()
        assert "not running" in part.stop()


def test_the_panel_will_not_start_next_to_another_panel(monkeypatch):
    from eepy.launcher import parts

    with socket.socket() as relay:                     # another panel's camera relay, on its own port
        relay.bind(("127.0.0.1", 0))
        relay.listen()
        monkeypatch.setattr(parts, "relay_port", lambda: relay.getsockname()[1])
        part = fake_part("panel", LogBook())
        part._blocked = parts.other_panel_message
        assert "another Wizard panel" in part.start(Settings())
        assert not part.running()


def test_the_relay_port_comes_from_the_config(tmp_path):
    from eepy.launcher.parts import relay_port

    config = tmp_path / "eepy.yaml"
    config.write_text("camera:\n  fps: 15\n  relay_port: 9123\n", encoding="utf-8")
    assert relay_port(config) == 9123
    assert relay_port(tmp_path / "missing.yaml") == 8766


def test_a_missing_virtualenv_says_so():
    part = Part("sim", "Simulator", lambda s: ["/nowhere/.venv-robot/bin/mjpython"], lambda s: free_port(), LogBook())
    assert "does not exist" in part.start(Settings())
    assert part.status(Settings())["missing"]


def test_the_log_book_numbers_lines_and_strips_colours():
    logs = LogBook(size=3)
    for i in range(5):
        logs.add("panel" if i % 2 else "sim", f"\x1b[32mline {i}\x1b[0m\n")
    rows, last = logs.since(0)
    assert last == 5 and [r[2] for r in rows] == ["line 2", "line 3", "line 4"]
    assert logs.since(3, part="panel")[0] == [[4, "panel", "line 3"]]


# --- everything at once -----------------------------------------------------------

def make_launcher(backend: str = "sim", **scripts: str) -> Launcher:
    logs = LogBook()
    parts = {name: fake_part(name, logs, script=scripts.get(name, SERVER)) for name in ("sim", "panel", "vision")}
    return Launcher(Settings(backend=backend), parts=parts, logs=logs, save=False)


def test_start_all_starts_in_order_and_stop_all_stops_everything():
    launcher = make_launcher()
    launcher.start_all()
    assert wait_for(lambda: not launcher.busy(), 20)
    assert all(p.state(launcher.settings) == "running" for p in launcher.parts.values())
    assert launcher.message == "Everything is running."
    launcher.stop_all()
    assert not any(p.running() for p in launcher.parts.values())


def test_start_all_skips_the_simulator_for_dry_and_real():
    launcher = make_launcher(backend="dry")
    assert launcher.needed() == ["panel", "vision"]
    launcher.start_all()
    assert wait_for(lambda: not launcher.busy(), 20)
    assert not launcher.parts["sim"].running() and launcher.parts["vision"].running()
    launcher.stop_all()


def test_start_all_stops_at_the_first_program_that_fails():
    launcher = make_launcher(panel="raise SystemExit(1)")
    launcher.start_all()
    assert wait_for(lambda: not launcher.busy(), 20)
    assert not launcher.parts["vision"].running()
    assert "stopped during start-up" in launcher.message
    launcher.stop_all()


def test_the_page_and_the_api(tmp_path):
    launcher = make_launcher(backend="dry")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(launcher))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path: str, body: dict) -> dict:
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            return json.load(exc)

    try:
        with urllib.request.urlopen(base + "/") as response:                 # the new UI is the default
            assert response.url.endswith("/v5/") and b"EEPY" in response.read()
        assert b"EEPY" in urllib.request.urlopen(base + "/v5").read()
        assert b"/api/status" in urllib.request.urlopen(base + "/v1").read()   # the old page, kept as v1
        assert b"window.icon" in urllib.request.urlopen(base + "/v5/ds/icons.js").read()
        status = json.load(urllib.request.urlopen(base + "/api/status"))
        assert status["needed"] == ["panel", "vision"] and status["parts"]["panel"]["state"] == "stopped"
        assert post("/api/start", {"part": "panel"})["ok"]
        assert wait_for(lambda: launcher.parts["panel"].state(launcher.settings) == "running")
        assert post("/api/settings", {"mode": "rules"})["message"].startswith("Settings saved. Restart")
        assert not post("/api/settings", {"backend": "nope"})["ok"]
        assert not post("/api/start", {"part": "nope"})["ok"]
        logs = json.load(urllib.request.urlopen(base + "/api/logs?since=0&part=panel"))
        assert logs["lines"] and all(row[1] == "panel" for row in logs["lines"])
        assert post("/api/stop-all", {})["ok"]
        assert not port_open(launcher.parts["panel"]._port(launcher.settings))
    finally:
        launcher.stop_all()
        server.shutdown()


def test_the_v5_page_serves_only_its_own_files():
    from eepy.launcher.__main__ import v5_file

    assert v5_file("/v5/") is not None and v5_file("/v5/").name == "index.html"
    assert v5_file("/v5/js/app.js") is not None and v5_file("/v5/fonts/inter-latin.woff2") is not None
    assert v5_file("/v5/../__main__.py") is None          # outside static/v5
    assert v5_file("/v5/../index.html") is None           # the old page is served at /v1 only
    assert v5_file("/v5/nothing-here.js") is None


# --- the /v5 page's own calls (v5api.py) ----------------------------------------------

class FakeServer:
    """A tiny HTTP server that answers with the canned replies and remembers each request."""

    def __init__(self, replies):
        from http.server import BaseHTTPRequestHandler

        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                outer.requests.append((method, self.path, body))
                code, data = replies.get((method, self.path), (404, {"detail": "Not Found"}))
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._answer("GET")

            def do_POST(self):
                self._answer("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_v5_passes_the_panel_calls_through_unchanged():
    from eepy.launcher import v5api

    panel = FakeServer({("GET", "/api/state"): (200, {"status": {"connected": True}, "seq": 3}),
                        ("POST", "/api/play"): (200, {"ok": True}),
                        ("POST", "/api/session/end"): (409, {"ok": False, "message": "No session is running."}),
                        ("POST", "/api/annotate"): (422, {"detail": [{"msg": "Field required"}]})})
    settings = Settings(backend="dry", panel_port=panel.port)
    try:
        assert v5api.panel(settings, "GET", "state") == (200, {"status": {"connected": True}, "seq": 3})
        body = {"behavior": "refocus_curious", "intensity": 0.7, "via": "key"}
        assert v5api.panel(settings, "POST", "play", body) == (200, {"ok": True})
        assert panel.requests[-1] == ("POST", "/api/play", body)                      # unchanged
        assert v5api.panel(settings, "POST", "session/end", {}) == (409, {"ok": False, "message": "No session is running."})
        assert v5api.panel(settings, "POST", "annotate", {}) == (422, {"ok": False, "message": "Field required"})
        seen = len(panel.requests)
        assert v5api.panel(settings, "POST", "shutdown", {})[0] == 404                # not on the list: never sent
        assert v5api.panel(settings, "GET", "play")[0] == 404                          # POST-only call
        assert v5api.panel(settings, "GET", "../state")[0] == 404
        assert len(panel.requests) == seen
    finally:
        panel.close()
    code, data = v5api.panel(settings, "GET", "state")                                 # the panel is gone
    assert code == 502 and "isn't answering" in data["message"]


def test_v5_sets_the_robot_volume_through_its_daemon(monkeypatch):
    from eepy.launcher import v5api

    assert v5api.volume(Settings(backend="dry"), "GET")[0] == 409                     # no robot, no speaker
    daemon = FakeServer({("GET", "/api/volume/current"): (200, {"volume": 40, "platform": "macOS", "device": "x"}),
                         ("POST", "/api/volume/set"): (200, {"volume": 60, "platform": "macOS", "device": "x"})})
    monkeypatch.setattr(v5api, "DAEMON_PORT", daemon.port)
    try:
        code, data = v5api.volume(Settings(backend="sim"), "GET")
        assert code == 200 and data["volume"] == 40 and data["simulator"]
        code, data = v5api.volume(Settings(backend="sim"), "POST", {"volume": 60})
        assert code == 200 and data["volume"] == 60 and daemon.requests[-1] == ("POST", "/api/volume/set", {"volume": 60})
        seen = len(daemon.requests)
        assert v5api.volume(Settings(backend="sim"), "POST", {"volume": 150})[0] == 400
        assert v5api.volume(Settings(backend="sim"), "POST", {"volume": "loud"})[0] == 400
        assert len(daemon.requests) == seen
    finally:
        daemon.close()
    assert v5api.volume(Settings(backend="sim"), "GET")[0] == 502


def write_session(root, name, share=0.5, extra_events=(), desk_s=100, cut_last_line=False):
    """A recorded session as the panel and the vision process write it (docs/DATA_GUIDE.md)."""
    folder = root / name
    folder.mkdir(parents=True)
    t0 = 500.0
    events = [{"t_mono": t0, "t_wall": "2026-10-16T21:00:00+02:00", "type": "session_start", "participant": name.split("_")[1], "mode": "hmm"}]
    events += [dict(e, t_mono=t0 + e["t_mono"]) for e in extra_events]
    events.append({"t_mono": t0 + desk_s + 20, "t_wall": "", "type": "session_end", "reason": "operator"})
    text = "".join(json.dumps(e) + "\n" for e in events)
    (folder / "events.jsonl").write_text(text + ('{"t_mono": 9' if cut_last_line else ""))
    focused = round(desk_s * share)
    rows = ["t_mono,frame,state,p_focused,confidence,estimator"]
    rows += [f"{t0 + i},{i},focused,0.9,0.8,hmm" for i in range(focused)]
    rows += [f"{t0 + i},{i},distracted,0.2,0.7,hmm" for i in range(focused, desk_s)]
    rows += [f"{t0 + i},{i},absent,,,hmm" for i in range(desk_s, desk_s + 20)]          # away: left out of the share
    (folder / "state.csv").write_text("\n".join(rows) + "\n")
    (folder / "meta.json").write_text(json.dumps({"participant": name.split("_")[1], "mode": "hmm", "calibration": {"result": "new"}}))
    return folder


def test_v5_reads_a_recorded_session(tmp_path):
    from eepy.launcher import v5api

    write_session(tmp_path, "2026-10-16_P05_hmm", share=0.75, cut_last_line=True, extra_events=[
        {"t_mono": 30, "type": "nudge_decision", "decision_id": "d1", "ladder": "refocus", "level": 1,
         "behavior": "refocus_joint_attention", "tone": "hedged", "reason": "distracted", "propensity": 0.5,
         "context": {"probs": {"focused": 0.2, "distracted": 0.8}, "confidence": 0.8, "evidence": ["gaze off work"]},
         "explanation": {"sentence": "They looked away, so I glanced at the notes.", "contrast": {"text": "Not spoken yet."}}},
        {"t_mono": 50, "type": "nudge_outcome", "decision_id": "d1", "outcome": "success", "seconds": 12, "how": "focus"},
        {"t_mono": 60, "type": "wizard_nudge", "ladder": "bedtime", "level": 2, "behavior": "bedtime_attentive"},
        {"t_mono": 70, "type": "nudge_reward", "source": "wizard", "decision_id": "w1", "outcome": "dismissed", "latency": 4},
        {"t_mono": 80, "type": "annotation", "marker": "yawn"},
        {"t_mono": 90, "type": "note", "text": "a sip of water"},
        {"t_mono": 100, "type": "escalation", "what": "goodnight"},
        {"t_mono": 110, "type": "escalation", "what": "went_to_bed"},
    ])
    (tmp_path / "not-a-session").mkdir()                                                # no events.jsonl: skipped
    [s] = v5api.sessions(tmp_path)
    assert s["participant"] == "P05" and s["mode"] == "hmm" and s["clock"] == "21:00" and s["end_clock"] == "21:02"
    assert s["duration_s"] == 120 and s["ended"] and s["ending"] == "rest"
    assert s["nudges"] == 1 and s["by_hand"] == 1 and s["outcomes"] == {"success": 1, "dismissed": 1}
    assert s["focused_share"] == 0.75 and s["desk_s"] == 100                            # the 20 s away left out
    assert s["strip"][0]["state"] == "focused" and s["marks_at"] == [30, 60]
    assert v5api.sessions(tmp_path, "P06") == []

    d = v5api.detail(tmp_path, "2026-10-16_P05_hmm")
    eepy, you = d["moments"]
    assert eepy["by"] == "eepy" and eepy["outcome"] == "success" and eepy["seconds"] == 12 and eepy["clock"] == "21:00"
    assert eepy["sentence"].startswith("They looked away") and eepy["contrast"] == "Not spoken yet." and eepy["evidence"] == ["gaze off work"]
    assert you["by"] == "you" and you["outcome"] == "dismissed" and you["seconds"] == 4               # matched by w1
    assert d["marks"] == [{"t": 80, "clock": "21:01", "marker": "yawn"}] and d["notes"][0]["text"] == "a sip of water"
    assert d["curve"] and all(0 <= p <= 1 for _, p in d["curve"])
    assert [r["state"] for r in d["strip"]] == ["focused", "distracted", "absent"]
    for bad in ("", "../2026-10-16_P05_hmm", "nope", "/etc/passwd", "not-a-session"):
        assert v5api.detail(tmp_path, bad) is None


def test_v5_time_won_back_needs_four_sessions(tmp_path):
    from eepy.launcher import v5api

    for day, share in ((10, 0.5), (11, 0.4), (12, 0.6)):
        write_session(tmp_path, f"2026-10-{day}_P05_hmm", share=share, desk_s=600)
    assert v5api.progress(tmp_path, "P05") == {"participant": "P05", "enough": False, "sessions": 3}
    write_session(tmp_path, "2026-10-14_P05_hmm", share=0.8, desk_s=600)
    write_session(tmp_path, "2026-10-15_P07_hmm", share=0.1, desk_s=600)                            # someone else
    p = v5api.progress(tmp_path, "P05")
    assert p["enough"] and p["first_share"] == 0.5 and p["latest_share"] == 0.8
    assert p["gain_points"] == 30 and p["minutes_more"] == 3            # 30 points of 10 minutes at the desk


def test_the_v5_routes(tmp_path, monkeypatch):
    from eepy.launcher import v5api

    write_session(tmp_path, "2026-10-16_P05_hmm")
    monkeypatch.setattr(v5api, "SESSIONS", tmp_path)
    launcher = make_launcher(backend="dry")
    launcher.settings.panel_port = free_port()                                          # no panel there
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(launcher))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        code, data = call("/api/v5/sessions?participant=P05")
        assert code == 200 and [s["name"] for s in data["sessions"]] == ["2026-10-16_P05_hmm"]
        assert call("/api/v5/session?name=2026-10-16_P05_hmm")[1]["participant"] == "P05"
        assert call("/api/v5/session?name=..%2Fsecret")[0] == 404
        assert call("/api/v5/progress?participant=P05")[1]["enough"] is False
        assert call("/api/v5/panel/state")[0] == 502                                    # the panel is not running
        assert call("/api/v5/panel/play", {"behavior": "x", "intensity": 0.5})[0] == 502
        assert call("/api/v5/panel/nope", {})[0] == 404
        assert call("/api/v5/volume")[0] == 409                                         # dry: no speaker
        assert call("/api/v5/whatever", {})[0] == 404
        assert "refocus" in call("/api/v5/model")[1]["act_confidence"]
        assert call("/api/v5/panel/play", [1, 2])[0] == 400                             # not a JSON object
    finally:
        server.shutdown()


def test_v5_reads_the_configured_model_and_the_line_words(tmp_path):
    from eepy.launcher import v5api

    m = v5api.model()                                                     # the real config files
    assert m["states"][:2] == ["focused", "distracted"] and m["transitions"]["distracted"]["focused"] > 0
    assert 0 < m["act_confidence"]["refocus"] < 1 and 0 < m["direct_confidence"] <= 1
    assert m["lines"]["refocus_check"]["hedged"] and m["lines"]["refocus_check"]["direct"]
    (tmp_path / "hmm.yaml").write_text("states: [a, b]\ntransitions_per_hour:\n  a:\n    b:  {value: 6, why: x}\n  b:\n    a: {value: 60}\n")
    (tmp_path / "esc.yaml").write_text("confidence:\n  act:\n    refocus: 0.7\n  direct: 0.85\nother: 1\n")
    (tmp_path / "lines.yaml").write_text('lines:\n  hi:\n    variants:\n      hedged:\n        - "Hello?"\n      direct: ["Hello."]\n')
    m = v5api.model(tmp_path / "hmm.yaml", tmp_path / "esc.yaml", tmp_path / "lines.yaml")
    assert m["states"] == ["a", "b"] and m["transitions"] == {"a": {"b": 6.0}, "b": {"a": 60.0}}
    assert m["act_confidence"] == {"refocus": 0.7} and m["direct_confidence"] == 0.85
    assert m["lines"] == {"hi": {"hedged": "Hello?", "direct": "Hello."}}
    assert v5api.model(tmp_path / "none.yaml", tmp_path / "none.yaml", tmp_path / "none.yaml")["states"] == []
