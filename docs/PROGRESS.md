# Progress

Read `docs/PLAN.md` (the full plan) and this file at the start of every session.
Update this file at the end of every phase. Commit after every phase.

## Status

| Phase | State |
| --- | --- |
| 1. Inspect the project | Done, approved. Decisions recorded in PLAN.md section 0.1 |
| 2. Audio extraction and AI transcription | Done, approved |
| 3. Transcript processing | Done, approved; follow-up (one-word rule, experiments) done, **waiting for review** |
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

## Phase 3 follow-up: one-word rule, yt-dlp + Deno, movie clip, model/VAD/prompt experiments (Oct 31, 2026)

### Decisions applied
- **Clock times as words:** approved.
- **Split order** (comma → pause ≥ 0.3 s → before a joining word → longest gap): approved. PLAN.md 10C is updated, and the one-word rule and clock times are recorded there as [Decided].
- **One-word pieces:**
  - A standalone expression from `STANDALONE_EXPRESSIONS` (yes, yeah, no, okay, well, oh, wow, hey, right, perfect, really, sorry, …) stays as its own sentence, with or without punctuation.
  - Any other single word joins the nearest sentence if the gap is under `SINGLE_WORD_MERGE_SECONDS` = 1.5 s, even a finished one, and stays alone only if both gaps are longer.
  - Pieces of 2+ words keep the old rule.
  - `PROCESSING_VERSION` is now 2.

  Before/after (all four clips were compared; only these changed):

  | Clip | Before | After |
  | --- | --- | --- |
  | conversation | "Whereas." / "Because if you break the rules at school you're a naughty boy" | "Whereas. Because if you break the rules at school you're a naughty boy" |
  | conversation (unchanged) | "Really?", "Yeah.", "Well" | still alone: they are expressions |
  | music | "on" (23.49–24.57) | **unchanged**: gaps of 2.1 s and 3.1 s are both over 1.5 s |
  | numbers | "10-30." / "Or I would say half ten." | "10-30. Or I would say half ten." |
  | movie (VAD on) | "sorry All I can do is keep you cold Pitch" | "sorry" / "All I can do is keep you cold Pitch" ("sorry" is in the list) |

### yt-dlp needed a JavaScript runtime
- **Cause:** `cpbARmnt_nU` failed with "This video is not available" (`UNPLAYABLE`), but it was not a restriction. yt-dlp had no JS runtime for YouTube's challenge, so it fell back to a limited player.
- **Fix:** installed in the venv and pinned:
  - `deno==2.9.8`, the official pip-packaged binary at `.venv/bin/deno`
  - `yt-dlp-ejs==0.8.0`, yt-dlp's own challenge scripts, the exact version yt-dlp pins in its default extras
- **Result:** the download then worked.
- **Detail:** Deno must be passed explicitly (`--js-runtimes deno:.venv/bin/deno`) because `.venv/bin` is not on the PATH unless the venv is activated. The Phase 6 service must do this. The README documents it, along with how to update yt-dlp.

### Movie clip (cpbARmnt_nU, "Rise of the Guardians" clip, 8:30)
- **Section used:** 00:00–05:00. It holds three dialogue-free stretches (about 43 s, 32 s and 20 s), found from the old caption cache's timing.
- **Fixture:** `movie.json`, small with VAD on: 1.43 s, cuda/float16, 818 MiB.
- **Reference used for recall:** YouTube's old auto-captions for this video (`cache/cpbARmnt_nU.json`), used **only as a test reference** for what was said.

**Main finding: the VAD drops speech mixed with a music score.**

| Run | Audio kept | Words | Reference lines missed | Segments flagged |
| --- | --- | --- | --- | --- |
| `movie` (VAD on, threshold 0.5, the current default) | 44.4 s of 300 | 95 | **36 / 56** | 0 |
| `movie_vad025` (threshold 0.25) | 54.9 s | 111 | 34 / 56 | 2 |
| `movie_vad01` (threshold 0.1) | 75.3 s | 128 | 29 / 56 | 0 |
| `movie_novad` (VAD off) | 300 s | 271 | **1 / 56** | 7 |

- **VAD on output is poor:** 17 sentences, several fragments ("Maybe to", "was right like"). VAD also glues distant chunks into one Whisper segment, for example one segment spans 26–113 s. Word times stay correct, and the 2.0 s silence rule keeps each sentence within one moment.
- **VAD off output is clean:** 59 sentences, for example "It's okay." / "Don't look down." / "Just look at me." / "Jack, I'm scared." / "You're gonna be fine."

