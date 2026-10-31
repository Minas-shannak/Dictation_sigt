# Progress

Read `docs/PLAN.md` (the full plan) and this file at the start of every session.
Update this file at the end of every phase. Commit after every phase.

## Status

| Phase | State |
| --- | --- |
| 1. Inspect the project | Done, approved. Decisions recorded in PLAN.md section 0.1 |
| 2. Audio extraction and AI transcription | Done, approved |
| 3. Transcript processing | Done, approved; follow-ups done. Model decided: large-v3 + prompt, VAD off |
| 4. Unified format and caching | Done |
| 5. Background jobs and local upload | Done, approved |
| 6. Replace the YouTube caption pipeline | Done, approved |
| 7. Integrate with the dictation engine | Done, **waiting for review** (manual checklist below) |
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

## Phase 3 follow-up 2: VAD off, experiment C, lowercase "i" (Oct 31, 2026)

### Decisions applied
- **VAD:** `VAD_FILTER = False` by default (configurable, recorded in `asr_settings`, so part of the layer-1 key). No "has music" option.
- **Short exercises** ("Two.", "Make that five.") and "3D" are accepted.
- **Lowercase "i":** standalone "i"/"i'm"/"i'll"/"i've"/"i'd" become "I…" in the training transcript only; the raw result is untouched. Tested, and "i.e." is left alone.
- **Captions:** used only as a test reference. Confirmed by grep:
  - The **new pipeline** (`ai/`, `services/`, `config.py`) never reads captions. Its only mention is the hallucination phrase "captions by".
  - The **current app** (`app.py`, the caption-based app that Phase 6 replaces) still imports `youtube-transcript-api` and serves captions through `/api/transcript`. That is the existing feature, not new code.
  - **Test-only use:** `tests/reference/*.json` (fetched by a scratch script) and `tests/tools/evaluate_fixtures.py`.
- **PLAN.md:** section 0.1 items 8–12, plus 5C, 6A, 6E, 10A and 10B updated.
- `PROCESSING_VERSION = 3`.

### Fixtures reorganized
- **Main fixtures** (`tests/fixtures/*.json`) match the defaults: small, VAD off, no prompt. All five were re-transcribed on the GPU (cuda/float16): `conversation`, `numbers`, `movie`, `music`, `silence`.
- **Everything else** is in `tests/fixtures/experiments/`: the earlier VAD-on runs (`*_vad.json`), the VAD-threshold runs (`*_vad025`, `*_vad01`), and the experiment C runs (`*_small_prompt`, `*_large-v3`, `*_large-v3_prompt`, `*_turbo`).
- **References** (`tests/reference/`), YouTube captions for the clip sections, shifted to clip time:
  - conversation: **manual** en-GB captions, the best reference
  - numbers and movie: **auto** captions only, themselves machine-made, so their error rates are rough
  - music: captions disabled, no reference
- **Evaluation tool:** `tests/tools/evaluate_fixtures.py` computes WER of the processed training transcript vs the reference (same normalization for both: lowercase, no punctuation, numbers as words, fillers dropped, gonna→going to), punctuation counts, timing agreement, flagged segments and unreferenced sentences.

### 1. All clips with VAD off (small, default)

| Clip | Flagged (excluded) | Slipped through? | Real speech wrongly flagged? | Timing |
| --- | --- | --- | --- | --- |
| conversation | none | no kept sentence lacks a reference line | none | 720/720 matched words inside their reference line ±1.5 s; longest word 2.0 s ("saying") |
| numbers | none | none | none | 565/565 matched words in place; longest word 1.2 s |
| movie | 7: "Sigh." 30.0, "Jack!" 38.0, "Whoa!" 128.9, "Oh" 149.2, "Huh." 252.0, "Four." 293.5, "Three." 298.6 | **4 unverified sentences** at 39.7–67 s ("Jack! Jack!", "Come on, Jack.", "You guys have fun all the time.", "Jack, get down from there."): the auto-captions have nothing at 28.7–71.8 s. They are coherent and fit the scene, so most likely real dialogue the captions missed. **Needs a listen to 0:38–1:10.** | **none confirmed.** "Four."/"Three." are later duplicates of kept counts that match the reference (290.9 / 293.9). "Jack!" 38.0, "Whoa!", "Oh", "Huh." have no reference line, so unverifiable | 229/229 matched words in place, **but 2 stretched words**: "they're" 271.26–277.38 (6.1 s; the reference has "They're all going out." at 277.16) and "I'm" (3.2 s) |
| music (song) | none this run (an earlier identical run flagged "Oh" and "Music") | no reference; 16 sentences of partly plausible lyrics, unverified | n/a | longest word 2.4 s ("no") |
| silence | "You" (classic hallucination) | nothing | n/a | n/a |

**Stretched words** are a timing problem for playback:
- **What happens:** after music or a pause, Whisper sometimes stretches the first word back over the music. Its start is early and its end is correct.
- **Where:** every model has some: up to 6.1 s (small, movie), 2.1–2.6 s (large-v3, movie), 11.1 s (small + prompt, movie).
- **Effect:** the sentence starting with that word would start playing several seconds early.
- **Constraint:** stored timestamps must stay unchanged (plan 9A).
- **Proposal for Phase 7 (needs your decision):** a derived playback start for sentences whose first word is longer than a limit (e.g. 1.5 s): `play_start = word_end − limit`. The raw and stored word times stay as they are.

**Song results are not reproducible:** an identical VAD-off run gave 79 words before and 108 now. Whisper re-decodes hard audio with random sampling (temperature fallback). Speech clips gave identical results across runs.

### Bug found and fixed: "1030"
- **Problem:** with VAD off, Whisper wrote the time "ten thirty" as "1030" (instead of "10.30"). The year rule only covered 1100–2099, so it became "one thousand and thirty".
- **Fix:** 4-digit numbers 1000–2099 without a comma now use the year reading ("1030" → "ten thirty", "1066" → "ten sixty-six"); "1,030" → "one thousand and thirty". Tested.
- **Result:** small's WER on the numbers clip went from 7.4% to 5.4%.

### Experiment C: model and prompt (VAD off, GPU, cuda/float16)
- **Settings:** prompt = "Hi, how are you? I'm fine, thanks. Well, let's talk about it. It's a long story, but I'll keep it short."
- **Measurement:**
  - **VRAM** is the peak for the process from nvidia-smi.
  - **Ends/commas/lowercase i** are counted in the raw output; lowercase "i" is now fixed by processing anyway.
  - **WER** is computed on the processed training transcript.
- **First loads:** large-v3 took 138 s for its first load, mostly downloading about 3 GB, once; turbo about 74 s once.

**Conversation** (manual reference, 755 words; the most reliable):

| Run | Time | VRAM | WER | S/D/I | Ends | Commas | lowercase i |
| --- | --- | --- | --- | --- | --- | --- | --- |
| small | 3.6 s | 0.8 GB | 5.4% | 22/13/6 | 40 | 9 | 0 |
| small + prompt | 3.8 s | 0.8 GB | 7.9% | 14/**39**/7 | 42 | 26 | 0 |
| large-v3 | 11.5 s | 4.1 GB | 4.1% | 10/13/8 | 10 | 0 | 31 |
| **large-v3 + prompt** | 11.9 s | 4.1 GB | **3.8%** | 8/14/7 | 18 | 9 | 15 |
| large-v3-turbo | 4.6 s | 2.3 GB | 4.9% | 11/19/7 | 11 | 1 | 25 |

Known error spots (✓ = matches the manual reference):

| | small | small+P | large-v3 | large-v3+P | turbo |
| --- | --- | --- | --- | --- | --- |
| grass me up | ✗ | ✗ | ✓ | ✓ | ✓ |
| dobbing | ✗ | ✗ | ✓ | ✓ | ✓ |
| ciggy | ✗ | ✓ | ✓ | ✓ | ✓ |
| up for a fall | ✗ | ✗ | ✓ | ✓ | ✓ |
| rat someone out | ✗ | ✓ | ✓ | ✓ | ✓ |
| goody two-shoes | ✓ | ✗ | ✗ | ✓ | ✗ |
| mouth shut | ✓ | ✓ | ✓ | ✓ | ✗ |

**small + prompt drops real speech:** "Yes, yes. Whereas.", "I did hang around with. As an adult.", "pet describes the student who is". The prompt makes small skip asides and repeats, which is unacceptable for dictation. large-v3 + prompt drops only the two spots every run misses ("don't think we I" self-correction at 19 s, "for the most part so you'd" at 177 s).

**Movie** (auto-caption reference, 252 words; noisy, and many "insertions" are probably real lines the captions missed):

