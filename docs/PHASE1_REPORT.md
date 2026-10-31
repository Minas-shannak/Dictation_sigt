# Phase 1 Report: Inspection

Oct 31, 2026. No application code was changed in this phase.

## 0. Environment (checked on this machine)

| Item | Result |
| --- | --- |
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-42 |
| Python | **3.14.4 only** (`/usr/bin/python3.14`). No 3.10–3.13 installed. The project venv `.venv` is 3.14.4. |
| ffmpeg / ffprobe | Installed: `/usr/bin/ffmpeg`, `/usr/bin/ffprobe`, version 8.0.1-3ubuntu2 |
| GPUs (lspci) | Intel Raptor Lake-P iGPU (00:02.0) + NVIDIA GB206M, GeForce RTX 5060 Max-Q / Mobile (01:00.0) |
| PRIME mode | `on-demand` |
| nvidia-smi | RTX 5060 Laptop GPU, **compute capability 12.0** (Blackwell), 8151 MiB VRAM, driver **615.71.09**, CUDA UMD version **13.4**, 13 MiB used, P4 idle |
| CUDA driver library | `libcuda.so.1` present (comes with the driver) |
| CUDA runtime / cuBLAS / cuDNN | **Not installed.** No `libcudart`, `libcublas` or `libcudnn` anywhere on the system, no `/usr/local/cuda`, no `nvcc`, no `nvidia-*` pip packages, `LD_LIBRARY_PATH` empty |
| RAM / disk | 14 GiB RAM, 409 GB free |
| Installed Python packages (venv) | Flask 3.1.3, youtube-transcript-api 1.2.4, pytest 9.2.0 |

Not checked: whether `faster-whisper` / `ctranslate2` / `onnxruntime` / `av` have wheels for Python 3.14, and which CUDA builds of CTranslate2 support sm_120. Checking needs a network `pip` dry run, which was stopped. This is open question Q1.

## 1. Current architecture and data flow

```text
Browser (index.html + static/app.js + static/style.css)
  │  GET /api/transcript?url=<link or id>&lang=en
  ▼
app.py (Flask, 127.0.0.1:8000)
  extract_video_id(url) ─► cache/<videoId>.json exists and same base language? ─► return it
                         └► youtube-transcript-api: list(id) → manual captions, else auto
                            → fetch() → to_raw_data() [{text, start, duration}]
                            → segment(): clean_cues → merge_cues → split_long_line
                            → write cache/<videoId>.json (non-atomic write_text)
  ◄─ JSON { videoId, lang, source: "manual"|"auto", lines: [...] }
  ▼
Frontend: YouTube IFrame player (always YouTube; no HTML5 <video>)
  playLine(i): seekTo(from) + playVideo(), requestAnimationFrame loop polls
  getCurrentTime() and pauses at `to` ("armed" guard ignores stale times after a seek)
  Dictation engine types against lines[i].text
```

- `app.py` (280 lines): routes `/` (sends index.html) and `/api/transcript`. Flask's default `/static/` route serves CSS, JS and sounds. Errors map to JSON `{error, message, available?}` with 400/404/429/502.
- Segmentation (`app.py`): cleans HTML entities, `[..]`/`(..)` tags, `>>`, ♪. Clips overlapping cue ends to the next cue's start. Merges cues until `. ? !` (if ≥10% of cues are punctuated) or a 0.6 s gap, capped at 12 words / 6 s. Splits lines over 15 words at the comma nearest the middle, else the middle word, with time **distributed by character share**. That interpolation is exactly what the plan forbids (9A).
- `test_segment.py`: 15 pytest tests, all on the caption functions plus `extract_video_id`.
- Frontend (`static/app.js`, 792 lines, one classic script):
  - Player: IFrame API loaded dynamically, `controls:0, disablekb:1, cc_load_policy:0`. `unloadModule("captions")` hides YouTube's subtitles. A shield div blocks clicks, and clicking it replays the line.
  - Window per line: `from = start + offset − PLAY.leadIn(0.2)`, `to = start + duration + offset + PLAY.tail(0.15)`. **Padding already exists**, with no clamping against neighbouring lines.
  - Polling: `requestAnimationFrame` (about 60 Hz) with `getCurrentTime()`. This is already what plan 9C asks for. `togglePlay` / Enter, a `lineFinished` flag, and the play/pause icon driven by `onStateChange`.
  - Typing: per-line "slots" `{ch, norm, space, word, typeable, revealed, how}`. Letters and digits are typed. Punctuation is auto-filled. Spaces between words are required. `normalizeText` does NFD with accents stripped, lowercase, and curly quotes and dashes mapped to ASCII. Backspace across words, hints (Ctrl+H, auto after 3 wrong keys), accept word (Ctrl+Enter).
  - Sounds: Web Audio. `key.mp3`/`error.mp3`/`hint.wav` are decoded in an OfflineAudioContext. Mute (Ctrl+M) and volume are stored in `localStorage`.
  - Fullscreen: `document.documentElement.requestFullscreen()`, so the whole page goes fullscreen, not the player. This will work the same for an HTML5 `<video>`.

