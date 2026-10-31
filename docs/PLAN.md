# Dictation App – Audio-Based Transcription Plan (v2)

Oct 31, 2026 · @minas

## 0. How to Work on This Plan

This plan is executed in phases across several sessions, not in one pass.

- Before changing anything, make sure the project is under git and commit the current state. Commit again at the end of every phase so each phase can be rolled back.
- **Phase 1 (inspection) ends with a written report and STOPS.** Do not write code until I approve the proposed design.
- Work on one or two phases per session. Do not try to complete townerhe whole plan at once.
- At the end of each phase report: what changed, files created or modified, new dependencies, tests performed and their actual results, and what remains incomplete.
- Never claim a test passed if it was not actually run. If a test needs a video, hardware or environment you do not have, say so clearly.
- If an architectural decision is unclear or potentially destructive, explain the trade-off and ask before acting.
- Items marked **\[**Decided\] are my final decisions. Follow them, and ask before deviating.

## 0.1 Decisions After Phase 1 (Oct 31, 2026)

Answers to the Phase 1 open questions. These are **\[Decided\]** items.

1. **Python:** the project venv uses Python 3.12, created with `uv`. Do not use 3.14. Network access for pip is allowed.
2. **GPU libraries:** CUDA libraries (cuBLAS, cuDNN) are installed as pip packages inside the venv only, together with faster-whisper, yt-dlp and num2words. Use versions that support Blackwell (CUDA 12.8 or newer) and match the installed ctranslate2. Pin the working versions in `requirements.txt`. The GPU counts as working only after a real transcription runs on it and `nvidia-smi` shows the process.
3. **Padding:** `pad_before` 0.1 s and `pad_after` 0.25 s with clamping, replacing the old 0.2 s / 0.15 s. Keep them configurable; tune after real tests.
4. **Clean start:** back up first, then clear per-video progress, statistics (WPM/accuracy history, missed words), recent videos and the old cache. Keep sound and theme settings. Reset the timing offset to 0.
5. **Language box:** kept and passed to Whisper. Prefilled with "en"; empty means auto-detect. The language is part of the layer-1 cache key.
6. **Upload limit:** 2 GB.
7. **Git identity:** keep the configured git email as it is.

## 1. Main Objective

I have an existing personal web application built with Flask, HTML, CSS and JavaScript. It is used for practicing English listening and typing with YouTube videos. It currently retrieves captions with `youtube-transcript-api`, splits them into sentence-sized segments, and uses them for dictation exercises.

Change the transcription strategy so the application ALWAYS extracts and transcribes the actual audio of the video, whether or not YouTube captions exist. Captions must no longer be a transcription source, primary or fallback.

Use:

- FFmpeg for audio extraction and preprocessing.
- faster-whisper for automatic speech recognition (ASR), with a pretrained model. Do not build or train a model.
- Word-level timestamps for synchronization.
- A dedicated transcript-processing pipeline for cleaning and sentence segmentation.

Supported sources:

1. YouTube video URLs.
2. Locally uploaded video files.

All transcription runs locally on my computer. No paid APIs, no external transcription services, and no audio sent to any external AI service.

## 2. Inspect the Existing Project First

Before modifying anything, inspect the project and understand how it works. Do not assume it matches any structure proposed in this plan.

Pay particular attention to:

- `app.py`, `index.html` and the existing JavaScript.
- YouTube player integration (IFrame API usage, seeking, pausing, polling).
- Caption fetching and processing functions, and current sentence segmentation.
- Video playback and timing synchronization, including the timing offset feature.
- Typing validation, hint system, colors and sounds.
- Progress tracking, WPM, accuracy, missed words.
- Recent videos and saved progress: **where they are stored and how they are keyed** (video ID, sentence index, sentence text?).
- Audio effects and volume controls.
- The existing cache implementation and its file format.

Identify the current data structures for sentences and timestamps.

### Phase 1 report (then stop)

Deliver a short report containing:

1. The current architecture and data flow.
2. The current sentence/timestamp structure and how the frontend consumes it.
3. How saved progress and recent videos are keyed, and what the new segmentation will break (see section 13).
4. The proposed modifications, files to create and files to change.
5. Open questions**, and anything in the project that conflicts with a \[Decided\] item**.

