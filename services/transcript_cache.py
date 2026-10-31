"""Unified transcripts with a two-layer cache.

Layer 1 (config.ASR_CACHE_DIR): the raw recognition result, keyed by source, model,
requested language and recognition settings. Rebuilding it means transcribing again,
so it is only rebuilt when one of those changes.

Layer 2 (config.TRANSCRIPT_CACHE_DIR): the processed transcript in the unified
format, keyed by the layer-1 key plus the processing settings and PROCESSING_VERSION.
It is rebuilt from layer 1, without transcribing, when processing changes.

Entries are JSON written atomically (temp file, then rename), so an interrupted job
never leaves a half-written entry, and only valid results are cached. The old
caption cache (cache/<id>.json) is a different format in a different folder and is
never read here.

Public function: get_transcript(source, extract_audio, ...).
"""

import hashlib
import json
import logging
import math
import os
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import config
from ai.transcriber import asr_settings, transcribe_audio
from ai.transcript_processor import process_transcript, resolve_settings

log = logging.getLogger(__name__)

HASH_CHUNK_BYTES = 1024 * 1024

EMPTY_MESSAGES = {
    "no_speech": "No speech was found in this video.",
    "all_excluded": "Only music or unclear sounds were found, so there are no sentences to practice.",
    "nothing_left": "No sentences could be made from the speech in this video.",
}


class TranscriptError(Exception):
    """A pipeline failure with a message that is safe to show to the user."""

    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code, self.user_message = code, user_message


@dataclass(frozen=True)
class Source:
    source_type: str                      # "youtube" or "local"
    source_ref: str                       # YouTube video ID, or SHA-256 of the file's content
    original_filename: Optional[str] = None

    @property
    def video_id(self) -> str:
        """Prefixed by source, so IDs from the two sources never collide."""
        return ("yt:" if self.source_type == "youtube" else "local:") + self.source_ref


def youtube_source(video_id: str) -> Source:
    return Source("youtube", video_id)


def file_sha256(path: str) -> str:
    """SHA-256 of a file's content, read in chunks so large videos don't fill memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_source(path: str, original_filename: Optional[str] = None) -> Source:
    """A local file is identified by its content, never by its name."""
    return Source("local", file_sha256(path), original_filename or Path(path).name)


# --- Keys and files -------------------------------------------------------------------

def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def layer1_key(source: Source, model: str, asr: Dict) -> str:
    return _hash({"video_id": source.video_id, "model": model, "asr_settings": asr})


def layer2_key(l1_key: str, processing_settings: Dict) -> str:
    return _hash({"asr_key": l1_key, "processing_settings": processing_settings,
                  "processing_version": config.PROCESSING_VERSION})


def _file_stem(source: Source) -> str:
    return source.video_id.replace(":", "-")


def _asr_path(source: Source, l1: str) -> Path:
    return Path(config.ASR_CACHE_DIR) / f"{_file_stem(source)}__{l1}.json"


def _transcript_path(source: Source, l2: str) -> Path:
    return Path(config.TRANSCRIPT_CACHE_DIR) / f"{_file_stem(source)}__{l2}.json"


def write_json_atomic(path: Path, data: Dict) -> None:
    """Write JSON to a temp file in the same folder, then rename it over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise


def _read_entry(path: Path, kind: str, key: str) -> Optional[Dict]:
    """A cache entry, or None if missing, unreadable or not the expected kind/key/format."""
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Ignoring unreadable cache file %s: %s", path, e)
        return None
    if not isinstance(entry, dict) or (entry.get("cache_format"), entry.get("kind"), entry.get("key")) != (
            config.CACHE_FORMAT, kind, key):
        log.warning("Ignoring cache file %s: not a format-%s %s entry for key %s", path, config.CACHE_FORMAT, kind, key)
        return None
    return entry


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Validation and the unified format ------------------------------------------------

def validate_raw(raw: Dict) -> None:
    """Raise TranscriptError unless the raw result has words with sane timestamps."""
    duration = raw.get("duration") or 0.0
    prev_start = 0.0
    n_words = 0
    for seg in raw.get("segments") or []:
        for w in seg.get("words") or []:
            start, end = w.get("start"), w.get("end")
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (start, end)) \
                    or start < 0 or end < start or end > duration + 1.0 or start < prev_start - 0.05:
                log.error("Invalid word timestamps in raw result: %r (previous start %.2f, duration %.2f)",
                          w, prev_start, duration)
                raise TranscriptError("invalid_result", "Transcription produced an invalid result. Please try again.")
            prev_start = start
            n_words += 1
    if n_words == 0:
        raise TranscriptError("no_speech", EMPTY_MESSAGES["no_speech"])


