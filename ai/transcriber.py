"""Speech recognition with faster-whisper, on the NVIDIA GPU when possible.

One model is loaded lazily on the first transcription and shared by all later
ones; a lock makes jobs take turns on it. If CUDA fails, either when the model
loads or during a transcription (CTranslate2 loads cuBLAS lazily, so a missing
library often only shows up then), the model is reloaded on the CPU once and
stays there for the rest of the session.

Public function: transcribe_audio(audio_path, ...).
"""

import ctypes
import importlib.util
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import config

log = logging.getLogger(__name__)

# The CUDA libraries are pip packages inside the venv (nvidia-cublas-cu12, ...),
# which the dynamic loader doesn't search. Loading them by full path first lets
# CTranslate2's later dlopen("libcublas.so.12") find them without LD_LIBRARY_PATH.
# Order matters: libcublas depends on libcublasLt. cuDNN is optional for CTranslate2 4.6+.
PIP_CUDA_LIBS = ["cublas/lib/libcublasLt.so.12", "cublas/lib/libcublas.so.12", "cudnn/lib/libcudnn.so.9"]

# Substrings of CTranslate2 / CUDA error messages that mean "the GPU path is broken".
CUDA_ERROR_MARKERS = ("cuda", "cublas", "cudnn", "libcu", "kernel image", "nvrtc")

ProgressCallback = Callable[[float], None]
StageCallback = Callable[[str], None]


class TranscriptionError(Exception):
    """A transcription failure with a message that is safe to show to the user."""

    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code, self.user_message = code, user_message


_lock = threading.Lock()
_model = None
_model_key: Optional[Tuple[str, str, str]] = None  # (model name, device, compute type)
_cuda_failed = False  # set after the first CUDA failure; the session then stays on the CPU
_pip_cuda_preloaded = False


def is_cuda_error(err: BaseException) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in CUDA_ERROR_MARKERS)


def cuda_fix_hint(err: BaseException) -> str:
    """An actionable hint for the log, based on which library or feature failed."""
    msg = str(err).lower()
    if "libcublas" in msg:
        return ("cuBLAS for CUDA 12 is missing. Install it inside the venv: "
                "pip install nvidia-cublas-cu12 (or add its lib folder to LD_LIBRARY_PATH).")
    if "libcudnn" in msg:
        return ("cuDNN 9 for CUDA 12 is missing. Install it inside the venv: "
                "pip install nvidia-cudnn-cu12 (or add its lib folder to LD_LIBRARY_PATH).")
    if "kernel image" in msg or "not_supported" in msg or "no kernel" in msg:
        return ("This CTranslate2 build has no kernels for this GPU architecture. "
                "Upgrade ctranslate2 to a build made with CUDA 12.8+ (needed for Blackwell / RTX 50xx).")
    if "driver" in msg:
        return "The NVIDIA driver is missing or too old for this CUDA version. Check nvidia-smi."
    return "Check nvidia-smi and the CUDA library packages in the venv (nvidia-cublas-cu12)."


def _preload_pip_cuda_libs() -> None:
    global _pip_cuda_preloaded
    if _pip_cuda_preloaded:
        return
    _pip_cuda_preloaded = True
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        log.info("No pip-installed NVIDIA libraries found; relying on the system library path")
        return
    root = Path(list(spec.submodule_search_locations)[0])
    for rel in PIP_CUDA_LIBS:
        lib = root / rel
        if not lib.exists():
            continue
        try:
            ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            log.info("Loaded %s", lib)
        except OSError as e:
            log.warning("Could not load %s: %s", lib, e)


def _cuda_device_count() -> int:
    import ctranslate2
    try:
        return ctranslate2.get_cuda_device_count()
    except Exception as e:  # broken driver install
        log.warning("CUDA device query failed: %s", e)
        return 0