| Run | Time | VRAM | WER | S/D/I | Ends | Commas | lowercase i | Flagged |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| small | 3.7 s | 0.9 GB | 18.7% | 15/8/24 | 65 | 19 | 0 | 7 |
| small + prompt | 5.5 s | 1.0 GB | 22.6% | 7/18/32 | 66 | 23 | 0 | 10 |
| large-v3 | 8.1 s | 4.1 GB | 19.0% | 4/25/19 | 48 | 18 | 0 | 1 |
| **large-v3 + prompt** | 8.1 s | 4.1 GB | **17.9%** | **3**/23/19 | 48 | 18 | 0 | 1 |
| large-v3-turbo | 4.7 s | 2.3 GB | 18.3% | 5/15/26 | 53 | 21 | 0 | 1 |

**Numbers** (auto-caption reference, 595 words; "3/4" vs "three quarters" style differences count as errors for every run):

| Run | Time | VRAM | WER | S/D/I | Ends | Commas | lowercase i |
| --- | --- | --- | --- | --- | --- | --- | --- |
| small | 3.5 s | 0.9 GB | 5.4% | 16/10/6 | 66 | 52 | 0 |
| small + prompt | 3.6 s | 0.9 GB | 5.5% | 16/10/7 | 65 | 54 | 0 |
| large-v3 | 11.0 s | 4.1 GB | 4.0% | 13/2/9 | 65 | 35 | 0 |
| large-v3 + prompt | 11.1 s | 4.1 GB | 4.0% | 13/2/9 | 64 | 36 | 0 |
| large-v3-turbo | 4.3 s | 2.3 GB | 3.9% | 13/4/6 | 45 | 20 | 6 |

All runs: matched-word timing agrees with the references (0–5 words off per run, only in the prompt runs).

### Recommendation: default model large-v3 with the prompt
- **Words (priority 1):**
  - **large-v3 + prompt** is best on the manual reference (3.8%) and gets every checked spot right.
  - It is best or tied on the other two clips, with the fewest substitutions (3 on movie, 8 on conversation).
  - It is the only run with no casualties among the known spots.
- **Punctuation (priority 2):** large-v3 punctuates the movie and numbers clips well (48–65 ends), but only weakly in the casual conversation (18 ends vs 40 for small). The prompt doubles that from 10 and halves lowercase "i" (now fixed by processing anyway). Unpunctuated stretches still split acceptably (pause, joining word, max length).
- **Prompt:** keep `INITIAL_PROMPT` **on for large-v3** (small gains everywhere, no losses seen) but **never with small** (it deletes speech).
- **Cost:**
  - **Time:** about 3 s per minute of audio on this GPU (20-minute video ≈ 1 minute).
  - **VRAM:** 4.1 GB of 8 GB.
  - **Download:** about 3 GB, once.
  - **Risk:** if CUDA fails, the CPU fallback with large-v3 would be very slow (not measured; likely slower than real time).
  - **Option to decide:** fall back to `small` on CPU. It is a different layer-1 key, so it is cached separately.
- **Alternative:** `large-v3-turbo` if speed matters more. Its word accuracy is close (4.9 / 18.3 / 3.9%), it takes 2.5× less time and 2.3 GB, but it has the least punctuation and missed "goody two-shoes" and "mouth shut".
- **Not recommended:**
  - `small + prompt`: drops speech.
  - `small` alone: it remains the most-punctuated, fastest option, but makes the most word errors.

### Files changed
- **Code:**
  - `config.py`: `VAD_FILTER = False`, comments, `PROCESSING_VERSION = 3`
  - `ai/transcript_processor.py`: lowercase-i rule, year reading 1000–2099
- **Tests:**
  - `tests/test_transcript_processor.py` and `tests/test_transcriber.py`: VAD-off fixtures, exact expected exclusions per real clip, filler/expression and silence regressions moved to the new fixtures, "1030"/"1066"/"1,030", lowercase-i test
  - `tests/tools/evaluate_fixtures.py` (new)
- **Data:**
  - `tests/reference/{conversation,numbers,movie}.json` (new)
  - fixtures regenerated and moved
- **Docs:** `README.md`, `docs/PLAN.md`, `docs/PROGRESS.md`
- **Local only:** `tests/media/silence.wav` (git-ignored).

### Tests run (real results)
- **pytest:** 128 passed.
- **Evaluation:** `python tests/tools/evaluate_fixtures.py` ran on all 27 fixtures; the tables above come from its output.
- **GPU:** 17 transcription runs (5 defaults + 12 experiment C), all cuda/float16, with VRAM measured from nvidia-smi.

### Open (needs your decision)
- **Default model and prompt:** recommendation above.
- **Playback start for stretched first words** (Phase 7 proposal above).
- **CPU fallback model** if large-v3 is chosen.
- **Listen to the movie at 0:38–1:10** to confirm the 4 unverified sentences.

## Model decision follow-up: large-v3 + prompt, safeguards, CPU timing (Oct 31, 2026)

### Decisions applied
- **Model:**
  - `WHISPER_MODEL = "large-v3"` and `INITIAL_PROMPT` set to the tested prompt by default.
  - `small` is removed from config, README and CLI usage.
  - The CPU fallback already reloads the **same** model (int8); there is no second model.
  - The small fixtures stay as historical data (`tests/fixtures/experiments/*_small.json`).
- **Main fixtures** (`tests/fixtures/{conversation,numbers,movie,music,silence}.json`) re-transcribed with the defaults: large-v3 + prompt, VAD off, cuda/float16, 3.8–4.1 GB VRAM, 0.7–12 s each. The identical `experiments/*_large-v3_prompt.json` were removed.
- **PLAN.md:**
  - Typo fixed in section 0.
  - Section 0.1: item 10 rewritten (large-v3 only, same model on the CPU, "Running on CPU, this will be slow" with real progress), and items 13 (prompt safeguards) and 14 (`play_start` for stretched first words, Phase 7) added.
  - Section 6A: large-v3 decision and the prompt-scope finding. Section 6C: same model on the CPU.
  - Section 7: prompt-leak and isolated-phrase rules. Section 9B: `play_start`.
  - Section 11: example shows `"model": "large-v3"`, `"vad_filter": false` and the prompt. Section 14C: CPU message.

### 1a. Prompt leakage: real, now flagged
- **Checked:** large-v3 + prompt on silence, song, movie, conversation and numbers, looking for any segment sharing a 3-word run with the prompt.
  - silence, movie, conversation, numbers: **none**
  - **song: one leak**, "Let's talk about it." at 27.1 s over music (no_speech_prob 0.98, word probability 0.26)
