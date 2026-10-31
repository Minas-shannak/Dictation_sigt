# Progress

Read `docs/PLAN.md` (the full plan) and this file at the start of every session.
Update this file at the end of every phase. Commit after every phase.

## Status

| Phase | State |
| --- | --- |
| 1. Inspect the project | Done, approved. Decisions recorded in PLAN.md section 0.1 |
| 2. Audio extraction and AI transcription | Done, approved |
| 3. Transcript processing | Done, **waiting for review** |
| 4. Unified format and caching | Not started |
| 5. Background jobs and local upload | Not started |
| 6. Replace the YouTube caption pipeline | Not started |
| 7. Integrate with the dictation engine | Not started |
| 8. Test and fix regressions | Not started |

## Phase 1: Inspection (Oct 31, 2026)

**Done**
- Put the project under git (`main`). The first commit, "Snapshot of the caption-based app before the audio pipeline work", is the rollback point.
- `.gitignore` excludes `.venv/`, `__pycache__/`, `.pytest_cache/`, `cache/`, `uploads/`, `backup/` and video/large audio files. `static/sounds/` is tracked.
- Moved the plan to `docs/PLAN.md`.
- Inspected the code and environment and wrote `docs/PHASE1_REPORT.md`. It covers the architecture, data formats, progress keying, proposed changes and open questions Q1–Q11.

**Files changed:** `.gitignore` (new), `docs/PLAN.md` (moved), `docs/PHASE1_REPORT.md` (new), `docs/PROGRESS.md` (new). No application code changed.

**Tests run:** none. Phase 1 is inspection only. Environment checks run, with their real output summarized in the report:
- `python3 --version` gave 3.14.4.
- `ffmpeg` / `ffprobe -version` gave 8.0.1.
- `nvidia-smi` showed the RTX 5060 Laptop, compute capability 12.0, driver 615.71.09, CUDA 13.4, 8 GB.
- No cuBLAS or cuDNN libraries were found.

**Not done / blocked**
- Wheel availability for faster-whisper, ctranslate2 and onnxruntime on Python 3.14, and the right CTranslate2 build for Blackwell. This needs network access and was not checked (Q1, Q2).

**Next:** wait for answers to Q1–Q11 and approval of the design, then Phase 2.

Correction found in Phase 2: Python 3.12.15 was already installed through uv (`~/.local/share/uv/python/`), but not on the PATH, so the Phase 1 check missed it.

## Phase 2: Audio extraction and AI transcription (Oct 31, 2026)

**Done**
- **Decisions:** recorded the Phase 1 answers as [Decided] items in `docs/PLAN.md` section 0.1.
- **Venv:** `.venv` recreated on **Python 3.12.15** with uv. The old 3.14 venv was replaced.
- **Packages** (pinned in `requirements.txt`, verified with `uv pip install --dry-run -r requirements.txt`, which reported "Would make no changes"):
  - faster-whisper 1.2.1 and ctranslate2 4.8.4. The Linux wheel is built with CUDA 12.8, all common GPU architectures and `WITH_CUDNN=OFF`, per the upstream build script.
  - av 18.1.0, onnxruntime 1.31.1 and nvidia-cublas-cu12 12.9.3.13.
  - yt-dlp 2026.10.26 and num2words 0.5.14.
  - **cuDNN not installed:** CTranslate2 4.6+ does not use it, and the GPU works without it.
  - **av pinned to 18.1.0:** av 19.0.2 breaks faster-whisper 1.2.1 (`open() got an unexpected keyword argument 'metadata_errors'`).
- **`config.py`:** model (`DICTATION_WHISPER_MODEL`, default `small`), device preference (`DICTATION_DEVICE`, auto or cpu), compute types, default language, VAD, condition_on_previous_text, beam size, FFmpeg/ffprobe timeouts, sample rate and browser-playable codecs.
- **`services/audio_extractor.py`:**
  - `probe()` uses ffprobe to detect corrupt or unreadable files, no audio stream, zero duration, and the video codec / browser playability. Cover art doesn't count as video.
  - The `extracted_audio()` context manager produces a 16 kHz mono PCM WAV in a temp file. FFmpeg runs with a timeout and its stderr is logged. The file is deleted in `finally`.
  - Errors are `AudioError(code, user_message)`.
- **`ai/transcriber.py`:**
  - `transcribe_audio(audio_path, language, model_name, on_progress, on_stage)`.
  - The model loads lazily and once, then is shared behind a lock.
  - The CUDA libraries from pip are loaded from the venv by full path, so no `LD_LIBRARY_PATH` is needed.
  - CUDA→CPU fallback happens at load time and at transcription time: it switches once, retries the job, stays on the CPU for the session, and logs an actionable hint.
  - The raw result keeps every segment (text, times, avg_logprob, no_speech_prob, compression_ratio, temperature) and every word (text, start, end, probability), plus the settings, device, compute type and versions. All values are plain floats, so the result is JSON-safe.
  - Progress is reported as segment end / duration.
