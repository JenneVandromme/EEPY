"""Tests for loading sessions, the command-line tools, settings and the mode switch."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

from eepy.logs import SessionLog  # noqa: E402
from eepy.logs.features import FeatureStream  # noqa: E402
from eepy.logs.load import all_annotations, load_session, sessions_table  # noqa: E402
from eepy.logs.schema import EVENT_TYPES  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
K = 0.05          # see make_session


@pytest.fixture
def data(tmp_path):
    return tmp_path / "sessions"


def make_session(data: Path, participant="P01", mode="wizard", features=True) -> Path:
    """A small but realistic session: behaviours, interruptions, a refusal,
    annotations, a note, a line, and a feature stream."""
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    stream = FeatureStream(["face", "gaze_x"], rate_hz=20, data_dir=data) if features else None
    log.start(participant, mode=mode, backend="dry", notes="test")
    time.sleep(0.6)                                       # the stream notices the session
    t0 = log.started_mono
    # Scripted times, squeezed by K so they all fall inside this short session.
    ev = lambda kind, source, dt, **d: log.event(kind, source, t_mono=t0 + dt * K, **d)  # noqa: E731
    ev("behavior_started", "idle", 0.1, behavior="idle_loop", intensity=1.0)
    ev("wizard_command", "wizard", 1.0, action="play", behavior="refocus_curious", intensity=0.6)
    ev("behavior_interrupted", "idle", 1.01, behavior="idle_loop", by="refocus_curious")
    ev("behavior_started", "wizard", 1.01, behavior="refocus_curious", intensity=0.6)
    ev("annotation", "wizard", 1.5, marker="looked_away", label="looked away", via="key")
    ev("line_requested", "wizard", 2.0, behavior="refocus_curious", line_id="refocus_check",
       text="Back to it?", tone="direct")
    ev("behavior_finished", "wizard", 5.7, behavior="refocus_curious")
    ev("behavior_refused", "wizard", 6.0, behavior="relative_up", reason="head pitch 45")
    ev("annotation", "wizard", 7.0, marker="back_on_task", label="back on task", via="key")
    ev("note", "wizard", 7.5, text="smiled at the robot")
    ev("behavior_started", "wizard", 8.0, behavior="approving_nod", intensity=1.0)
    if stream:
        for i in range(10):
            stream.row([1, i / 10])
            time.sleep(0.01)
        stream.close()
    log.end(notes="all fine")
    return next(p for p in data.iterdir() if p.is_dir() and participant in p.name)


# --- loading -------------------------------------------------------------------------


def test_events_table(data):
    s = load_session(make_session(data), data)
    e = s.events
    assert list(e.columns[:7]) == ["t", "t_mono", "t_wall", "type", "source", "process", "seq"]
    assert e["t"].iloc[0] == pytest.approx(0, abs=0.01), "t counts from session start"
    assert e["t"].is_monotonic_increasing, "sorted by time"
    assert str(e["t_wall"].dt.tz) not in ("None", ""), "wall clock is timezone-aware"
    assert e["type"].iloc[0] == "session_start" and e["type"].iloc[-1] == "session_end"


def test_annotations_are_their_own_table(data):
    s = load_session(make_session(data), data)
    a = s.annotations
    assert list(a.columns) == ["t", "t_mono", "t_wall", "marker", "label", "via", "source"]
    assert list(a["marker"]) == ["looked_away", "back_on_task"]
    assert list((a["t"] / K).round(2)) == [1.5, 7.0]


def test_behaviour_runs_are_paired_up(data):
    b = load_session(make_session(data), data).behaviors.set_index("behavior")
    assert b.loc["idle_loop", "outcome"] == "interrupted"
    assert b.loc["idle_loop", "interrupted_by"] == "refocus_curious"
    assert b.loc["refocus_curious", "outcome"] == "finished"
    assert b.loc["refocus_curious", "duration"] == pytest.approx(4.69 * K, abs=0.001)
    assert b.loc["refocus_curious", "source"] == "wizard"
    assert b.loc["relative_up", "outcome"] == "refused"
    assert b.loc["approving_nod", "outcome"] == "unfinished", "still running when it ended"


def test_notes_and_meta(data):
    s = load_session(make_session(data), data)
    assert list(s.notes["text"]) == ["smiled at the robot"]
    assert s.meta["notes"] == ["test", "all fine"] and s.participant == "P01"


def test_features_line_up_with_events(data):
    s = load_session(make_session(data), data)
    f = s.features
    assert len(f) == 10 and list(f.columns[:4]) == ["t", "t_mono", "t_wall", "frame"]
    # The same instant must get the same wall-clock time in both tables.
    first = s.events.iloc[0]
    offset = (f["t_wall"].iloc[0] - first["t_wall"]).total_seconds()
    assert offset == pytest.approx(f["t_mono"].iloc[0] - first["t_mono"], abs=0.002)


def test_loading_by_part_of_the_name_and_latest(data):
    make_session(data, "P01")
    make_session(data, "P02", mode="rules")
    assert load_session("P02", data).mode == "rules"
    assert load_session("latest", data).participant == "P02"
    year = next(p for p in data.iterdir() if p.is_dir()).name[:4]   # in both names
    with pytest.raises(FileNotFoundError, match="matches 2 sessions"):
        load_session(year, data)


def test_sessions_table_and_all_annotations(data):
    make_session(data, "P01")
    make_session(data, "P02", mode="hmm")
    table = sessions_table(data)
    assert list(table["participant"]) == ["P01", "P02"]
    assert set(table["status"]) == {"OK"}
    labels = all_annotations(data)
    assert len(labels) == 4 and set(labels["participant"]) == {"P01", "P02"}
    assert {"session", "mode", "marker", "t"} <= set(labels.columns)


def test_broken_sessions_are_left_out_of_the_training_labels(data):
    make_session(data, "P01")
    broken = make_session(data, "P02")
    (broken / "meta.json").unlink()
    assert set(all_annotations(data)["participant"]) == {"P01"}
    assert len(all_annotations(data, include_broken=True)) == 4


def test_a_crashed_session_still_loads(data):
    folder = make_session(data, features=False)
    meta = json.loads((folder / "meta.json").read_text())
    meta["ended"] = None
    (folder / "meta.json").write_text(json.dumps(meta))
    with (folder / "events.jsonl").open("a") as handle:
        handle.write('{"v": 1, "seq": 99, "t_mo')
    s = load_session(folder, data)
    assert s.check.status == "INCOMPLETE" and len(s.annotations) == 2


# --- the command-line tools ---------------------------------------------------------------


def run(module: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *args], capture_output=True,
                          text=True, timeout=60, cwd=REPO)


def test_list_tool(data):
    make_session(data, "P01")
    out = run("eepy.logs.list", "--data-dir", str(data))
    assert out.returncode == 0 and "P01" in out.stdout and "all OK" in out.stdout


def test_summary_tool(data):
    make_session(data, "P01")
    out = run("eepy.logs.summary", "P01", "--data-dir", str(data))
    assert out.returncode == 0, out.stderr
    for text in ("[OK]", "ANNOTATIONS (2)", "looked_away", "smiled at the robot", "FEATURES"):
        assert text in out.stdout, text


def test_check_tool_exit_code(data):
    folder = make_session(data, "P01")
    assert run("eepy.logs.check", "--data-dir", str(data)).returncode == 0
    (folder / "meta.json").unlink()
    out = run("eepy.logs.check", "--data-dir", str(data))
    assert out.returncode == 1 and "BROKEN" in out.stdout


def test_unknown_session_is_explained(data):
    make_session(data, "P01")
    out = run("eepy.logs.summary", "P77", "--data-dir", str(data))
    assert out.returncode == 2 and "No session matches" in out.stderr


# --- settings and the mode switch -------------------------------------------------------------


def test_settings_defaults_and_override():
    from eepy.settings import load_settings

    settings = load_settings()
    assert settings.mode == "wizard" and settings.mode_implemented
    assert load_settings(mode="hmm_bandit").mode == "hmm_bandit"
    assert load_settings(mode="hmm_bandit").mode_implemented          # built since A24


def test_settings_errors_are_friendly(tmp_path):
    from eepy.settings import SettingsError, load_settings

    with pytest.raises(SettingsError, match='Did you mean "hmm_bandit"'):
        load_settings(mode="hmm_bandt")
    path = tmp_path / "eepy.yaml"
    path.write_text("mood: wizard\n")
    with pytest.raises(SettingsError, match='Did you mean "mode"'):
        load_settings(path)
    path.write_text("participant_pattern: '^P[0-9'\n")
    with pytest.raises(SettingsError, match="not a valid pattern"):
        load_settings(path)


def test_the_wizard_entry_point_rejects_an_unknown_mode():
    out = run("eepy.wizard", "--mode", "telepathy", "--port", "1")
    assert out.returncode != 0 and "invalid choice" in out.stderr


# --- guards that keep the data honest -----------------------------------------------------------


def test_every_event_type_the_code_emits_is_in_the_schema():
    """A typo in an event name anywhere in eepy/ should fail here, not in the field."""
    import ast

    emitted = set()
    for path in (REPO / "eepy").rglob("*.py"):
        # The syntax tree, not a text search: schema.py's docstring shows a
        # misspelled call on purpose, and that must not count as code.
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                    and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
                    in ("_emit", "_note", "event")):
                emitted.add(node.args[0].value)
    unknown = emitted - set(EVENT_TYPES)
    assert not unknown, f"emitted but not in eepy/logs/schema.py: {sorted(unknown)}"
    assert {"behavior_started", "annotation", "wizard_command"} <= emitted


def test_session_data_can_never_be_committed():
    for path in ("data/sessions/2026-10-06_P03_wizard/events.jsonl",
                 "data/sessions/CURRENT", "logs/sessions/old.jsonl"):
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO).returncode == 0, path
    assert subprocess.run(["git", "check-ignore", "-q", "eepy/behaviors/data/kinematics_data.json"],
                          cwd=REPO).returncode == 1, "the vendored geometry must stay committed"


def test_no_source_file_is_ever_gitignored():
    """A broad ignore rule once matched eepy/logs/ -- the whole logging package --
    and git silently left it out. Every file under eepy/ must be committable."""
    sources = [str(p.relative_to(REPO)) for p in (REPO / "eepy").rglob("*")
               if p.is_file() and "__pycache__" not in p.parts]
    out = subprocess.run(["git", "check-ignore", "--stdin"], input="\n".join(sources),
                         capture_output=True, text=True, cwd=REPO)
    ignored = [line for line in out.stdout.splitlines() if line]
    assert not ignored, f"source files git would leave out: {ignored}"