## 2. Current sentence/timestamp structure and how the frontend uses it

Server response and cache file (`cache/<11-char id>.json`, pretty-printed, one file per video, any one language):

```json
{ "videoId": "arj7oStGLkU", "lang": "en", "source": "manual",
  "lines": [ { "id": 0, "start": 12.645, "duration": 3.255, "text": "So in college, I was a government major," } ] }
```

Frontend uses: `body.videoId` (player id, storage key), `body.lines.length`, `lines[i].start`, `lines[i].duration`, `lines[i].text`. `id` and `source` are unused. There are no word timings, and the frontend never needs them for typing, because it builds its slots from `text`.

Seven cache files exist (about 64 KB total, all caption-based, no format version field).

## 3. Saved progress and recent videos: keying, and what the new segmentation breaks

Everything is in **browser `localStorage`** (origin `http://localhost:8000`). Nothing is stored on the server.

| Key | Contents |
| --- | --- |
| `dictation:video:<youtubeId>` | `{ id, title, lang, lineIndex, lineCount, offset, lastOpened, stats: { [lineIndex]: {chars, ms, correct, keys} }, missed: [{word, count}] }` |
| `dictation:sound` | `{ muted, volume }` |
| `dictation:theme` | `"light"` / `"dark"` |

- Recent videos = every `dictation:video:*` record, sorted by `lastOpened`. Clicking one rebuilds a **YouTube URL** from `rec.id`, so it can't open a local video.
- Keyed by **video ID + line index**. No sentence text and no version are stored.

What new segmentation breaks:
1. `lineIndex` resumes at the wrong sentence, or is clamped to the last one if the count shrinks.
2. `stats` is keyed by line index. Old per-line entries attach to different sentences. The progress bar shows finished lines that the user never typed in the new transcript, and the video average mixes old and new lines.
3. `lineCount` in Recent ("line 12 of 245") is wrong until the video is reopened.
4. `missed` is a word list, not index-based, so it stays meaningful but mixes old and new sessions.
5. `offset` is per video. It was tuned for caption timing and is probably wrong for audio timing (it is usually 0).
6. Old `cache/*.json` would be served as if valid, because the cache key is the bare video ID.

Per the [Decided] clean start (section 13), items 1–6 are handled by a one-time migration. `dictation:sound` and `dictation:theme` are settings, not progress. I propose **keeping** them (Q6).

## 4. Proposed modifications

Following the plan's suggested layout, adapted to what exists:

**New files**
- `config.py`: every tunable value with documented defaults (model, language, device preference, padding, hallucination thresholds, filler list, segmentation limits, upload limit, retention days, FFmpeg timeout). Environment-variable overrides for model and device.
- `ai/__init__.py`, `ai/transcriber.py` (lazy model, lock, CUDA→CPU fallback at load **and** run time, progress callback), `ai/transcript_processor.py` (hallucination filter, word normalization, contraction merge, num2words, cleaning, segmentation, unified format), `ai/cli.py`.
- `services/audio_extractor.py` (ffprobe checks, 16 kHz mono WAV temp file, timeout, `finally` cleanup, codec playability check, convert job), `services/youtube_audio.py` (yt-dlp, audio only, isolated), `services/jobs.py` (one worker thread, in-memory queue, dedup by source, cancel, status), `services/storage.py` (atomic JSON write, two cache layers, uploads index with `last_opened`, 30-day cleanup). Merging the last two into fewer files is possible if you prefer.
- `tests/` (move `test_segment.py` there), with `tests/test_transcript_processor.py`, `tests/test_cache.py`, and frontend-free tests for the progress versioning helper.
- `docs/PROGRESS.md` (created now), `docs/PHASE1_REPORT.md` (this file).

**Changed files**
- `app.py`: keep `extract_video_id` and the error-to-JSON style. Replace `/api/transcript` with job endpoints (`POST /api/jobs` for a YouTube URL, `POST /api/upload`, `GET /api/jobs/<id>`, `POST /api/jobs/<id>/cancel`, `POST /api/convert/<video_id>`, `GET /media/<video_id>` for uploaded video with Range support). Remove the caption functions in Phase 6, and the youtube-transcript-api import with them.
- `static/app.js`:
  - A small player adapter with the same interface for YouTube and `<video>`. The existing `playLine` / `togglePlay` / `watchPlayback` logic moves behind it unchanged.
  - Read `sentences[i].{start,end,text}` instead of `lines[i].{start,duration,text}`.
  - Replace `PLAY.leadIn/tail` with `pad_before/pad_after` clamped to neighbours.
  - An upload control and a processing-status view with a progress %.
  - A device/compute label.
  - Migration and backup export, and `transcript_version` checks on progress.
  - Source-aware Recent videos, including an "expired, upload again" state.
