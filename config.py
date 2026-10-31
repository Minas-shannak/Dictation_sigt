"""All tunable settings for the audio pipeline, with documented defaults.

A few settings can be overridden with environment variables (noted below), so
they can change without editing code.
"""

import os

# --- Speech recognition (faster-whisper) -------------------------------------
# Model size: tiny, base, small, medium, large-v3 or large-v3-turbo. Only this
# one model is downloaded (on first use, into the Hugging Face cache).
WHISPER_MODEL = os.environ.get("DICTATION_WHISPER_MODEL", "small")
# "auto" uses the NVIDIA GPU when CUDA is available and falls back to the CPU;
# "cpu" never touches CUDA.
DEVICE_PREFERENCE = os.environ.get("DICTATION_DEVICE", "auto")
CUDA_COMPUTE_TYPE = "float16"
CPU_COMPUTE_TYPE = "int8"
# Language passed to Whisper when the caller gives none; None = auto-detect.
DEFAULT_LANGUAGE = "en"
VAD_FILTER = True                    # skip silence before decoding (timestamps stay on the original timeline)
CONDITION_ON_PREVIOUS_TEXT = False   # reduces repetition loops
BEAM_SIZE = 5                        # faster-whisper's default

# --- Audio extraction (FFmpeg) -----------------------------------------------
FFPROBE_TIMEOUT_SECONDS = 30
FFMPEG_TIMEOUT_SECONDS = 15 * 60     # long videos take a while to decode
AUDIO_SAMPLE_RATE = 16000            # what Whisper expects
# Video codecs Chrome plays natively; anything else (HEVC, ProRes, ...) gets a
# warning and an offer to convert.
BROWSER_PLAYABLE_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