### Critical requirements

- Preserve all existing working features. Do not remove functionality.
- Do not rewrite the application unnecessarily. Reuse existing components and functions.
- Keep the current UI design and interaction behavior unless a change is required.
- Do not introduce unnecessary dependencies.
- Do not break existing saved progress or cached data silently. If a data format must change, provide a migration or a safe, explicit fallback.

## 3. New Transcription Architecture

The transcription pipeline is independent of the video source. Both sources produce the same internal transcript format.

```text
Video input
   |
   +-- YouTube URL ----> YouTube audio service --+
   |                                              |
   +-- Local upload ---> validate + probe -------+
                                                  |
                                                  v
                                   FFmpeg: extract + preprocess
                                   (16 kHz mono WAV, temp file)
                                                  |
                                                  v
                              ai/transcriber.py  (faster-whisper)
                              -> raw result: segments + words
                                                  |
                                  [cache layer 1: raw ASR result]
                                                  |
                                                  v
                         ai/transcript_processor.py
                         hallucination filter -> normalize words
                         -> clean (fillers, annotations)
                         -> sentence segmentation
                                                  |
                         [layer 2: derived, cheap to recompute]
                                                  |
                                                  v
                          Unified transcript -> Dictation engine
                          (YouTube player or HTML5 player,
                           same player interface)
```

## 4. Mandatory AI Module Separation

All AI functionality lives in a separate Python package, independent of Flask. Do NOT put model loading, transcription or transcript processing inside `app.py`.

```text
ai/
├── __init__.py
├── transcriber.py
└── transcript_processor.py
```

The structure may be adjusted if the existing project has a good reason, but the separation of responsibilities must stay.

### A. ai/transcriber.py

Responsible for:

- Loading the faster-whisper model and managing its configuration.
- Detecting GPU availability, CUDA initialization and CPU fallback (section 6C).
- Running transcription and extracting word-level timestamps.
- Collecting metadata (device, compute type, model, language, duration).
- Returning a structured raw result and handling model errors.
- Serializing access to the shared model with a lock, so two jobs never run on it at the same time.

Requirements:

- A simple public function: `transcribe_audio(audio_path, **options)`.
- Lazy initialization, separate from the transcription function. The model is loaded once and reused; never reloaded per request.
- An optional progress callback, e.g. `on_progress(fraction)`, so callers can report progress without the module knowing about Flask.
- No Flask imports. Testable by passing an audio file directly.

### B. ai/transcript\_processor.py

Responsible for:

- Receiving the raw result and preserving it unchanged.
- Hallucination filtering (section 7).
- Word normalization (section 10A).
- Conservative cleaning: non-speech annotations and configurable fillers (section 8).
- Sentence segmentation with word timestamps preserved (section 10).
- Producing the unified training transcript (section 11).

It must not depend on Flask, and must be testable by passing a raw result (for example, loaded from a cached JSON file).

### C. Intended usage without Flask

```python
from ai.transcriber import transcribe_audio
from ai.transcript_processor import process_transcript

raw = transcribe_audio("sample.mp3")
processed = process_transcript(raw)
print(processed)
```

Also provide a small command-line script, e.g. `python -m ai.cli sample.mp3`, that prints the device used, the timing and the resulting sentences.

### D. Separation of responsibilities

- `app.py`: routes, request handling, calling services.
- `ai/transcriber.py`: model loading and ASR.
- `ai/transcript_processor.py`: filtering, cleaning, segmentation.
- Audio services: getting audio from YouTube or a local file.
- Frontend: playback, dictation, processing states.

`app.py` talks to the AI package only through its public functions. Do not add layers or abstractions beyond these.

## 5. Audio Extraction

Audio extraction is separate from transcription. The extraction service returns an audio file path; the AI package never knows where the audio came from.

### A. Common FFmpeg requirements

