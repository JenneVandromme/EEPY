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
        assert b"EEPY" in urllib.request.urlopen(base + "/").read()
        assert b"EEPY" in urllib.request.urlopen(base + "/v5").read()          # the new UI, next to the old one
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
    assert v5_file("/v5/app.js") is not None
    assert v5_file("/v5/../__main__.py") is None          # outside static/v5
    assert v5_file("/v5/../index.html") is None           # the old page is served at / only
    assert v5_file("/v5/nothing-here.js") is None