def transcript_version(sentences: List[Dict]) -> str:
    """A short hash of the sentence texts; saved progress is tied to it."""
    return hashlib.sha256("\n".join(s["text"] for s in sentences).encode("utf-8")).hexdigest()[:12]


def build_unified(source: Source, raw: Dict, processed: Dict) -> Dict:
    return {
        "video_id": source.video_id,
        "source_type": source.source_type,
        "source_ref": source.source_ref,
        "original_filename": source.original_filename,
        "duration": raw.get("duration"),
        "language": processed["language"],
        "language_probability": processed["language_probability"],
        "model": processed["model"],
        "device": processed["device"],
        "compute_type": processed["compute_type"],
        "asr_settings": processed["asr_settings"],
        "processing_settings": processed["processing_settings"],
        "processing_version": processed["processing_version"],
        "transcript_version": transcript_version(processed["sentences"]),
        "created_at": _now(),
        "original_transcript": processed["original_transcript"],
        "training_transcript": processed["training_transcript"],
        "excluded_segments": processed["excluded_segments"],
        "excluded": processed["excluded"],
        "sentences": processed["sentences"],
    }


# --- Pipeline ---------------------------------------------------------------------------

def get_transcript(
    source: Source,
    extract_audio: Callable[[], AbstractContextManager],
    language: Optional[str] = config.DEFAULT_LANGUAGE,
    processing_settings: Optional[Dict] = None,
    on_progress: Optional[Callable[[float], None]] = None,
    on_stage: Optional[Callable[[str], None]] = None,
) -> Dict:
    """The unified transcript for a source, transcribing only on a layer-1 miss.

    extract_audio: returns a context manager yielding a temporary audio path (for
    example services.audio_extractor.extracted_audio bound to a file); it is only
    called when the audio actually has to be transcribed.
    Raises TranscriptError for empty or invalid results, which are never cached as valid.
    """
    model = config.WHISPER_MODEL
    asr = asr_settings(language)
    l1 = layer1_key(source, model, asr)
    asr_path = _asr_path(source, l1)

    entry = _read_entry(asr_path, "asr_raw", l1)
    if entry is not None:
        raw = entry["raw"]
        log.info("Layer-1 cache hit for %s (%s): no transcription needed", source.video_id, asr_path.name)
    else:
        log.info("Layer-1 cache miss for %s: transcribing with %s", source.video_id, model)
        if on_stage:
            on_stage("extracting_audio")
        with extract_audio() as audio_path:
            raw = transcribe_audio(audio_path, language=language, model_name=model,
                                   on_progress=on_progress, on_stage=on_stage)
        validate_raw(raw)
        write_json_atomic(asr_path, {"cache_format": config.CACHE_FORMAT, "kind": "asr_raw", "key": l1,
                                     "video_id": source.video_id, "model": model, "created_at": _now(), "raw": raw})

    if on_stage:
        on_stage("processing")
    settings = resolve_settings(processing_settings)
    l2 = layer2_key(l1, settings)
    transcript_path = _transcript_path(source, l2)
    entry = _read_entry(transcript_path, "transcript", l2)
    if entry is not None:
        log.info("Layer-2 cache hit for %s (%s)", source.video_id, transcript_path.name)
        return entry["transcript"]

    processed = process_transcript(raw, settings)
    if processed["empty_reason"]:
        # The raw result stays in layer 1 (if it was valid) for review; nothing goes in layer 2.
        log.warning("No sentences for %s: %s (%d segments excluded)", source.video_id,
                    processed["empty_reason"], processed["excluded_segments"])
        raise TranscriptError(processed["empty_reason"], EMPTY_MESSAGES[processed["empty_reason"]])
    if on_stage:
        on_stage("preparing")
    unified = build_unified(source, raw, processed)
    write_json_atomic(transcript_path, {"cache_format": config.CACHE_FORMAT, "kind": "transcript", "key": l2,
                                        "asr_key": l1, "video_id": source.video_id, "transcript": unified})
    log.info("Cached transcript %s for %s: %d sentences, transcript_version %s",
             transcript_path.name, source.video_id, len(unified["sentences"]), unified["transcript_version"])
    return unified