- Support MP4, WebM and MOV input.
- Output 16 kHz mono WAV (or another format faster-whisper accepts) to a temporary file.
- Use `ffprobe` first to detect: no audio stream, zero duration, corrupted or unsupported files.
- Delete temporary audio in a `finally` block, including on failure or cancellation. Never keep duplicate audio files permanently.
- Run FFmpeg with a timeout and capture stderr for logs, but show the user a friendly message.

### B. Local video upload

- Add an upload interface.
- Validate extension, actual content type (via `ffprobe`, not the extension alone) and size. Set a maximum upload size (`MAX_CONTENT_LENGTH`).
- Use `secure_filename` and store files under an application-managed directory with generated names. Prevent path traversal.
- **Browser playability check:** probe the video codec. Browsers often cannot play HEVC/H.265, ProRes or some MOV files even though their audio transcribes fine (H.264, VP8, VP9 and AV1 generally play).
  - **\[Decided\]** show a clear warning with a **Convert** button. Do not transcode automatically.
  - Convert runs FFmpeg to H.264 + AAC MP4 as a background job with progress, using the same job system as transcription.
  - Transcription does not wait for conversion: audio can be extracted from the original file.
  - After a successful conversion, replace the stored video with the converted copy (no duplicate kept). The `video_id` stays based on the hash of the original upload, so the cache still matches.
- **Retention of uploaded videos \[Decided\]:** delete an uploaded video automatically when it has not been opened for **30 days**.
  - Record a `last_opened` time for each uploaded video and update it whenever the video is loaded.
  - Run the cleanup at app startup and once a day while running.
  - Keep the cached transcript (it is small). Re-uploading the same file matches by content hash and reuses it without retranscribing.
  - In Recent videos, an expired local video shows a clear "file expired, upload it again" state instead of failing.

### C. YouTube audio

- Implement a dedicated YouTube audio service, isolated in its own module so it can be disabled or replaced without touching anything else.
- Do not fetch captions. Remove `youtube-transcript-api` from the transcription path.
- **\[Decided\]** use `yt-dlp`, requesting the audio-only stream, then FFmpeg preprocessing. I accept that YouTube's terms generally do not permit downloading outside YouTube's own features; this app is for my personal, local study of publicly accessible videos.
- Limits that always apply: never bypass DRM, age gates, login walls, private or members-only restrictions; never store downloaded audio beyond the temporary file needed for transcription.
- `yt-dlp` breaks periodically when YouTube changes. When it fails, show a clear message (for example: YouTube audio is unavailable; try updating yt-dlp or use local upload) and log the real error. Pin it in requirements, and document how to update it.
- Validate URLs and extract a stable video ID before any download.

## 6. faster-whisper Integration

