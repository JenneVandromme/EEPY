"""Tests for eepy.speech, with a mock engine and a mock output: no audio is
played, nothing goes over the network, and no voice model is needed."""

from __future__ import annotations

import io
import random
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from eepy.speech import Speaker, SpeechError
from eepy.speech.audio import duration
from eepy.speech.cache import SpeechCache, fixed_wordings
from eepy.speech.engines import Engine, EngineError, _chunks
from eepy.speech.lines import LineSelector
from eepy.speech.outputs import Output


def wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(b"\x00\x00" * int(8000 * seconds))
    return buffer.getvalue()


class MockEngine(Engine):
    """Makes silent WAVs. `seconds` long; can be told to fail or to be slow."""

    name = "mock"

    def __init__(self, voice="one", seconds=0.2, fail=False, delay=0.0):
        super().__init__(voice)
        self.seconds, self.fail, self.delay = seconds, fail, delay
        self.calls: list[str] = []

    def _synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("the mock engine is broken")
        return wav(self.seconds)


class MockOutput(Output):
    name = "mock"

    def __init__(self, fail=False):
        self.played: list[Path] = []
        self.stops = 0
        self.fail = fail

    def play(self, path):
        if self.fail:
            from eepy.speech.outputs import OutputError
            raise OutputError("the speaker is unplugged")
        self.played.append(Path(path))

    def stop(self):
        self.stops += 1


def lines(**spec):
    """A tiny lines.yaml: lines(greet={"hedged": [...], "direct": [...]})."""
    return {line_id: SimpleNamespace(variants=SimpleNamespace(
        hedged=v.get("hedged", []), direct=v.get("direct", []))) for line_id, v in spec.items()}


LINES = lines(
    greet={"hedged": ["Hi?", "Hello?", "Hey?"], "direct": ["Hello."]},
    wait={"hedged": ["Just {minutes} more?", "A little longer?"],
          "direct": ["Stop in {minutes} minutes."]},
    only_direct={"direct": ["Back to it."]},
)


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.lock = threading.Lock()

    def __call__(self, kind, data):
        with self.lock:
            self.events.append((kind, dict(data)))

    def kinds(self):
        with self.lock:
            return [k for k, _ in self.events]

    def of(self, kind):
        with self.lock:
            return [d for k, d in self.events if k == kind]


@pytest.fixture
def make(tmp_path):
    made = []

    def _make(engine=None, fallback=None, output=None, spec=LINES, prepare=False):
        recorder = Recorder()
        speaker = Speaker(engine if engine is not None else MockEngine(), fallback=fallback,
                          output=output or MockOutput(), cache=SpeechCache(tmp_path / "cache"),
                          lines=lambda: spec, emit=recorder, prepare=prepare)
        speaker.start()
        made.append(speaker)
        return speaker, recorder

    yield _make
    for speaker in made:
        speaker.close()


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


# --- choosing the words --------------------------------------------------------------


def test_never_the_same_wording_twice_in_a_row():
    selector = LineSelector(lambda: LINES, rng=random.Random(0))
    said = [selector.pick("greet", "hedged") for _ in range(200)]
    assert all(a != b for a, b in zip(said, said[1:]))
    assert set(said) == {"Hi?", "Hello?", "Hey?"}, "and every wording gets used"


def test_a_line_with_one_wording_may_repeat():
    selector = LineSelector(lambda: LINES)
    assert [selector.pick("greet", "direct") for _ in range(3)] == ["Hello."] * 3


def test_placeholders_are_filled():
    selector = LineSelector(lambda: LINES)
    assert selector.pick("wait", "direct", minutes=5) == "Stop in 5 minutes."


def test_a_wording_that_cannot_be_filled_is_passed_over():
    selector = LineSelector(lambda: LINES)
    assert {selector.pick("wait", "hedged") for _ in range(20)} == {"A little longer?"}


def test_a_missing_placeholder_is_explained_when_nothing_can_be_said():
    selector = LineSelector(lambda: LINES)
    with pytest.raises(SpeechError, match=r"needs \{minutes\}.*minutes=\.\.\."):
        selector.pick("wait", "direct")


def test_a_tone_with_no_wordings_falls_back_to_the_other():
    assert LineSelector(lambda: LINES).pick("only_direct", "hedged") == "Back to it."


def test_an_unknown_line_gets_a_suggestion():
    with pytest.raises(SpeechError, match='Did you mean "greet"'):
        LineSelector(lambda: LINES).pick("gret", "hedged")


def test_an_unknown_variant_is_refused():
    with pytest.raises(SpeechError, match="hedged"):
        LineSelector(lambda: LINES).pick("greet", "shouty")


# --- the cache ---------------------------------------------------------------------------


