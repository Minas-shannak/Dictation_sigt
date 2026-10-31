"""services.transcript_cache: the unified format and both cache layers, with a fake transcriber."""

import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import config
import services.transcript_cache as tc

FIXTURES = Path(__file__).parent / "fixtures"
CONVERSATION = json.loads((FIXTURES / "conversation.json").read_text(encoding="utf-8"))["raw"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Cache folders in tmp_path; a fake transcriber that returns `raw` and counts calls."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "ASR_CACHE_DIR", tmp_path / "cache" / "asr")
    monkeypatch.setattr(config, "TRANSCRIPT_CACHE_DIR", tmp_path / "cache" / "transcripts")
    state = type("State", (), {})()
    state.raw = copy.deepcopy(CONVERSATION)
    state.transcribe_calls = []
    state.extract_calls = 0

    def fake_transcribe(audio_path, language=None, model_name=None, on_progress=None, on_stage=None):
        state.transcribe_calls.append((audio_path, language, model_name))
        if on_progress:
            on_progress(1.0)
        raw = copy.deepcopy(state.raw)
        raw["model"] = model_name
        raw["asr_settings"] = tc.asr_settings(language)
        return raw

    @contextmanager
    def fake_extract():
        state.extract_calls += 1
        yield "/tmp/fake.wav"

    monkeypatch.setattr(tc, "transcribe_audio", fake_transcribe)
    state.extract = fake_extract
    state.tmp = tmp_path
    return state


def files(folder):
    return sorted(p.name for p in Path(folder).glob("*")) if Path(folder).exists() else []


SOURCE = tc.youtube_source("eyoFYpI47Qg")


# --- Unified format -------------------------------------------------------------------

def test_unified_transcript_has_the_section_11_fields(env):
    t = tc.get_transcript(SOURCE, env.extract)
    for key in ["video_id", "source_type", "source_ref", "original_filename", "language", "language_probability",
                "model", "device", "compute_type", "asr_settings", "processing_settings", "processing_version",
                "transcript_version", "created_at", "original_transcript", "training_transcript",
                "excluded_segments", "sentences"]:
        assert key in t, key
    assert (t["video_id"], t["source_type"], t["source_ref"]) == ("yt:eyoFYpI47Qg", "youtube", "eyoFYpI47Qg")
    assert t["model"] == config.WHISPER_MODEL and t["processing_version"] == config.PROCESSING_VERSION
    s0 = t["sentences"][0]
    assert set(s0) == {"index", "text", "start", "end", "clarity", "unclear", "words"}
    assert (s0["start"], s0["end"]) == (s0["words"][0]["start"], s0["words"][-1]["end"])
    json.dumps(t)


def test_raw_result_is_stored_separately_not_inside_the_transcript(env):
    t = tc.get_transcript(SOURCE, env.extract)
    assert "segments" not in t and "raw" not in t
    (asr_file,) = Path(config.ASR_CACHE_DIR).glob("*.json")
    assert json.loads(asr_file.read_text())["raw"]["segments"] == CONVERSATION["segments"]


def test_transcript_version_is_a_hash_of_the_sentence_texts():
    a = [{"text": "Hello there."}, {"text": "How are you?"}]
    assert tc.transcript_version(a) == tc.transcript_version(copy.deepcopy(a))
    assert tc.transcript_version(a) != tc.transcript_version([{"text": "Hello there."}, {"text": "How are you!"}])
    assert len(tc.transcript_version(a)) == 12


def test_source_ids_are_prefixed_and_local_files_hashed_by_content(tmp_path):
    a = tmp_path / "talk.mp4"
    b = tmp_path / "renamed copy.mp4"
    data = b"\x00video" * 400_000  # several hash chunks
    a.write_bytes(data)
    b.write_bytes(data)
    sa, sb = tc.local_source(str(a)), tc.local_source(str(b), "My talk.mp4")
    assert sa.source_ref == sb.source_ref == hashlib.sha256(data).hexdigest()
    assert sa.video_id == "local:" + sa.source_ref and sb.original_filename == "My talk.mp4"
    assert tc.youtube_source("abc").video_id == "yt:abc"
    assert tc.youtube_source("abc").video_id != tc.Source("local", "abc").video_id


# --- Layer 1 / layer 2 ------------------------------------------------------------------

def test_second_load_uses_the_cache_without_transcribing_or_extracting(env):
    first = tc.get_transcript(SOURCE, env.extract)
    second = tc.get_transcript(SOURCE, env.extract)
    assert len(env.transcribe_calls) == 1 and env.extract_calls == 1
    assert second == first


def test_changing_processing_settings_reuses_layer_1(env):
    a = tc.get_transcript(SOURCE, env.extract)
    b = tc.get_transcript(SOURCE, env.extract, processing_settings={"remove_fillers": False})
    assert len(env.transcribe_calls) == 1  # no retranscription
    assert a["processing_settings"]["remove_fillers"] is True and b["processing_settings"]["remove_fillers"] is False
    # The conversation clip has two real "um": keeping them changes the sentences, so the version changes.
    assert a["transcript_version"] != b["transcript_version"]
    assert len(files(config.ASR_CACHE_DIR)) == 1 and len(files(config.TRANSCRIPT_CACHE_DIR)) == 2


def test_new_processing_version_reprocesses_without_transcribing(env, monkeypatch):
    tc.get_transcript(SOURCE, env.extract)
    monkeypatch.setattr(config, "PROCESSING_VERSION", config.PROCESSING_VERSION + 1)
    t = tc.get_transcript(SOURCE, env.extract)
    assert len(env.transcribe_calls) == 1
    assert t["processing_version"] == config.PROCESSING_VERSION
    assert len(files(config.TRANSCRIPT_CACHE_DIR)) == 2


@pytest.mark.parametrize("change", [
    lambda mp: mp.setattr(config, "WHISPER_MODEL", "large-v3"),
    lambda mp: mp.setattr(config, "INITIAL_PROMPT", "Something else."),
    lambda mp: mp.setattr(config, "VAD_FILTER", True),
    lambda mp: mp.setattr(config, "BEAM_SIZE", 1),
])
def test_changing_model_or_recognition_settings_retranscribes(env, monkeypatch, change):
    tc.get_transcript(SOURCE, env.extract)
    change(monkeypatch)
    tc.get_transcript(SOURCE, env.extract)
    assert len(env.transcribe_calls) == 2
    assert len(files(config.ASR_CACHE_DIR)) == 2  # both versions are kept


def test_language_is_part_of_the_layer_1_key(env):
    tc.get_transcript(SOURCE, env.extract, language="en")
    tc.get_transcript(SOURCE, env.extract, language=None)  # auto-detect
    tc.get_transcript(SOURCE, env.extract, language="en")
    assert [c[1] for c in env.transcribe_calls] == ["en", None]


def test_different_sources_never_share_entries(env):
    tc.get_transcript(tc.youtube_source("aaaaaaaaaaa"), env.extract)
    tc.get_transcript(tc.Source("local", "aaaaaaaaaaa", "a.mp4"), env.extract)
    assert len(env.transcribe_calls) == 2


def test_old_caption_cache_files_are_never_read(env):
    # The caption-based app's cache: cache/<id>.json with {"videoId", "lines"}.
    Path(config.CACHE_DIR).mkdir(parents=True)
    old = {"videoId": "eyoFYpI47Qg", "lang": "en", "source": "manual",
           "lines": [{"id": 0, "start": 0.0, "duration": 1.0, "text": "Caption text"}]}
    (Path(config.CACHE_DIR) / "eyoFYpI47Qg.json").write_text(json.dumps(old))
    t = tc.get_transcript(SOURCE, env.extract)
    assert len(env.transcribe_calls) == 1
    assert "Caption text" not in t["training_transcript"]
    assert json.loads((Path(config.CACHE_DIR) / "eyoFYpI47Qg.json").read_text()) == old  # untouched


def test_unreadable_or_foreign_cache_entries_are_treated_as_misses(env):
    tc.get_transcript(SOURCE, env.extract)
    (asr_file,) = Path(config.ASR_CACHE_DIR).glob("*.json")
    asr_file.write_text('{"truncated": ')
    for f in Path(config.TRANSCRIPT_CACHE_DIR).glob("*.json"):
        f.write_text(json.dumps({"cache_format": 999, "kind": "transcript", "key": "x"}))
    t = tc.get_transcript(SOURCE, env.extract)
    assert len(env.transcribe_calls) == 2 and t["sentences"]
    assert json.loads(asr_file.read_text())["kind"] == "asr_raw"  # rewritten


# --- Failures are never cached ----------------------------------------------------------------

def test_empty_result_is_an_error_and_nothing_is_cached(env):
    env.raw = dict(env.raw, segments=[])
    with pytest.raises(tc.TranscriptError) as exc:
        tc.get_transcript(SOURCE, env.extract)
    assert exc.value.code == "no_speech"
    assert files(config.ASR_CACHE_DIR) == [] and files(config.TRANSCRIPT_CACHE_DIR) == []


@pytest.mark.parametrize("bad_word", [
    {"word": " x", "start": 5.0, "end": 4.0, "probability": 0.9},             # ends before it starts
    {"word": " x", "start": float("nan"), "end": 1.0, "probability": 0.9},    # not a number
    {"word": " x", "start": 1.0, "end": 9999.0, "probability": 0.9},          # beyond the audio
])
def test_broken_timestamps_are_an_error_and_nothing_is_cached(env, bad_word):
    raw = copy.deepcopy(env.raw)
    raw["segments"][3]["words"][2] = bad_word
    env.raw = raw
    with pytest.raises(tc.TranscriptError) as exc:
        tc.get_transcript(SOURCE, env.extract)
    assert exc.value.code == "invalid_result"
    assert files(config.ASR_CACHE_DIR) == [] and files(config.TRANSCRIPT_CACHE_DIR) == []


def test_all_hallucinated_keeps_raw_for_review_but_no_transcript(env):
    silence = json.loads((FIXTURES / "silence.json").read_text(encoding="utf-8"))["raw"]  # "Thank you." on silence
    env.raw = silence
    for _ in range(2):
        with pytest.raises(tc.TranscriptError) as exc:
            tc.get_transcript(SOURCE, env.extract)
        assert exc.value.code == "all_excluded"
    assert len(env.transcribe_calls) == 1  # the raw result was kept, not transcribed again
    assert len(files(config.ASR_CACHE_DIR)) == 1 and files(config.TRANSCRIPT_CACHE_DIR) == []


def test_failed_transcription_writes_nothing(env, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("model crashed")
    monkeypatch.setattr(tc, "transcribe_audio", broken)
    with pytest.raises(RuntimeError):
        tc.get_transcript(SOURCE, env.extract)
    assert files(config.ASR_CACHE_DIR) == [] and files(config.TRANSCRIPT_CACHE_DIR) == []


# --- Atomic writes --------------------------------------------------------------------------

def test_atomic_write_leaves_no_partial_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "entry.json"
    tc.write_json_atomic(target, {"a": 1})

    def exploding_dump(*a, **kw):
        raise KeyboardInterrupt  # e.g. the server stopped mid-write
    monkeypatch.setattr(tc.json, "dump", exploding_dump)
    with pytest.raises(KeyboardInterrupt):
        tc.write_json_atomic(target, {"a": 2})
    assert json.loads(target.read_text()) == {"a": 1}  # old entry intact
    assert sorted(p.name for p in tmp_path.iterdir()) == ["entry.json"]  # no temp file left


def test_progress_and_stages_are_reported_on_a_miss_only(env):
    stages, progress = [], []
    tc.get_transcript(SOURCE, env.extract, on_progress=progress.append, on_stage=stages.append)
    assert stages == ["processing", "preparing"] and progress == [1.0]  # the audio source reports its own stages
    stages.clear()
    tc.get_transcript(SOURCE, env.extract, on_stage=stages.append)
    assert stages == ["processing"]