**Fillers in the raw results** (the default VAD-on movie run has none):
- `movie_novad`:
  - "Uh," at 100.16 s ("Uh, we're gonna have a little fun instead.") was removed, giving "we're gonna have a little fun instead."
  - "uh," at 125.62 s ("It's as easy as, uh, one.") was removed, giving "It's as easy as, one."
- `conversation_turbo`: "um" at 43.80 s and "um" at 116.90 s were both removed.

**Expressions:**
- All kept: right ×6, Hey!, okay ×2, No ×2, Well ×2, Yes in the movie; yes/yeah/oh/well/no/right in the turbo conversation.
- Three exceptions were dropped by the **hallucination filter** (low word probability), not by cleaning. They are single-word segments inside the score: "Whoa!" (128.88 s, p 0.20), "Oh" (149.20 s, p 0.01) and "Huh." (252.04 s, p 0.07). The reference has no line at those times, so whether they were really spoken is unverified.

**Hallucinations in music/silence:**
- **VAD on:** none flagged. Suspicious: "Maybe to sorry" (3–12 s, word p 0.48) is a mishearing of "Baby Tooth." / "Sorry.". "…this year. Thank you" (267 s) is really "you nor ever again" (VAD off hears "this year thank you nor ever again"), so it is not the classic "Thank you" hallucination.
- **VAD off:** 7 segments flagged, all low word probability: "Sigh." (30.0 s, p 0.08, a sound written as a word), "Jack!" (38.0 s, p 0.01), "Whoa!", "Oh", "Huh.", and duplicate counts "Four." (p 0.00) and "Three." (p 0.07).
- **Song with VAD off:** 2 flagged, "Oh" and **"Music"** (118 s, a classic hallucination).
- **20 s of silence with VAD off:** Whisper wrote **"You"**, which was flagged and excluded, so no sentences came out (fixture `silence_novad.json`).

**10 sample sentences (`movie_novad`, processed):**
- 1 [5.62–6.36] You all right?
- 3 [13.12–14.44] All I can do is keep you cold.
- 5 [20.84–22.40] I make a mess of everything.
- 10 [66.74–68.14] Jack, get down from there.
- 16 [85.54–86.24] Don't look down.
- 20 [96.10–97.52] But you're gonna be all right.
- 24 [104.58–105.44] Well, I'll trick you.
- 33 [121.20–122.38] We're gonna play hops, Scotch.
- 45 [234.12–236.00] Come on, baby-tooth, I owe you one.
- 52 [265.12–271.26] any Christmas toys this year thank you nor ever again

### VAD off on the speech clips (does it hurt?)
- **conversation:** identical word count (747), 0 flagged.
- **numbers:** 591 vs 606 words, 0 flagged. It **lost one phrase** at 83 s ("it means delayed it or postponed it") and heard "third D" as "3D". It wrote "1030" instead of "10.30", which the number rules still read as "ten thirty". The rest are punctuation and digit-form differences.
- **Silence:** it hallucinates "You" on pure silence, and the filter catches it.

### Experiment A: large-v3-turbo vs small (conversation clip, GPU)

| | small | large-v3-turbo |
| --- | --- | --- |
| Transcribe time (warm) | 4.0 s | 5.2 s |
| First load | 0.8 s | 73.9 s (download about 1.6 GB, one time); 1.7 s warm |
| Peak VRAM (process) | 850 MiB | 2,258–2,290 MiB |
| "grasp me up" (×2) | wrong | **grass me up** ✓ |
| "dobbin" | wrong | **dobbing** ✓ |
| "sneaky ziggy" | wrong | **ciggy** ✓ |
| "setting me up for a form" | wrong | **a fall** ✓ |
| "wrap someone out" | wrong | **rat someone out** ✓ |
| "teachers pet / The teachers favorite" | wrong | **teacher's pet / teacher's favorite** ✓ |
| "You really were a goody two-shoes" | ✓ | **"a good idea"** ✗ |
| "kept my mouth shut" | ✓ | "kept my mouth you up" ✗ |
| Fillers | none written | writes "um" (removed by cleaning) |
| Punctuation and case | 40 sentence ends | **10 sentence ends, lowercase "i" ×22** |
| Sentences after processing | 80, avg 9.3 words | 69, avg 10.8 words (fewer natural breaks) |

