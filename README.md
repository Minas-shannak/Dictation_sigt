# Dictation

A local web app for practising listening and typing: you give it a video, it plays one sentence at a time, and you type what you hear.

---

## 1. Project Overview

### What is it?

Dictation turns any YouTube video, or a video file of your own, into a typing exercise. The app plays a short sentence from the video, you type it, and then it moves on to the next sentence. As you go, it tracks your speed, your accuracy and the words you got wrong.

### What problem does it solve?

Dictation (listening to real speech and writing it down) is a good way to train your listening and spelling in a language. Doing it by hand is slow, though: you have to pause the video, rewind, check your answer against the subtitles, and so on. Subtitles are often missing, badly timed or different from what is actually said.

This app does that work for you. It makes the exercise text from **what is actually said in the audio**, splits it into sentences with exact timings, and checks your typing letter by letter.

### Main goal

To let you practise dictation with **any** video you like, using accurate, sentence-by-sentence exercises made from the real audio. It never relies on captions.

### How it works (high level)

```
YouTube link or video file
        │
        ▼
1. Get the audio         yt-dlp (YouTube) or FFmpeg (uploaded file)
        │
        ▼
2. Speech to text        Whisper AI model → words with timestamps
        │
        ▼
3. Clean up              remove AI mistakes, filler words; numbers → words
        │
        ▼
4. Split into sentences  short lines, each with a start and end time
        │
        ▼
5. Practise              the browser plays a line, you type it
```

The result of steps 1–4 is cached, so each video is only transcribed once.

---

## 2. AI Technology

### The AI model