Library: [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Check its README for the CUDA and cuDNN versions required by the installed version before configuring the GPU.

### A. Model

- Start with `small`. Do not download multiple models automatically.
- Configurable via one setting (config file or environment variable), not hardcoded: `tiny`, `base`, `small`, `medium`, `large-v3`, and `large-v3-turbo` if the installed version supports it.

### B. Loading strategy

- Lazy load on the first transcription, then reuse for all later requests.
- Do not load the model at Flask startup.
- Report a "Loading AI model" state the first time, since the first load (and download) can take a while.

### C. Hardware: GPU with honest CPU fallback

My machine: Ubuntu laptop with hybrid graphics: Intel Raptor Lake iGPU + NVIDIA GeForce RTX 5060 Laptop / Max-Q (Blackwell, GB206). Verify VRAM, driver version and CUDA version with nvidia-smi. Blackwell is newer than most guides assume: it needs a recent NVIDIA driver, CUDA 12.8 or newer, and a recent CTranslate2 build (older builds fail with errors such as CUBLAS\_STATUS\_NOT\_SUPPORTED or no kernel image available). Pin faster-whisper and ctranslate2 to versions confirmed to work on this GPU, and confirm the NVIDIA GPU (not the Intel iGPU) is the one in use.

- Preferred: `device="cuda"`, `compute_type="float16"`. Fallback: `device="cpu"`, `compute_type="int8"`.
- **Important:** with CTranslate2, the model can load on CUDA successfully and then fail on the FIRST transcription (for example, a missing `libcudnn` or `libcublas` library). The fallback must therefore cover errors at load time AND at transcription time. On a CUDA runtime error during transcription: log it, switch the shared model to CPU once, retry that job on CPU, and keep using CPU for the session.
- Never claim GPU acceleration when running on CPU. Include `device` and `compute_type` in the transcription metadata and logs, and show them in the UI (small text is enough).
- If CUDA fails, log an actionable message: which library was missing and how to install it (for example the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` pip packages and `LD_LIBRARY_PATH`).

### D. Concurrency

- One shared model on one GPU: run jobs one at a time. Use a single background worker (section 14) and a lock in the transcriber.
- A second request while a job runs is queued, not run in parallel.

### E. Transcription settings

```python
segments, info = model.transcribe(
    audio_path,
    language="en",                    # configurable; None = auto-detect
    word_timestamps=True,
    vad_filter=True,
    condition_on_previous_text=False,  # reduces repetition loops
)
```

Use the parameter names appropriate for the installed version. `segments` is a generator: transcription happens while iterating it.

Collect for every segment and word, without discarding timing:

- Detected language and language probability.
- Segment text, start, end, `avg_logprob`, `no_speech_prob`, `compression_ratio`.
- Word text, start, end, and `probability`.
- Audio duration (`info.duration`) and the settings used.

### F. Progress

Compute real progress as `segment.end / info.duration` while iterating segments, and pass it to the progress callback.

## 7. Hallucination Protection

On silence, music or noise, Whisper can produce text that was never spoken (for example "Thanks for watching!", "Subtitles by...") or repeat one phrase many times. For a dictation app this is worse than missing text, because the user is asked to type words that are not in the audio.

Add a hallucination filter in `transcript_processor.py`, applied before cleaning:

- Flag segments with high `no_speech_prob` combined with low `avg_logprob`.
- Flag segments with a high `compression_ratio` (repetitive text).
- Detect the same phrase repeated consecutively more than N times.
- Flag segments whose words have very low average `probability`.
- Optionally, a small configurable list of known hallucination phrases, matched only when the segment is also low-confidence.

Rules:

- Thresholds live in configuration, with sensible defaults documented in code.
- Flagged segments are excluded from the training transcript but remain in the raw result, marked with the reason, so I can review them.
- Log how many segments were excluded per video.
- Be conservative: a legitimate sentence must not be removed just because it is short.

## 8. Transcript Cleaning

Cleaning is conservative. The goal is to reproduce what was actually said, not to improve it.

Note: Whisper usually omits fillers such as "um" and "uh" on its own, so this stage will often change little. It must still exist and be configurable.

### A. Non-speech annotations

Remove only clearly identified non-speech annotations, in any bracket style Whisper produces:

- `[sighs]`, `[laughs]`, `[music]`, `[applause]`, `[breathing]`
- `(laughs)`, `(music)`, and music symbols such as `♪`.

Do not delete ordinary spoken words because they look like interjections.

### B. Filler words

- Handle `um`, `uh`, `er` (and spelling variants such as `umm`, `uhh`).
- Configurable: remove or keep. Default: remove when they are standalone fillers.

### C. Preserve meaningful expressions

Never remove automatically: Wow, Hey, Oh, Well, Yeah, Yes, No.

### D. Preserve the original

Keep two versions:

1. `original_transcript`: the raw faster-whisper output, never modified.
2. `training_transcript`: the cleaned, processed version used for dictation.

Changing cleaning settings must reprocess from the raw result, never retranscribe.

### E. No rewriting

- No LLM rewriting or paraphrasing.
- Do not remove repeated words or fix grammar.

### F. Preserve timing

- Remove only the affected word or annotation.
- Never shift or recalculate the timestamps of remaining words.
- Keep a reference from each training word to its original word (for example an index), so the relationship is never lost.

## 9. Word-level Timestamps and Synchronization

This is one of the highest priorities. Timing must come from the audio itself.

### A. Timing rules

- Sentence `start` = start of its first word; sentence `end` = end of its last word.
- Preserve every word's timestamps. Never distribute time evenly across words.
- Never lose timing when merging or splitting segments.
- Handle pauses between words and sentences correctly.
- With `vad_filter=True`, confirm that the timestamps returned are on the original audio timeline (faster-whisper restores them). Verify this with a test that has long pauses.

### B. Timestamps are approximate: add padding

Whisper word timestamps are estimates and can be off by roughly 0.1–0.3 s, especially on the last word of a sentence. If playback stops exactly at `end`, the last word may be cut off.

- Add configurable padding at playback time: `pad_before` (default about 0.1 s) and `pad_after` (default about 0.25 s).
- Clamp padding so a sentence never plays into the next sentence's first word or before the previous sentence's end.
- Apply padding in the player logic only. Stored timestamps stay unpadded.
- Keep the existing timing offset feature; it applies on top of padding.

### C. One player interface

Investigate whether the current playback logic uses the YouTube IFrame API correctly (seeking, pausing at the right time, polling frequency).

- YouTube: the IFrame API, seeking and pausing with the extracted timestamps.
- Local videos: the HTML5 `<video>` element.
- Both implement the same small interface, for example `load`, `play`, `pause`, `seekTo(seconds)`, `getCurrentTime()`, `onReady`, `onStateChange`, and a "play segment from A to B then pause" helper.
- The dictation engine uses only this interface. Do not duplicate dictation logic for the two sources.
- Stopping at `end`: prefer a `requestAnimationFrame` or short-interval check of `getCurrentTime()` over a single `setTimeout`, because playback can drift.

## 10. Word Normalization and Sentence Segmentation

### A. Word normalization

faster-whisper word tokens are not clean words:

- They usually carry a leading space (`" Hello"`). Strip it for display and matching.
- Punctuation is attached to the word (`"English."`, `"Well,"`). Keep it attached for display, and keep a separate punctuation-free form for typing validation, consistent with how the existing engine handles punctuation.
- Contractions may be split oddly (`"don"` + `"'t"`). Merge such fragments into one word, combining their timestamps (start of the first, end of the last).
- Normalize curly quotes and apostrophes the same way the existing engine does.

### B. Numbers

Whisper may write numbers as digits ("5", "$20", "1990s") or as words. This affects what the user must type.

- **\[Decided\]** always convert numbers to words in the **training transcript**, so the user types what is heard ("5" becomes "five"). The original transcript is never changed. This is the one intentional text normalization; everything else follows section 8E.
- Use a small library such as `num2words` rather than hand-written rules.
- Handle common forms: integers ("5" to "five"), decimals ("3.5" to "three point five"), ordinals ("1st" to "first"), years ("1990" to "nineteen ninety"), decades ("1990s" to "nineteen nineties"), currency ("$20" to "twenty dollars"), percentages ("50%" to "fifty percent").
- Ambiguous or unrecognized patterns (phone numbers, codes, mixed tokens) stay as written and are logged, rather than guessed.
- Timing: the words produced from one number token all keep that token's original `start` and `end`. Do not split its duration across them.
- Make the conversion a processing setting, so it can be turned off later without retranscribing.

### C. Sentence segmentation

Group words into natural sentence-sized exercises, operating on word-level data, not only on Whisper segment boundaries.

- Split on sentence-ending punctuation (`.`, `?`, `!`), being careful with abbreviations (Mr., Dr., U.S.) and decimals.
- Use pauses as a secondary signal: a long gap between words (configurable, e.g. over 0.7 s) can end a sentence when punctuation is missing.
- Enforce a configurable maximum length (words or seconds). Split overly long sentences, never mid-word, at the first available of **\[Decided\]** (after Phase 3):
  1. the comma nearest the middle;
  2. the longest real pause (configurable minimum, 0.3 s);
  3. before a joining word (configurable list: and, but, because, so, or, when, ...), before the first of consecutive ones. In fluent speech Whisper's word gaps are mostly 0, so without this the split falls mid-phrase;
  4. otherwise the longest gap.
- Avoid fragments that are too short to be useful: merge a very short trailing piece into its neighbour, unless it is a complete short sentence ("Yes, yes.").
- **\[Decided\]** One-word pieces: a standalone expression from a configurable list (yes, no, okay, well, oh, wow, hey, yeah, right, perfect, ...) stays as its own sentence, with or without punctuation. Any other single word joins the nearest sentence if the gap to it is under a configurable limit (1.5 s), and stays alone only if both gaps are longer.
- Clock times written as "10.30" / "11.00" / "12:05" are read as times ("ten thirty", "eleven", "twelve oh five") in the training transcript **\[Decided\]** (see 10B).
- Never combine words across a long silence into one sentence.
- Preserve word order and original timestamps exactly.

Each sentence has: `index`, `text`, `start`, `end`, `words`.

## 11. Unified Transcript Data Format

The dictation engine receives the same structure for every source. Illustrative schema; adapt names to the existing app where sensible:

```json
{
  "video_id": "yt:dQw4w9WgXcQ",
  "source_type": "youtube",
  "source_ref": "dQw4w9WgXcQ",
  "original_filename": null,
  "language": "en",
  "language_probability": 0.99,
  "model": "small",
  "device": "cuda",
  "compute_type": "float16",
  "asr_settings": { "vad_filter": true, "condition_on_previous_text": false },
  "processing_settings": { "remove_fillers": true },
  "processing_version": 1,
  "transcript_version": "a1b2c3",
  "created_at": "2026-10-31T20:00:00Z",
  "original_transcript": "...",
  "training_transcript": "...",
  "excluded_segments": 0,
  "sentences": [
    {
      "index": 0,
      "text": "Hello everyone!",
      "start": 0.5,
      "end": 1.8,
      "words": [
        { "word": "Hello", "start": 0.5, "end": 1.1, "probability": 0.97 },
        { "word": "everyone!", "start": 1.1, "end": 1.8, "probability": 0.95 }
      ]
    }
  ]
}
```

- `video_id` is prefixed by source (`yt:` or `local:`) so IDs never collide.
- `processing_version` is a constant in code, bumped whenever processing logic changes.
- `transcript_version` is a short hash of the sentence texts. Saved progress is tied to it (section 13).
- Store the raw ASR result separately (section 12), not duplicated inside this object.

## 12. Caching

Two layers, so that cleaning changes never rerun Whisper and model changes always do.

| Layer | Contents | Cache key | Rebuilt when |
| --- | --- | --- | --- |
| 1. Raw ASR result | Segments, words, timestamps, confidences, metadata | Source ID + model + language + ASR settings (VAD, condition\_on\_previous\_text, etc.) | Model or ASR settings change |
| 2. Processed transcript | Unified format (section 11) | Layer-1 key + processing settings + `processing_version` | Cleaning/segmentation settings or `processing_version` change; cheap, can also be computed on the fly |

- Source ID: YouTube video ID for YouTube; SHA-256 of the file content for local files (never the filename alone). Hash in chunks to handle large files.
- Write cache files atomically (write to a temp file, then rename), so an interrupted job never leaves a half-written entry.
- Never cache a failed or empty result as valid.
- Do not store audio permanently.
- Keep the existing cache format readable, or migrate it explicitly. Old caption-based cache entries must not be served as audio-based transcripts.

## 13. Saved Progress Compatibility

The new segmentation will produce different sentences (count, text, boundaries) than the caption-based version. Any saved progress keyed by sentence index will no longer match the new sentences. This must be handled explicitly, not discovered as a bug.

Requirements:

- **\[Decided\] Clean start for old data:** on the first launch of the new version, clear all old saved progress, statistics (WPM, accuracy history, missed words) and recent videos, and old caption-based cache entries.
- **Before clearing, back up** every file or storage entry being cleared into a timestamped backup (e.g. `backup/pre-audio-pipeline-YYYYMMDD-HHMM/`), and log what was backed up. If the old data lives in browser `localStorage`, export it to a downloadable JSON file once before clearing.
- The migration runs once, controlled by a stored data-format version, and never again afterwards.
- From then on, tie saved progress to `transcript_version` (section 11) in addition to the video ID.
- Future mismatch (for example, cleaning or number settings change and produce different sentences): reset only that video's per-sentence position, keep its statistics, and show a one-line notice that the transcript was regenerated.
- Local videos appear in Recent videos with their original filename.

## 14. User Interface and Background Processing

Preserve the current design. Add a second video source.

### A. Sources

- **YouTube:** the user enters a URL; the app extracts audio and transcribes it. No caption checks.
- **Local video:** an upload option. After upload: display the video, extract audio, transcribe, then start the existing dictation experience.

### B. Background jobs

Transcription can take minutes, so it must not run inside the HTTP request.

- One background worker thread with an in-memory job queue. No Celery, Redis or other infrastructure.
- `POST` starts a job and returns a `job_id` immediately (or the cached result if one exists).
- A status endpoint returns: state, stage, progress fraction, device in use, and a user-friendly error if failed.
- The frontend polls the status endpoint (about once per second) and stays responsive.
- Submitting the same source while a job for it is already running returns the existing `job_id`, not a duplicate job.
- Support cancelling a job. A cancelled job cleans up its temporary files and writes nothing to the cache.
- If the server restarts mid-job, the job is simply lost; the next request starts it again. No partial cache entries may remain.

### C. Processing states shown to the user

1. Downloading / extracting audio.
2. Loading the AI model (first run only).
3. Transcribing audio, with a percentage from section 6F.
4. Processing transcript.
5. Preparing dictation exercises.

Show meaningful errors when processing fails, never raw Python exceptions.

## 15. Preserve Existing Dictation Features

All of the following must keep working, with both YouTube and local videos. Do not simplify or remove any of them to make the new pipeline easier.

- **Dictation:** each sentence plays once and pauses; the user must complete it before moving on; the next sentence starts only after successful completion.
- **Typing:** spaces are required; punctuation is handled automatically; accents and curly quotes do not count against the user; backspace can delete previously correct letters; incorrect letters appear in red.
- **Colors:** completed words teal, current word orange, hinted letters purple.
- **Hints:** Ctrl+H manually; automatically after three mistakes on the same letter.
- **Sounds:** keep `key.mp3`, `error.mp3`, `hint.wav`; keep Ctrl+M and the volume slider.
- **Controls:** progress counter and bar, video time, Previous, Play/Pause, Replay, Next, Hint, Fullscreen.
- **Statistics:** WPM, accuracy per sentence, accuracy per video, missed words, saved progress, recent videos, timing offset.

Fullscreen must work for the HTML5 video element as well as the YouTube player.

## 16. Error Handling and Resource Management

Each case needs a clear user message and a logged technical cause:

| Case | Expected behavior |
| --- | --- |
| Unsupported or corrupted video | Rejected at upload by `ffprobe`; clear message |
| No audio stream | Detected before transcription; "This video has no audio" |
| Silent or empty audio | No sentences produced; clear message, nothing cached as valid |
| Video codec not playable in browser | Warning or transcoding (section 5B) |
| File too large | Rejected before saving; shows the limit |
| FFmpeg fails or times out | Temp files removed; clear message |
| Invalid YouTube URL | Rejected before any download |
| YouTube audio unavailable / yt-dlp broken | Clear message suggesting update or local upload |
| Model download or load fails | Clear message; job fails cleanly |
| CUDA fails at load or at first transcription | Automatic CPU fallback (section 6C), logged, device shown in UI |
| Transcription fails mid-way | Temp files removed; no cache written |
| Job cancelled or server restarted | No partial files or cache entries remain |
| Result invalid (no words, broken timestamps) | Treated as a failure, not cached |
| All segments flagged as hallucinations | Clear message; raw result kept for review |

Use `try/finally` for every temporary file. Never expose raw Python exceptions in the UI.

## 17. Code Quality and Project Structure

- Keep responsibilities separated (section 4D). No transcription logic in `app.py`.
- Clear names, type hints where useful, no duplicated code, no unnecessary abstractions.
- All tunable values (model, device preference, padding, thresholds, filler list, max sentence length, upload limit) in one configuration place with documented defaults.
- Use Python `logging`, not `print`, for pipeline steps, device selection and errors.
- Keep dependencies minimal: `faster-whisper`, `yt-dlp`, and FFmpeg as a system dependency. Document installation, including the CUDA libraries, in the README.
- No paid services; no audio sent to external services.

Suggested structure (a suggestion, not a mandate to reorganize):

```text
typing-app/
├── app.py
├── index.html
├── config.py
├── ai/
│   ├── __init__.py
│   ├── transcriber.py
│   ├── transcript_processor.py
│   └── cli.py
├── services/
│   ├── audio_extractor.py
│   ├── youtube_audio.py
│   └── jobs.py
├── tests/
├── cache/
├── uploads/
├── static/
│   └── sounds/
└── requirements.txt
```

## 18. Testing

Test the pipeline independently before integrating it with the interface. Report each test as passed, failed, or not performed (with the reason).

### A. Automated tests (no Flask, no GPU needed)

- `transcript_processor` with hand-made raw results: annotation removal, filler on/off, meaningful expressions kept, timestamps unchanged after cleaning, contraction merging, number-to-words conversion and its timestamps, segmentation (punctuation, pauses, long sentences, short expressions), hallucination filter.
- Cache keys: changing cleaning settings reuses layer 1; changing the model invalidates it.
- Progress versioning: a mismatched `transcript_version` does not apply old progress.

### B. Pipeline tests with real media

1. YouTube video with captions available (confirm captions are not used).
2. YouTube video without captions.
3. Local MP4 upload.
4. Local MOV or HEVC file (browser playability check).
5. Video with background music.
6. Video with long pauses and silence (check for hallucinated text).
7. Video with filler words.
8. Video with "Wow", "Hey", "Oh".
9. Fast speech.
10. Video with no audio stream.
11. Same video loaded twice (cache hit, no retranscription).

### C. Hardware tests

- Model loads once and is reused (check logs across two jobs).
- GPU path: logs and UI show `cuda` / `float16`.
- CPU fallback at load time: force it (e.g. hide the GPU with `CUDA_VISIBLE_DEVICES=""`).
- CPU fallback at transcription time: simulate a CUDA runtime error and confirm the job retries on CPU.

### D. Playback and regression tests

- The video pauses at the correct sentence boundaries, without cutting the last word, on both players. Do not claim timing is accurate without checking it against real playback.
- Every feature in section 15 still works on both sources.
- Old saved progress and recent videos behave as defined in section 13.

## 19. Implementation Phases

Commit to git after each phase. Report after each phase as described in section 0.

1. **Inspect the project.** Deliver the Phase 1 report (section 2). **Stop and wait for approval.**
2. **Audio extraction and AI transcription.** FFmpeg service with `ffprobe` checks; `ai/transcriber.py` with lazy loading, lock, GPU/CPU fallback at load and run time, progress callback; `ai/cli.py`. Test directly on a local audio file without Flask.
3. **Transcript processing.** Hallucination filter, word normalization, conservative cleaning, segmentation, original preserved. Automated tests on hand-made raw results.
4. **Unified format and caching.** Two cache layers, atomic writes, `processing_version`, `transcript_version`. Verify cleaning changes do not retranscribe.
5. **Background jobs and local upload.** Job queue, status endpoint, cancellation; secure upload, codec check; HTML5 player behind the shared player interface.
6. **Replace the YouTube caption pipeline.** YouTube audio service **using yt-dlp as decided** in section 5C. Remove captions as a source entirely.
7. **Integrate with the dictation engine.** Shared player interface, padding, timing offset, saved progress versioning (section 13), all features in section 15.
8. **Test and fix regressions.** Run section 18 in full. The work is not complete until this phase is reported honestly.

Do not skip testing between phases. Do not silently remove functionality.

## 20. Final Goal

A reliable, audio-based English dictation tool whose training transcript always comes from the actual audio, whether or not captions exist.

The transcription should:

- Reproduce the actual spoken words, without rewriting.
- Provide word-level timestamps that keep playback in sync with each sentence.
- Exclude hallucinated text and clearly non-speech artifacts.
- Preserve meaningful conversational expressions.
- Produce natural sentence-sized exercises.
- Work with both YouTube videos and local files, entirely on my machine.

Most importantly: **preserve the existing dictation experience** and make the new pipeline work seamlessly with it. The AI package must stay independent of Flask and testable on its own.