def _load(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel
    t0 = time.monotonic()
    model = WhisperModel(name, device=device, compute_type=compute_type)
    log.info("Loaded Whisper model %r on %s (%s) in %.1fs", name, device, compute_type, time.monotonic() - t0)
    return model


def _switch_to_cpu(name: str, reason: BaseException) -> None:
    """Reload the shared model on the CPU after a CUDA failure, for the rest of the session."""
    global _model, _model_key, _cuda_failed
    _cuda_failed = True
    log.error("CUDA failed (%s: %s). %s Falling back to the CPU (%s) for this session.",
              type(reason).__name__, reason, cuda_fix_hint(reason), config.CPU_COMPUTE_TYPE)
    _model = None
    _model_key = None
    try:
        _model = _load(name, "cpu", config.CPU_COMPUTE_TYPE)
    except Exception as e:
        log.exception("Loading the Whisper model on the CPU failed")
        raise TranscriptionError("model_load_failed", "Could not load the speech recognition model.") from e
    _model_key = (name, "cpu", config.CPU_COMPUTE_TYPE)


def _ensure_model(name: str, on_stage: Optional[StageCallback]):
    """Load the model if needed (caller holds the lock). Returns (model, device, compute_type)."""
    global _model, _model_key
    if _model is not None and _model_key[0] == name:
        return _model, _model_key[1], _model_key[2]
    if on_stage:
        on_stage("loading_model")
    _model = None
    want_cuda = config.DEVICE_PREFERENCE != "cpu" and not _cuda_failed
    if want_cuda and _cuda_device_count() == 0:
        log.info("No CUDA device available; using the CPU")
        want_cuda = False
    if want_cuda:
        _preload_pip_cuda_libs()
        try:
            _model = _load(name, "cuda", config.CUDA_COMPUTE_TYPE)
            _model_key = (name, "cuda", config.CUDA_COMPUTE_TYPE)
        except Exception as e:
            if not is_cuda_error(e):
                log.exception("Loading the Whisper model failed")
                raise TranscriptionError("model_load_failed", "Could not load the speech recognition model.") from e
            _switch_to_cpu(name, e)
    else:
        try:
            _model = _load(name, "cpu", config.CPU_COMPUTE_TYPE)
        except Exception as e:
            log.exception("Loading the Whisper model failed")
            raise TranscriptionError("model_load_failed", "Could not load the speech recognition model.") from e
        _model_key = (name, "cpu", config.CPU_COMPUTE_TYPE)
    return _model, _model_key[1], _model_key[2]


def _run(model, audio_path: str, language: Optional[str], on_progress: Optional[ProgressCallback]):
    """One transcription pass. Returns (info, segments as dicts)."""
    segments_iter, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=config.BEAM_SIZE,
        word_timestamps=True,
        vad_filter=config.VAD_FILTER,
        vad_parameters={"threshold": config.VAD_THRESHOLD},
        condition_on_previous_text=config.CONDITION_ON_PREVIOUS_TEXT,
        initial_prompt=config.INITIAL_PROMPT or None,
    )
    segments: List[Dict] = []
    # The work happens while iterating the generator. Values are converted to plain
    # floats because some come back as NumPy scalars, which JSON can't serialize.
    for seg in segments_iter:
        segments.append({
            "id": seg.id,
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text,
            "avg_logprob": float(seg.avg_logprob),
            "no_speech_prob": float(seg.no_speech_prob),
            "compression_ratio": float(seg.compression_ratio),
            "temperature": float(seg.temperature) if seg.temperature is not None else None,
            "words": [{"word": w.word, "start": float(w.start), "end": float(w.end), "probability": float(w.probability)}
                      for w in (seg.words or [])],
        })
        if on_progress and info.duration > 0:
            on_progress(min(1.0, float(seg.end) / info.duration))
    if on_progress:
        on_progress(1.0)
    return info, segments


def asr_settings(language: Optional[str] = config.DEFAULT_LANGUAGE) -> Dict:
    """The recognition settings a transcription would use: recorded in every raw result,
    and part of the cache key (a change means retranscribing). The model is keyed separately."""
    return {
        "language": language or None,
        "vad_filter": config.VAD_FILTER,
        "vad_threshold": config.VAD_THRESHOLD,
        "condition_on_previous_text": config.CONDITION_ON_PREVIOUS_TEXT,
        "beam_size": config.BEAM_SIZE,
        "word_timestamps": True,
        "initial_prompt": config.INITIAL_PROMPT,
    }


def transcribe_audio(
    audio_path: str,
    language: Optional[str] = config.DEFAULT_LANGUAGE,
    model_name: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_stage: Optional[StageCallback] = None,
) -> Dict:
    """Transcribe an audio (or video) file with word timestamps.

    language: a code such as "en", or None to auto-detect.
    on_progress(fraction): called as segments are decoded (segment end / audio duration).
    on_stage(name): called with "loading_model" before a model load and "transcribing" before decoding.

    Returns the raw result: settings, device metadata and every segment with its words,
    timestamps and confidence values, unmodified.
    """
    import ctranslate2
    import faster_whisper

    name = model_name or config.WHISPER_MODEL
    language = language or None
    with _lock:
        model, device, compute_type = _ensure_model(name, on_stage)
        if on_stage:
            on_stage("transcribing")
        t0 = time.monotonic()
        try:
            info, segments = _run(model, audio_path, language, on_progress)
        except Exception as e:
            if device != "cuda" or not is_cuda_error(e):
                log.exception("Transcription of %s failed on %s", audio_path, device)
                raise TranscriptionError("transcription_failed", "Transcribing the audio failed.") from e
            _switch_to_cpu(name, e)
            model, device, compute_type = _model, "cpu", config.CPU_COMPUTE_TYPE
            log.info("Retrying %s on the CPU", audio_path)
            if on_stage:
                on_stage("transcribing")
            t0 = time.monotonic()
            try:
                info, segments = _run(model, audio_path, language, on_progress)
            except Exception as e2:
                log.exception("Transcription of %s failed on the CPU retry", audio_path)
                raise TranscriptionError("transcription_failed", "Transcribing the audio failed.") from e2
        elapsed = time.monotonic() - t0

    word_count = sum(len(s["words"]) for s in segments)
    log.info("Transcribed %s on %s (%s): %.1fs of audio in %.1fs, %d segments, %d words, language %s (%.2f)",
             audio_path, device, compute_type, info.duration, elapsed, len(segments), word_count,
             info.language, info.language_probability)
    return {
        "model": name,
        "device": device,
        "compute_type": compute_type,
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "duration_after_vad": info.duration_after_vad,
        "asr_settings": asr_settings(language),
        "versions": {"faster_whisper": faster_whisper.__version__, "ctranslate2": ctranslate2.__version__},
        "transcribe_seconds": round(elapsed, 2),
        "segments": segments,
    }