- `index.html` / `style.css`: an upload button next to the URL box, a status panel and a `<video>` container, styled like the existing UI.
- `requirements.txt`: pinned versions. `README.md`: install instructions (FFmpeg, CUDA libraries, updating yt-dlp), new workflow and files.

**Reused as-is:** the typing engine (slots, normalization, hints, Backspace, Enter), sounds, stats formulas, missed words, theme, shortcuts, fullscreen and the rAF stop logic.

**New dependencies (need your approval):**
- `faster-whisper`, which pulls in `ctranslate2`, `tokenizers`, `huggingface_hub`, `av` and `onnxruntime` for the VAD.
- `yt-dlp` and `num2words`.
- For GPU only: `nvidia-cublas-cu12` and `nvidia-cudnn-cu12`, or their CUDA-13 equivalents, whichever the chosen CTranslate2 build needs. See Q2.
- FFmpeg is already installed.

## 5. Open questions and conflicts with [Decided] items

**Q1. Python version (conflict).** You originally asked for Python 3.10 compatibility, but only Python 3.14 exists on this machine. The ML wheels (ctranslate2, onnxruntime, av) may not exist for 3.14. Options:
- **(a) Recommended:** stay on 3.14 if the wheels exist. I'll confirm with a `pip --dry-run` when you allow network access. Code stays 3.10-compatible either way.
- (b) Install Python 3.12 in a separate venv (via `uv` or the deadsnakes PPA). This adds a system-level tool.

**Q2. CUDA libraries for Blackwell (sm_120).** The system has only the driver (CUDA 13.4 capable). No cuBLAS or cuDNN are installed.
- Plan: install them as pip wheels inside the venv (no system CUDA toolkit), then pin a CTranslate2 build whose release notes confirm sm_120 / CUDA ≥ 12.8 support.
- Verifying this needs network access to read the faster-whisper and CTranslate2 release notes, plus a real GPU run in Phase 2. I won't claim the GPU works until a transcription has actually run on `cuda` and nvidia-smi shows the process on the RTX 5060.
- May I install these packages in Phase 2?

**Q3. Hybrid graphics.** PRIME is `on-demand`. CUDA always runs on the NVIDIA GPU regardless of PRIME mode; the Intel iGPU has no CUDA. The check in Phase 2 will be the python process appearing in `nvidia-smi` during transcription, plus the device name logged by the transcriber.

**Q4. yt-dlp and a JavaScript runtime.** Recent yt-dlp versions need an external JS runtime (for example Deno) for full YouTube support. If that is still the case for the pinned version, it would be one more system dependency, so I'd ask before installing it in Phase 6. It is a decided item; I am only flagging the extra requirement.

**Q5. Padding (overlap with the existing feature).** The app already pads by 0.2 s before / 0.15 s after, with no clamping. Plan 9B wants 0.1 / 0.25 with clamping. I propose replacing the old values with the plan's, configured in one place, and keeping the per-video offset on top.

**Q6. Clean start scope.**
- Clear: `dictation:video:*` (progress, stats, missed words, recent videos, offset) and `cache/*.json`. Back up the cache files to `backup/pre-audio-pipeline-YYYYMMDD-HHMM/` and export the localStorage records to a downloaded JSON file first.
- Keep: `dictation:sound` and `dictation:theme`.
- Is that right?

**Q7. Language box.** The load screen has a small language-code box, used today to pick a caption track. I propose keeping it and passing it to Whisper as `language`, with an empty value meaning auto-detect. Language is part of the layer-1 cache key. Alternatively, remove the box and use config only.

**Q8. Upload size limit.** Which `MAX_CONTENT_LENGTH`? I suggest 2 GB.

**Q9. Old caption code and tests.** In Phase 6, `clean_cues` / `merge_cues` / `split_long_line` / `segment` and their tests are removed along with captions. The bracket and ♪ cleaning ideas are reused in the processor. `extract_video_id` and its test stay. Removing them is required by "remove captions as a source entirely", not a feature loss. Confirm.

**Q10. Git identity.** The global git email is `minas.shannak200@gmail.com`. Your account email is `minas.shannak2002@gmail.com`. Commits use the global setting. Fix it if that's a typo.

**Q11. Server.** Flask's development server (threaded by default) plus one worker thread for jobs. That's enough for a single local user, so no new server software.
