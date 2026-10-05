"""Action units (eepy/perception/aus/) with a MOCK detector: no Py-Feat, no PyTorch,
no camera. Runs in every environment, including the ones without the extra.
"""

from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace as NS
from typing import Dict, List

import numpy as np
import pytest

from eepy.camera.config import CameraConfigError
from eepy.camera.frame import Frame
from eepy.camera.region import Box
from eepy.logs.schema import check_event
from eepy.perception.aus import config as au_config
from eepy.perception.aus.config import AUConfig, Profile, parse
from eepy.perception.aus.detector import AUDetector
from eepy.perception.aus.device import choose_device, cpu_threads, gpu_advice
from eepy.perception.aus.extractor import ActionUnitExtractor, AUResult, _kappa
from eepy.perception.baseline import Baseline
from eepy.perception.extractors import FaceEvent
from eepy.perception.observation import FaceObservation

BASE = Baseline()
UNITS = ("AU04", "AU26", "AU43")


class MockDetector(AUDetector):
    """Answers after `delay` seconds; records which frames it was given."""

    def __init__(self, delay: float = 0.0, values: Dict[str, float] = None, device: str = "cpu"):
        self.delay, self.device, self.units, self.missing = delay, device, UNITS, ()
        self.values = values or {"AU04": 0.7, "AU26": 0.1, "AU43": 0.05}
        self.seen: List[int] = []
        self.closed = False

    def predict(self, image_bgr, box):
        time.sleep(self.delay)
        self.seen.append(int(image_bgr[0, 0, 0]))     # the frame index, painted into the pixels
        return dict(self.values)

    def close(self):
        self.closed = True


def make(detector: AUDetector = None, start: bool = True, **profile) -> ActionUnitExtractor:
    config = AUConfig(units=UNITS, stale_seconds=1.0, min_rate=2.0, slow_grace_seconds=1.0,
                      retry_seconds=1.0)
    config = replace(config, profile=Profile("test", **{"max_rate": 50.0, **profile}))
    detector = detector or MockDetector()
    return ActionUnitExtractor(config, detector_factory=lambda c: detector, start=start)


def face(index: int, t: float = None) -> FaceObservation:
    image = np.full((48, 64, 3), index % 256, dtype=np.uint8)
    t = index / 15.0 if t is None else t
    frame = Frame(image=image, t=t, index=index, source="fake")
    return FaceObservation(t=t, frame_index=index, source="fake", frame=frame, present=True,
                           face_box=Box(10, 10, 30, 30), width=64, height=48)


def wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


# --- device selection -----------------------------------------------------------------------


def fake_torch(cuda: bool, mps: bool):
    return NS(cuda=NS(is_available=lambda: cuda),
              backends=NS(mps=NS(is_available=lambda: mps)))


@pytest.mark.parametrize("cuda, mps, expected", [(True, True, "cuda"), (True, False, "cuda"),
                                                 (False, True, "mps"), (False, False, "cpu")])
def test_auto_prefers_cuda_then_mps_then_cpu(cuda, mps, expected):
    assert choose_device("auto", fake_torch(cuda, mps)) == (expected, None)


def test_an_unavailable_device_falls_back_and_says_so():
    device, note = choose_device("cuda", fake_torch(False, True))
    assert device == "mps" and "cuda" in note
    assert choose_device("cpu", fake_torch(True, True)) == ("cpu", None)   # explicit choice wins


def test_cpu_threads_are_set_explicitly_and_moderately():
    assert cpu_threads("auto", cores=12) == 4
    assert cpu_threads("auto", cores=4) == 2
    assert cpu_threads("auto", cores=1) == 2
    assert cpu_threads(6) == 6


def test_gpu_advice(monkeypatch):
    from eepy.perception.aus import device

    monkeypatch.setattr(device, "nvidia_gpu_present", lambda: True)
    assert "CPU-only PyTorch" in gpu_advice("cpu", require_gpu=False)
    assert gpu_advice("cuda", require_gpu=True) is None
    monkeypatch.setattr(device, "nvidia_gpu_present", lambda: False)
    assert "expects a GPU" in gpu_advice("cpu", require_gpu=True)
    assert gpu_advice("cpu", require_gpu=False) is None
    assert gpu_advice("mps", require_gpu=True) is None


# --- profiles and config ------------------------------------------------------------------------


def test_profile_selection_flag_then_env_then_laptop(monkeypatch):
    config = parse(None)
    monkeypatch.delenv("EEPY_PROFILE", raising=False)
    monkeypatch.setattr(au_config, "env_value", lambda name: None)
    assert config.with_profile(None).profile.name == "laptop"
    monkeypatch.setattr(au_config, "env_value", lambda name: "demo-desktop")
    chosen = config.with_profile(None).profile
    assert chosen.name == "demo-desktop" and chosen.require_gpu and chosen.max_rate == 15
    assert config.with_profile("laptop").profile.name == "laptop"           # the flag wins
    with pytest.raises(CameraConfigError, match="not a profile"):
        config.with_profile("gaming-pc")


