# Dictation

Practice dictation with YouTube videos: paste a link, hear one sentence at a time, type what you hear.

## Setup

Requires Python 3.10+.

```bash
cd typing-app
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Open http://localhost:8000 in Chrome, paste a YouTube link and press **Load**.

Run the tests with `pytest`.

## Files

- `app.py`: Flask server (serves the page, `/static/` and the caption API)
- `index.html`: page markup only
- `static/style.css`: all styles, including the light and dark themes
- `static/app.js`: all frontend code (player, typing, sounds, stats, saved progress)
- `static/sounds/`: typing sounds (`key.mp3`, `error.mp3`, `hint.wav`)
- `test_segment.py`: tests for caption cleanup and segmentation
- `cache/`: fetched captions, one JSON file per video

After editing `style.css` or `app.js`, bump the version in its link in `index.html`
(`style.css?v=1` → `?v=2`, `app.js?v=1` → `?v=2`) so Chrome doesn't keep using a cached old copy.

## How it works

- `app.py` fetches the video's captions (manual ones first, auto-generated as a fallback),
  cleans them and merges them into sentence-sized lines. The result is cached in
  `cache/<videoId>.json`, so a video is only fetched from YouTube once. Delete the file to refetch.
- Letters and digits are hidden as dots. Type the letters and digits of each word, then press
  **Space** to finish the word (the last word of a line needs no space). Punctuation is filled in
  for you, so for "story, of" you type `story`, then Space, then `of`. Case and accents don't matter.
- A wrong key (including a letter where a space belongs, or a space mid-word) flashes red in the
  box under the sentence, which shows only the word you're typing.
- Colors: teal for finished words, orange for the word you're typing, purple for hinted letters.
- A line never plays on its own. The next one starts after you finish the current one.
- Use **Offset** (−0.5 s to +0.5 s) if a video's captions are slightly early or late. It's saved per video.
- The theme follows your system's light/dark setting. The 🌙/☀️ button in the corner overrides it, and the choice is remembered.
- Progress, offset, stats and missed words are stored in the browser's localStorage, per video.

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
| Ctrl+Enter | Accept the current word as-is (for wrong auto-captions) |
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

## Stats

- **WPM**: (characters you typed on the line / 5) / minutes, counting from your first keystroke on
  the line to its end, minus any time the video was playing. Each character counts once, so
  deleting and retyping doesn't inflate it; hinted characters don't count.
- **Accuracy**: correct keystrokes / all letter, digit and space keystrokes (Backspace isn't counted).
- **Video average**: over all completed lines, weighted by length. Redoing a line replaces its stats.
- **Missed words**: any word where you made an error or used a hint.

## Troubleshooting

- *"YouTube is blocking or rate-limiting requests"*: YouTube throttles caption requests from
  some IPs. Wait a while and try again. Already-cached videos keep working.
- *"The owner doesn't allow this video to be embedded"*: that video can't be played outside
  youtube.com. Pick another one.
- *No captions in that language*: the error lists the languages that are available. Type
  one of those codes in the small box next to the link.
