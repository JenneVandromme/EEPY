"""Tests for the Wizard-of-Oz panel: its endpoints, and the session log format.

Each test gets its own copy of behaviors/ and the panel config in a temp
folder, a DryRunBackend, and a temp logs folder -- so nothing real is touched,
and tests can break files on purpose.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from eepy.behaviors import reach  # noqa: E402
from eepy.behaviors.backends import BackendError, DryRunBackend  # noqa: E402
from eepy.wizard.config import ConfigError, load_config  # noqa: E402
from eepy.wizard.server import Wizard, create_app  # noqa: E402

pytestmark = pytest.mark.skipif(not reach.available(), reason="the player needs the IK solver")

REPO = Path(__file__).resolve().parents[1]
CORE = ("t", "wall", "session", "kind", "source")


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    shutil.copytree(REPO / "behaviors", tmp_path / "behaviors")
    shutil.copytree(REPO / "sounds", tmp_path / "sounds")
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "wizard_panel.yaml", tmp_path / "config" / "wizard_panel.yaml")
    return {"behaviors": tmp_path / "behaviors", "sounds": tmp_path / "sounds",
            "config": tmp_path / "config" / "wizard_panel.yaml", "logs": tmp_path / "logs"}


class FlakyBackend(DryRunBackend):
    """A dry backend whose link can be cut, like a robot dropping off Wi-Fi."""

    alive = True

    def is_connected(self) -> bool:
        return self.connected and FlakyBackend.alive

    def send(self, pose) -> None:
        if not FlakyBackend.alive:
            raise ConnectionError("Lost connection with the server.")
        super().send(pose)


@pytest.fixture
def make(files):
    made = []

    def _make(factory=DryRunBackend, spoken=None):
        wizard = Wizard(factory, backend_name="dry", behaviors_dir=files["behaviors"],
                        sounds_dir=files["sounds"], config_path=files["config"],
                        logs_dir=files["logs"],
                        speech_callback=(lambda l, t: spoken.append((l, t))) if spoken is not None else None)
        client = TestClient(create_app(wizard))
        client.__enter__()
        made.append(client)
        return wizard, client

    yield _make
    for client in made:
        client.__exit__(None, None, None)
    FlakyBackend.alive = True


def lines(files) -> list[dict]:
    (path,) = files["logs"].glob("*.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


# --- the buttons come from the YAML -------------------------------------------


def test_buttons_are_generated_from_the_behaviour_files(make):
    _, client = make()
    groups = {g["id"]: g for g in client.get("/api/state").json()["panel"]["groups"]}
    assert [b["key"] for b in groups["refocus"]["buttons"]] == ["1", "2", "3", "4", "5"]
    assert [b["key"] for b in groups["bedtime"]["buttons"]] == list("qwerty")
    assert [b["label"] for b in groups["refocus"]["buttons"]] == ["L0", "L1", "L2", "L3", "L4"]
    assert "approving_nod" in [b["target"] for b in groups["basics"]["buttons"]]
    assert "study_buddy_idle" not in [b["target"] for b in groups["basics"]["buttons"]], \
        "the idle has its own on/off button"
    assert len(groups["markers"]["buttons"]) == 8


def test_a_new_behaviour_appears_after_reload_with_a_key(make, files):
    _, client = make()
    (files["behaviors"] / "extra.yaml").write_text("""
behaviors:
  - name: wave_hello
    description: A new one.
    keyframes:
      - head: { yaw: 10 }
        duration: 0.5
