# Progress

Read `docs/PLAN.md` (the full plan) and this file at the start of every session.
Update this file at the end of every phase. Commit after every phase.

## Status

| Phase | State |
| --- | --- |
| 1. Inspect the project | Done, approved. Decisions recorded in PLAN.md section 0.1 |
| 2. Audio extraction and AI transcription | Done, **waiting for review** |
| 3. Transcript processing | Not started |
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
