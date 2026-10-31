# Dictation

Practice dictation with YouTube videos: paste a link, hear one sentence at a time, type what you hear.

## Setup

Uses Python 3.12 in a venv made with [uv](https://docs.astral.sh/uv/), and FFmpeg from the system.

```bash
sudo apt install ffmpeg            # provides ffmpeg and ffprobe
cd typing-app
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
```

### GPU (optional, NVIDIA)

Speech recognition runs on the NVIDIA GPU when it can, and on the CPU otherwise. No system
CUDA toolkit is needed: only the NVIDIA driver (check with `nvidia-smi`) plus the cuBLAS pip
package in `requirements.txt`, which `ai/transcriber.py` loads from the venv. RTX 50xx
(Blackwell) cards need CUDA 12.8+ builds; the pinned `ctranslate2` has them. The very first
transcription on a new GPU takes about 20 s longer while CUDA compiles and caches its kernels.

If the GPU can't be used, the log says why and how to fix it (for example a missing
`libcublas`), and transcription continues on the CPU. The device in use is logged with every
transcription. To force the CPU, run with `DICTATION_DEVICE=cpu`.

### YouTube audio (yt-dlp)

YouTube requires a JavaScript challenge to be solved, so yt-dlp needs a JavaScript runtime:
Deno is installed into the venv by `requirements.txt` (`.venv/bin/deno`, no system install).
Without it, some videos fail with "This video is not available". Pass it explicitly:

```bash
.venv/bin/yt-dlp --js-runtimes deno:.venv/bin/deno -f bestaudio URL
```

yt-dlp breaks when YouTube changes. To update it (then update the pins in `requirements.txt`):

```bash
uv pip install --python .venv/bin/python -U yt-dlp yt-dlp-ejs
```

### Transcribing a file from the command line

```bash
python -m ai.cli some-video.mp4 --words     # --language auto, --twice
python -m ai.cli tests/fixtures/numbers.json  # reprocess a saved raw result, no transcription
```

The app uses one Whisper model, `large-v3-turbo` (`config.py`). It is
downloaded once (about 1.6 GB) on first use into `~/.cache/huggingface` and needs about 2.3 GB of GPU
memory. Without a working GPU the same model runs on the CPU, which is much slower.

## Run

```bash
python app.py
```

Open http://localhost:8000 in Chrome, paste a YouTube link and press **Load**, or press
**Upload a video…** to practise with a video file of your own (MP4, MOV or WebM, up to 2 GB).

Run the tests with `pytest` (install the dev requirements first:
`uv pip install --python .venv/bin/python -r requirements-dev.txt`).

## Files

- `app.py`: Flask server (the page, `/static/`, and the API for YouTube and uploaded videos, jobs and media)
- `index.html`: page markup only
- `static/style.css`: all styles, including the light and dark themes
- `static/app.js`: all frontend code (player, typing, sounds, stats, saved progress)
- `static/sounds/`: typing sounds (`key.mp3`, `error.mp3`, `hint.wav`)
- `config.py`: settings for speech recognition, audio extraction and transcript processing, with defaults
- `ai/transcriber.py`: faster-whisper speech recognition (GPU with CPU fallback); `ai/cli.py` runs it from the command line
- `ai/transcript_processor.py`: hallucination filter, word cleanup, numbers to words, sentence segmentation
- `tests/fixtures/`: real transcriber output for the test clips with the default settings, used by the tests;
  `tests/fixtures/experiments/` holds other models and settings (the clips themselves stay in the git-ignored `tests/media/`)
- `tests/reference/`: YouTube captions for the test clips, used only as a test reference (the app never reads captions);
  `python tests/tools/evaluate_fixtures.py` compares every fixture against them
- `services/audio_extractor.py`: checks media with ffprobe and extracts 16 kHz mono audio with FFmpeg
- `services/transcript_cache.py`: builds the unified transcript for a video and caches it in two layers
- `services/jobs.py`: runs transcriptions and conversions in the background, one at a time, with progress and cancel
- `services/uploads.py`: stores uploaded videos (`uploads/`, named by content hash) and deletes ones not opened for 30 days
- `services/youtube_audio.py`: downloads a YouTube video's audio-only stream with yt-dlp (never captions)
- `tests/`: pytest tests (run `pytest`); `tests/tools/fetch_references.py` refetches the caption references
- `docs/`: the plan for the audio-based pipeline and the progress log
- `cache/`: `asr/` holds raw speech recognition results (rebuilt only when the model or recognition settings
  change), `transcripts/` the processed transcripts (rebuilt from `asr/` when processing changes, without
  transcribing again). The `<videoId>.json` files directly in `cache/` are the old caption cache; the app no
  longer reads them.