def test_every_fixed_wording_is_prepared_and_placeholders_are_not(tmp_path):
    engine, cache = MockEngine(), SpeechCache(tmp_path)
    report = cache.prepare(engine, LINES)
    fixed = [t for _, _, t in fixed_wordings(LINES)]
    assert report["generated"] == len(fixed) == 6
    assert "Stop in {minutes} minutes." not in engine.calls
    for text in fixed:
        assert cache.get(engine, text) is not None


def test_preparing_again_reuses_everything(tmp_path):
    engine, cache = MockEngine(), SpeechCache(tmp_path)
    cache.prepare(engine, LINES)
    engine.calls.clear()
    report = cache.prepare(engine, LINES)
    assert report["generated"] == 0 and report["reused"] == 6 and engine.calls == []


def test_editing_one_wording_regenerates_only_that_one(tmp_path):
    engine, cache = MockEngine(), SpeechCache(tmp_path)
    cache.prepare(engine, LINES)
    engine.calls.clear()
    edited = lines(greet={"hedged": ["Hi?", "Hello?", "Hey there?"], "direct": ["Hello."]},
                   wait={"hedged": ["A little longer?"]}, only_direct={"direct": ["Back to it."]})
    report = cache.prepare(engine, edited)
    assert engine.calls == ["Hey there?"]
    assert report["removed"] == 1, "the old 'Hey?' clip is deleted"
    assert cache.get(engine, "Hey?") is None


def test_a_different_voice_gets_its_own_clips(tmp_path):
    cache = SpeechCache(tmp_path)
    cache.prepare(MockEngine("one"), LINES)
    second = MockEngine("two")
    assert cache.prepare(second, LINES)["generated"] == 6
    assert cache.get(MockEngine("one"), "Hi?") is None, "the other voice's clips are pruned"


def test_a_line_that_fails_to_prepare_is_reported_not_fatal(tmp_path):
    report = SpeechCache(tmp_path).prepare(MockEngine(fail=True), LINES)
    assert len(report["failed"]) == 6 and report["generated"] == 0


# --- speaking --------------------------------------------------------------------------


def test_speak_line_returns_at_once_and_reports_every_step(make):
    speaker, events = make(MockEngine(seconds=0.2, delay=0.3))
    started = time.monotonic()
    utterance = speaker.speak_line("greet", "direct", source="wizard")
    assert time.monotonic() - started < 0.05, "never waits for synthesis"
    assert utterance.text == "Hello."
    assert utterance.wait(3)
    assert events.kinds() == ["speech_requested", "speech_started", "speech_finished"]
    start = events.of("speech_started")[0]
    assert start["engine"] == "mock / one" and start["source"] == "wizard"
    assert start["latency_ms"] >= 290, "latency includes the synthesis"
    assert start["duration_s"] == pytest.approx(0.2, abs=0.01)


def test_a_cached_line_starts_without_synthesis(make):
    engine = MockEngine()
    speaker, events = make(engine, prepare=True)
    wait_for(speaker.ready.is_set)
    engine.calls.clear()
    speaker.speak_line("greet", "direct").wait(3)
    assert engine.calls == [] and events.of("speech_started")[0]["cached"] is True


def test_free_text_and_filled_placeholders_are_synthesized_live(make):
    engine = MockEngine()
    speaker, events = make(engine, prepare=True)
    wait_for(speaker.ready.is_set)
    engine.calls.clear()
    speaker.speak_text("Need a hand?").wait(3)
    speaker.speak_line("wait", "direct", minutes=3).wait(3)
    assert engine.calls == ["Need a hand?", "Stop in 3 minutes."]
    assert [d["cached"] for d in events.of("speech_started")] == [False, False]
    assert events.of("speech_requested")[0]["line_id"] == "free_text"


def test_finishing_waits_for_the_clip_length(make):
    speaker, events = make(MockEngine(seconds=0.4))
    started = time.monotonic()
    speaker.speak_line("greet", "direct").wait(3)
    assert time.monotonic() - started >= 0.38


def test_stop_cuts_speech_quickly(make):
    output = MockOutput()
    speaker, events = make(MockEngine(seconds=5.0), output=output)
    utterance = speaker.speak_line("greet", "direct")
    wait_for(lambda: "speech_started" in events.kinds())
    started = time.monotonic()
    speaker.stop(by="stop")
    assert utterance.wait(1) is False
    assert time.monotonic() - started < 0.2, "cut within a few polls"
    assert utterance.outcome == "interrupted" and output.stops >= 1
    assert events.of("speech_interrupted")[0]["by"] == "stop"