`large-v3-turbo` is supported by faster-whisper 1.2.1 (`mobiuslabsgmbh/faster-whisper-large-v3-turbo`).

### Experiment B: initial_prompt (conversation clip)
- **Setup:** `INITIAL_PROMPT` added to config (empty = off, the default) and recorded in `asr_settings`. Prompt used: "Hi, how are you? I'm fine, thanks. Well, let's talk about it. It's a long story, but I'll keep it short."
- **small + prompt:** the unpunctuated 94–139 s stretch went from **0 sentence ends, 0 commas to 15 sentence ends, 10 commas** (160 words). Example: "Do you think your friends saw the teacher coming? Maybe, maybe they were just like setting me up for a form, but even in those circumstances…". Side effects: "rat someone out" ✓, "neighbours" (British spelling), "dobbling" (still wrong), "Yeah, I was a kid" (was "yeah as a kid"). The whole clip has 41 sentence ends vs 40, giving 77 sentences.
- **large-v3-turbo + prompt:** **no effect**. Still 0 punctuation in the stretch and lowercase "i".

### Other changes
- `config.VAD_THRESHOLD = 0.5` (faster-whisper's default, so behavior is unchanged), passed as `vad_parameters` and recorded in `asr_settings`.
- **Older fixtures:** the four made before these settings existed (`conversation`, `music`, `numbers`, `movie`) lack `initial_prompt`/`vad_threshold` in `asr_settings`, which means none / 0.5. `conversation_turbo` and `movie_novad` lack `vad_threshold` (0.5 and n/a). Phase 4 cache keys must treat a missing value as the default.
- **New fixtures** (532 KB total): `movie`, `movie_novad`, `movie_vad025`, `movie_vad01`, `music_novad`, `music_vad025`, `conversation_novad`, `numbers_novad`, `conversation_turbo`, `conversation_prompt`, `conversation_turbo_prompt`, `silence_novad`.

### Recommendations (for decision)
1. **VAD: turn it off by default** (`VAD_FILTER = False`) and rely on the hallucination filter.
   - For movie clips VAD on loses most dialogue (36/56 lines missed vs 1/56), and lowering the threshold barely helps (29/56 at 0.1).
   - Off, the filter caught every real hallucination we saw: "You" in silence, "Music", "Sigh.", and low-confidence duplicate counts.
   - Cost: one lost 7-word phrase and "3D" on the numbers clip, and about 2.5 s more GPU time per 4 minutes.
   - Before Phase 4 decides the cache key, the alternative is to keep VAD on as the default with a per-video "this video has music" retranscribe option. That is a new UI feature, so it's your call.
2. **Model: keep `small` as the default for now.**
   - large-v3-turbo fixes most word errors and transcribes fillers, but almost entirely drops punctuation and capitals, which the segmentation depends on, and the prompt doesn't fix it. It also made two new errors.
   - The VRAM (2.3 GB) and speed are fine on this GPU.
   - Worth a later test: `large-v3` (non-turbo, about 3–4 GB VRAM), which usually keeps punctuation.
3. **initial_prompt: enable the tested prompt for `small`.** It gave a clear punctuation gain on the hardest stretch, with no visible harm in this clip. Test it on the other clips before relying on it.

### Tests run (real results)
- **pytest: 124 passed.**
  - New one-word-rule tests (expression kept; other words merge within 1.5 s, even after a finished sentence; alone only if both gaps are longer; the limit is configurable).
  - The movie clip is added to the real-fixture checks.
  - New regression tests on real output: fillers in `movie_novad` (Uh ×2) and `conversation_turbo` (um ×2) removed, with all expressions in kept segments still present; "You" on silence excluded.
- `uv pip install --dry-run -r requirements.txt` reported "Would make no changes".
- All GPU runs: cuda/float16, with nvidia-smi showing the process (peak VRAM recorded per run).

### Not done / open
- **Decisions needed:** the VAD default, model choice and prompt (recommendations above) affect the layer-1 cache key, so they should be settled before Phase 4.
- **Speech with background music** (not a song or score) is still untested. So are `large-v3` (non-turbo) and the prompt on the numbers and movie clips.
- **One-word sentences:** with VAD off the movie clip gives many short ones ("Two.", "Three. Two.", "Make that five."). That's correct for the dialogue, but it shows that counting scenes produce tiny exercises.