""")
    assert client.post("/api/reload").json()["ok"]
    state = client.get("/api/state").json()
    basics = {b["target"]: b for g in state["panel"]["groups"] if g["id"] == "basics"
              for b in g["buttons"]}
    assert "wave_hello" in basics
    assert basics["wave_hello"]["key"] and basics["wave_hello"]["auto_key"]
    assert any("wave_hello" in w for w in state["panel"]["warnings"])


def test_a_broken_behaviour_file_on_reload_keeps_the_last_good_version(make, files):
    _, client = make()
    path = files["behaviors"] / "refocus.yaml"
    path.write_text(path.read_text().replace("roll: 12", "roll: 200"))
    result = client.post("/api/reload").json()
    assert not result["ok"]
    assert "head roll" in result["message"], "the validator's explanation is shown"
    assert "head roll" in client.get("/api/state").json()["status"]["last_error"]["message"]
    assert client.post("/api/play", json={"behavior": "refocus_curious", "intensity": 0.5}).status_code == 200


# --- the panel config ---------------------------------------------------------


def test_config_rejects_a_key_used_twice(files):
    files["config"].write_text(files["config"].read_text().replace("idle_toggle: a", "idle_toggle: s"))
    with pytest.raises(ConfigError, match="can only do one thing"):
        load_config(files["config"])


def test_config_rejects_an_impossible_key(files):
    files["config"].write_text(files["config"].read_text().replace("idle_toggle: a", "idle_toggle: ctrl-a"))
    with pytest.raises(ConfigError, match="not a key the panel can use"):
        load_config(files["config"])


def test_config_suggests_a_fix_for_a_typo(files):
    files["config"].write_text(files["config"].read_text().replace("default_intensity:", "default_intensty:"))
    with pytest.raises(ConfigError, match='Did you mean "default_intensity"'):
        load_config(files["config"])


def test_a_broken_config_on_reload_keeps_the_last_good_one(make, files):
    _, client = make()
    files["config"].write_text("keys: [this is not right\n")
    result = client.post("/api/reload").json()
    assert not result["ok"] and "wizard_panel.yaml" in result["message"]
    groups = client.get("/api/state").json()["panel"]["groups"]
    assert any(b["key"] == "1" for g in groups for b in g["buttons"]), "old keys still in place"


# --- actions -------------------------------------------------------------------


def test_play_and_stop(make, files):
    wizard, client = make()
    assert client.post("/api/play", json={"behavior": "refocus_curious", "intensity": 0.6}).json()["ok"]
    wait_for(lambda: wizard.player.current() == "refocus_curious")
    assert client.post("/api/stop", json={"via": "key"}).json()["ok"]
    wait_for(lambda: wizard.player.current() is None)


def test_friendly_errors(make):
    _, client = make()
    bad = client.post("/api/play", json={"behavior": "refocus_curios", "intensity": 0.5})
    assert bad.status_code == 409 and 'Did you mean "refocus_curious"' in bad.json()["message"]
    assert client.post("/api/play", json={"behavior": "approving_nod", "intensity": 2}).status_code == 409
    assert client.post("/api/annotate", json={"marker": "sneezed"}).status_code == 409
    assert client.post("/api/note", json={"text": "  "}).status_code == 409


def test_say_a_line_and_free_text(make):
    spoken: list = []
    _, client = make(spoken=spoken)
    r = client.post("/api/say", json={"line_id": "refocus_check", "tone": "direct"}).json()
    assert r["text"] in ("Back to it?", "Shall we get back to it?")
    client.post("/api/say", json={"text": "Need a hand?"})
    wait_for(lambda: len(spoken) == 2)
    assert spoken[1] == ("free_text", "Need a hand?")


def test_idle_toggle(make):
    wizard, client = make()
    assert client.get("/api/state").json()["status"]["idle_on"]
    client.post("/api/idle", json={"on": False})
    assert not client.get("/api/state").json()["status"]["idle_on"]


def test_pressing_everything_at_once_never_breaks_anything(make):
    """Two operators mashing buttons as fast as they can, for a moment."""
    wizard, client = make()
    statuses: list[int] = []

    def hammer(seed: int) -> None:
        for i in range(25):
            choice = (seed + i) % 4
            if choice == 0:
                r = client.post("/api/play", json={"behavior": "refocus_curious", "intensity": 0.5})
            elif choice == 1:
                r = client.post("/api/play", json={"behavior": "approving_nod", "intensity": 0.3})
            elif choice == 2:
                r = client.post("/api/stop", json={})
            else:
                r = client.post("/api/annotate", json={"marker": "yawn"})
            statuses.append(r.status_code)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(statuses) <= {200}, statuses
    assert wizard.player.error is None
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/play", json={"behavior": "approving_nod", "intensity": 0.5}).json()["ok"]


# --- sessions and the log format ------------------------------------------------


def test_session_file_name_and_format(make, files):
    _, client = make()
    client.post("/api/session/start", json={"participant": "P07"})
    client.post("/api/play", json={"behavior": "approving_nod", "intensity": 0.5})
    time.sleep(0.3)
    client.post("/api/annotate", json={"marker": "yawn", "via": "key"})
    client.post("/api/note", json={"text": "rubbed eyes"})
    time.sleep(0.3)
    client.post("/api/session/end")

    (path,) = files["logs"].glob("*.jsonl")
    assert path.name.startswith("P07_") and path.suffix == ".jsonl"
    datetime.strptime(path.stem[4:], "%Y-%m-%d_%H-%M-%S")

    rows = lines(files)
    assert all(all(k in row for k in CORE) for row in rows), "core fields on every line"
    assert rows[0]["kind"] == "session_start" and rows[-1]["kind"] == "session_end"
    assert {row["session"] for row in rows} == {path.stem}
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows), "lines are in time order"
    assert {r["source"] for r in rows} <= {"wizard", "idle", "system", "auto"}

    annotation = next(r for r in rows if r["kind"] == "annotation")
    assert annotation["marker"] == "yawn" and annotation["source"] == "wizard"
    started = next(r for r in rows if r["kind"] == "behavior_started" and r["behavior"] == "approving_nod")
    assert started["source"] == "wizard", "operator-triggered behaviours are marked as such"
    assert any(r["kind"] == "note" and r["text"] == "rubbed eyes" for r in rows)


def test_t_and_wall_describe_the_same_moment(make, files):
    """`wall` is derived from `t` through one anchor, so they never disagree."""
    _, client = make()
    client.post("/api/session/start", json={"participant": "P08"})
    time.sleep(0.25)
    client.post("/api/annotate", json={"marker": "yawn"})
    client.post("/api/session/end")
    rows = lines(files)
    first, mark = rows[0], next(r for r in rows if r["kind"] == "annotation")
    wall_gap = (datetime.fromisoformat(mark["wall"]) - datetime.fromisoformat(first["wall"])).total_seconds()
    assert abs(wall_gap - (mark["t"] - first["t"])) < 0.002


def test_annotations_and_player_events_share_one_clock(make, files):
    """The whole point of running the panel in the player's process."""
    _, client = make()
    client.post("/api/session/start", json={"participant": "P09"})
    before = time.monotonic()
    client.post("/api/play", json={"behavior": "approving_nod", "intensity": 0.5})
    time.sleep(0.2)
    client.post("/api/annotate", json={"marker": "user_replied"})
    after = time.monotonic()
    client.post("/api/session/end")
    rows = lines(files)
    started = next(r for r in rows if r["kind"] == "behavior_started" and r["source"] == "wizard")
    mark = next(r for r in rows if r["kind"] == "annotation")
    assert before <= started["t"] < mark["t"] <= after