- **`ai/cli.py`:** `python -m ai.cli FILE [--language auto] [--model] [--words] [--twice]`.
- **Tests:** moved `test_segment.py` to `tests/` and added `pytest.ini` (`testpaths = tests`, `pythonpath = .`).

**Files changed:** `config.py`, `ai/__init__.py`, `ai/transcriber.py`, `ai/cli.py`, `services/__init__.py`, `services/audio_extractor.py`, `tests/test_transcriber.py`, `tests/test_audio_extractor.py` and `pytest.ini` are new. `test_segment.py` moved to `tests/test_segment.py`. `requirements.txt`, `README.md` (setup with uv/3.12, FFmpeg, GPU notes, CLI, file list), `docs/PLAN.md` and `docs/PROGRESS.md` were updated. `app.py` and the frontend are unchanged.

**Tests run (real results)**
- **Test audio:** `jfk.wav`, 11 s, 16 kHz mono, a public-domain clip of JFK's 1961 inaugural address, downloaded from the whisper.cpp repository (`samples/jfk.wav`). Other test files were generated from it with FFmpeg in the session scratchpad, not the repo:
  - MP4 (H.264+AAC), WebM (VP9+Opus) and MOV (HEVC+AAC)
  - a video with no audio, random bytes as a corrupt file, and an empty file
  - 20 s of silence
  - `pauses.wav`: JFK, 20 s silence, JFK, 9 s silence (51 s)
- **pytest:** **41 passed** (15 caption tests, 14 transcriber tests with a fake model, 12 extractor tests on FFmpeg-generated media).
- **GPU:** passed. `small` on `cuda`/`float16`. nvidia-smi showed `.venv/bin/python` (PID 35303) using up to 818 MiB on the RTX 5060 during the run. Output was correct: "And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country."
- **First CUDA run:** transcription took **20.4 s** (11 s of audio), with the second run at 0.2 s. This was a one-time compile of GPU kernels for Blackwell. The CUDA cache `~/.nv/ComputeCache` (193 MB) was written during that run (20:39:33–35). In a fresh process afterwards, the model loaded in 0.9 s, transcription took about 0.5 s, and the whole CLI run took 4.4 s wall time. Every later process was fast too. The compile will happen again if that cache is cleared or the driver changes.
- **Model loaded once across two jobs:** passed. "Loaded Whisper model" was logged once for two transcriptions (`--twice`).
- **Containers:** MP4, WebM and HEVC MOV were extracted and transcribed on cuda, all with the same correct text. HEVC was flagged `browser_playable=False`. The temp WAV was gone afterwards, including when the caller raised an error.
- **Error cases:**
  - no audio: `no_audio`, "This video has no audio."
  - corrupt, empty or missing file: `unreadable`, "This file is corrupted or not a supported video format."
- **VAD timeline:** passed. In `pauses.wav` the second copy's words are at 31.34–41.30 s, compared with 0.00–10.36 s for the first copy. That is the original timeline, within 0.06 s at the end word; the first word differs by 0.34 s because Whisper puts the very first word at 0.00. VAD removed 29 s, and progress was reported as 0.15 → 0.2 → 0.75 → 0.81 → 1.0.
- **Silence:** 20 s of silence gives 0 segments, with 0.0 s left after VAD.
- **Auto language:** `language=None` detected en (0.96).
- **CPU fallback at load:** passed. With `CUDA_VISIBLE_DEVICES=""`, it logged "No CUDA device available; using the CPU" and ran on `cpu`/`int8`, with correct text.
- **CPU fallback at transcription:** passed, with a **real** failure rather than a mock. With the pip cuBLAS preload skipped, the model loaded on cuda and then the first transcription failed: "Library libcublas.so.12 is not found or cannot be loaded". The install hint was logged, the model reloaded on the CPU, the job was retried and completed (22 words), and the next job stayed on the CPU.
- **Existing caption app on the 3.12 venv:** passed. `python app.py` returned 200 for `/`, `/static/app.js?v=1` and `/api/transcript?url=arj7oStGLkU&lang=en` (served from the cache). Not retested in a browser.
- **Python 3.10 syntax:** passed. All new files parse with `ast.parse(..., feature_version=(3, 10))`.

**Not done / not tested**
- Only short test audio (11 s and 51 s) was used. No long video, background music, fillers or fast speech yet (Phase 8 media tests).
- The CUDA load-time fallback for a *real* load failure is covered only by a fake-model unit test. Hiding the GPU exercises the "no device" path, not a crash during load.
- No cancellation inside FFmpeg yet. It comes with the job system in Phase 5.
- No sentence segmentation. `ai/cli.py` prints Whisper segments until Phase 3 adds `transcript_processor.py`.
- yt-dlp and num2words are installed but not used yet (Phases 6 and 3).