def test_the_repo_config_has_both_profiles_and_the_default_units():
    pytest.importorskip("yaml")
    loaded = au_config.load(profile="laptop")
    assert set(loaded.profiles) >= {"laptop", "demo-desktop"}
    assert loaded.units == au_config.DEFAULT_UNITS


@pytest.mark.parametrize("raw, message", [
    ({"units": ["AU99"]}, "not available from Py-Feat"),
    ({"profiles": {"x": {"device": "tpu"}}}, "device must be"),
    ({"profiles": {"x": {"enabled": "maybe"}}}, "enabled must be"),
    ({"stale_seconds": 0}, "between"),
    ({"colour": 1}, "not an aus setting"),
])
def test_bad_au_settings_are_explained(raw, message):
    with pytest.raises(CameraConfigError, match=message):
        parse(raw)


def test_settings_py_validates_the_aus_section():
    pytest.importorskip("pydantic")
    from eepy.settings import load_settings

    assert load_settings().aus is not None


# --- the disabled path (designers' laptops without the extra) -------------------------------------


def test_without_the_extra_aus_are_off_and_nothing_breaks(monkeypatch):
    from eepy.perception.aus import extractor as module

    monkeypatch.setattr(module, "aus_available", lambda: (False, "AUs are not installed (optional)."))
    au = ActionUnitExtractor(AUConfig(units=UNITS))           # the real factory: must not load
    assert au.status == "off" and "not installed" in au.reason and au._thread is None
    out = au.update(face(1), BASE)
    assert all(v is None for v in out.values.values())
    assert au.summary(1.0) == []
    status = au.drain()
    assert status[0][0] == "au_status" and status[0][1]["status"] == "off"
    au.close()


def test_a_profile_can_switch_aus_off():
    au = make(enabled=False)
    assert au.status == "off" and "switched off" in au.reason
    au.close()


def test_a_failing_model_load_turns_aus_off_not_the_pipeline():
    from eepy.perception.aus.detector import AUUnavailable

    def broken(config):
        raise AUUnavailable("no internet for the download")

    au = ActionUnitExtractor(AUConfig(units=UNITS), detector_factory=broken)
    assert wait_until(lambda: au.status == "off")
    assert "no internet" in au.reason
    au.close()


def test_the_real_extra_is_detected_correctly():
    from eepy.perception.aus import aus_available

    from eepy.perception.aus.detector import import_pyfeat

    from eepy.perception.aus.detector import AUUnavailable

    ok, why = aus_available()
    try:
        import_pyfeat()          # never `import feat` directly: torch must come first
        installed = True
    except ImportError:
        installed = False
    except AUUnavailable as exc:  # installed, but broken (e.g. torch/torchcodec mismatch)
        assert "same" in str(exc).lower() and "README" in str(exc)   # says how to fix it
        installed = True
    assert ok == installed, why


def test_a_mismatched_pytorch_install_gives_a_clear_message(monkeypatch):
    """CI found this one: a CUDA-13 torchcodec next to a CPU PyTorch makes Py-Feat's
    import raise OSError ("libnvrtc.so.13: cannot open shared object file"). It must
    become a clear 'AUs off' reason, never a crash."""
    import builtins

    from eepy.perception.aus.detector import AUUnavailable, import_pyfeat

    real_import = builtins.__import__

    import types

    def broken(name, *args, **kwargs):
        if name == "torch":       # torch itself loads (a stand-in where it is not installed)
            try:
                return real_import(name, *args, **kwargs)
            except ImportError:
                return types.ModuleType("torch")
        if name.startswith("feat"):
            raise OSError("Could not load this library: libtorchcodec_image.so")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(AUUnavailable, match="do not match"):
        import_pyfeat()
    monkeypatch.setattr(builtins, "__import__", real_import)

    # ...and the extractor turns it into "off", with that reason, instead of dying.
    def factory(config):
        raise AUUnavailable("PyTorch and torchcodec do not match ...")

    au = ActionUnitExtractor(AUConfig(units=UNITS), detector_factory=factory)
    assert wait_until(lambda: au.status == "off")
    assert "do not match" in au.reason
    au.close()


# --- never blocking, latest face only ---------------------------------------------------------------