After editing `style.css` or `app.js`, bump the version in its link in `index.html`
(`style.css?v=1` → `?v=2`, `app.js?v=1` → `?v=2`) so Chrome doesn't keep using a cached old copy.

## How it works

- The text always comes from the video's actual audio, never from captions: the audio is downloaded
  (YouTube, with yt-dlp) or taken from the uploaded file, transcribed with Whisper (large-v3-turbo) on
  the GPU, cleaned up and split into sentence-sized lines with word timings. Text Whisper writes for
  music or silence ("Thank you.", subtitle credits, the prompt) is left out. The result is cached, so
  a video is only transcribed once. The small box next to the link is the spoken language (`en`
  by default; empty = detect it).
- Each sentence gets a clarity score (how sure Whisper is of its words). Below 0.8
  (`SENTENCE_CLARITY_MIN` in `config.py`) it shows an **unclear audio** badge: the audio may be hard to
  hear, or Whisper may have misheard it, so the exercise may be wrong. Skip it with **Next** if you like:
  only completed sentences count in the statistics, so skipping never affects them.
- Each sentence plays from 0.1 s before its first word to 0.25 s after its last one
  (`PAD_BEFORE_SECONDS` / `PAD_AFTER_SECONDS`), but never into the neighbouring sentences. If Whisper
  stretched a sentence's first word back over music, playback starts closer to the actual words.
- Letters and digits are hidden as dots. Type the letters and digits of each word, then press
  **Space** to finish the word (the last word of a line needs no space). Punctuation is filled in
  for you, so for "story, of" you type `story`, then Space, then `of`. Case and accents don't matter.
- A wrong key (including a letter where a space belongs, or a space mid-word) flashes red in the
  box under the sentence, which shows only the word you're typing.
- Colors: teal for finished words, orange for the word you're typing, purple for hinted letters.
- A line never plays on its own. The next one starts after you finish the current one.
- Use **Offset** (−0.5 s to +0.5 s) if a video's lines start slightly early or late. It's saved per video.
- The theme follows your system's light/dark setting. The 🌙/☀️ button in the corner overrides it, and the choice is remembered.
- Progress, offset, stats and missed words are stored in the browser's localStorage, per video.
- **Uploaded videos** are checked (type, content, size), stored in `uploads/` and transcribed from their own audio
  in the background. The panel shows each step with real progress and whether the GPU or the CPU is used
  ("Running on CPU, this will be slow" if the GPU can't be used); **Cancel** stops it. Uploading the same file
  again, or opening it from Recent videos, reuses the cached transcript.
- Some formats (HEVC/H.265, ProRes) don't play in browsers. The panel then offers **Convert**, which re-encodes the
  video to H.264 in the background and replaces the stored file; the audio is transcribed either way.
- An uploaded video not opened for 30 days is deleted to save space (its transcript is kept). Recent videos
  then shows "file expired, upload it again".

## The switch to audio transcription (one-time clean start)

The first version made its exercises from YouTube captions. The audio-based sentences are
different, so the old saved data can't be reused. On the first start of this version:

- the server copies the old caption cache (`cache/<videoId>.json`) into
  `backup/pre-audio-pipeline-<date-time>/cache/` (with SHA-256 checksums in `manifest.json`), checks
  the copies, and only then removes the originals; the terminal log lists the files;
- on your first visit, the page sends its saved progress, stats, missed words, recent videos and
  offsets to the same backup folder (`localStorage-<date-time>.json`), also downloads a copy
  (`dictation-old-progress-backup.json`), and only then clears them, with a one-line notice. Sound
  and theme settings are kept.

It happens once (`data_version.json` records it). Progress is now tied to each transcript's version:
if a video's transcript is ever regenerated with different sentences, only your position in it is
reset (with a notice); its statistics are kept.

### Restoring the old data

To go back to the caption-based version with your old data:

1. Stop the app and put the cache files back: `cp backup/pre-audio-pipeline-*/cache/*.json cache/`
   (or, before switching versions, `python -m services.migration --restore backup/pre-audio-pipeline-<date-time>`,
   which also checks the checksums).
2. Switch to the caption-based version: `git switch --detach cd42f31`, install its requirements
   (`uv pip install --python .venv/bin/python -r requirements-dev.txt` covers them) and run `python app.py`.
3. Open http://localhost:8000, press F12, open the Console, and paste (with your backup file's
   contents in place of `BACKUP`):

   ```js
   const backup = BACKUP;  // the contents of localStorage-<date-time>.json or dictation-old-progress-backup.json
   for (const [key, value] of Object.entries(backup.items)) localStorage.setItem(key, value);
   ```

   Reload the page. (In this version, the same file can also be fetched with
   `fetch("/api/migration/browser-backup/localStorage-<date-time>.json")`.)