**Next:** Phase 3, transcript processing, after review.

## Phase 3: Transcript processing (Oct 31, 2026)

### Real test clips and fixtures

Audio only, downloaded with the venv's yt-dlp (`-f bestaudio`, no video, no captions). Stored in `tests/media/`, which is git-ignored and kept locally for Phase 8. The full downloads are kept there too (`*.full.webm`).

| Fixture | Video | Section | Duration | Transcribe (GPU) | Incl. extraction | Device | Segments / words |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `conversation.json` | eyoFYpI47Qg, "Real English Conversation…" (16:15) | 01:00–05:00 | 240.0 s (VAD kept 234.7 s) | 4.26 s | 7.7 s | cuda/float16 | 45 / 747 |
| `music.json` | 3-sfGRswzq0, BUNT. "Unbreakable" music video (4:03) | whole | 242.9 s (VAD kept **18.2 s**) | 0.75 s | 1.3 s | cuda/float16 | 2 / 30 |
| `numbers.json` | Jnw4J8Wj3k4, "Master Tricky Numbers & Dates" (20:19) | 02:00–06:00 | 240.0 s (VAD kept 224.0 s) | 3.68 s | 4.2 s | cuda/float16 | 36 / 606 |

The fixtures are the raw `transcribe_audio()` result plus clip info (128 KB total). nvidia-smi showed the process on the GPU during the run.

What the raw output showed:
- **Hallucinations: none found.** No text in silences, no loops. Segment confidence never came close to Whisper's thresholds. The nearest were a no_speech_prob of 0.48 (song), a compression ratio of 2.22 (a line the speakers genuinely say twice), and a mean word probability of 0.34 (the one-word reply "Perfect.").
- **Fillers: none.** Whisper dropped every "um"/"uh" itself, as the plan predicted. "like" appears often, but it's a real word and is kept.
- **Odd tokens:**
  - Compounds and times are split into fragments without a leading space: "grown" + "-up", "red" + "-handed", "two" + "-shoes", "10" + ".30", "1 p" + ".m.,", "o" + "'clock".
  - 8 + 4 words have zero duration (start == end), for example "Yeah." at 154.64.
  - Whisper writes clock times as "10.30" / "11.00", and once as "10-30".
