"""Local server for the YouTube dictation app.

Serves index.html and a caption API that fetches, cleans and merges YouTube
captions into sentence-sized lines. Run with `python app.py`.
"""

import html
import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from flask import Flask, jsonify, request, send_from_directory
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)

# --- Segmentation thresholds -------------------------------------------------
MAX_WORDS = 12            # stop merging cues once a line reaches this many words
MAX_SECONDS = 6.0         # ...or spans this many seconds
PAUSE_SECONDS = 0.6       # unpunctuated captions: a gap longer than this ends a line
SPLIT_WORDS = 15          # lines longer than this are split in two
MIN_SPLIT_PART_WORDS = 3  # don't split at a comma that leaves a piece shorter than this
PUNCTUATED_RATIO = 0.1    # captions count as punctuated if this share of cues has . ? !

PORT = 8000
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SENTENCE_END_RE = re.compile(r"[.?!][\"'”’)\]]*$")
BRACKETED_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
SPEAKER_MARK_RE = re.compile(r">>+")

app = Flask(__name__)


# --- Video ID ------------------------------------------------------------------

def extract_video_id(url: str) -> Optional[str]:
    """Return the 11-character video ID from a YouTube link (or a bare ID)."""
    url = (url or "").strip()
    if VIDEO_ID_RE.match(url):
        return url
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in parsed.path.split("/") if p]

    candidate = None
    if host == "youtu.be" and parts:
        candidate = parts[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if parts[:1] == ["watch"]:
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            candidate = parts[1]
    if candidate and VIDEO_ID_RE.match(candidate):
        return candidate
    return None


# --- Cleanup and segmentation (pure functions) ------------------------------

def clean_text(text: str) -> str:
    """Decode entities, drop non-speech tags and ♪, collapse whitespace."""
    # Captions are sometimes double-escaped (&amp;#39;), so unescape until stable.
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = BRACKETED_RE.sub(" ", text)
    text = SPEAKER_MARK_RE.sub(" ", text)
    text = text.replace("♪", " ")
    return " ".join(text.split())


def word_count(text: str) -> int:
    return len(text.split())


def looks_punctuated(cues: List[Dict]) -> bool:
    """True if enough cues contain sentence punctuation to merge on it."""
    if not cues:
        return False
    with_punct = sum(1 for c in cues if re.search(r"[.?!]", c["text"]))
    return with_punct / len(cues) >= PUNCTUATED_RATIO


def clean_cues(raw: List[Dict]) -> List[Dict]:
    """Clean each cue's text, drop empty cues, and give each cue an end time.

    A cue's end is clipped to the next cue's start. Auto-generated captions stay
    on screen ~2s into the following cues, so their raw durations overlap and
    would make a line play into the next sentence.
    """
    cues = []
    for c in raw:
        text = clean_text(c["text"])
        if text:
            cues.append({"start": float(c["start"]), "end": float(c["start"]) + float(c["duration"]), "text": text})
    cues.sort(key=lambda c: c["start"])
    for cur, nxt in zip(cues, cues[1:]):
        cur["end"] = max(cur["start"], min(cur["end"], nxt["start"]))
    return cues


def merge_cues(cues: List[Dict], punctuated: bool) -> List[Dict]:
    """Merge cleaned cues into sentence-sized lines of {start, end, text}.

    Punctuated captions end a line at . ? !; unpunctuated ones end a line at a
    pause longer than PAUSE_SECONDS. Both also stop at MAX_WORDS / MAX_SECONDS.
    """
    lines: List[Dict] = []
    current: Optional[Dict] = None
    for cue in cues:
        if current is not None and not punctuated and cue["start"] - current["end"] > PAUSE_SECONDS:
            lines.append(current)
            current = None
        if current is None:
            current = dict(cue)
        else:
            current["text"] += " " + cue["text"]
            current["end"] = cue["end"]
        if (
            (punctuated and SENTENCE_END_RE.search(current["text"]))
            or word_count(current["text"]) >= MAX_WORDS
            or current["end"] - current["start"] >= MAX_SECONDS
        ):
            lines.append(current)
            current = None
    if current is not None:
        lines.append(current)
    return lines


def _split_index(words: List[str]) -> int:
    """Index of the first word of the second half: after the comma nearest the middle, else the middle."""
    middle = len(words) / 2
    comma_splits = [
        i + 1 for i, w in enumerate(words[:-1])
        if w.endswith(",") and MIN_SPLIT_PART_WORDS <= i + 1 <= len(words) - MIN_SPLIT_PART_WORDS
    ]
    if comma_splits:
        return min(comma_splits, key=lambda i: abs(i - middle))
    return len(words) // 2


def split_long_line(line: Dict) -> List[Dict]:
    """Split a line over SPLIT_WORDS words, recursively.

    Captions have no word timings here, so each part's time is estimated in
    proportion to its share of characters.
    """
    words = line["text"].split()
    if len(words) <= SPLIT_WORDS:
        return [line]
    idx = _split_index(words)
    first, second = " ".join(words[:idx]), " ".join(words[idx:])
    span = line["end"] - line["start"]
    cut = line["start"] + span * len(first) / (len(first) + len(second))
    return (
        split_long_line({"start": line["start"], "end": cut, "text": first})
        + split_long_line({"start": cut, "end": line["end"], "text": second})
    )


def segment(raw_cues: List[Dict]) -> List[Dict]:
    """Raw caption cues -> numbered lines of {id, start, duration, text}."""
    cues = clean_cues(raw_cues)
    merged = merge_cues(cues, looks_punctuated(cues))
    lines = []
    for line in merged:
        for part in split_long_line(line):
            if part["text"].strip():
                lines.append({
                    "id": len(lines),
                    "start": round(part["start"], 3),
                    "duration": round(part["end"] - part["start"], 3),
                    "text": part["text"],
                })
    return lines


# --- Fetching --------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def _find_in_lang(finder, transcript_list, lang: str):
    """Exact language code first, then regional variants (en -> en-US, en-GB)."""
    codes = [lang] + sorted({t.language_code for t in transcript_list if t.language_code.split("-")[0] == lang.split("-")[0]} - {lang})
    try:
        return finder(codes)
    except NoTranscriptFound:
        return None


def fetch_transcript(video_id: str, lang: str) -> Dict:
    api = YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
        transcript = _find_in_lang(transcript_list.find_manually_created_transcript, transcript_list, lang)
        source = "manual"
        if transcript is None:
            transcript = _find_in_lang(transcript_list.find_generated_transcript, transcript_list, lang)
            source = "auto"
        if transcript is None:
            available = [
                {"code": t.language_code, "name": t.language, "source": "auto" if t.is_generated else "manual"}
                for t in transcript_list
            ]
            raise ApiError(404, "no_captions_in_language",
                           f"No '{lang}' captions for this video.", available=available)
        fetched = transcript.fetch()
    except TranscriptsDisabled:
        raise ApiError(404, "no_captions_in_language", "This video has no captions at all.", available=[])
    except (RequestBlocked, IpBlocked, PoTokenRequired):
        raise ApiError(429, "blocked", "YouTube is blocking or rate-limiting requests from this computer. Wait a while and try again.")
    except (VideoUnavailable, VideoUnplayable, InvalidVideoId) as e:
        raise ApiError(404, "video_unavailable", f"Video unavailable: {type(e).__name__}")
    except YouTubeRequestFailed as e:
        raise ApiError(502, "youtube_error", f"Request to YouTube failed: {e.reason}")
    except CouldNotRetrieveTranscript as e:
        raise ApiError(502, "youtube_error", f"Could not retrieve captions: {type(e).__name__}")

    lines = segment(fetched.to_raw_data())
    return {"videoId": video_id, "lang": fetched.language_code, "source": source, "lines": lines}


# --- Routes ------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/transcript")
def api_transcript():
    url = request.args.get("url", "")
    lang = request.args.get("lang", "en").strip() or "en"
    try:
        video_id = extract_video_id(url)
        if not video_id:
            raise ApiError(400, "invalid_link", "That doesn't look like a YouTube video link.")
        cache_file = CACHE_DIR / f"{video_id}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            # One cache file per video: reuse it unless a different language was asked for.
            if cached.get("lang", "").split("-")[0] == lang.split("-")[0]:
                return jsonify(cached)
        data = fetch_transcript(video_id, lang)
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return jsonify(data)
    except ApiError as e:
        return jsonify({"error": e.code, "message": e.message, **e.extra}), e.status


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=False)