def test_the_face_loop_is_never_blocked_by_a_slow_model():
    # Timing is measured, not assumed: CI machines (GitHub's macOS runners above
    # all) sleep much longer than asked, so "45 frames = 3 s" does not hold there.
    detector = MockDetector(delay=0.3)
    au = make(detector)
    assert wait_until(lambda: au.status == "running")
    worst = 0.0
    started = time.monotonic()
    frames = 45
    for index in range(1, frames + 1):                 # ~15 fps
        start = time.perf_counter()
        au.update(face(index), BASE)
        worst = max(worst, time.perf_counter() - start)
        time.sleep(1 / 15)
    elapsed = time.monotonic() - started
    au.close()
    assert worst < 0.02                                # never waits for the 300 ms model
    # The model can finish at most one face per 0.3 s, so most faces were SKIPPED,
    # never queued up...
    assert len(detector.seen) <= elapsed / 0.3 + 2
    assert len(detector.seen) < frames / 2
    # ...always moving on to the newest face.
    assert detector.seen == sorted(set(detector.seen))
    assert detector.closed


def test_results_are_not_more_frequent_than_max_rate():
    detector = MockDetector(delay=0.0)
    au = make(detector, max_rate=10.0)
    assert wait_until(lambda: au.status == "running")
    started = time.monotonic()
    index = 0
    while time.monotonic() < started + 2.0:
        index += 1
        au.update(face(index, t=time.monotonic()), BASE)
        time.sleep(0.005)                              # a fast camera (slower on CI: measured)
    elapsed = time.monotonic() - started
    au.close()
    offered = index / elapsed
    assert len(detector.seen) <= 10 * elapsed + 2      # never above max_rate...
    if offered > 15:                                   # ...and close to it when faces are plentiful
        assert len(detector.seen) >= 0.6 * 10 * elapsed


def test_a_fast_machine_keeps_up_with_every_face_offered():
    # A fast model is limited only by how many faces arrive (and max_rate). How
    # many arrive depends on the machine running the test, so it is measured.
    au = make(MockDetector(delay=0.001), max_rate=40.0)
    assert wait_until(lambda: au.status == "running")
    started = time.monotonic()
    index = 0
    while time.monotonic() < started + 1.5:
        index += 1
        au.update(face(index, t=time.monotonic()), BASE)
        time.sleep(0.01)
    offered = index / (time.monotonic() - started)
    rate = au.achieved_rate()
    au.close()
    assert rate >= 0.7 * min(offered, 40.0)
    assert rate <= 40.0 * 1.15


def test_too_slow_pauses_the_aus_and_then_retries():
    detector = MockDetector(delay=0.6)                 # < 2 results/s possible
    au = make(detector)
    assert wait_until(lambda: au.status == "running")
    index = 0
    paused = False
    end = time.monotonic() + 4.0
    while time.monotonic() < end and not paused:
        index += 1
        au.update(face(index, t=time.monotonic()), BASE)
        time.sleep(0.05)
        paused = au.status == "paused"
    assert paused
    assert all(v is None for k, v in au.current(time.monotonic()).items() if k.startswith("au_AU"))
    statuses = [fields["status"] for kind, fields in au.drain() if kind == "au_status"]
    assert "paused" in statuses
    # After retry_seconds it tries again.
    detector.delay = 0.0
    end = time.monotonic() + 3.0
    while time.monotonic() < end and au.status != "running":
        index += 1
        au.update(face(index, t=time.monotonic()), BASE)
        time.sleep(0.05)
    assert au.status == "running"
    au.close()


# --- staleness ---------------------------------------------------------------------------------


def test_results_carry_their_frame_time_and_go_stale():
    au = make(start=False)
    au.status = "running"
    au._latest = AUResult(t=10.0, frame_index=150, values={"AU04": 0.8}, infer_ms=20, done=10.1)
    fresh = au.current(10.4)
    assert fresh["au_AU04"] == 0.8 and fresh["au_stale"] == 0 and fresh["au_age_ms"] == pytest.approx(400)
    old = au.current(11.5)
    assert old["au_AU04"] is None and old["au_stale"] == 1 and old["au_age_ms"] == pytest.approx(1500)


def test_rows_go_to_the_au_stream_with_the_source_frame_time():
    rows = []

    class Rows:
        def row(self, values, t_mono=None, frame=None):
            rows.append((t_mono, frame, values))

    au = make()
    au.features = Rows()
    assert wait_until(lambda: au.status == "running")
    au.update(face(7, t=123.4), BASE)
    assert wait_until(lambda: rows)
    au.close()
    t_mono, frame, values = rows[0]
    assert t_mono == 123.4 and frame == 7 and values["AU04"] == 0.7 and "infer_ms" in values


# --- windows and cross-checks --------------------------------------------------------------------


def results(au: ActionUnitExtractor, rows: List[tuple]) -> None:
    """Inject AU results directly: (t, {AU: value})."""
    for t, values in rows:
        au._results.append(AUResult(t, int(t * 10), values, 15.0, t + 0.05))