def test_a_rejected_command_is_not_logged_as_one(make, files):
    _, client = make()
    client.post("/api/session/start", json={"participant": "P10"})
    client.post("/api/play", json={"behavior": "no_such_thing", "intensity": 0.5})
    client.post("/api/session/end")
    assert not any(r["kind"] == "wizard_command" and r.get("behavior") == "no_such_thing"
                   for r in lines(files))


def test_markers_outside_a_session_are_shown_but_not_saved(make, files):
    _, client = make()
    r = client.post("/api/annotate", json={"marker": "yawn"}).json()
    assert r["ok"] and r["saved"] is False
    assert not list(files["logs"].glob("*.jsonl")) if files["logs"].exists() else True


def test_the_session_survives_a_page_refresh(make):
    _, client = make()
    client.post("/api/session/start", json={"participant": "P11"})
    session = client.get("/api/state").json()["status"]["session"]   # what a fresh page loads
    assert session["active"] and session["participant"] == "P11"
    again = client.post("/api/session/start", json={"participant": "P12"})
    assert again.status_code == 409 and "still running" in again.json()["message"]


def test_a_bad_participant_id_is_explained(make):
    _, client = make()
    r = client.post("/api/session/start", json={"participant": "P 01/../x"})
    assert r.status_code == 409 and "letters, numbers" in r.json()["message"]