- **Safeguard** (`prompt_leak` reason; the prompt comes from the raw result's `asr_settings`, so reprocessing always uses the prompt that was actually used). A segment is flagged if either:
  1. it contains ≥ 50% of the prompt in runs of 2+ words, or
  2. ≥ 60% of its 3-word sequences occur in the prompt **and** it is low-confidence or has ≥ 5 words.

  Short confident real speech ("I'm fine, thanks.", "Hi, how are you doing today?") is kept.
- **Tested:**
  - hand-made cases (whole and most of the prompt, the low-confidence short leak, confident short replies kept, no prompt means no rule)
  - real leaks: the song (main fixture), plus the hotwords experiment below, where 6 of 7 leaked lines are flagged `prompt_leak`. The 7th, a confident 3-word "I'm fine, thanks.", is kept by this rule but still excluded by low word probability.
- **Second slip-through found and fixed:**
  - **Problem:** on the 20 s of silence, large-v3 wrote **"Thank you."** with no_speech_prob 0.14, avg_logprob −0.31 and word probability 0.59. No rule caught it. The song also ends with an unflagged "Thank you." after 7.6 s of music.
  - **New rule** `isolated_stock_phrase`: flags a segment that is only a stock phrase ("thank you", "thanks", "you", "bye", …), has ≥ 3 s of nothing on both sides (or the clip edge), and has a mean word probability < 0.75. A clearly spoken closing "Thank you." (about 0.9) is kept. Both real cases are now flagged, and hand-made tests cover kept cases (clear speech, nearby speech).
- **Real-clip exclusions now:**
  - song: "Let's talk about it." (prompt leak) and "Thank you." (isolated)
  - silence: "Thank you."
  - movie: "Oh, my God." at 147.2 s (low word probability, during the score, unverified)
  - conversation and numbers: none

### 1b. Prompt scope (faster-whisper 1.2.1 source, `transcribe.py`)
- **What the source does:**
  - `generate_segments` puts the prompt tokens at the start of `all_tokens`. Each 30 s window uses `previous_tokens = all_tokens[prompt_reset_since:]`.
  - After every window, `prompt_reset_since = len(all_tokens)` when `condition_on_previous_text` is False (lines 1372–1383).
  - **So `initial_prompt` only conditions the first 30 s window.**
- **Consistent with the data, but the earlier "improvement" was mostly incidental:**
  - Per-window comparisons show text changing in every later window too. That is a knock-on effect: a different first window shifts where every later window starts.
  - For small, 30–60 s went 9 → 3 sentence ends; for large-v3, 60–240 s went 6 → 13.
  - The prompt's *direct* effect is the first window (large-v3: commas 0 → 5 in 0–30 s). The WER difference (3.8% vs 4.1%) is within this knock-on variation.
- **`hotwords` tested, rejected:** passing the prompt as `hotwords` (which `get_prompt` inserts into every window) was tried on conversation, song, silence and movie (`experiments/*_large-v3_hotwords.json`).
  - **Punctuation:** much better (conversation 59 sentence ends / 38 commas / 0 lowercase "i", vs 18 / 9 / 15).
  - **Speech lost:** **178 words of real speech deleted** on the conversation clip (WER 25.2%).
  - **Leaks:** the prompt leaked into the song 7 times and into the movie once.
  - Not used. `INITIAL_PROMPT` stays (harmless, small first-window benefit).

### 2. CPU fallback timing (large-v3 int8, GPU hidden)
- **Conversation clip (240 s):** **97.2 s** to transcribe, 103.9 s in total including a 3.8 s model load. That is about 0.4× real time on this Intel Core 7 240H (16 threads), with peak RAM about 4.0 GB.
- **Progress** is real and steady: updates every ~11 s (one 30 s window per step), 3% → 100%.
- **Accuracy is the same as on the GPU:** WER 3.7% vs 3.8%, same punctuation, 73 sentences (`experiments/conversation_large-v3_cpu.json`).
- **Rough estimate:** a 20-minute video would take about 8–9 minutes on the CPU, extrapolated, not measured.
- **Phase 5/7 UI:** "Running on CPU, this will be slow" plus the real percentage.

### Also fixed
- **Split inside a converted number:**
  - **Problem:** the max-length splitter split between words made from one token ("…one hundred" | "and fifty kids…" from "150"), because "and" is a joining word. Sentences then overlapped in time.
  - **Fix:** a split never falls between words with the same source token.
  - **Tested:** hand-made, plus the real conversation fixture via the ordering test.
- `PROCESSING_VERSION = 4`.

### Tests run
- pytest: **137 passed** (before Phase 4).
- GPU runs: 5 main fixtures + 4 hotwords experiments (cuda/float16). CPU run: 1 (cpu/int8).

## Phase 4: Unified format and caching (Oct 31, 2026)

### Done
- **`services/transcript_cache.py`**, `get_transcript(source, extract_audio, language, processing_settings, on_progress, on_stage)` returns the unified transcript.
  - **Inputs:** `extract_audio` is a callable returning a context manager that yields a temporary audio path (such as `extracted_audio` bound to a file). It is only called on a layer-1 miss.
  - **Stages reported:** `extracting_audio`, `loading_model`, `transcribing` (the last two from the transcriber), `processing`, `preparing`.
  - **Sources:** `Source(source_type, source_ref, original_filename)` gives `video_id` `yt:<id>` or `local:<sha256>`.
    - `youtube_source(id)`
    - `local_source(path, original_filename)`, which hashes the content in 1 MiB chunks, so the same bytes under another name give the same ID
  - **Layer 1** (`cache/asr/<video>__<key>.json`): the raw result.
    - **Key:** sha256 of `{video_id, model, asr_settings}`, where `asr_settings` comes from the new `ai.transcriber.asr_settings(language)` and holds language, vad_filter, vad_threshold, condition_on_previous_text, beam_size, word_timestamps and initial_prompt. The transcriber records the same function's output in every raw result, so the key and the record can't drift apart.
    - **Not in the key:** device/compute type. The GPU and CPU runs are the same model, and the CPU run measured equal accuracy.
  - **Layer 2** (`cache/transcripts/<video>__<key>.json`): the unified transcript. **Key:** sha256 of `{layer-1 key, resolved processing settings, PROCESSING_VERSION}`.
  - **Entries:** every entry carries `cache_format`, `kind` and `key`. Anything else (unreadable, wrong kind/format/key) is ignored as a miss and overwritten, with a log warning.
  - **Atomic writes:** temp file in the same folder, fsync, `os.replace`; the temp file is removed on any exception, including KeyboardInterrupt.
  - **Validation before caching:**
    - the raw result must have words with finite timestamps, 0 ≤ start ≤ end ≤ duration + 1 s, and starts in order (tolerance 0.05 s)
    - otherwise `invalid_result`, or `no_speech` when there are no words, and nothing is cached
  - **No sentences** (all segments hallucinated, or nothing left): `TranscriptError` with a friendly message. The raw result **stays in layer 1 for review**, so the same verdict comes back without retranscribing; nothing is written to layer 2.
  - **Old caption files** (`cache/<id>.json`) are never read: different folder, and no `cache_format` / `kind` / `key`. They are left untouched for the current app and for the Phase 7 migration.
- **Unified format** (PLAN section 11): `video_id`, `source_type`, `source_ref`, `original_filename`, `duration`, `language`, `language_probability`, `model`, `device`, `compute_type`, `asr_settings`, `processing_settings`, `processing_version`, `transcript_version` (12 hex chars of sha256 over the sentence texts), `created_at` (UTC), `original_transcript`, `training_transcript`, `excluded_segments`, `excluded` (details for review), and `sentences` `[{index, text, start, end, words}]`. The raw result is not duplicated inside it.
- **Refactor:** `ai.transcript_processor.resolve_settings()`, used by both the processor and the layer-2 key.
- **`config.py`:** `BASE_DIR`, `CACHE_DIR`, `ASR_CACHE_DIR`, `TRANSCRIPT_CACHE_DIR`, `CACHE_FORMAT = 1`.

### Files changed
- New: `services/transcript_cache.py`, `tests/test_transcript_cache.py`.
- Changed: `config.py`, `README.md`, `docs/PROGRESS.md`.
- In the previous commit, but made for Phase 4 (behavior-neutral refactors): `ai/transcriber.py` (`asr_settings()`) and `ai/transcript_processor.py` (`resolve_settings()`).
- `app.py` and the frontend are unchanged: the running app still uses captions until Phase 6.

### Tests run (real results)
- **pytest: 160 passed** (137 + 23 new cache tests, fake transcriber, temporary cache folders):
  - **Format:** the section 11 fields; the raw stored separately; `transcript_version` stable and changing with the text; prefixed IDs; local content hashing (a renamed copy gets the same ID).
  - **Second load:** no transcription and no audio extraction.
  - **Processing changes:** `remove_fillers` reuses layer 1, and the version changes because the clip has two real "um". A new PROCESSING_VERSION reprocesses without transcribing.
  - **Recognition changes:** a different model, prompt, VAD or beam size retranscribes, and both entries are kept. The language is part of the key. Different sources never share entries.
  - **Old caption file:** never read and left untouched.
  - **Bad entries:** corrupted or foreign entries are treated as misses and rewritten.
  - **Failures:** empty and broken-timestamp results raise errors and nothing is cached. All-hallucinated keeps the raw result, adds no transcript, and the second try doesn't retranscribe. A failed transcription writes nothing.
  - **Atomic write:** an interrupted write leaves the old file intact and no temp file.
  - **Stages:** reported on a miss; only `processing` on a hit.
- **End to end on the GPU** (real audio, temporary cache folder, conversation clip as a local file):
  - first load: 18.2 s, large-v3 cuda, 73 sentences, all 5 stages
  - same again: 0.00 s, no transcription
  - `remove_fillers=False`: 0.01 s, no transcription, new `transcript_version`
  - a renamed copy of the file: same `video_id`
- **Not tested here:** the frontend side of `transcript_version` (progress tied to it, a reset on mismatch). Progress lives in browser localStorage, which is Phase 7. A Python helper would be untestable against the real storage, so that test belongs with the frontend work.

### Not done / next
- **Phase 5:** jobs, status and cancellation; uploads (2 GB limit, secure names, ffprobe validation, codec check and Convert); the HTML5 player behind the shared interface; 30-day upload retention. `get_transcript` is ready to be called from the job worker. **Cancellation inside FFmpeg or Whisper is not implemented yet.** A cancelled job must not write cache entries, and since writes only happen after a step completes, cancelling between steps already satisfies that. Phase 5 adds the checks.
- `app.py` still serves the old caption pipeline (replaced in Phase 6).

## Verified result: human listening check of the movie clip (Oct 31, 2026)

The user listened to the movie clip (cpbARmnt_nU, 00:00–05:00):
- **0:38–1:10:** the 4 sentences small kept with no caption reference are **spoken exactly as transcribed**: "Jack! Jack!", "Come on, Jack.", "You guys have fun all the time.", "Jack, get down from there." Keeping them was correct.
- **~2:27 (147 s):** **music only.** large-v3's "Oh, my God." there is a real hallucination, and excluding it was correct.
- **Verdict:** on this clip the filter removed no real speech and kept no confirmed hallucination.

**Recorded in `tests/reference/movie.json`:**
- the 4 verified lines are added to `lines` with `"source": "human"` (the auto-captions have nothing at 28.7–71.8 s)
- a `human_verified` block holds the speech spans and the music-only span 145–150 s

**New test:** `test_movie_filter_agrees_with_the_listening_check` runs on both the large-v3 and the small movie fixtures. It checks that no excluded segment overlaps verified speech, that no kept sentence falls in the music-only span, and that something written there was excluded.

**Model impact (needs attention).** With the verified lines in the reference, the movie WER changes order:
- small 11.2%, turbo 11.5%, **large-v3 + prompt 16.7%** (S4 **D31** I10)
- Earlier, small's real lines counted as "insertions" against the incomplete auto-captions.
- **large-v3 drops whole lines of dialogue spoken over the score:**
  - "You guys have fun all the time." and "Come on, Jack" (both verified by listening)
  - "No, we're not. Would I", "Yes, you always play tricks. No, all right.", "Turn them off"
- small keeps nearly all of them.
- It also runs the remaining verified lines together without punctuation: "Jack, get down from there You're funny, Jack Be careful We will".
- So large-v3 is best on clean speech (conversation 3.8%, numbers 4.0%) but loses dialogue under music, which the filter cannot recover. The model is part of the layer-1 key, so this does not block Phase 5. **The model decision should be revisited with this in mind.**

pytest: 162 passed.

## Phase 5: Background jobs and local upload (Oct 31, 2026)

### Done
**Backend**
- **`services/jobs.py`:** one worker thread, an in-memory queue, jobs run one at a time.
  - **Status:** `state` (queued/running/done/failed/cancelled), `stage`, `progress`, `device`, `compute_type`, `from_cache`, a friendly `error`, `queue_position`, and `result` once done.
  - **Duplicates:** submitting the same key (same video and language, or the same conversion) while queued or running returns the existing job.
  - **Cancellation:** `JobCancelled` is a BaseException raised from the job's `check()`, `set_stage()` and `set_progress()`. The transcriber's `except Exception` lets it through, and every `finally` still runs, so temp audio is deleted and no cache entry is written.
  - **Errors:** known errors show their message; anything else shows a generic one, with the traceback in the log.
  - **History:** finished jobs are forgotten after 50. A server restart loses jobs; there are no partial cache files thanks to the atomic writes.
- **`services/audio_extractor.py`:**
  - `run_ffmpeg()` replaces `subprocess.run`. It checks for cancellation about twice a second (killing FFmpeg), enforces the timeout, sends stderr to the log, and can report progress (`-progress pipe:1`).
  - `extracted_audio(path, check)` is cancellable.
  - New `convert_for_browser()`: H.264 (veryfast, CRF 23) + AAC MP4 with faststart, with progress. It writes a temp file and renames it only after the result probes as H.264 of the same length; on failure or cancel nothing is left.
- **`services/uploads.py`:**
  - **Checks:** the extension (`.mp4 .m4v .mov .webm`), then the **content** with ffprobe (container mov/mp4 or matroska/webm, has audio, has video, non-zero length).
  - **Size:** 2 GB. Flask's `MAX_CONTENT_LENGTH` refuses larger requests **before saving**, with a JSON message giving the limit.
  - **Storage:** the file is hashed with SHA-256 while it is saved (read once) and stored as `uploads/<sha256><ext>`. `secure_filename` is applied to the display name only, so names can't reach the path.
  - **Index:** `uploads/index.json` holds the original name, codecs, browser playability, `converted` and `last_opened`. The same content gets the same entry and video_id.
  - **Retention:** files not opened for 30 days are deleted at startup and daily. The entry stays as `expired`, and the transcripts stay in the cache. Leftover `incoming-*.part` / `*.converting.mp4` files are removed at startup, or after a day while running.
  - **Convert:** `replace_with_converted()` swaps in the converted MP4, deletes the original (no duplicate) and keeps the video_id (hash of the original), so cached transcripts still match.
- **`app.py` routes** (thin, calling services):
  - `POST /api/uploads` (file + language) → video info + job
  - `POST /api/local/<id>/open` → resets the retention clock and returns the transcript job; 410 if expired
  - `POST /api/local/<id>/convert`
  - `GET /api/jobs/<id>`, `POST /api/jobs/<id>/cancel`
  - `GET /media/<id>` (HTTP Range support for seeking)
  - The retention thread starts with the server. The caption route is unchanged (Phase 6).
- **`ai/transcriber.current_device()`** lets jobs report the device in use as soon as transcription starts, including after a CPU fallback.

**Frontend**
- **Shared player interface** in `static/app.js`: `play, pause, seekTo, getCurrentTime, getDuration, isPlaying, setRate, destroy` + events `onReady(title)`, `onPlayingChange(playing, running)`, `onError(message)`.
  - `makeYouTubePlayer` holds all YouTube-specific code, including hiding captions.
  - `makeHtml5Player` uses a `<video>` element.
  - The dictation engine (`playLine`, `togglePlay`, `watchPlayback`, stats clock, time display) only uses the interface. "running" keeps the old stats behavior (buffering doesn't count as playback time).
  - Fullscreen covers the whole app, so it works for both.
- **Upload flow:**
  - an "Upload a video…" button and a processing panel showing each stage (Uploading %, Waiting, Extracting audio, Loading the AI model, Transcribing %, Processing, Preparing)
  - the device ("GPU (cuda, float16)", "CPU (int8)", "loaded from cache"); during CPU transcription, **"Running on CPU, this will be slow."**
  - Cancel
- **Codec check:** if the browser can't play the video, the panel says so and offers **Convert**. Transcription doesn't wait. Practice starts when the transcript is ready **and** the video is playable.
- **Shared start path:** `startPractice()` is shared by YouTube and local videos. A small adapter turns the unified transcript into the engine's current `{videoId, lines}`. The practice header shows the model and device ("large-v3 · GPU float16").
- **Recent videos:** a local entry reopens through the server (cache hit). If expired, it shows the server's message, and the entry reads "file expired, upload it again".
- Asset versions bumped to `?v=2`.

### Files changed
- New: `services/jobs.py`, `services/uploads.py`, `tests/test_jobs.py`, `tests/test_uploads_api.py`.
- Changed: `services/audio_extractor.py`, `ai/transcriber.py`, `app.py`, `config.py`, `index.html`, `static/app.js`, `static/style.css`, `tests/test_audio_extractor.py`, `README.md`, `docs/PROGRESS.md`.

### Tests run (real results)
- **pytest: 194 passed**, on three consecutive runs. That is 160 + 34 new:
  - **jobs (9):** result and status; one at a time in order; duplicates; queue position and cancelling a queued job; a running job stops at its next check and cleans up; cancellation passes through `except Exception`; friendly vs generic errors; history limit; refused cancels
  - **extractor (5 new):** FFmpeg killed by `check()` within 5 s; cancelled extraction removes the temp WAV; HEVC→H.264 conversion with monotonic progress ending at 1.0 and no temp left; cancelled conversion leaves nothing
  - **upload API (20, real FFmpeg, fake transcription):**
    - storage and job: content-hash storage, transcription job, the same file under another name stored once, path traversal in the name contained
    - rejections (each leaving nothing behind): wrong extension, corrupt content, no audio, audio only (no video)
    - limits and serving: too large → 413 with "2 GB limit" before saving; missing file field; Range requests (206); unknown or malicious IDs → 404
    - conversion: HEVC flagged, Convert replaces it with H.264 keeping the ID, original deleted; a cancelled conversion keeps the original
    - jobs and retention: job endpoints; retention (29 days kept, opening resets the clock, 31 days deleted with the transcript cache kept, 410 "Upload it again", re-upload revives it); startup cleanup of interrupted uploads
- **Browser** (headless Firefox, server with scratch upload/cache folders; the user's real `uploads/` and `cache/` untouched):
  - **Upload `jfk.mp4`:** practice in 6.9 s on GPU (all stages shown, "GPU (cuda, float16)"). Two sentences, video served from `/media/local:…`.
  - **HTML5 playback through the interface:** the line auto-paused at its end; Enter worked when finished (replay), playing (pause) and paused partway (resume, then stop at line end); typing and Backspace worked; the time display read "00:07 / 00:11".
  - **Recent videos:** reopened from the cache in 0.2 s ("loaded from cache").
  - **Broken file:** "This file is corrupted or not a supported video format."
  - **HEVC `.mov`:** codec warning, then "Transcript ready. Convert the video to practice with it." After Convert, practice started 1.1 s later and the converted video played to the line end with no player error.
  - **YouTube regression** (caption pipeline through the new YouTube adapter): loaded, auto-paused at line end, Enter played.
  - **Expiry:** after a 31-day cleanup both video files were removed (2 transcripts kept). Clicking the entry showed "This video file has expired (not opened for 30 days). Upload it again.", and the entry read "file expired, upload it again".
  - **Browser console:** no errors (only YouTube's cookie warnings).
- **Real CPU path** (second server with `CUDA_VISIBLE_DEVICES=""`, browser upload of `jfk.mp4`):
  - the panel showed "Transcribing audio", "CPU (int8)" and **"Running on CPU, this will be slow."**
  - practice after 13.2 s, header "large-v3 · CPU int8"
  - server log: "No CUDA device available; using the CPU", model on cpu (int8)
- **Real cancellation on the GPU** (4-minute video via curl): cancelled at 11% of transcription. The job reported `cancelled` 0.86 s later, with no layer-1 cache entry written and no temp WAV left in `/tmp`.
- **Syntax:** Python 3.10 parse of all files OK, and `node --check static/app.js` OK.

### Not done / limitations
- **Cancel delays:**
  - During the **model load** (seconds; the first download takes minutes), cancel waits until loading finishes.
  - During **transcription**, cancel takes effect at the next decoded segment (about 1 s on GPU; on CPU one 30 s window can take about 11 s).
- **Uploads:** the upload itself runs inside the HTTP request (the file transfer). Cancel aborts it in the browser, and any partial `.part` is removed at the next startup or after a day.
- **Deferred to Phase 7** (left as before, not changed here):
  - the engine still uses the old `lines` format through an adapter, and the old padding (0.2 / 0.15 s)
  - no `play_start` and no clamping
  - saved progress not yet tied to `transcript_version`
  - the old-data migration
- **Phase 6:** YouTube still uses captions. The Deno/yt-dlp audio service and removing captions are next.

## Final model (large-v3-turbo) and per-sentence clarity (Nov 1, 2026)

### Model: large-v3-turbo + prompt, VAD off
- **Settings:** `WHISPER_MODEL = "large-v3-turbo"`, on GPU float16 and in the CPU fallback (int8, same model). `INITIAL_PROMPT` kept.
- **Docs updated:** PLAN.md 0.1 item 10, 6A, 6C and the section 11 example; README.
- **Fixtures:** main fixtures re-transcribed with the defaults (cuda/float16, 2.2–2.3 GB VRAM, 0.6–4.6 s each). The large-v3 + prompt ones moved to `experiments/*_large-v3_prompt.json`.

**Evaluation (VAD off; movie reference = auto-captions + the human-verified lines)**, from `python tests/tools/evaluate_fixtures.py`:

| Run | Conversation WER (manual ref.) | Movie WER (auto + verified) | Numbers WER (auto) | Time per clip | VRAM |
| --- | --- | --- | --- | --- | --- |
| small | 5.4% | **11.2%** | 5.4% | 3.5–3.7 s | 0.8–1.0 GB |
| small + prompt | 7.9% | 15.6% | 5.5% | 3.6–5.5 s | 0.8–1.0 GB |
| large-v3 | 4.1% | 17.8% (D33) | 4.0% | 8–11.5 s | 4.1 GB |
| large-v3 + prompt | **3.8%** | 16.7% (D31) | 4.0% | 8–12 s | 4.1 GB |
| large-v3-turbo | 4.9% | 11.5% | **3.9%** | 4.3–4.7 s | 2.3 GB |
| **large-v3-turbo + prompt (default)** | 4.6% | 12.3% (D16) | **3.9%** | 4.3–4.6 s | 2.3 GB |

Default, details:
- **Conversation:** 13 sentence ends, 6 commas, 25 lowercase "i" (fixed by processing), 70 sentences.
- **Movie:** 55 ends, 55 sentences.
- **Numbers:** 44 ends, 69 sentences. Every number is read as words, with no digits left.

Real exclusions (default):
- **Song:** "Let's talk about it." (prompt leak) and the closing "Thank you." (isolated stock phrase).
- **Silence:** "Thank you."
- **Movie, conversation, numbers:** none.
- **Movie listening check:** turbo writes nothing in the verified music-only span. All 4 verified sentences are present (the first as "Oh, Jack, you guys have fun all the time.", which merges "Come on, Jack" heard as "Oh, Jack").

**Fix: repeated single-word segments.**
- **Problem:** turbo heard "Jack?" four times as four one-word segments, and the cross-segment "repeated segment" rule flagged the fourth with the phrase limit (3). Within one segment a single word is allowed 6 repeats, so the two rules disagreed.
- **Fix:** one-word segments now use the single-word limit (6). Calling a name repeatedly is speech, not a loop. Tested: 4 kept, 8 flags the 7th and 8th, and phrases still use 3.

### Clarity (layer 2, no retranscription)
- **What each sentence now carries:** `clarity` (mean word probability, 3 decimals) and `unclear` (clarity < `SENTENCE_CLARITY_MIN`, starting value **0.7**, configurable and part of the processing settings, so it is in the layer-2 key).
- Nothing is deleted. The badge and the stats rule come in Phase 7. `PROCESSING_VERSION = 5`.
- **Movie sentence clarities (turbo + prompt):** most clean dialogue is 0.89–1.00. The low ones:
  - "Dad!" 0.44
  - "Jack? Jack? Jack?" 0.64 (verified real speech, shouted over the score)
  - "I'll make that five," 0.67
  - "Baby tooth." 0.68
  - "It was me." 0.73
  - "You alright?" 0.76
  - "Oh, Jack, you guys have fun all the time." 0.77 (verified; includes the misheard "Oh")
  - "We're not trick you." 0.79 (misheard "Would I trick you?")

Sentences marked unclear at each threshold:

| Threshold | movie (56) | conversation (70) | numbers (69) | song (22) |
| --- | --- | --- | --- | --- |
| 0.5 / 0.6 | 1 ("Dad!") | 0 | 0 | 2 |
| 0.65 | 2 (+ "Jack? Jack? Jack?") | 0 | 0 | 2 |
| **0.7** | 4 (+ "I'll make that five,", "Baby tooth.") | 1 ("And in like PE," 0.66) | 0 | 2 |
| 0.75 | 5 (+ "It was me.") | 1 | 1 | 3 |
| 0.8 | 8 (+ "You alright?", "Oh, Jack, you guys…", "We're not trick you.") | 1 | 3 | 3 |

- **Why 0.7:** at 0.7 the badge marks exactly the shouted or music-covered lines in the movie, and almost nothing in clean speech. At 0.8 it would also mark misheard lines ("We're not trick you."), which arguably deserve the badge too. **Your call.**

### Tests
- **pytest: 197 passed.** Changes:
  - real-clip expectations updated to the turbo fixtures (exclusions, fillers: movie "uh", conversation and numbers two "um" each, numbers with no digits left)
  - the listening-check test now requires that anything written in the music-only span is excluded (turbo writes nothing there)
  - new clarity test and single-word repeat test
  - the cache test's "other model" is now large-v3

## Item 3: Recent-video reopen label (verified)
- **Recorded** (browser, MutationObserver on the stage text): reopening an uploaded video from Recent videos showed exactly `["Opening"]`, with no "Uploading 0%".
- **YouTube loads:** these now start with "Starting". Before, they briefly showed "Waiting for another video to finish". Recorded: `["Starting"]` for a cached YouTube video.

## Phase 6: YouTube audio instead of captions (Nov 1, 2026)

### Done
- **`services/youtube_audio.py`:** isolated, and the only place yt-dlp is used.
  - **ID first:** `extract_video_id()` (moved from `app.py`) validates the link before any download.
  - **Download:** `downloaded_audio(video_id, check, on_progress, on_info)` downloads the **audio-only** stream (`bestaudio/best`, no playlist, `writesubtitles`/`writeautomaticsub` False, no cookies or login) into a temporary folder, which is always deleted afterwards (including on cancel or failure).
  - **Deno:** the venv's Deno is passed as `js_runtimes`, with a log warning if it is missing.
  - **Live streams:** rejected.
  - **Progress and cancel:** progress is downloaded bytes / total; cancellation goes through the progress hook (a real check: the BaseException propagates out of yt-dlp).
  - **Errors** are logged in full and mapped to friendly messages: private, age-restricted, members-only, DRM, blocked/429/"not a bot", unavailable, or generic. The unavailable and generic messages suggest updating yt-dlp or uploading the file.
- **`app.py`:** **no caption code left.**
  - **Removed:** the `youtube-transcript-api` import, caption fetching and caption segmentation (`clean_text`, `clean_cues`, `merge_cues`, `split_long_line`, `segment`, `ApiError`, `fetch_transcript`) and `GET /api/transcript`.
  - **New** `POST /api/youtube {url, language}` returns `{video: {video_id: "yt:<id>", youtube_id, browser_playable}, job}`. The job uses the same pipeline as uploads, `get_transcript` with an audio callable: stage **"downloading_audio"** (with progress and the video title from yt-dlp), then "extracting_audio", then loading, transcribing, processing and preparing. A cached transcript skips the download.
- **Stage reporting:** `get_transcript` no longer reports "extracting_audio" itself; each audio source reports its own stages. Jobs carry a `title`.
- **Frontend:**
  - **Load** posts to `/api/youtube` and uses the same processing panel, showing "Downloading audio NN%", the title, the device, Cancel, and the CPU warning. When done, practice starts with the YouTube player through the shared interface.
  - **New records** use `yt:<id>` keys. Pre-Phase-6 records (bare ID) still open from Recent; the Phase 7 migration clears them.
  - The caption-language error display is gone, and the language box is the spoken language for Whisper (empty = detect).
  - `app.js?v=4`.
- **Requirements:** `youtube-transcript-api` and `pytest` moved to the new `requirements-dev.txt`, which includes `requirements.txt`. **App code does not import the caption library**; a test enforces it for `app.py`, `config.py`, `ai/` and `services/`. The reference fetcher is now a committed tool, `tests/tools/fetch_references.py` (dev only), and it keeps the human-verified lines when refetching.
- **Old caption cache files** (`cache/<id>.json`, 7 files) are **untouched**: modification dates are still Oct 29–31 and nothing reads them. They are left for the Phase 7 migration.
- **README:** how it works (audio only), files, dev requirements, troubleshooting for yt-dlp, blocked, restricted and CPU cases, and the Ctrl+Enter wording.

### Found and fixed during real testing
1. **Subtitle-credit hallucination:**
   - **Problem:** large-v3-turbo wrote **"Tanya Cushman Reviewer."** over a TED talk's intro music (no_speech_prob 0.00, word probability 0.67, isolated: speech starts 10.5 s later). This is the classic credit text Whisper learned from subtitled videos, and no rule caught it.
   - **Fix:** `ISOLATED_CREDIT_WORDS` (reviewer, translator, transcriber, subtitles, captions, …). An isolated segment containing one, with low word probability, is flagged `isolated_stock_phrase`; the word inside normal speech is kept. `PROCESSING_VERSION = 6`.
   - **Verified:** the cached TED transcript was reprocessed without re-downloading, and the credit is excluded.
2. **yt-dlp wording:** yt-dlp now says "This video is unavailable", which fell through to the generic message. Added, with a test.
3. **Temp files after a hard kill:**
   - **Problem:** temp files were left in `/tmp` if the process died mid-job (found because the pytest worker thread was killed at exit). `finally` blocks don't run on a hard kill.
   - **Fix:** `remove_stale_temp_files()` deletes the app's `dictation-audio-*` / `dictation-youtube-*` items older than 6 h, at startup and daily. The tests now wait for background jobs (`JobManager.wait_idle()`), and no temp files are left after test runs (checked twice).

### Tests run (real results)
- **pytest: 204 passed** (twice, 0 temp files left). Caption tests (`test_segment.py`) removed. New `tests/test_youtube_audio.py` (fake yt-dlp):
  - **IDs and errors:** link formats including `&list=`; error mapping for 9 messages
  - **Download options and cleanup:** audio only, no subtitles, no cookies, Deno passed; progress; the temp folder is deleted, including after a cancel; live and unavailable errors; an invalid ID is rejected before yt-dlp is created
  - **Route:** `/api/youtube` validation and a job with the download stage and title; a friendly failure
  - **Guard:** app code never imports the caption library
  - **Other new tests:** isolated subtitle credit; stale temp cleanup
- **Real YouTube, browser** (headless Firefox, scratch data folders):
  - **"Me at the zoo" (`jNQXAC9IVRw`):** Downloading audio → Loading the AI model → Transcribing (GPU cuda float16) → practice in **8.6 s**, header "large-v3-turbo · GPU float16".
    - Four sentences with timings, starting "Alright, so here we are, one of the elephants."
    - The YouTube player paused at line end, Enter played, and the title came from the player.
  - **Reopen from Recent:** cached, 0.1 s.
  - **Unavailable video (`aaaaaaaaaaa`):** now "This video is unavailable (removed, private, or blocked in your country). Try updating yt-dlp (see the README), or upload the video file instead."
  - **Invalid link:** "That doesn't look like a YouTube video link."
  - **TED talk, 14 min (`arj7oStGLkU`):** audio download 1.3 s (13 MB, opus), transcription 16.7 s (843.7 s of audio, 2280 words), **practice after 23.7 s**, 236 sentences after the credit fix (2 marked unclear). Paused at the line end.
  - **Browser console:** no errors.
- **Real cancel** (`Jnw4J8Wj3k4`, 20 min, curl): cancelled at 21% of transcription. Status `cancelled`, **no layer-1 entry**, **no temp folder or WAV** left.
- **Syntax:** Python 3.10 parse of all files OK; `node --check` OK.

### Not done / next (Phase 7)
- The engine still uses the old `lines` format through the adapter, and the old padding (0.2 / 0.15 s). Still to come: `play_start`, clamping, the clarity badge and the stats rule for skipped unclear sentences, progress tied to `transcript_version`, and the clean-start migration (back up and clear old localStorage records and the old caption cache).

## Phase 7: in progress (Nov 1, 2026), stopped at the usage limit

### Done
- **Decisions applied:**
  - `SENTENCE_CLARITY_MIN = 0.8`, which also marks misheard lines.
  - `INITIAL_PROMPT = ""`; the prompt-leak filter is kept.
  - PLAN.md 0.1 (items 10 and 15), 6A and the section 11 example updated.
- **Main fixtures** re-transcribed with the defaults (large-v3-turbo, no prompt, VAD off). The turbo + prompt ones moved to `experiments/*_turbo_prompt.json`, and the old experiment-C turbo runs (same settings) were removed. WER: conversation 4.9%, movie 11.9%, numbers 3.9%. Song: nothing excluded (no prompt to leak). Silence: "Thank you." excluded.
- **`play_start` (layer 2)** is implemented. A sentence whose first word is longer than `STRETCHED_FIRST_WORD_SECONDS` (1.0) gets `play_start = word end − 1.0`. It is never earlier than `start`, stored timestamps are unchanged, and each adjustment is logged. `PROCESSING_VERSION = 7`. Tested with the real "they're" case (small movie fixture: start 271.26 → play_start 276.38; captions put the line at 277.16).
- **Read-only inventory of the real data** (nothing modified):
  - `cache/`: 7 old caption files. No `uploads/`.
  - **Browser:** Firefox (no Chrome installed). Real profile `lv6td6v4.default` has localStorage for `localhost:8000`. A copy was decoded in the scratchpad (sqlite + a pure-Python snappy decoder). It holds three keys:
    - `dictation:video:H-_Wx7VETxE` ("Frozen – Meeting Olaf Clip (HD)", line 33 of 33, stats for 32 lines, 97 missed words)
    - `dictation:video:cpbARmnt_nU` (the Rise of the Guardians clip, line 103 of 103, stats for 103 lines, 106 missed words, offset −0.5)
    - `dictation:sound` (volume 6, not muted)
- **Already true in the engine:** stats are recorded only when a sentence is completed. Next/Previous never record, so skipped sentences (unclear or not) are already excluded from WPM/accuracy. This still needs an explicit test.

### Left for Phase 7
1. **Migration:**
   - **Server** (`services/migration.py`): back up `cache/*.json` to `backup/pre-audio-pipeline-YYYYMMDD-HHMM/` with a manifest (sha256), clear them, and record a data version so it runs once.
   - **Browser:** on first load, POST all `dictation:*` keys to the server backup folder, download a JSON copy, then clear the old bare-ID `dictation:video:*` records (keeping sound and theme), set `dictation:dataVersion`, and show a one-time notice.
   - **Before wiring it:** dry-run on a scratch copy (real `cache/` copy + the decoded localStorage export), show what would be backed up and cleared, test restoring, and document how to restore.
2. **Frontend:**
   - the engine reads the unified `sentences` directly
   - padding 0.1 / 0.25 s (configurable) with clamping to the neighbouring sentences, plus the offset on top
   - `play_start`
   - the "unclear audio" badge
   - progress tied to `transcript_version`: on a mismatch, reset the position, keep the stats and show a notice
3. **Testing and docs:**
   - a section 15 browser regression on YouTube and uploaded videos
   - the manual test checklist
   - README restore instructions

## Phase 7: integration with the dictation engine, completed (Nov 1, 2026)

### Migration (clean start, PLAN.md section 13)
- **Server: `services/migration.py`** runs once at startup (`data_version.json`, `DATA_FORMAT_VERSION = 2`).
  - **What it touches:** it finds old-format caption files only (`cache/<id>.json` with `videoId` + `lines` and no `cache_format`). New `cache/asr/` and `cache/transcripts/` entries and unrelated files are never touched.
  - **Backup:** it copies them to `backup/pre-audio-pipeline-<date-time>/cache/`, **verifies each copy's SHA-256**, and writes `manifest.json` and a `README.txt`.
  - **Clearing:** only then does it delete the originals. If any copy mismatches, nothing is deleted and it retries at the next start.
  - **CLI:** `python -m services.migration --dry-run` and `--restore DIR`, which checks checksums.
  - **Routes:**
    - `GET /api/migration`: the plan or the state
    - `POST /api/migration/browser-backup`: stores the page's copy
    - `GET /api/migration/browser-backup/<name>`: for restoring
    - `GET /api/config`: padding values
- **Browser, on first visit:**
  - **Scope:** if old bare-ID records (`dictation:video:<11-char id>`) exist, it collects **all** `dictation:*` keys.
  - **Backup:** it POSTs them to the backup folder; only after the server confirms does it download `dictation-old-progress-backup.json`.
  - **Clearing:** it removes the old records (progress, stats, missed words, recent videos, offsets) and sets `dictation:dataVersion = 2`.
  - **Kept:** sound and theme settings, and any new `yt:` / `local:` records.
  - **Notice:** a one-line notice is shown. If the backup POST fails, **nothing is cleared**.

**Dry run on a COPY of the real data** (scratch folder; the real data was never modified):
- **Real data found (read-only):**
  - `cache/`: the 7 old caption files
  - **Firefox:** your real profile `lv6td6v4.default` (Chrome is not installed). Its localStorage for `localhost:8000` was copied and decoded with sqlite plus a small pure-Python snappy decoder (scratchpad tool `firefox_ls_export.py`). It contains:
    - `dictation:video:H-_Wx7VETxE`: "Frozen – Meeting Olaf Clip (HD)", line 33/33, stats for 32 lines, 97 missed words
    - `dictation:video:cpbARmnt_nU`: the Rise of the Guardians clip, line 103/103, stats for 103 lines, 106 missed words, offset −0.5
    - `dictation:sound`: volume 6, not muted
  - **Also found:** the real `cache/` now also has `asr/` and `transcripts/` folders from today 00:23–00:34 (`JLsz5vV3Q08`, `Vv9KJYUnVvA`), apparently from you using the Phase 6 version. They are new-format and are kept.
- **Dry-run plan:** back up and remove 7 files (`H-_Wx7VETxE` 4063 B, `JLsz5vV3Q08` 3229 B, `arj7oStGLkU` 31548 B, `cpbARmnt_nU` 10209 B, `haXeyBHW2l0` 4390 B, `jNQXAC9IVRw` 649 B, `yIGhoZDFYBA` 9642 B), with checksums listed.
- **Startup migration on the copy:** the 7 files went into `backup/pre-audio-pipeline-…/cache/` with the manifest and README, then were removed from the copied `cache/`.
- **First page visit** (fresh headless Firefox profile, your real localStorage loaded onto the origin first):
  - notice: "Transcripts now come from the audio, so your old caption-based progress (2 videos) was backed up to backup/…/localStorage-….json (and downloaded) and cleared. Your sound and theme settings were kept."
  - afterwards, localStorage held only `dictation:sound` (volume 6) and `dictation:dataVersion`; Recent videos was empty
  - a second visit did nothing
- **Restore verified:**
  - the server copy and the downloaded copy of localStorage are **identical** to your original export
  - the documented console restore brought back exactly the original items
  - `restore_cache` brought back all 7 cache files **byte-identical** to the real ones
- **Wired** to run once on real startup (`python app.py`) and on the first page visit. **It has not run on your real data**: there is no `backup/` or `data_version.json` in the project. It runs the next time you start the app.
- **README:** "The switch to audio transcription" and "Restoring the old data" (cp or `--restore`, `git switch --detach cd42f31`, console snippet).

### Engine integration
- **Data:** the page uses the unified transcript directly (`sentences` with `start`, `end`, `play_start`, `clarity`, `unclear`). The `lines` adapter is gone.
- **Playback window:**
  - **Start:** `play_start − 0.1 s`, but never before the previous sentence's end.
  - **End:** `end + 0.25 s`, but never past the next sentence's start, and never before this sentence's own end.
  - **Offset:** applied on top.
  - **Settings:** `PAD_BEFORE_SECONDS` / `PAD_AFTER_SECONDS` in config.py, sent to the page by `/api/config`.
- **`play_start`** is used for stretched first words.
- **"unclear audio" badge** above the sentence when `unclear` (clarity < 0.8). Skipping with Next records nothing, because stats are only recorded on completion (now commented and tested).
- **Progress tied to `transcript_version`:**
  - **On a mismatch:** the position goes to 0, the old per-sentence stats move to `archivedStats` (still counted in the video average, with missed words kept), and a notice says the transcript was regenerated.
- **YouTube pause fix** (found by the regression):
  - **Problem:** a pause requested while YouTube was buffering after a seek only took effect up to ~0.6 s later, so the icon and a quick second Enter acted on the old state.
  - **Fix:** the adapter now remembers the request, reports "paused" immediately, and pauses again if YouTube starts playing anyway.

### Tests run (real results)
- **pytest: 217 passed.** That is 206 + 11 new migration tests:
  - dry run changes nothing
  - backup, verify, then clear, once
  - new-format and unrelated files kept
  - fresh install
  - a mismatching copy means nothing is deleted
  - browser backup next to the cache backup, or in a new folder
  - bad data rejected
  - byte-for-byte restore
  - routes
- **Section 15 regression in headless Firefox: 66 of 68 checks passed, 0 console errors.** Run on **YouTube** ("Me at the zoo", 4 sentences) and an **uploaded video** (4-minute conversation MP4, 70 sentences). Passed on both:
  - **Dictation:** sentence window = play_start−0.1 … end+0.25 clamped to the next sentence; plays once and pauses at its end; doesn't move on by itself; video time shown.
  - **Typing:** correct key advances (key sound); wrong key shows red (error sound); Backspace deletes a correct letter; spaces are required.
  - **Hints:** Ctrl+H reveals a purple letter (hint sound, rgb(124,58,237)); 3 wrong keys auto-reveal.
  - **Colors:** done words teal (rgb(13,148,136)).
  - **Completion and stats:** completing moves on and plays the next sentence; line stats and video average shown; missed words recorded; progress bar fills.
  - **Controls:** Replay; Play/Pause and Enter, each taking effect within 0.05 s; Next; Previous; the Hint button.
  - **Skipping:** a sentence skipped with Next adds no stats.
  - **Settings:** offset +0.1 shifts the window; Ctrl+M mutes; the volume slider sets and saves the volume.
  - **Saved data:** Recent videos lists both after a reload; reopening restores the saved position (index 2 and 64).
  - **Regenerated transcript:** position reset, stats kept (archived and still in the video average), notice shown.
  - **Clarity:** "unclear audio" badge on an unclear sentence ("Well..." clarity 0.794), with skip → no stats change; hidden on clear ones.
  - **Not passed (2):** **Fullscreen** was refused by headless Firefox, which needs a real display and user gesture. It is on the manual checklist.
- **Syntax:** Python 3.10 parse OK; `node --check` OK.

### Manual test checklist (things only a human can judge)
1. **First start:** run `python app.py`.
   - The log says the old caption cache (7 files) was backed up to `backup/pre-audio-pipeline-…`.
   - Open http://localhost:8000 in Firefox: a notice, a downloaded `dictation-old-progress-backup.json`, the two old videos gone from Recent videos, volume still 6.
   - Glance at the backup folder.
2. **Sentence boundaries:** on a movie clip (e.g. the Rise of the Guardians clip) and a YouTube talk, play about 10 sentences each. Each should start at the sentence's beginning and end after its last word, with no words from the neighbouring sentences.
3. **Last word not cut:** listen especially to sentence endings followed immediately by the next sentence. Report the video and time if a word is clipped (padding can be tuned in `config.py`).
4. **Stretched first words:** sentences after music shouldn't begin with seconds of music before the words.
5. **Colors** in both light and dark theme: teal finished words, orange current word, purple hinted letters, a wrong key red in the word box.
6. **Hints:** Ctrl+H reveals one letter, twice in a row the rest of the word; 3 wrong keys on one letter reveal it.
7. **Sounds:** key, error and hint sounds are audible and pleasant at your volume. Ctrl+M mutes and unmutes (icon changes). The volume slider changes loudness and is remembered after a reload.
8. **Fullscreen:** on YouTube and on an uploaded video, the whole app goes fullscreen, typing still works, and Esc exits.
9. **Timing offset:** −/+ shifts when sentences start and stop, and the value is remembered for that video after a reload.
10. **Previous / Replay / Next / Play-Pause buttons**, and Enter, Tab, Shift+Tab (slow), Ctrl+←/→: each does what it says. Pressing Enter right after Replay pauses immediately.
11. **"unclear audio" badge:** does it appear on lines that really are hard to hear or misheard, and not on clear ones? Skipping one with Next leaves "Video average" unchanged.
12. **Saved progress:** do a few sentences, reload the page, and reopen the video from Recent videos. You should be at the same sentence with stats and missed words kept.
13. **Upload a video of your own** (e.g. an MP4 from your phone). It processes, plays and behaves like a YouTube video. If it's HEVC/MOV, Convert works.
14. **Recent videos:** YouTube and uploaded entries show titles and filenames, and reopen instantly from the cache.
15. **Typing feel:** Backspace across words, auto-filled punctuation, required spaces, accents and curly quotes not counted as mistakes.
16. **Deleting a video:**
    - The 🗑 on a Recent videos entry asks for confirmation, naming the video.
    - **Undo** within 8 s keeps everything.
    - Reloading during the 8 s keeps everything.
    - Letting the 8 s pass removes it. Reopening it from YouTube transcribes it again; an uploaded file has to be uploaded again.
    - Other videos and their stats are unaffected.

### Files changed
- New: `services/migration.py`, `tests/test_migration.py`.
- Changed:
  - code: `app.py`, `config.py` (migration paths, `DATA_FORMAT_VERSION`, padding), `ai/transcript_processor.py` (`play_start`, in part 1)
  - frontend: `static/app.js`, `static/style.css`, `index.html` (`app.js?v=6`, `style.css?v=3`)
  - other: `.gitignore` (`data_version.json`), `README.md`, `docs/PROGRESS.md`

### Not done / next (Phase 8)
- The section 18 test list in full, especially playback-timing judgements on real listening (the checklist above) and the media tests not yet covered (speech over background music, fast speech on a full video).
- The fullscreen check needs a real browser window.

## Deleting a video (added before Phase 8, Nov 1, 2026)

### Answers first
- **Checklist results:** none received yet, so there were no failed items to fix first. Any items that fail when you run the checklist will be fixed before Phase 8.
- **Overall stats across videos:** **none exist.** Every figure (WPM, accuracy, missed words, the per-video average in Recent videos) is stored in that one video's browser record and computed from it. Deleting a video changes no other figures, so nothing needed implementing for that part.

### Done
- **PLAN.md:** decision 16 (what is deleted, confirm + undo, edge cases, ID validation).
- **Server:** `DELETE /api/videos/<id>`.
  - **ID validation:** only `yt:<11 chars>` or `local:<64 hex>` (`is_valid_video_id`); anything else gets 400.
  - **Jobs first:** it **cancels** the video's queued or running jobs (transcription, download, conversion) via `JobManager.cancel_where()`, waits for them to stop (`wait_finished`, up to 60 s, otherwise 409 "try again"), then deletes:
    - **`transcript_cache.delete_cached()`:** every layer-1 and layer-2 file named `<id>__*.json`, only directly inside `cache/asr/` and `cache/transcripts/` (resolved-path check)
    - **`uploads.delete()`:** for an upload, the stored file, conversion leftovers and the index entry, only inside `uploads/`
  - **Never touched:** backups, old caption files, other videos.
- **Browser:**
  - a 🗑 button on each Recent videos entry, and a confirmation dialog naming the video (title or filename, saying what gets deleted)
  - after confirming, the entry disappears and the notice shows "… will be deleted." with **Undo** for 8 s
  - the pending deletion is kept only in memory, so a reload or closed page deletes nothing
  - when the window ends: leave the video if it is open or being processed, call the server, and only after it succeeds remove the browser record (progress, stats, missed words, offset); on a server error, the entry comes back with a message
  - pre-Phase-6 records (bare IDs) are removed in the browser only
- **YouTube download cancel made responsive** (found while testing deletion):
  - **Problem:** YouTube held a connection for 14 s without data, and yt-dlp's progress hook, the only place that checked for cancellation, didn't run during that time. A cancel (Cancel button or deletion) waited up to 14 s.
  - **Fix:** yt-dlp now runs in a background thread. The job checks for cancellation every 0.25 s, and a stopped download is abandoned at once (its thread ends at its next progress update; the temp folder is deleted).
  - **Measured on a real download:** cancel requested at 2.0 s took effect at 2.00 s, with no temp folder left.

### Tests run (real results)
- **pytest: 231 passed.** New:
  - **`test_delete_video.py` (13):**
    - **YouTube:** all variants in both layers deleted, other videos, old caption files and backups untouched, a second delete is harmless
    - **Upload:** file, conversion leftover, index entry and both layers deleted; `/media` → 404
    - **Running job:** cancelled first, then deletion; the other video's job left alone
    - **Invalid IDs and path tricks** (9) → rejected and nothing deleted; the helpers refuse bad IDs
  - **YouTube download:** a stalled download (no data) is cancelled within 1 s.
- **Browser, headless Firefox, real YouTube + upload (scratch data): 13 of 13 passed.**
  - **Dialog and undo:** the dialog names the video; Cancel keeps everything; the entry hides with an Undo notice; Undo brings it back and after 9 s nothing is deleted; a reload during the window brings it back and deletes nothing.
  - **Upload deleted:** after the window, the stored file, index entry and both cache layers are gone, along with the browser record. The YouTube video's data is untouched.
  - **Deleting during a running download:** the job was cancelled 26 ms after the request, and everything was deleted 8.2 s after confirming.
  - **YouTube video deleted:** all variants and the record are gone.
- **Section 15 regression re-run: 66 of 68.** The same 2 fullscreen items fail (headless only). The console showed 2 messages from YouTube's own player script ("postMessage … target origin does not match"), which appear when a YouTube player is created and removed quickly. They are harmless and not from the app's code.

### Real migration observed
**You started the app during this session**, so the real one-time migration ran at 01:12:07:
- **Server:** `backup/pre-audio-pipeline-20261101-011207/cache/` holds all 7 old caption files. Each matches its manifest checksum and is **byte-identical** to the originals (compared with the dry-run copies); the originals were then removed from `cache/`.
- **Browser:** at 01:12:14 a page at **`http://127.0.0.1:8000`** backed up its 8 keys (old records for 4 videos, sound, theme, two new `yt:` records) to `localStorage-20261101-011214-541291.json`, then cleared the old ones.
- **Note:** browsers treat `127.0.0.1:8000` and `localhost:8000` as different sites with separate storage. The `localhost:8000` data (Rise of the Guardians, Frozen, volume 6) is still in Firefox and will be backed up and cleared the first time you open `http://localhost:8000`.

### Files changed
- New: `tests/test_delete_video.py`.
- Changed:
  - server: `app.py`, `services/transcript_cache.py`, `services/uploads.py`, `services/jobs.py`, `services/youtube_audio.py`
  - frontend: `static/app.js`, `static/style.css`, `index.html` (`app.js?v=7`, `style.css?v=4`)
  - tests: `tests/test_youtube_audio.py`
  - docs: `README.md`, `docs/PLAN.md`, `docs/PROGRESS.md`

## One address: localhost:8000 redirects to 127.0.0.1:8000 (Nov 1, 2026)

### The localhost:8000 backup (checked before adding the redirect)
- **Not written by the page.** `backup/` held only the `127.0.0.1:8000` browser backup (01:12:14). The only downloaded copy, `~/Downloads/dictation-old-progress-backup.json` (01:12:14), is that same backup.
- **Firefox's storage for `localhost:8000` was unchanged since Oct 31 02:50** (fresh read-only copy). It still held the old records, so the page's migration had not run there. Most likely the app wasn't running when that address was opened, or the backup request failed. Either way the safe default held: nothing was cleared without a backup.
- **Why it matters now:** once localhost redirects, the app can never run on that address again, so I wrote the backup myself. `backup/pre-audio-pipeline-20261101-011207/localStorage-localhost-8000-20261101-014802.json` is decoded from the read-only copy, in the same format as the page's backups, and is identical to the export. It contains:
  - "Frozen – Meeting Olaf Clip (HD)": 33/33, stats for 32 sentences, 97 missed words
  - "Rise of the Guardians" clip: 103/103, stats for 103 sentences, 106 missed words
  - sound settings
- **The stale data** stays in Firefox under `localhost:8000`, unused. It can be removed in Firefox: Settings → Privacy & Security → Cookies and Site Data → Manage Data… → localhost.

### Redirect
- **`app.py` `before_request`:** a request whose Host is `localhost:<port>` gets **307** to `http://127.0.0.1:<port><path>?<query>`.
  - Path and query are kept, and 307 keeps the method and body.
  - A host without a port (Flask's test client) is not redirected.
  - `config.CANONICAL_HOST = "127.0.0.1"`; the server binds to it.
- **Startup log** shows only the canonical address: "Open http://127.0.0.1:8000 in your browser (localhost:8000 redirects there)". Flask's own banner line is already hidden by the werkzeug log level.
- **README:** it uses http://127.0.0.1:8000, with one sentence explaining the redirect. The restore steps use the exact address, since the caption-based version has no redirect.

### Tests run (real results)
- **pytest: 238 passed.** New `tests/test_canonical_host.py` (7):
  - path + query kept (4 cases, including an encoded query and a `local:` media path)
  - POST redirected with 307 on another port, case-insensitive host
  - 127.0.0.1 served directly
  - the test client's portless host not redirected
- **Real start of `python app.py`:**
  - log line as above
  - `curl localhost:8000/?a=1` gave 307 → `http://127.0.0.1:8000/?a=1`, and `127.0.0.1:8000` gave 200
  - headless Firefox opening `http://localhost:8000/static/style.css?v=4` ended at `http://127.0.0.1:8000/static/style.css?v=4`, and opening `/` ended at `http://127.0.0.1:8000/` with the app loaded
- **Real data after that start:** unchanged. The migration marker still records the 01:12 run; `cache/` has `asr/` and `transcripts/` only; the backup folder holds both browser backups.
