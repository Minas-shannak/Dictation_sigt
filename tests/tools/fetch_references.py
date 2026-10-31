"""Fetch YouTube captions for the test clips into tests/reference/ (test tooling only).

    python tests/tools/fetch_references.py [clip ...]

Captions are only a reference for what was said, to score transcripts in
tests/tools/evaluate_fixtures.py; the app never reads them. Needs the dev
requirements (youtube-transcript-api). Manually written captions are preferred
over auto-generated ones. Lines added after a listening check ("source": "human")
and the "human_verified" block are kept when a reference is refetched.
"""

import json
import sys
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi

REFERENCE = Path(__file__).resolve().parents[1] / "reference"
# clip -> (YouTube ID, start, end of the clip section in seconds)
CLIPS = {
    "conversation": ("eyoFYpI47Qg", 60, 300),
    "numbers": ("Jnw4J8Wj3k4", 120, 360),
    "movie": ("cpbARmnt_nU", 0, 300),
    "music": ("3-sfGRswzq0", 0, 243),
}
NOTE = "YouTube captions, used only as a test reference for what was said; never read by the app."


def fetch(name: str) -> None:
    vid, a, b = CLIPS[name]
    transcripts = YouTubeTranscriptApi().list(vid)
    try:
        t = transcripts.find_manually_created_transcript(["en", "en-GB", "en-US"])
        source = "manual"
    except Exception:
        t = transcripts.find_generated_transcript(["en"])
        source = "auto"
    # Shift to clip time: the clip starts `a` seconds into the video.
    lines = [{"start": round(line["start"] - a, 3), "duration": line["duration"], "text": line["text"]}
             for line in t.fetch().to_raw_data() if a <= line["start"] < b]
    path = REFERENCE / f"{name}.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    human = [line for line in old.get("lines", []) if line.get("source") == "human"]
    out = {"youtube_id": vid, "section_seconds": [a, b], "caption_source": source, "language": t.language_code,
           "note": old.get("note", NOTE), "lines": sorted(lines + human, key=lambda line: line["start"])}
    if "human_verified" in old:
        out["human_verified"] = old["human_verified"]
    path.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{name}: {source} captions ({t.language_code}), {len(lines)} lines, {len(human)} human-verified lines kept")


if __name__ == "__main__":
    for clip in sys.argv[1:] or CLIPS:
        try:
            fetch(clip)
        except Exception as e:
            print(f"{clip}: failed: {type(e).__name__}: {str(e).splitlines()[0]}")