def test_window_mean_and_active_share_are_time_weighted():
    au = make(start=False)
    au.status = "running"
    # AU04 "on" (0.8) for the first 4 s, "off" (0.2) for the next 6 s, one result per 0.1 s.
    results(au, [(i / 10, {"AU04": 0.8 if i < 40 else 0.2, "AU26": 0.0, "AU43": 0.0}) for i in range(100)])
    window = dict(au.summary(10.0))["au_window"]
    assert window["AU04_active_10s"] == pytest.approx(0.4, abs=0.02)
    assert window["AU04_mean_10s"] == pytest.approx(0.44, abs=0.02)
    assert window["results_10s"] >= 99 and "AU04_mean_60s" in window


def test_au43_is_cross_checked_against_the_eye_features():
    au = make(start=False)
    au.status = "running"
    rows = []
    for i in range(100):
        t = i / 10
        eyes_shut = 2.0 <= t < 4.0
        au.notice(t, {"eye_closure": 0.95 if eyes_shut else 0.05}, [])
        au43 = 0.9 if (2.0 <= t < 3.8) else 0.1           # agrees except 0.2 s
        rows.append((t, {"AU04": 0.0, "AU26": 0.0, "AU43": au43}))
    results(au, rows)
    window = dict(au.summary(10.0))["au_window"]
    assert window["au43_eyes_both_10s"] == 18 and window["eyes_only_10s"] == 2 and window["au43_only_10s"] == 0
    assert window["au43_eyes_agree_10s"] == pytest.approx(0.98)
    assert window["au43_eyes_kappa_10s"] > 0.9


def test_jaw_drop_is_cross_checked_against_yawns():
    au = make(start=False)
    au.status = "running"
    au.notice(5.0, {}, [FaceEvent("yawn", 3.0, {"duration_s": 2.0})])
    results(au, [(i / 10, {"AU04": 0.0, "AU43": 0.0, "AU26": 0.9 if 3.0 <= i / 10 <= 5.0 else 0.1})
                 for i in range(80)])
    window = dict(au.summary(8.0))["au_window"]
    assert window["jaw_in_yawn_10s"] == pytest.approx(0.9) and window["jaw_outside_yawn_10s"] == pytest.approx(0.1)


def test_kappa():
    assert _kappa(50, 0, 0, 50) == 1.0
    assert _kappa(25, 25, 25, 25) == 0.0
    assert _kappa(0, 0, 0, 0) is None


def test_au_records_fit_the_logging_schema():
    au = make()
    assert wait_until(lambda: au.status == "running")
    au.update(face(1, t=time.monotonic()), BASE)
    time.sleep(0.2)
    records = au.drain() + au.summary(time.monotonic())
    au.close()
    assert {kind for kind, _ in records} >= {"au_status", "au_window"}
    for kind, fields in records:
        check_event(kind, "perception", fields)
        assert all(isinstance(v, (int, float, str, type(None))) for v in fields.values())


# --- in the pipeline ---------------------------------------------------------------------------------


def test_the_pipeline_passes_au_values_and_records_through():
    from eepy.perception import FacePipeline, default_extractors

    au = make(MockDetector(delay=0.01))
    pipeline = FacePipeline(camera=None, extractors=default_extractors() + [au],
                            landmarker_factory=lambda: None)
    assert wait_until(lambda: au.status == "running")
    kinds, last = set(), None
    for index in range(1, 40):
        # A9's extractors need real landmarks, so the pipeline sees "no face" here,
        # and the AU extractor gets the face directly -- as update() would.
        au.update(face(index, t=index / 15), BASE)
        last = pipeline.process(replace(face(index, t=index / 15), present=False))
        kinds |= {kind for kind, _ in last.records}
        time.sleep(0.02)
    pipeline.stop(stop_camera=False)
    assert "au_status" in kinds and "au_window" in kinds
    assert "au_stale" in last.values and "au_AU04" in last.values
    assert "au_AU04" not in pipeline.columns          # per-result rows go to aus.csv instead


# --- the real model (only where requirements-aus.txt is installed) ----------------------------------


def test_the_real_au_model_runs_on_the_cpu():
    from eepy.perception.aus import aus_available
    from eepy.perception.aus.config import DEFAULT_UNITS
    from eepy.perception.aus.detector import AUUnavailable, load_detector

    ok, why = aus_available()
    if not ok:
        pytest.skip(why)
    try:
        detector = load_detector(DEFAULT_UNITS, device="cpu")
    except AUUnavailable as exc:
        pytest.skip(f"model not downloadable here: {exc}")
    values = detector.predict(np.full((462, 821, 3), 128, np.uint8), Box(300, 100, 220, 260))
    detector.close()
    assert set(values) == set(DEFAULT_UNITS) and detector.missing == ()
    assert all(0.0 <= v <= 1.0 for v in values.values())
