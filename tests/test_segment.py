from app import (
    MAX_WORDS,
    SPLIT_WORDS,
    clean_cues,
    clean_text,
    extract_video_id,
    merge_cues,
    segment,
    split_long_line,
)


def cue(start, duration, text):
    return {"start": start, "duration": duration, "text": text}


# --- clean_text ---

def test_decodes_html_entities():
    assert clean_text("it&#39;s rock &amp; roll") == "it's rock & roll"


def test_decodes_double_escaped_entities():
    assert clean_text("don&amp;#39;t") == "don't"


def test_removes_bracketed_tags_and_music_symbol():
    assert clean_text("[Music] ♪ la la ♪ (laughs) hello [Applause]") == "la la hello"


def test_collapses_whitespace_and_newlines():
    assert clean_text("  hello\n   there\tfriend ") == "hello there friend"


def test_tag_only_cues_are_dropped():
    cues = clean_cues([cue(0, 1, "[Music]"), cue(1, 1, "hi there"), cue(2, 1, "♪")])
    assert [c["text"] for c in cues] == ["hi there"]


# --- merging ---

def test_merges_until_sentence_punctuation():
    cues = clean_cues([
        cue(0.0, 1.0, "So in college,"),
        cue(1.0, 1.0, "I was a government major."),
        cue(2.0, 1.0, "That meant papers."),
    ])
    lines = merge_cues(cues, punctuated=True)
    assert [l["text"] for l in lines] == ["So in college, I was a government major.", "That meant papers."]
    assert lines[0]["start"] == 0.0 and lines[0]["end"] == 2.0


def test_merges_on_pauses_for_unpunctuated_text():
    cues = clean_cues([
        cue(0.0, 1.0, "so in college"),
        cue(1.1, 1.0, "i was a major"),      # 0.1s gap: same line
        cue(3.0, 1.0, "which meant papers"),  # 0.9s gap: new line
    ])
    lines = merge_cues(cues, punctuated=False)
    assert [l["text"] for l in lines] == ["so in college i was a major", "which meant papers"]


def test_merge_stops_at_word_limit_without_punctuation():
    cues = clean_cues([cue(i * 0.5, 0.5, "one two three four") for i in range(6)])
    lines = merge_cues(cues, punctuated=False)
    assert all(len(l["text"].split()) <= MAX_WORDS for l in lines)
    assert len(lines) == 2


def test_merge_stops_at_time_limit():
    cues = clean_cues([cue(i * 2.0, 2.0, "word") for i in range(6)])
    lines = merge_cues(cues, punctuated=False)
    assert [l["end"] - l["start"] for l in lines] == [6.0, 6.0]


def test_overlapping_auto_cues_are_clipped_to_next_start():
    cues = clean_cues([cue(0.0, 4.0, "so in"), cue(1.8, 5.0, "college i was")])
    assert cues[0]["end"] == 1.8
    assert cues[1]["end"] == 6.8


# --- splitting ---

def test_splits_long_line_at_comma_nearest_middle():
    text = "one two three four five six seven, eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
    parts = split_long_line({"start": 0.0, "end": 10.0, "text": text})
    assert [p["text"] for p in parts] == [
        "one two three four five six seven,",
        "eight nine ten eleven twelve thirteen fourteen fifteen sixteen",
    ]
    assert parts[0]["start"] == 0.0 and parts[-1]["end"] == 10.0
    assert parts[0]["end"] == parts[1]["start"]


def test_splits_long_line_at_middle_without_comma():
    words = [f"w{i}" for i in range(20)]
    parts = split_long_line({"start": 0.0, "end": 10.0, "text": " ".join(words)})
    assert [p["text"].split() for p in parts] == [words[:10], words[10:]]


def test_short_line_not_split():
    line = {"start": 0.0, "end": 1.0, "text": "just a few words"}
    assert split_long_line(line) == [line]


def test_segment_output_shape_and_limits():
    raw = [cue(i, 1.0, "this goes on and on and on without any stop at all") for i in range(5)]
    lines = segment(raw)
    assert [l["id"] for l in lines] == list(range(len(lines)))
    assert all(set(l) == {"id", "start", "duration", "text"} for l in lines)
    assert all(len(l["text"].split()) <= SPLIT_WORDS for l in lines)


# --- video IDs ---

def test_extract_video_id_formats():
    vid = "dQw4w9WgXcQ"
    for url in [
        f"https://www.youtube.com/watch?v={vid}",
        f"https://www.youtube.com/watch?v={vid}&t=42s",
        f"https://youtube.com/watch?feature=share&v={vid}",
        f"https://youtu.be/{vid}?t=10",
        f"https://www.youtube.com/shorts/{vid}",
        f"https://www.youtube.com/embed/{vid}?start=5",
        f"https://m.youtube.com/watch?v={vid}",
        f"youtube.com/watch?v={vid}",
        vid,
    ]:
        assert extract_video_id(url) == vid, url
    assert extract_video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert extract_video_id("not a link") is None