def test_stop_never_blocks_even_mid_synthesis(make):
    speaker, events = make(MockEngine(delay=1.0))
    utterance = speaker.speak_line("greet", "direct")
    time.sleep(0.1)
    started = time.monotonic()
    speaker.stop()
    assert time.monotonic() - started < 0.01
    assert utterance.wait(3) is False
    assert "speech_started" not in events.kinds(), "a stopped line is never played"


def test_new_speech_interrupts_old_speech(make):
    speaker, events = make(MockEngine(seconds=5.0))
    first = speaker.speak_line("greet", "direct")
    wait_for(lambda: "speech_started" in events.kinds())
    second = speaker.speak_text("Actually, never mind.")
    assert first.wait(1) is False and first.by == "newer speech"
    wait_for(lambda: len(events.of("speech_started")) == 2)
    speaker.stop()
    second.wait(1)


def test_only_the_newest_waiting_request_is_kept(make):
    speaker, events = make(MockEngine(delay=0.3))
    a = speaker.speak_text("one")
    time.sleep(0.05)                    # a is synthesizing
    b = speaker.speak_text("two")       # waits
    c = speaker.speak_text("three")     # replaces b
    assert b.wait(1) is False and b.by == "newer speech"
    assert c.wait(3)
    assert [d["text"] for d in events.of("speech_started")] == ["three"]
    assert a.outcome == "interrupted"


def test_is_speaking(make):
    speaker, events = make(MockEngine(seconds=0.3))
    assert not speaker.is_speaking()
    utterance = speaker.speak_text("hello")
    assert speaker.is_speaking()
    utterance.wait(3)
    assert not speaker.is_speaking()


# --- failure ---------------------------------------------------------------------------


def test_the_fallback_engine_is_used_when_the_main_one_fails(make):
    speaker, events = make(MockEngine(fail=True), fallback=MockEngine(voice="backup"))
    assert speaker.speak_line("greet", "direct").wait(3)
    assert events.of("speech_started")[0]["engine"] == "mock / backup"


def test_when_everything_fails_the_words_still_get_out(make, tmp_path):
    shown = []
    speaker = Speaker(MockEngine(fail=True), fallback=MockEngine(fail=True), output=MockOutput(),
                      cache=SpeechCache(tmp_path / "c"), lines=lambda: LINES,
                      emit=Recorder(), on_text=lambda line, text: shown.append(text), prepare=False)
    speaker.start()
    utterance = speaker.speak_line("greet", "direct")
    assert utterance.wait(3) is False and utterance.outcome == "failed"
    assert shown == ["Hello."]
    failed = speaker.emit.of("speech_failed")[0]
    assert failed["text"] == "Hello." and "broken" in failed["error"]
    assert speaker.speak_text("still working?").outcome is None, "keeps going afterwards"
    speaker.close()


def test_an_unplayable_output_is_a_failure_not_a_crash(make):
    speaker, events = make(MockEngine(), output=MockOutput(fail=True))
    assert speaker.speak_line("greet", "direct").wait(3) is False
    assert "unplugged" in events.of("speech_failed")[0]["error"]


def test_an_unavailable_engine_is_skipped_for_the_fallback(make):
    class Missing(MockEngine):
        def unavailable(self):
            return "the voice is not downloaded"

    speaker, events = make(Missing(), fallback=MockEngine(voice="backup"))
    speaker.speak_line("greet", "direct").wait(3)
    assert events.of("speech_started")[0]["engine"] == "mock / backup"
    assert speaker.active_engine().voice == "backup"


def test_text_only_speaker_hands_words_over_and_finishes():
    shown = []
    speaker = Speaker(None, lines=lambda: LINES, emit=Recorder(),
                      on_text=lambda line, text: shown.append((line, text)))
    speaker.start()
    assert speaker.speak_line("greet", "direct").wait(2)
    assert shown == [("greet", "Hello.")]
    assert speaker.emit.of("speech_started")[0]["engine"] == "text-only"
    speaker.close()


# --- bits and pieces -------------------------------------------------------------------------


def test_wav_duration_is_exact():
    assert duration(wav(1.25)) == pytest.approx(1.25)


def test_google_text_is_packed_into_few_requests():
    text = ("Looks like you've been staring at the same page for a while. " * 6).strip()
    pieces = _chunks(text, 200)
    assert len(pieces) == 2 and all(len(p) <= 200 for p in pieces)
    assert " ".join(pieces) == text, "nothing lost"
    long_word_run = "word " * 90
    assert all(len(p) <= 200 for p in _chunks(long_word_run.strip(), 200))


def test_engines_report_failure_as_engine_error():
    with pytest.raises(EngineError, match="broken"):
        MockEngine(fail=True).synthesize("hello")
    with pytest.raises(EngineError, match="nothing to say"):
        MockEngine().synthesize("   ")
