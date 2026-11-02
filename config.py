"""All tunable settings for the audio pipeline, with documented defaults.

A few settings can be overridden with environment variables (noted below), so
they can change without editing code.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# The one address the app is used at. Browsers keep saved progress separately per address,
# so http://localhost:<port> is redirected here to keep everything in one place.
CANONICAL_HOST = "127.0.0.1"

# --- Speech recognition (faster-whisper) -------------------------------------
# The Whisper model: large-v3-turbo. Chosen on real clips (docs/PROGRESS.md): close
# to large-v3 on clean speech, and unlike large-v3 it keeps dialogue spoken over a
# movie score (human-verified reference). Downloaded once (about 1.6 GB) on first use
# into the Hugging Face cache; it is the only model the app uses: the CPU fallback
# runs the same model (int8), just slower.
WHISPER_MODEL = os.environ.get("DICTATION_WHISPER_MODEL", "large-v3-turbo")
# "auto" uses the NVIDIA GPU when CUDA is available and falls back to the CPU
# (same model, int8; much slower); "cpu" never touches CUDA.
DEVICE_PREFERENCE = os.environ.get("DICTATION_DEVICE", "auto")
CUDA_COMPUTE_TYPE = "float16"
CPU_COMPUTE_TYPE = "int8"
# Language passed to Whisper when the caller gives none; None = auto-detect.
DEFAULT_LANGUAGE = "en"
# Voice activity detection before decoding. Off by default: on a movie clip it
# dropped 36 of 56 dialogue lines spoken over the music score (1 of 56 with it
# off); the hallucination filter handles what Whisper writes for music/silence.
# Timestamps are on the original timeline either way.
VAD_FILTER = False
# Speech probability above which the VAD keeps audio, when VAD_FILTER is on
# (faster-whisper's default: 0.5). Lowering it barely helped speech mixed with music.
VAD_THRESHOLD = 0.5
CONDITION_ON_PREVIOUS_TEXT = False   # reduces repetition loops
BEAM_SIZE = 5                        # faster-whisper's default
# Text shown to Whisper as if it came before the audio (first 30 s window only, with
# CONDITION_ON_PREVIOUS_TEXT off). Empty by default: on movie clips turbo scored better
# without it (WER 11.5% vs 12.3%) and it can leak into music; the prompt-leak filter stays
# in place in case it is set. Part of the ASR settings (changing it means retranscribing).
INITIAL_PROMPT = ""

# --- Transcript processing (ai/transcript_processor.py) ----------------------
# Bump whenever processing logic changes in a way that changes the sentences.
PROCESSING_VERSION = 8

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
# Prompt leakage: over music or silence Whisper can write the INITIAL_PROMPT text itself
# (seen on the song: "Let's talk about it." at no_speech_prob 0.98). A segment is flagged
# if it contains at least this share of the prompt in runs of 2+ words...
PROMPT_LEAK_MIN_PROMPT_FRACTION = 0.5
# ...or if at least this share of the segment's 3-word sequences occur in the prompt and the
# segment is low-confidence (as for known hallucinations) or has at least
# PROMPT_LEAK_CONFIDENT_MIN_WORDS words. A short, confident "I'm fine, thanks." is real speech
# and is kept.
PROMPT_LEAK_MIN_SEGMENT_FRACTION = 0.6
PROMPT_LEAK_CONFIDENT_MIN_WORDS = 5
# Stock phrases Whisper writes for silence or music when the segment is only that phrase,
# has at least ISOLATED_HALLUCINATION_GAP_SECONDS of nothing on both sides (or the clip edge),
# and a mean word probability below the limit. large-v3 wrote "Thank you." with no_speech_prob
# 0.14 on pure silence (word probability 0.59), which no other rule catches; a clearly
# spoken closing "Thank you." scores about 0.9 and is kept.
ISOLATED_HALLUCINATIONS = ["thank you", "thanks", "thank you very much", "you", "bye", "bye bye"]
# Subtitle credits Whisper learned from subtitled videos ("Translator: ... Reviewer: ..."): an
# isolated segment containing one of these words counts like a stock phrase (seen on a TED
# talk's intro music: "Tanya Cushman Reviewer.", word probability 0.67).
ISOLATED_CREDIT_WORDS = ["reviewer", "translator", "transcriber", "transcription", "subtitles", "subtitled",
                         "captions", "captioned", "captioning"]
ISOLATED_HALLUCINATION_GAP_SECONDS = 3.0
ISOLATED_HALLUCINATION_MAX_WORD_PROB = 0.75

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
# A one-word piece that is one of these stays its own sentence ("Okay,", "Well", "Perfect.").
# Any other single word joins the nearest sentence if the gap to it is under
# SINGLE_WORD_MERGE_SECONDS, and stays alone only if both gaps are longer.
STANDALONE_EXPRESSIONS = {
    "yes", "yeah", "yep", "no", "nope", "okay", "ok", "well", "oh", "wow", "hey", "hi", "hello", "bye",
    "right", "perfect", "really", "sure", "exactly", "absolutely", "great", "cool", "thanks", "sorry",
    "please", "whoa", "what", "why",
}
SINGLE_WORD_MERGE_SECONDS = 1.5
SENTENCE_PAUSE_SECONDS = 0.7     # in stretches Whisper left unpunctuated, a pause this long ends a sentence
# Words across a silence this long are never in one sentence. Not lower: Whisper
# sometimes mistimes a word at a segment boundary by 1.5 s or more ("Hang ... on.").
SENTENCE_LONG_SILENCE_SECONDS = 2.0
# Where an over-long sentence is split, in order of preference: the comma nearest the
# middle, the longest real pause (at least this long), before one of these words,
# and only then the longest gap. In fluent speech Whisper's word gaps are mostly 0,
# so without the word list the split falls mid-phrase.
# Per-sentence clarity = mean word probability. Below this a sentence is marked "unclear"
# (a badge in the UI; skipping it doesn't count in the stats). It is never deleted.
# 0.8 also marks lines Whisper misheard ("We're not trick you." 0.79), which matter most: a
# misheard line is a wrong exercise. Clean speech is almost all above it (conversation: 1 of 70
# below, numbers: 3 of 69); the movie's shouted, music-covered or misheard lines fall below.
SENTENCE_CLARITY_MIN = 0.8
# After music or a pause Whisper sometimes stretches a sentence's first word back over the
# music (movie clip: "they're" 271.26-277.38 s, spoken at about 277.2 s). If the first word is
# longer than this, playback starts at its end minus this (`play_start`); never earlier than
# the stored start, and the stored timestamps don't change.
STRETCHED_FIRST_WORD_SECONDS = 1.0
SENTENCE_SPLIT_PAUSE_SECONDS = 0.3
SENTENCE_SPLIT_BEFORE_WORDS = {"and", "but", "because", "so", "or", "when", "which", "then", "while",
                               "although", "though", "whereas", "if", "until", "unless", "before", "after"}
# An over-long sentence is never split inside one of these (movie clip: "picking up bits | and
# pieces"). Matched on whole words, case and punctuation ignored.
SENTENCE_FIXED_PHRASES = [
    "bits and pieces", "back and forth", "pros and cons", "more and more", "up and down",
    "here and there", "now and then", "safe and sound", "black and white", "on and off",
    "over and over", "again and again", "once and for all", "sooner or later", "more or less",
    "give or take", "rock and roll", "ladies and gentlemen", "trial and error", "by and large",
    "odds and ends", "loud and clear", "first and foremost", "time and again", "out and about",
    "far and wide", "each and every", "and so on", "and so forth", "or so", "if only", "what if",
    "even if", "even though", "as if", "only if", "can't help but",
]
# Abbreviations that don't end a sentence (titles never do; the rest only before a lowercase word).
TITLE_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof."}
ABBREVIATIONS = {"st.", "jr.", "sr.", "vs.", "etc.", "e.g.", "i.e.", "a.m.", "p.m.", "u.s.", "u.k.", "approx."}

# --- Transcript cache (services/transcript_cache.py) ------------------------
# Layer 1: raw recognition results (costly to rebuild: retranscribing).
# Layer 2: processed transcripts in the unified format (cheap: rebuilt from layer 1).
# Both live in subfolders of cache/, so the old caption files in cache/<id>.json are
# never read as transcripts (they stay untouched until the Phase 7 migration).
CACHE_DIR = BASE_DIR / "cache"
ASR_CACHE_DIR = CACHE_DIR / "asr"
TRANSCRIPT_CACHE_DIR = CACHE_DIR / "transcripts"
CACHE_FORMAT = 1  # bump if the cache entry layout changes; older entries are then ignored

# --- Audio extraction (FFmpeg) -----------------------------------------------
FFPROBE_TIMEOUT_SECONDS = 30
FFMPEG_TIMEOUT_SECONDS = 15 * 60     # long videos take a while to decode
AUDIO_SAMPLE_RATE = 16000            # what Whisper expects
# Video codecs Chrome plays natively; anything else (HEVC, ProRes, ...) gets a
# warning and an offer to convert.
BROWSER_PLAYABLE_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
# "Convert" (on request only) re-encodes to H.264 + AAC MP4.
FFMPEG_CONVERT_TIMEOUT_SECONDS = 4 * 60 * 60
CONVERT_X264_PRESET = "veryfast"
CONVERT_X264_CRF = 23
CONVERT_AUDIO_BITRATE = "160k"

# --- Local uploads (services/uploads.py) ---------------------------------------
UPLOAD_DIR = BASE_DIR / "uploads"
MAX_UPLOAD_BYTES = 2 * 1024 ** 3                    # 2 GB; Flask rejects larger requests before saving
ALLOWED_UPLOAD_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm"}
# ffprobe container names accepted (checked on the content, not the extension).
ALLOWED_CONTAINERS = {"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm"}
UPLOAD_RETENTION_DAYS = 30                          # delete a video not opened for this long (transcripts stay)
RETENTION_CHECK_SECONDS = 24 * 60 * 60              # cleanup at startup, then once a day

# --- Clean start for the audio pipeline (services/migration.py) -------------
# The old caption-based data is backed up into BACKUP_DIR and cleared once; the data
# format version in DATA_VERSION_FILE records that it happened.
DATA_FORMAT_VERSION = 2
DATA_VERSION_FILE = BASE_DIR / "data_version.json"
BACKUP_DIR = BASE_DIR / "backup"

# --- Playback (sent to the page) ------------------------------------------------
# Whisper's word timestamps are estimates, so each sentence plays a little before its
# first word and after its last one; clamped so it never plays into a neighbouring
# sentence. The per-video timing offset is applied on top.
PAD_BEFORE_SECONDS = 0.1
PAD_AFTER_SECONDS = 0.25

# --- Background jobs (services/jobs.py) ---------------------------------------
FINISHED_JOBS_KEPT = 50                             # finished jobs stay queryable until this many newer ones finish