| Item | Value |
| --- | --- |
| Model | **Whisper `large-v3-turbo`** (OpenAI's open speech recognition model) |
| Task | Speech recognition: turns spoken audio into text, with a timestamp for every word |
| Where it runs | **Locally on your computer.** No external AI API, no API key, no account |
| Hardware | NVIDIA GPU if available (used about 2.3 GB of GPU memory in testing), otherwise the CPU (same model, much slower). A GPU is optional |
| Download | Downloaded automatically once, on the first transcription (about 1.6 GB, Hugging Face repo `mobiuslabsgmbh/faster-whisper-large-v3-turbo`), into `~/.cache/huggingface/hub`. Needs internet only that first time |

The model name is set in `config.py` (`WHISPER_MODEL`).

### AI libraries vs. other technologies

| Type | Name | Role |
| --- | --- | --- |
| **AI model** | Whisper `large-v3-turbo` | Recognizes the speech in the audio |
| AI library | `faster-whisper` | Loads and runs the Whisper model in Python |
| AI library | `ctranslate2` | The fast inference engine `faster-whisper` uses (GPU or CPU) |
| AI library (GPU only) | `nvidia-cublas-cu12` | NVIDIA's CUDA math library, used only when the model runs on the GPU (installed inside the venv, ~1.1 GB, no system CUDA needed) |
| AI library | `onnxruntime` | A dependency of `faster-whisper`; runs the optional voice-activity filter (off by default) |
| Not AI | `num2words` | Turns numbers into words (`5` → `five`) |
| Not AI | FFmpeg, yt-dlp | Get the audio out of videos |

The rest of the processing (cleaning, sentence splitting) is ordinary, rule-based Python code, not AI.

### How the AI turns audio into exercises

1. **Input:** the video's audio is converted to 16 kHz mono WAV (the format Whisper expects).
2. **Recognition** (`ai/transcriber.py`): Whisper listens to the audio and returns text segments. Every word comes with a start time, an end time and a confidence score (how sure the model is).
3. **Hallucination filter** (`ai/transcript_processor.py`): over music or silence, Whisper sometimes "invents" text such as *"Thanks for watching"*. The app uses Whisper's own confidence values and a list of known phrases to remove these.
4. **Cleanup:** filler words (*um*, *uh*) and sound labels (*[Music]*) are removed, and numbers become words.
5. **Sentences:** the words are grouped into short sentences (at most 16 words / 8 seconds), using punctuation and pauses.
6. **Clarity score:** each sentence gets the average confidence of its words. Below 0.8, the app shows an **unclear audio** badge, because the AI may have misheard it.
7. **Output:** a list of sentences with word timings, saved as JSON in `cache/` and sent to the browser.

If the GPU fails (for example a missing CUDA library), the log explains why and transcription continues on the CPU automatically.

---

## 3. Key Features

- **Practise with any YouTube video:** paste a link and press **Load**.
- **Practise with your own videos:** press **Upload a video…** (MP4, MOV or WebM, up to 2 GB). Videos that browsers can't play (for example HEVC or ProRes) can be converted to H.264 with one click.
- **One sentence at a time:** each sentence plays, then waits for you. The next one starts only after you finish.
- **Letter-by-letter checking:** letters and digits are shown as dots. Punctuation is filled in for you, and case and accents don't matter. Wrong keys flash red.
- **Hints:** reveal the next letter (Ctrl+H). After 3 wrong tries on one letter, it is revealed automatically.
- **Statistics:** words per minute (WPM), accuracy and a list of missed words, per video. Finishing a video shows an **End of tape** summary.
- **Saved progress:** your position, stats and settings are saved in the browser, per video. The start page lists recent videos as cassettes that fill up as you progress.
- **Background jobs with progress:** downloads, transcriptions and conversions show real progress and can be cancelled. The app shows whether it is running on the GPU or the CPU.
- **Caption timing:** shift a video's sentence timing by −0.50 s to +0.50 s if lines start too early or late.
- **Light and dark themes:** follows your system setting, with a toggle button.
- **Delete a video:** removes its progress, cached transcripts and (for uploads) the stored file, with an 8-second **Undo**.

### Keyboard shortcuts

| Keys | Action |
| --- | --- |
| Enter | Play / pause, or replay a finished line |
| Space | Finish the current word |
| Backspace | Delete the last letter or space on this line |
| Tab | Replay the current line |
| Shift+Tab | Replay at 0.75× speed |
| Ctrl+→ / Ctrl+← | Next / previous line |
| Ctrl+H | Hint: reveal the next letter (press twice to reveal the whole word) |
| Ctrl+Enter | Accept the current word as it is (when the transcript got it wrong) |
| Ctrl+M | Mute / unmute typing sounds |

Shortcuts can be changed in the `SHORTCUTS` object at the top of `static/app.js`.

### How the stats are calculated

- **WPM:** (characters typed / 5) per minute of typing. Time while the video is playing doesn't count, and hinted letters don't count.
- **Accuracy:** correct keystrokes / all letter, digit and space keystrokes.
- **Missed words:** any word where you made a mistake or used a hint.
- Skipped sentences (for example unclear ones) never affect the stats.

---

## 4. Technology Stack

| Technology | Purpose |
| --- | --- |
| **Python 3.12** | Backend language |
| **Flask** | Web server: serves the page and the JSON API |
| **faster-whisper + CTranslate2** | Runs the Whisper speech recognition model (see [AI Technology](#2-ai-technology)) |
| **NVIDIA cuBLAS** (pip package) | Lets the model run on an NVIDIA GPU |
| **onnxruntime** | Voice activity detection (optional, off by default) |
| **num2words** | Writes numbers as words |
| **yt-dlp** | Downloads the audio-only stream of a YouTube video |
| **Deno + yt-dlp-ejs** (pip packages) | JavaScript runtime that yt-dlp needs to pass YouTube's checks |
| **FFmpeg / ffprobe** (system) | Checks video files, extracts audio, converts videos |
| **av** (PyAV) | Audio decoding used by faster-whisper |
| **HTML, CSS, JavaScript** (no framework) | The frontend: player, typing, sounds, stats |
| **YouTube IFrame Player API** | Plays YouTube videos in the page |
| **Browser localStorage** | Stores your progress and stats |
| **uv** | Creates the virtual environment and installs packages |
| **pytest** | Tests (development only) |

---

## 5. Installation and Setup

The instructions below are for **Linux** (Ubuntu/Debian, x86_64). The GPU setup loads Linux libraries, so other systems can only use the CPU, and they haven't been tested.

### Required

| Requirement | Why | Check |
| --- | --- | --- |
| git, curl | Get the code and the uv installer (not always installed on a fresh Ubuntu) | `git --version`, `curl --version` |
| FFmpeg (includes ffprobe) | **Needed for every video, YouTube and uploads alike:** it checks files, extracts the audio for Whisper and converts videos | `ffmpeg -version`, `ffprobe -version` |
| [uv](https://docs.astral.sh/uv/) | Installs Python 3.12 and the packages | `uv --version` |
| Firefox or Chrome | To use the app | |
| Internet connection | First model download, YouTube videos, the YouTube player, web fonts | |
| About 5 GB free disk space | Whisper model (~1.6 GB), virtual environment (~1.6 GB, of which ~1.1 GB is the CUDA library) and uv's package cache (another 1–2 GB) | `df -h .` |

You do **not** need to install Python, Deno, the CUDA Toolkit or cuDNN yourself: uv and `requirements.txt` take care of everything the app uses. No API key or account is needed.

### Optional (faster transcription)

| Requirement | Why | Check |
| --- | --- | --- |
| NVIDIA GPU + NVIDIA driver | Transcription on the GPU is much faster than on the CPU | `nvidia-smi` |

Without a GPU, everything works on the CPU, just more slowly. See [GPU notes](#gpu-notes-optional).

### Steps

1. **Install the system packages:**

   ```bash
   sudo apt update
   sudo apt install -y git curl ffmpeg
   ```

2. **Install uv** (if you don't have it), then load it into the current terminal:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   source $HOME/.local/bin/env    # or open a new terminal
   uv --version
   ```

3. **Get the code:**

   ```bash
   git clone https://github.com/Minas-shannak/Dictation_sigt.git
   cd Dictation_sigt
   ```

   The repository root is the app folder (it contains `app.py`). All later commands run from here.

4. **Create a Python 3.12 virtual environment:**

   ```bash
   uv venv --python 3.12 .venv
   ```

   uv downloads Python 3.12 if it isn't installed. Other Python versions haven't been tested.

5. **Install the dependencies:**

   ```bash
   uv pip install --python .venv/bin/python -r requirements.txt
   ```

6. **Activate the environment:**

   ```bash
   source .venv/bin/activate
   ```

7. **Check the setup (optional):**

   ```bash
   ffmpeg -version | head -1
   .venv/bin/deno --version                                  # JavaScript runtime for yt-dlp
   python -c "import ctranslate2; print('CUDA devices:', ctranslate2.get_cuda_device_count())"
   ```

   `CUDA devices: 0` means transcription will run on the CPU.

### Automatically downloaded resources

| What | When | Where | How often |
| --- | --- | --- | --- |
| Whisper model `large-v3-turbo` (Hugging Face repo `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, ~1.6 GB) | The first transcription, not at server start | `~/.cache/huggingface/hub/` (set `HF_HOME` to use another folder) | Once |
| A YouTube video's audio | When a YouTube video is transcribed for the first time | A temporary folder, deleted afterwards | Once per video (the transcript is then cached in `cache/`) |
| Google Fonts and the YouTube player script | Every page load | The browser's cache | Handled by the browser |

The voice-activity model (Silero VAD) is included in the `faster-whisper` package and is not downloaded.

To download the Whisper model in advance (for example before going offline):

```bash
python -c "from faster_whisper import download_model; download_model('large-v3-turbo')"
```

To remove it, delete `~/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`.

### Configuration

No `.env` file or API key is needed. All settings are in `config.py`, with working defaults. Two can be changed with environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DICTATION_DEVICE` | `auto` | `auto` = use the NVIDIA GPU if possible, otherwise the CPU. `cpu` = always use the CPU |
| `DICTATION_WHISPER_MODEL` | `large-v3-turbo` | The Whisper model to use. Changing it means videos are transcribed again |

Example:

```bash
DICTATION_DEVICE=cpu python app.py
```

### GPU notes (optional)

- **Only the NVIDIA driver is needed.** The CUDA runtime is built into `ctranslate2`, and the CUDA math library (cuBLAS 12) is installed inside the venv by `requirements.txt`. You don't need the CUDA Toolkit or cuDNN, and you don't need `LD_LIBRARY_PATH`.
- **Driver version:** tested with driver 615.71 on an RTX 5060 Laptop GPU (Blackwell). The pinned `ctranslate2` is built with CUDA 12.8, so a driver that supports CUDA 12.8 or newer (`nvidia-smi` shows "CUDA Version: 12.8" or higher) should work. Other GPUs and older drivers haven't been tested.
- **GPU memory:** the model used about 2.3 GB of GPU memory in testing.
- **Automatic CPU fallback:** with `DICTATION_DEVICE=auto` (the default), the app uses the CPU if no CUDA device is found. It also switches to the CPU for the rest of the session if CUDA fails while loading the model or during a transcription. The terminal log explains why it fell back.
- **Which device is used:** after the first transcription starts, the terminal shows `Loaded Whisper model 'large-v3-turbo' on cuda (float16)` (GPU) or `on cpu (int8)` (CPU). The app also shows the device while it transcribes.
- **Without a GPU,** `nvidia-cublas-cu12` (~1.1 GB) is still installed by `requirements.txt` but never used.
- The very first transcription on a new GPU takes about 20 seconds longer while CUDA prepares itself.

---

## 6. How to Run the Project

1. **Start the server** (from the project folder, with the venv activated):

   ```bash
   python app.py
   ```

   This is the only service you need to run. Transcription runs in the background inside the same process. The server always listens on `127.0.0.1:8000`; the port can't be changed without editing `PORT` in `app.py`.

2. **Open the app:** go to **http://127.0.0.1:8000** in Firefox or Chrome.

   Always use this exact address. The browser saves your progress separately for each address, so `http://localhost:8000` is redirected to it.

3. **Load a video:**
   - Paste a YouTube link and press **Load**, or
   - Press **Upload a video…** and choose a file.

   The small box next to the link is the spoken language (`en` by default; leave it empty to detect the language automatically).

4. **Wait for the first transcription.** The first time you load any video, the Whisper model (about 1.6 GB) is downloaded, which can take a few minutes. After that:
   - With a GPU, transcription is fast.
   - On the CPU it is much slower: on a 16-thread laptop CPU it took about 0.4× the video's length (a 4-minute clip took about 1.5 minutes), and slower CPUs take longer. The app shows "Running on CPU, this will be slow".

   Videos you have already transcribed load instantly from the cache.

5. **Stop the server** with `Ctrl+C`.

### Transcribing from the command line (optional)

You can test the AI part without the web app:

```bash
python -m ai.cli some-video.mp4 --words          # print sentences and word timings
python -m ai.cli some-video.mp4 --language auto  # detect the language
```

### Running the tests (optional)

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
pytest
```

### Keeping YouTube downloads working

YouTube changes often, which can break yt-dlp. If YouTube videos stop loading, update it (then update the version pins in `requirements.txt`):

```bash
uv pip install --python .venv/bin/python -U yt-dlp yt-dlp-ejs
```

### Troubleshooting

| Message | What to do |
| --- | --- |
| *"YouTube audio is unavailable right now"* / *"This video is unavailable"* | Update yt-dlp (see above), or upload the video file instead |
| *"YouTube is refusing downloads from this computer"* | YouTube is limiting your IP address. Wait a while |
| *"The owner doesn't allow this video to be embedded"* | This video can only be played on youtube.com. Pick another one or upload the file |
| *"Running on CPU, this will be slow"* | The GPU couldn't be used. The terminal log says why (for example a missing CUDA library or an old driver) |
| Every video fails with a generic error, or uploads fail, and the log shows `FileNotFoundError: ... 'ffprobe'` or `'ffmpeg'` | FFmpeg isn't installed or isn't on the `PATH`: `sudo apt install ffmpeg` |
| `Address already in use` when starting the server | Another program is using port 8000. Stop it (`ss -ltnp \| grep :8000` shows which one) |
| The first video seems stuck at *"Loading the AI model"* | The Whisper model (~1.6 GB) is being downloaded. This happens only once |
| Private, age-restricted or members-only videos | These can't be downloaded. The app doesn't try to get around this |

---

## 7. Project Structure

```
Dictation_sigt/             (repository root)
├── app.py                  Flask server: the page, static files and the JSON API
├── config.py               All settings (AI model, filters, sentence rules, uploads, ...)
├── index.html              Page markup
├── requirements.txt        Packages needed to run the app
├── requirements-dev.txt    Extra packages for tests
├── ai/                     Speech recognition and text processing
│   ├── transcriber.py          Runs Whisper (GPU with CPU fallback)
│   ├── transcript_processor.py Hallucination filter, cleanup, numbers → words, sentences
│   ├── punctuation.py          Experimental punctuation restoring (not used by the app)
│   └── cli.py                  Command-line transcription
├── services/               Backend helpers
│   ├── youtube_audio.py        Downloads YouTube audio with yt-dlp (never captions)
│   ├── audio_extractor.py      Checks media and extracts/converts with FFmpeg
│   ├── transcript_cache.py     Builds transcripts and caches them
│   ├── jobs.py                 Runs long tasks in the background, one at a time
│   ├── uploads.py              Stores uploaded videos; deletes ones unused for 30 days
│   └── migration.py            One-time upgrade from the old caption-based version
├── static/                 Frontend
│   ├── app.js                  All frontend code (player, typing, stats, saved progress)
│   ├── style.css               All styles, including light and dark themes
│   ├── sounds/                 Typing sounds
│   └── *.svg, *.png            Logos and icons
├── design/                 Design spec (DESIGN.md) and mockups
├── docs/                   Project plan and progress log
├── tests/                  pytest tests, test fixtures and evaluation tools
├── cache/                  Created at runtime (git-ignored)
│   ├── asr/                    Raw Whisper results
│   └── transcripts/            Processed sentences sent to the browser
└── uploads/                Uploaded videos (created at runtime, git-ignored)
```

### Notes for developers

- **Cache:** `cache/asr/` is only rebuilt when the model or recognition settings change (slow, it means transcribing again). `cache/transcripts/` is rebuilt from it when the processing rules change (fast). Deleting `cache/` is safe; videos are then transcribed again.
- **After editing `static/style.css` or `static/app.js`,** increase the version number in its link in `index.html` (for example `app.js?v=9` → `?v=10`) so the browser doesn't use an old cached copy.
- **`data_version.json` and `backup/`:** on every start, `services/migration.py` checks for data from an old caption-based version of the app. On a fresh clone there is none, so it only writes `data_version.json`. Both are git-ignored.

### Restoring the old data

Only relevant if you used the old caption-based version. On its first start, this version backed up that data to `backup/<folder>/` before clearing it:

- **Server cache:** `python -m services.migration --restore backup/<folder>` copies the old caption files back into `cache/`.
- **Browser data** (progress, stats, settings) is in `backup/<folder>/localStorage-*.json` as `{"items": {key: value}}`. To put it back, open the app's page, then run this in the browser's developer console (with that file's name):

  ```js
  const { items } = await (await fetch("/api/migration/browser-backup/localStorage-XXXX.json")).json();
  for (const [k, v] of Object.entries(items)) localStorage.setItem(k, v);
  ```
