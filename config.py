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

# --- Transcript processing (ai/transcript_processor.py) ----------------------
# Bump whenever processing logic changes in a way that changes the sentences.
PROCESSING_VERSION = 1

# Hallucination filter. A segment is excluded from the training transcript (but
# kept in the raw result) if any rule matches. The defaults for the first two
# rules are Whisper's own decoding thresholds. On the real test clips, the
# closest legitimate segments were: no_speech_prob 0.48 with avg_logprob -0.49
# (song), compression_ratio 2.22 (a line genuinely spoken twice) and a
# mean word probability of 0.34 (a one-word reply, "Perfect.").
HALLUCINATION_NO_SPEECH_PROB = 0.6       # flag if no_speech_prob is above this...
HALLUCINATION_MAX_AVG_LOGPROB = -1.0     # ...and avg_logprob is below this
HALLUCINATION_MAX_COMPRESSION = 2.4      # flag if compression_ratio is above this (repetitive text)
HALLUCINATION_MIN_WORD_PROB = 0.3        # flag if the mean word probability is below this
# A phrase of 2+ words repeated back to back more than this many times is a decoding loop;
# single words get more room, since "no, no, no, no" is ordinary speech.
HALLUCINATION_MAX_PHRASE_REPEATS = 3
HALLUCINATION_MAX_WORD_REPEATS = 6
# Known Whisper hallucinations, matched only on low-confidence segments
# (avg_logprob below the first value or no_speech_prob above the second).
KNOWN_HALLUCINATIONS = [
    "thanks for watching", "thank you for watching", "thank you so much for watching",
    "please subscribe", "like and subscribe", "subtitles by", "subtitled by", "captions by",
    "transcribed by", "transcription by", "amara.org", "see you in the next video",
]
KNOWN_HALLUCINATION_MAX_AVG_LOGPROB = -0.5
KNOWN_HALLUCINATION_MIN_NO_SPEECH = 0.4

# Cleaning. Fillers are matched against whole words (punctuation ignored, any case).
REMOVE_FILLERS = True
FILLER_PATTERNS = [r"u+m+", r"u+h+", r"er", r"e+r+m+"]  # um, umm, uh, uhh, er, erm (not "err")
# Never removed, even if a filler pattern would match them.
PROTECTED_WORDS = {"wow", "hey", "oh", "well", "yeah", "yes", "no"}
# Words inside (...) are removed only if they are one of these; [...] is always removed.
NON_SPEECH_ANNOTATIONS = {
    "music", "applause", "laughs", "laughter", "laughing", "chuckles", "sighs", "sigh",
    "breathing", "breathes", "coughs", "cough", "clears throat", "inaudible", "silence",
    "cheering", "clapping", "background noise", "noise", "upbeat music", "soft music",
}

# Numbers become words in the training transcript ("5" -> "five").
NUMBERS_TO_WORDS = True

# Sentence segmentation.
SENTENCE_MAX_WORDS = 16          # longer sentences are split at a comma or the longest pause
SENTENCE_MAX_SECONDS = 8.0
SENTENCE_MIN_WORDS = 3           # shorter pieces merge into a neighbour, unless they are a complete sentence ("Yes.")
SENTENCE_PAUSE_SECONDS = 0.7     # in stretches Whisper left unpunctuated, a pause this long ends a sentence
# Words across a silence this long are never in one sentence. Not lower: Whisper
# sometimes mistimes a word at a segment boundary by 1.5 s or more ("Hang ... on.").
SENTENCE_LONG_SILENCE_SECONDS = 2.0
# Where an over-long sentence is split, in order of preference: the comma nearest the
# middle, the longest real pause (at least this long), before one of these words,
# and only then the longest gap. In fluent speech Whisper's word gaps are mostly 0,
# so without the word list the split falls mid-phrase.
SENTENCE_SPLIT_PAUSE_SECONDS = 0.3
SENTENCE_SPLIT_BEFORE_WORDS = {"and", "but", "because", "so", "or", "when", "which", "then", "while",
                               "although", "though", "whereas", "if", "until", "unless", "before", "after"}
# Abbreviations that don't end a sentence (titles never do; the rest only before a lowercase word).
TITLE_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof."}
ABBREVIATIONS = {"st.", "jr.", "sr.", "vs.", "etc.", "e.g.", "i.e.", "a.m.", "p.m.", "u.s.", "u.k.", "approx."}

# --- Audio extraction (FFmpeg) -----------------------------------------------
FFPROBE_TIMEOUT_SECONDS = 30
FFMPEG_TIMEOUT_SECONDS = 15 * 60     # long videos take a while to decode
AUDIO_SAMPLE_RATE = 16000            # what Whisper expects
# Video codecs Chrome plays natively; anything else (HEVC, ProRes, ...) gets a
# warning and an offer to convert.
BROWSER_PLAYABLE_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
