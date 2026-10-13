"""Tests for the writing side of eepy.logs: schema, writer, sessions, features, check.

Standard library only, and Python 3.9 compatible -- like the code it tests,
which the vision process runs on Python 3.9. CI runs this file on 3.9 as well
as 3.12, so a 3.10+ feature sneaking into eepy/logs fails the build.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from eepy.logs import SessionError, SessionLog
from eepy.logs.check import check_session, stale_pointer
from eepy.logs.clock import monotonic
from eepy.logs.features import FeatureStream, ProcessEventLog
from eepy.logs.schema import EVENT_TYPES, SchemaError
from eepy.logs.writer import clean

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def data(tmp_path):
    return tmp_path / "sessions"


@pytest.fixture
def log(data):
    made = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    yield made
    if made.active:
        made.end()


def read(folder, name="events.jsonl"):
    return [json.loads(line) for line in (folder / name).read_text().splitlines()]


def only_session(data):
    (folder,) = [p for p in data.iterdir() if p.is_dir()]
    return folder


# --- the schema -------------------------------------------------------------------


def test_an_unknown_event_type_fails_at_once_with_a_suggestion(log):
    with pytest.raises(SchemaError, match='Did you mean "annotation"'):
        log.event("anotation", source="wizard", marker="yawn")


def test_a_missing_required_field_fails_at_once(log):
    with pytest.raises(SchemaError, match="needs: marker"):
        log.event("annotation", source="wizard")


def test_an_unknown_source_fails(log):
    with pytest.raises(SchemaError, match="source must be one of"):
        log.event("note", source="someone", text="hi")


def test_extra_fields_are_allowed(log, data):
    log.start("P01", mode="wizard")
    log.event("annotation", source="wizard", marker="yawn", confidence="high")
    log.end()
    assert read(only_session(data))[1]["confidence"] == "high"


def test_reserved_types_for_later_tasks_exist():
    for name in ("bandit_decision", "bandit_reward", "reply_category", "llm_output"):
        assert EVENT_TYPES[name].reserved, name
    assert "hmm_belief" not in EVENT_TYPES            # A16: replaced by state_model + state.csv
    assert not EVENT_TYPES["state_model"].reserved


# --- privacy -------------------------------------------------------------------------


def test_raw_bytes_are_refused():
    with pytest.raises(SchemaError, match="Images, audio and video must never be logged"):
        clean({"frame": b"\x89PNG..."})


def test_a_large_array_is_refused_as_an_image():
    class FakeImage:                       # stands in for a numpy array, stdlib only
        shape, dtype, size = (480, 640, 3), "uint8", 480 * 640 * 3

        def tolist(self):
            raise AssertionError("must be refused before conversion")

    with pytest.raises(SchemaError, match="looks like an image"):
        clean({"frame": FakeImage()})


def test_nan_and_infinity_become_null():
    assert clean({"a": float("nan"), "b": float("inf"), "c": 1.5}) == {"a": None, "b": None, "c": 1.5}


def test_what_a_participant_said_is_redacted_by_default(log, data):
    log.start("P01", mode="wizard")
    log.event("reply_category", source="auto", category="agree", text="ok fine I'll go to bed")
    log.end()
    line = read(only_session(data))[1]
    assert "text" not in line and line["text_redacted"] is True
    assert line["category"] == "agree"


def test_raw_speech_is_kept_only_when_switched_on(data):
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO, store_raw_speech=True)
    log.start("P01", mode="wizard")
    log.event("reply_category", source="auto", category="agree", text="ok")
    log.end()
    assert read(only_session(data))[1]["text"] == "ok"


@pytest.mark.parametrize("bad", ["Jenne", "P1", "P1234", "p03", "../P03", "anna@uni.be", ""])
def test_only_pseudonymous_participant_ids(log, bad):
    with pytest.raises(SessionError, match="pseudonymous"):
        log.start(bad, mode="wizard")


def test_surrounding_spaces_in_a_participant_id_are_ignored(log, data):
    log.start("  P03 ", mode="wizard")
    log.end()
    assert only_session(data).name.endswith("_P03_wizard")


def test_features_refuse_an_image(data, log):
    log.start("P01", mode="wizard")
    stream = FeatureStream(["face"], data_dir=data)
    with pytest.raises(SchemaError, match="Never put an image"):
        stream.row([[0] * 100])
    stream.close()


# --- sessions and files -----------------------------------------------------------------


def test_folder_layout_and_meta(log, data):
    log.start("P03", mode="hmm", backend="dry", notes="first pilot")
    log.end(notes="went well")
    folder = only_session(data)
    assert folder.name.endswith("_P03_hmm")
    assert {p.name for p in folder.iterdir()} == {"meta.json", "events.jsonl", "snapshot"}
    meta = json.loads((folder / "meta.json").read_text())
    for key in ("schema_version", "participant", "mode", "backend", "started", "ended",
                "clock_anchor", "clock", "code", "snapshot", "sounds", "notes", "counts"):
        assert key in meta, key
    assert meta["notes"] == ["first pilot", "went well"]
    for relative, digest in meta["snapshot"].items():
        assert (folder / "snapshot" / relative).is_file() and digest.startswith("sha256:")
    assert not (data / "CURRENT").exists(), "the pointer is removed at the end"


def test_a_second_session_the_same_day_gets_its_own_folder(log, data):
    for _ in range(3):
        log.start("P03", mode="wizard")
        log.end()
    names = sorted(p.name for p in data.iterdir() if p.is_dir())
    assert len(names) == 3 and names[1].endswith("_2") and names[2].endswith("_3")


def test_events_reach_disk_within_about_a_second(data):
    log = SessionLog(data_dir=data, flush_seconds=1.0, repo_root=REPO)
    log.start("P01", mode="wizard")
    log.event("annotation", source="wizard", marker="yawn")
    time.sleep(1.5)
    assert any(r["type"] == "annotation" for r in read(only_session(data))), "not flushed"
    log.end()


def test_session_start_is_always_the_first_line_even_under_load(data):
    """Another thread logging nonstop must not get in ahead of session_start."""
    log = SessionLog(data_dir=data, flush_seconds=0.05, repo_root=REPO)
    stop = threading.Event()

    def chatter():
        while not stop.is_set():
            log.event("keyframe_reached", source="idle", behavior="idle", index=1)

    thread = threading.Thread(target=chatter)
    thread.start()
    time.sleep(0.05)
    log.start("P01", mode="wizard")
    time.sleep(0.05)
    log.end()
    stop.set()
    thread.join()
    rows = read(only_session(data))
    assert rows[0]["type"] == "session_start" and rows[-1]["type"] == "session_end"


def test_many_threads_writing_at_once_lose_nothing(log, data):
    log.start("P01", mode="wizard")
    threads_n, each = 8, 500

    def writer(n):
        for i in range(each):
            log.event("note", source="wizard", text=f"t{n}-{i}")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.end()
    rows = read(only_session(data))
    assert len(rows) == threads_n * each + 2
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    texts = {r["text"] for r in rows if r["type"] == "note"}
    assert texts == {f"t{n}-{i}" for n in range(threads_n) for i in range(each)}
    assert check_session(only_session(data)).status == "OK"


def test_logging_does_not_wait_for_the_disk(log):
    log.start("P01", mode="wizard")
    started = time.perf_counter()
    for _ in range(2000):
        log.event("note", source="wizard", text="x")
    per_event = (time.perf_counter() - started) / 2000
    assert per_event < 0.001, f"{per_event * 1e6:.0f} us per event"


# --- crashes ---------------------------------------------------------------------------


def _run(code: str, data: Path) -> subprocess.CompletedProcess:
    script = (f"import sys, os, time; sys.path.insert(0, {str(REPO)!r})\n"
              "from pathlib import Path\nfrom eepy.logs import SessionLog\n"
              f"log = SessionLog(data_dir=Path({str(data)!r}), flush_seconds=0.1, "
              f"repo_root=Path({str(REPO)!r}))\n" + code)
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)


def test_a_hard_crash_keeps_everything_flushed_and_is_flagged(data):
    """os._exit: no atexit, no final flush -- the worst case short of a power cut."""
    _run("log.start('P01', mode='wizard')\n"
         "for i in range(50): log.event('annotation', source='wizard', marker='yawn')\n"
         "time.sleep(0.5)\n"
         "for i in range(10): log.event('note', source='wizard', text='never flushed')\n"
         "os._exit(1)\n", data)
    folder = only_session(data)
    result = check_session(folder)
    assert result.status == "INCOMPLETE"
    rows = read(folder)
    assert sum(r["type"] == "annotation" for r in rows) == 50, "flushed data survived"
    assert stale_pointer(data) == folder.name, "CURRENT left pointing at the crashed session"


def test_a_normal_exit_without_end_still_closes_the_session(data):
    _run("log.start('P01', mode='wizard')\nlog.event('annotation', source='wizard', marker='yawn')\n",
         data)
    folder = only_session(data)
    rows = read(folder)
    assert rows[-1]["type"] == "session_end" and rows[-1]["reason"] == "process exit"
    assert json.loads((folder / "meta.json").read_text())["ended"]["reason"] == "process exit"
    assert check_session(folder).status == "OK"


def test_a_line_cut_off_mid_write_is_incomplete_not_broken(log, data):
    log.start("P01", mode="wizard")
    log.event("annotation", source="wizard", marker="yawn")
    log.end()
    folder = only_session(data)
    with (folder / "events.jsonl").open("a") as handle:
        handle.write('{"v": 1, "seq": 4, "t_mo')
    result = check_session(folder)
    assert result.status == "INCOMPLETE"
    assert any("cut off" in p.message for p in result.problems)


# --- the check's other verdicts -----------------------------------------------------------


def _session(log, data):
    log.start("P01", mode="wizard")
    for marker in ("yawn", "looked_away", "back_on_task"):
        log.event("annotation", source="wizard", marker=marker)
    log.end()
    return only_session(data)


def test_a_clean_session_is_ok(log, data):
    assert check_session(_session(log, data)).status == "OK"


def test_a_missing_middle_line_is_broken(log, data):
    folder = _session(log, data)
    lines = (folder / "events.jsonl").read_text().splitlines()
    (folder / "events.jsonl").write_text("\n".join(lines[:2] + lines[3:]) + "\n")
    result = check_session(folder)
    assert result.status == "BROKEN" and any("seq" in p.message for p in result.problems)


def test_a_changed_snapshot_is_broken(log, data):
    folder = _session(log, data)
    (folder / "snapshot" / "behaviors" / "refocus.yaml").write_text("tampered")
    assert check_session(folder).status == "BROKEN"


def test_missing_meta_is_broken(log, data):
    folder = _session(log, data)
    (folder / "meta.json").unlink()
    assert check_session(folder).status == "BROKEN"


def test_a_newer_schema_version_is_broken(log, data):
    folder = _session(log, data)
    meta = json.loads((folder / "meta.json").read_text())
    meta["schema_version"] = 99
    (folder / "meta.json").write_text(json.dumps(meta))
    assert check_session(folder).status == "BROKEN"


def test_an_unknown_event_type_is_only_a_warning(log, data):
    folder = _session(log, data)
    rows = read(folder)
    rows.insert(2, {**rows[1], "type": "from_the_future"})
    for i, row in enumerate(rows, start=1):
        row["seq"] = i
    (folder / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    result = check_session(folder)
    assert result.status == "OK" and any("unknown event types" in p.message for p in result.problems)


# --- the vision process's side ---------------------------------------------------------------


def test_features_follow_sessions_and_are_dropped_outside_them(log, data):
    stream = FeatureStream(["face", "gaze_x"], rate_hz=30, data_dir=data)
    assert stream.row([1, 0.5]) is False, "no session: dropped"
    log.start("P01", mode="wizard")
    time.sleep(0.6)
    for i in range(20):
        assert stream.row({"face": 1, "gaze_x": i / 20})
    log.end()
    time.sleep(0.6)
    assert stream.row([1, 0.5]) is False, "session over: dropped"
    log.start("P02", mode="wizard")
    time.sleep(0.6)
    for _ in range(5):
        stream.row([0, None])
    log.end()
    stream.close()
    folders = sorted(p for p in data.iterdir() if p.is_dir())
    rows = [len((f / "features.csv").read_text().splitlines()) - 1 for f in folders]
    assert rows == [20, 5]
    for folder in folders:
        assert check_session(folder).status == "OK"
        spec = json.loads((folder / "features.json").read_text())
        assert spec["columns"] == ["t_mono", "frame", "face", "gaze_x"] and spec["stopped"]


def test_features_stop_when_the_panel_has_crashed(data):
    data.mkdir(parents=True)
    folder = data / "2026-10-01_P05_wizard"
    folder.mkdir()
    (data / "CURRENT").write_text(json.dumps({"session": folder.name, "folder": str(folder),
                                              "pid": 999999}))
    stream = FeatureStream(["face"], data_dir=data)
    assert stream.row([1]) is False, "the panel that started it is gone"
    assert not (folder / "features.csv").exists()


def test_features_from_many_threads_stay_whole(log, data):
    log.start("P01", mode="wizard")
    stream = FeatureStream(["a", "b", "c"], data_dir=data, flush_seconds=0.05)
    time.sleep(0.6)
    threads = [threading.Thread(target=lambda: [stream.row([1, 2.5, "x"]) for _ in range(300)])
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stream.close()
    log.end()
    folder = only_session(data)
    lines = (folder / "features.csv").read_text().splitlines()
    assert len(lines) == 1 + 1200 and all(line.count(",") == 4 for line in lines)
    assert check_session(folder).feature_rows == 1200


def test_a_feature_csv_that_does_not_match_its_description_is_broken(log, data):
    log.start("P01", mode="wizard")
    stream = FeatureStream(["a"], data_dir=data)
    time.sleep(0.6)
    stream.row([1])
    stream.close()
    log.end()
    folder = only_session(data)
    spec = json.loads((folder / "features.json").read_text())
    spec["columns"].append("b")
    (folder / "features.json").write_text(json.dumps(spec))
    assert check_session(folder).status == "BROKEN"


def test_another_process_writes_its_own_events_file(log, data):
    vision = ProcessEventLog("vision", data_dir=data)
    log.start("P01", mode="wizard")
    time.sleep(0.6)
    assert vision.event("error", message="face lost")
    vision.close()
    log.end()
    folder = only_session(data)
    assert read(folder, "events.vision.jsonl")[0]["source"] == "perception"
    assert check_session(folder).status == "OK"


# --- the shared clock -------------------------------------------------------------------------


def _clock_in(python: str) -> float:
    out = subprocess.run([python, "-c", f"import sys; sys.path.insert(0, {str(REPO)!r}); "
                          "from eepy.logs.clock import monotonic; print(repr(monotonic()))"],
                         capture_output=True, text=True, timeout=30, check=True)
    return float(out.stdout)


def test_another_process_reads_the_same_clock():
    before = monotonic()
    other = _clock_in(sys.executable)
    after = monotonic()
    assert before <= other <= after


def test_the_vision_virtualenv_reads_the_same_clock():
    """The bug this module exists for: macOS Python 3.9's time.monotonic()
    restarts at zero in every process. Checked against the real vision venv."""
    python = REPO / ".venv-vision" / "bin" / "python"
    if not python.exists():
        pytest.skip("no .venv-vision on this machine")
    before = monotonic()
    other = _clock_in(str(python))
    after = monotonic()
    assert before <= other <= after, f"{before} <= {other} <= {after}"


# --- "is that program still running?" (the CURRENT pointer's pid) --------------------------


def test_pid_alive_for_this_and_a_finished_process():
    from eepy.logs.session import pid_alive

    assert pid_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not pid_alive(child.pid)
    assert not pid_alive(None) and not pid_alive("x") and not pid_alive(-1)


def test_pid_alive_never_uses_os_kill_on_windows(monkeypatch):
    """On Windows os.kill(pid, 0) SENDS CTRL+C (signal 0 is CTRL_C_EVENT): it
    interrupted CI's Windows run. pid_alive must ask Windows instead."""
    from eepy.logs import session

    monkeypatch.setattr(session.os, "name", "nt")
    monkeypatch.setattr(session.os, "kill", lambda *a: pytest.fail("os.kill called on Windows"))
    monkeypatch.setattr(session, "_pid_alive_windows", lambda pid: pid == 1234)
    assert session.pid_alive(1234) and not session.pid_alive(99)
