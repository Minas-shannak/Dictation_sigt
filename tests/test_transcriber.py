"""ai.transcriber logic with a fake Whisper model: no GPU, no model download."""

import threading
import time
from types import SimpleNamespace

import pytest

import ai.transcriber as t


def segment(i, start, end, text="hello there"):
    words = [SimpleNamespace(word=" hello", start=start, end=(start + end) / 2, probability=0.9),
             SimpleNamespace(word=" there", start=(start + end) / 2, end=end, probability=0.8)]
    return SimpleNamespace(id=i, start=start, end=end, text=" " + text, avg_logprob=-0.2, no_speech_prob=0.01,
                           compression_ratio=1.2, temperature=0.0, words=words)


class FakeModel:
    def __init__(self, device, fail_with=None, delay=0.0):
        self.device, self.fail_with, self.delay = device, fail_with, delay
        self.calls = []
        self.active = 0
        self.max_active = 0

    def transcribe(self, audio, **opts):
        self.calls.append((audio, opts))
        info = SimpleNamespace(language="en", language_probability=0.99, duration=10.0, duration_after_vad=9.0)

        def gen():
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                yield segment(0, 0.0, 4.0)
                time.sleep(self.delay)
                if self.fail_with:
                    raise self.fail_with
                yield segment(1, 5.0, 9.0)
            finally:
                self.active -= 1
        return gen(), info


@pytest.fixture
def fake(monkeypatch):
    """Reset the shared model and record every load as (device, compute_type)."""
    monkeypatch.setattr(t, "_model", None)
    monkeypatch.setattr(t, "_model_key", None)
    monkeypatch.setattr(t, "_cuda_failed", False)
    monkeypatch.setattr(t, "_preload_pip_cuda_libs", lambda: None)
    monkeypatch.setattr(t, "_cuda_device_count", lambda: 1)
    monkeypatch.setattr(t.config, "DEVICE_PREFERENCE", "auto")
    state = SimpleNamespace(loads=[], models=[], cuda_load_error=None, cuda_run_error=None, delay=0.0)

    def load(name, device, compute_type):
        state.loads.append((device, compute_type))
        if device == "cuda" and state.cuda_load_error:
            raise state.cuda_load_error
        model = FakeModel(device, fail_with=state.cuda_run_error if device == "cuda" else None, delay=state.delay)
        state.models.append(model)
        return model

    monkeypatch.setattr(t, "_load", load)
    return state


def test_model_is_loaded_lazily_once_and_reused(fake):
    assert fake.loads == []
    stages = []
    r1 = t.transcribe_audio("a.wav", on_stage=stages.append)
    r2 = t.transcribe_audio("b.wav", on_stage=stages.append)
    assert fake.loads == [("cuda", "float16")]
    assert stages == ["loading_model", "transcribing", "transcribing"]
    assert (r1["device"], r1["compute_type"]) == (r2["device"], r2["compute_type"]) == ("cuda", "float16")


def test_raw_result_keeps_segments_words_and_settings(fake):
    r = t.transcribe_audio("a.wav", language="en")
    assert [s["start"] for s in r["segments"]] == [0.0, 5.0]
    assert r["segments"][0]["words"][0] == {"word": " hello", "start": 0.0, "end": 2.0, "probability": 0.9}
    assert r["segments"][0]["no_speech_prob"] == 0.01
    assert r["asr_settings"]["vad_filter"] is False and r["asr_settings"]["condition_on_previous_text"] is False
    opts = fake.models[0].calls[0][1]
    assert opts["word_timestamps"] is True and opts["language"] == "en"


def test_empty_language_means_auto_detect(fake):
    t.transcribe_audio("a.wav", language="")
    assert fake.models[0].calls[0][1]["language"] is None


def test_progress_is_segment_end_over_duration(fake):
    fractions = []
    t.transcribe_audio("a.wav", on_progress=fractions.append)
    assert fractions == [0.4, 0.9, 1.0]


def test_no_cuda_device_uses_cpu(fake, monkeypatch):
    monkeypatch.setattr(t, "_cuda_device_count", lambda: 0)
    r = t.transcribe_audio("a.wav")
    assert fake.loads == [("cpu", "int8")]
    assert r["device"] == "cpu"


def test_cpu_preference_never_tries_cuda(fake, monkeypatch):
    monkeypatch.setattr(t.config, "DEVICE_PREFERENCE", "cpu")
    t.transcribe_audio("a.wav")
    assert fake.loads == [("cpu", "int8")]


def test_cuda_error_at_load_falls_back_to_cpu(fake):
    fake.cuda_load_error = RuntimeError("CUDA failed with error no CUDA-capable device is detected")
    r = t.transcribe_audio("a.wav")
    assert fake.loads == [("cuda", "float16"), ("cpu", "int8")]
    assert (r["device"], r["compute_type"]) == ("cpu", "int8")


def test_cuda_error_during_transcription_retries_on_cpu_and_stays_there(fake):
    fake.cuda_run_error = RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
    r1 = t.transcribe_audio("a.wav")
    r2 = t.transcribe_audio("b.wav")
    assert fake.loads == [("cuda", "float16"), ("cpu", "int8")]  # switched once, not per job
    assert r1["device"] == r2["device"] == "cpu"
    assert len(r1["segments"]) == 2  # the retried job is complete, not partial


def test_non_cuda_error_during_transcription_is_reported_not_retried(fake):
    fake.cuda_run_error = ValueError("broken audio")
    with pytest.raises(t.TranscriptionError) as exc:
        t.transcribe_audio("a.wav")
    assert exc.value.code == "transcription_failed"
    assert fake.loads == [("cuda", "float16")]
    assert t._cuda_failed is False


def test_non_cuda_load_error_is_reported(fake):
    fake.cuda_load_error = OSError("model download failed: connection refused")
    with pytest.raises(t.TranscriptionError) as exc:
        t.transcribe_audio("a.wav")
    assert exc.value.code == "model_load_failed"


def test_jobs_take_turns_on_the_shared_model(fake):
    fake.delay = 0.05
    threads = [threading.Thread(target=t.transcribe_audio, args=(f"{i}.wav",)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(fake.models) == 1
    assert len(fake.models[0].calls) == 4
    assert fake.models[0].max_active == 1


@pytest.mark.parametrize("message, hint", [
    ("Library libcublas.so.12 is not found or cannot be loaded", "nvidia-cublas-cu12"),
    ("Could not load library libcudnn_ops.so.9", "nvidia-cudnn-cu12"),
    ("CUDA failed with error no kernel image is available for execution on the device", "CUDA 12.8"),
])
def test_cuda_errors_get_actionable_hints(message, hint):
    err = RuntimeError(message)
    assert t.is_cuda_error(err)
    assert hint in t.cuda_fix_hint(err)