Back on this version (`git switch main`), the clean start does not run again.

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| Enter | Play / pause: pauses if playing, continues a line paused partway, replays a finished line |
| Backspace | Delete the last letter or space you entered on this line, even a correct one (see below) |
| Tab | Replay the current line |
| Shift+Tab | Replay at 0.75× speed |
| Ctrl+→ | Next line |
| Ctrl+← | Previous line |
| Ctrl+H | Hint: reveal the next letter. Press twice in a row to reveal the rest of the word |
| Ctrl+Enter | Accept the current word as-is (when the transcript got a word wrong) |
| Ctrl+M | Mute / unmute typing sounds (volume slider next to the speaker button) |

After 3 wrong keys on the same letter (or space), it's revealed automatically in purple and counts like a
hint (change `MAX_WRONG_ATTEMPTS` in `static/app.js` to adjust).

Backspace steps back through the current line only: across spaces into the previous word, over
punctuation (removed together with the letter before it), and over hinted letters, which show as
typed if you retype them (the word stays in the missed-words list). It does nothing at the start of
a line or once a line is finished.

## On-screen buttons

Under the video, left to right: previous line, play/pause (continues the current line), replay line,
next line, hint (same as Ctrl+H) and fullscreen (the whole app, so the sentence stays visible).
The bar under them fills as you finish lines, with the video time on the right.

Clicking the video also replays the line. Shortcuts are configured in the `SHORTCUTS` object
at the top of `static/app.js`.

## Deleting a video

Each entry in Recent videos has a 🗑 button. After you confirm, the entry disappears and an **Undo**
notice shows for 8 seconds; only then is everything for that video deleted: its saved progress,
statistics, missed words and timing offset, all its cached transcripts (every model/settings
variant), and for an uploaded video the stored file. A running download, transcription or
conversion for it is stopped first. If you close or reload the page during those 8 seconds,
nothing is deleted. Statistics are per video only, so no other figures change. Backups in
`backup/` are never touched.

## Stats

- **WPM**: (characters you typed on the line / 5) / minutes, counting from your first keystroke on
  the line to its end, minus any time the video was playing. Each character counts once, so
  deleting and retyping doesn't inflate it; hinted characters don't count.
- **Accuracy**: correct keystrokes / all letter, digit and space keystrokes (Backspace isn't counted).
- **Video average**: over all completed lines, weighted by length. Redoing a line replaces its stats.
- **Missed words**: any word where you made an error or used a hint.

## Troubleshooting

- *"YouTube audio is unavailable right now"* or *"This video is unavailable"*: yt-dlp breaks when
  YouTube changes. Update it (see "YouTube audio" above), or upload the video file instead.
  Already-transcribed videos keep working from the cache.
- *"YouTube is refusing downloads from this computer"*: YouTube throttles some IPs. Wait a while.
- *Private, age-restricted or members-only videos* can't be downloaded; nothing tries to get around that.
- *"The owner doesn't allow this video to be embedded"*: that video can't be played outside
  youtube.com. Pick another one, or upload the file.
- *"Running on CPU, this will be slow"*: the GPU couldn't be used; the log says why (for example a
  missing CUDA library). The same model runs on the CPU, about 0.4× real time.