def test_stopping_the_panel_closes_the_session(make, files):
    wizard, client = make()
    client.post("/api/session/start", json={"participant": "P13"})
    wizard.shutdown()
    assert lines(files)[-1]["kind"] == "session_end"
    assert lines(files)[-1]["reason"] == "panel stopped"


# --- the robot dropping out -----------------------------------------------------


def test_a_dropped_robot_shows_red_and_annotations_keep_working(make, files):
    wizard, client = make(factory=FlakyBackend)
    client.post("/api/session/start", json={"participant": "P14"})
    assert client.get("/api/state").json()["status"]["connected"]

    FlakyBackend.alive = False
    wait_for(lambda: not client.get("/api/state").json()["status"]["connected"])
    blocked = client.post("/api/play", json={"behavior": "approving_nod", "intensity": 0.5})
    assert blocked.status_code == 409 and "not connected" in blocked.json()["message"]
    assert client.post("/api/annotate", json={"marker": "yawn"}).json()["saved"]
    assert client.post("/api/stop", json={}).json()["ok"], "STOP never errors"

    FlakyBackend.alive = True
    assert client.post("/api/reconnect").json()["ok"]
    assert client.post("/api/play", json={"behavior": "approving_nod", "intensity": 0.5}).json()["ok"]
    client.post("/api/session/end")

    statuses = [r.get("status") for r in lines(files) if r["kind"] == "connection"]
    assert "lost" in statuses and statuses[-1] == "connected"
    assert sum(1 for r in lines(files) if r["kind"] == "session_start") == 1, "one session throughout"


def test_the_panel_starts_even_if_the_robot_is_unreachable(make):
    def unreachable():
        raise BackendError("Could not reach the robot.")

    _, client = make(factory=unreachable)
    status = client.get("/api/state").json()["status"]
    assert not status["connected"]
    assert "Could not reach" in status["last_error"]["message"]
    client.post("/api/session/start", json={"participant": "P15"})
    assert client.post("/api/annotate", json={"marker": "yawn"}).json()["saved"]


def test_stop_answers_at_once_even_during_a_slow_reconnect(make):
    """A reconnect to an unreachable robot can take seconds. STOP must not wait."""
    connects = {"n": 0}

    def slow_second_time():
        connects["n"] += 1
        if connects["n"] > 1:
            time.sleep(2.0)            # a robot that is slow to answer
        return DryRunBackend()

    _, client = make(factory=slow_second_time)
    worker = threading.Thread(target=lambda: client.post("/api/reconnect"))
    worker.start()
    time.sleep(0.3)                    # the reconnect is now mid-way
    started = time.monotonic()
    assert client.post("/api/stop", json={}).json()["ok"]
    assert client.get("/api/state").status_code == 200
    assert time.monotonic() - started < 0.5, "STOP and the page waited for the reconnect"
    worker.join()


def test_a_reconnect_does_not_log_a_drop_that_never_happened(make, files):
    _, client = make()
    client.post("/api/session/start", json={"participant": "P16"})
    for _ in range(3):
        client.post("/api/reconnect")
        client.get("/api/state")
    client.post("/api/session/end")
    assert not [r for r in lines(files) if r["kind"] == "connection" and r["status"] == "lost"]


def test_a_port_held_on_ipv6_only_counts_as_taken():
    """The real failure: a dev server on [::1]:8080, the panel on 127.0.0.1:8080,
    and the browser's 'localhost' quietly reaching the other one."""
    import socket

    from eepy.wizard.__main__ import _port_taken

    try:
        holder = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        holder.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 on this machine")
    holder.listen()
    port = holder.getsockname()[1]
    try:
        assert _port_taken(port) == f"::1:{port}"
    finally:
        holder.close()


def test_a_free_port_is_free():
    import socket

    from eepy.wizard.__main__ import _port_taken

    with socket.socket() as probe:          # ask the OS for a port nobody is using
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert _port_taken(port) is None