- **Punctuation:** missing for about 45 s of the conversation clip (≈ 94–139 s).
- **ASR mistakes** (not hallucinations, which the filter can't catch):
  - "grasp me up" for "grass me up", "dobbin" for "dobbing".
  - "Katzphrase" for "Kat's phrase", and "neighbors right or home and away", which is probably the TV shows *Neighbours* or *Home and Away*.
- **Music clip:** the Silero VAD treats singing as non-speech and removed 3:44 of 4:03, so only 30 words from two short stretches remain. Their correctness is unverified; I didn't check them against the lyrics. It is a song, not speech over background music, so the "speech with background music" case is still untested.

### Done
- **`ai/transcript_processor.py`**, `process_transcript(raw, settings=None)`. The raw result is never modified; a test checks this on every fixture. The steps:
  1. **Hallucination filter:**
     - no_speech_prob > 0.6 with avg_logprob < −1.0
     - compression ratio > 2.4
     - mean word probability < 0.3
     - a phrase repeated back to back more than 3 times (single words more than 6)
     - the same segment text more than 3 times in a row
     - known phrases ("thanks for watching", …), only on low-confidence segments

     Flagged segments are listed with their reasons in `excluded` (the raw result stays untouched), are left out of the training transcript, act as sentence breaks, and are logged.
  2. **Word normalization:**
     - strip leading spaces
     - glue fragments without a leading space onto the previous word, and contraction endings even with a space ("don" + " 't"); the merged word spans the first start to the last end and keeps the lowest probability
     - curly quotes become straight
  3. **Cleaning:**
     - `[...]` is always removed; `(...)` only when its content is a known non-speech label (laughs, music, applause, …)
     - ♪ symbols are removed, but sung words kept
     - fillers um/umm/uh/uhh/er/erm (not "err", "uh-huh" or "mm-hmm") are removed by default, configurable
     - Wow/Hey/Oh/Well/Yeah/Yes/No are protected even if a filler pattern matches them
     - if a removed word carried the sentence end, the end moves to the previous word
  4. **Numbers to words** with num2words:
     - integers, thousands with commas, decimals ("three point five"), ordinals, years ("nineteen ninety"), decades ("nineteen nineties", "'90s"), currency ("twenty dollars", "five pounds fifty", "$5 million" → "five million dollars") and percent
     - clock times "10.30" / "11.00" / "12:05" → "ten thirty" / "eleven" / "twelve oh five"
     - left as written and logged: ranges and dash forms ("10-30"), leading zeros, 8+ digit strings, letter+digit mixes (MP3, COVID-19, 4K, 24/7)
     - all words made from one token share that token's start and end
     - can be switched off
  5. **Segmentation** on word data:
     - ends at . ? ! (titles like Mr./Dr. never end a sentence; a.m./p.m./etc./U.S. and "..." only before a capital)
     - a pause ≥ 0.7 s ends a sentence only where Whisper left the segment unpunctuated
     - a silence ≥ 2.0 s always ends one
     - maximum 16 words / 8 s; longer pieces are split at the comma nearest the middle, else the longest pause ≥ 0.3 s, else before a joining word (and, but, because, so, …; before the first of consecutive ones), else the longest gap
     - pieces under 3 words merge into the nearer neighbour unless they are a complete sentence ("Yes.", "Wow!") or are separated by a long silence
  6. **Output:** sentences `{index, text, start, end, words: [{word, start, end, probability, source}]}`, where `source` holds the indices of the raw words. Also returned: `original_transcript`, `training_transcript`, `excluded_segments` plus details, `numbers_left_as_written`, `empty_reason` (no_speech / all_excluded / nothing_left), `processing_settings` and `processing_version`.
- **`config.py`:** every threshold, list and limit above, documented, with the real-data evidence for the thresholds. `PROCESSING_VERSION = 1`.
- **`ai/cli.py`:** prints sentences. It also accepts a saved raw result (`.json`) and reprocesses it without transcribing.
- **`.gitignore`:** now covers `tests/media/`.

### Tuning done on the real clips
Each change was checked by printing all sentences of all three clips; samples are in the Phase 3 report.
1. **Joining words:** in fluent speech Whisper's word gaps are almost always 0, so "longest pause" splits fell mid-phrase ("…I was the golden" / "girl because…"). I added "before a joining word" as the third split choice, after comma and real pause. This is a refinement of plan 10C, which says to split at a comma or the longest pause.
2. **Long-silence limit:** raised from 1.5 s to 2.0 s. "Hang on." was split in two by a 1.66 s gap, which was really a mistimed word at a segment boundary.
3. **Consecutive joining words:** split before the first of them ("girl | but if you're", not "girl but | if").

### Files changed
- New: `ai/transcript_processor.py`, `tests/test_transcript_processor.py`, `tests/fixtures/{conversation,music,numbers}.json`.
- Changed: `config.py`, `ai/cli.py`, `.gitignore`, `README.md`, `docs/PROGRESS.md`.
- Not committed: `tests/media/`.

### Tests run (real results)
- **pytest: 115 passed.** This is 41 earlier plus 74 new processor tests:
  - hand-made cases for annotations, fillers on/off, protected expressions, timestamps unchanged, fragment and contraction merging, curly quotes, 22 number forms, 7 ambiguous forms, number timing, punctuation/abbreviation/ellipsis/pause/silence/max-length/comma/pause/joiner splitting, short-piece rules, and every hallucination rule
  - the conservative cases not flagged: "Perfect." at p 0.34, "no, no, no, no, no!", a line said twice, the sung segment
  - excluded segments kept in the original but not the training text
  - empty reasons, settings validation, JSON output
  - on the 3 real fixtures: no false flags, every raw word used with its original timestamps, sentences ordered and sized, raw unchanged, and number reading on the numbers clip
- **Real fixtures processed:** conversation gave 81 sentences (avg 9.2 words), music 5, numbers 79 (avg 7.5). There were 0 exclusions, and 2 tokens were left as written ("10-30.", twice).
- **End to end on the GPU (JFK):** `python -m ai.cli jfk.wav` gave cuda/float16 and 2 sentences.
- **Python 3.10:** all files parse with a real Python 3.10.22, fetched by uv into its own cache.

### Not done / open
- **No real hallucination in the clips**, so the filter rules are verified only on hand-made segments, plus the check that real speech isn't flagged.
- **No real fillers in the clips.** Filler removal is tested only on hand-made input.
- **Music:** the VAD drops singing, so songs yield very little text. A clip of speech over background music is still needed. The VAD threshold isn't configurable yet. Decide later whether songs should be supported.
- **Isolated one-word sentences** remain when they sit between long silences: "Okay,", "on", "Well". There are also zero-duration words ("Yeah." 154.64–154.64); playback padding (Phase 7) gives them a playable window.
- **"10-30"** is left as written (ambiguous range/time), so the user would type "1030".
- **ASR mistakes** in the transcripts can't be fixed by processing. A larger model is a config change (`DICTATION_WHISPER_MODEL`), not tested.
- **Caching** (Phase 4): `transcript_version`, video IDs and the cache layers are not done.

**Next:** Phase 4, unified format and caching, after review.
