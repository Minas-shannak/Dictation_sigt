"""Turn a raw transcriber result into dictation sentences.

Pipeline: hallucination filter -> word normalization -> cleaning -> numbers to
words -> sentence segmentation.

The raw result is never modified. Every training word keeps the timestamps of
the raw word(s) it came from, unchanged, plus their indices ("source": positions
in the raw result's words, counted across all segments in order), so the link
to the original is never lost.

Public function: process_transcript(raw, settings=None).
"""

import logging
import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from num2words import num2words

import config

log = logging.getLogger(__name__)

# Every setting that changes the output. They are stored with the result, so a
# cached transcript can be matched to the settings that produced it.
SETTING_NAMES = [
    "HALLUCINATION_NO_SPEECH_PROB", "HALLUCINATION_MAX_AVG_LOGPROB", "HALLUCINATION_MAX_COMPRESSION",
    "HALLUCINATION_MIN_WORD_PROB", "HALLUCINATION_MAX_PHRASE_REPEATS", "HALLUCINATION_MAX_WORD_REPEATS",
    "KNOWN_HALLUCINATIONS", "KNOWN_HALLUCINATION_MAX_AVG_LOGPROB", "KNOWN_HALLUCINATION_MIN_NO_SPEECH",
    "REMOVE_FILLERS", "FILLER_PATTERNS", "PROTECTED_WORDS", "NON_SPEECH_ANNOTATIONS",
    "NUMBERS_TO_WORDS",
    "SENTENCE_MAX_WORDS", "SENTENCE_MAX_SECONDS", "SENTENCE_MIN_WORDS", "SENTENCE_PAUSE_SECONDS",
    "SENTENCE_LONG_SILENCE_SECONDS", "SENTENCE_SPLIT_PAUSE_SECONDS", "SENTENCE_SPLIT_BEFORE_WORDS",
    "TITLE_ABBREVIATIONS", "ABBREVIATIONS",
]

SENTENCE_END_RE = re.compile(r"(\.|\?|!|…)[\"')\]]*$")
ELLIPSIS_END_RE = re.compile(r"(\.\.\.|…)[\"')\]]*$")
# Whisper sometimes splits a contraction with a space before the second part: "don" + " 't".
CONTRACTION_PART_RE = re.compile(r"^(['’](s|t|re|ve|ll|d|m)|n['’]t)\W*$", re.I)
CURLY = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"'})
MUSIC_SYMBOLS_RE = re.compile(r"[♪♫♬]")
# Leading punctuation, the token itself, trailing punctuation.
TOKEN_RE = re.compile(r"^([\"'(\[]*)(.*?)([.,?!;:…\"')\]]*)$", re.S)
SCALE_WORDS = {"thousand", "million", "billion", "trillion"}
CURRENCIES = {"$": ("dollar", "dollars"), "£": ("pound", "pounds"), "€": ("euro", "euros")}


def default_settings() -> Dict:
    def jsonable(v):
        return sorted(v) if isinstance(v, set) else v
    return {name.lower(): jsonable(getattr(config, name)) for name in SETTING_NAMES}


def _plain(text: str) -> str:
    """Lowercase, punctuation removed (apostrophes kept), single spaces."""
    text = text.translate(CURLY).lower()
    return " ".join(re.sub(r"[^\w' ]+", " ", text).split())


def _has_sentence_punct(text: str) -> bool:
    return bool(re.search(r"[.?!…]", text))


# --- 1. Hallucination filter ------------------------------------------------------

def _repetition_loop(tokens: List[str], max_phrase: int, max_word: int) -> bool:
    """True if some phrase is repeated back to back more often than allowed."""
    for n in range(1, 9):
        limit = max_word if n == 1 else max_phrase
        i = 0
        while i + n <= len(tokens):
            count = 1
            while tokens[i + count * n:i + (count + 1) * n] == tokens[i:i + n]:
                count += 1
            if count > limit:
                return True
            i += 1
    return False


def find_hallucinations(segments: List[Dict], s: Dict) -> Dict[int, List[str]]:
    """Segment index -> reasons it is flagged. Unflagged segments are absent."""
    known = [_plain(p) for p in s["known_hallucinations"]]
    flagged: Dict[int, List[str]] = {}
    prev_text, run = None, 0
    for i, seg in enumerate(segments):
        reasons = []
        words = seg.get("words") or []
        text = _plain(seg["text"])
        if seg["no_speech_prob"] > s["hallucination_no_speech_prob"] and seg["avg_logprob"] < s["hallucination_max_avg_logprob"]:
            reasons.append("no_speech")
        if seg["compression_ratio"] > s["hallucination_max_compression"]:
            reasons.append("compression_ratio")
        if words and sum(w["probability"] for w in words) / len(words) < s["hallucination_min_word_prob"]:
            reasons.append("low_word_probability")
        if _repetition_loop(text.split(), s["hallucination_max_phrase_repeats"], s["hallucination_max_word_repeats"]):
            reasons.append("repetition_loop")
        low_confidence = (seg["avg_logprob"] < s["known_hallucination_max_avg_logprob"]
                          or seg["no_speech_prob"] > s["known_hallucination_min_no_speech"])
        if low_confidence and any(p and p in text for p in known):
            reasons.append("known_hallucination")
        # The same segment text over and over is a decoding loop across segments.
        run = run + 1 if text and text == prev_text else 1
        prev_text = text
        if run > s["hallucination_max_phrase_repeats"]:
            reasons.append("repeated_segment")
        if reasons:
            flagged[i] = reasons
    return flagged


# --- 2. Word normalization ---------------------------------------------------------

def _normalize_segment_words(seg: Dict, first_index: int, punctuated: bool) -> List[Dict]:
    """Strip Whisper's leading spaces and glue word fragments back together.

    A token without a leading space continues the previous word ("grown" + "-up",
    "10" + ".30", "don" + "'t"); contraction endings are glued even with a space.
    """
    out: List[Dict] = []
    for k, w in enumerate(seg.get("words") or []):
        raw_text = w["word"]
        text = raw_text.strip().translate(CURLY)
        if not text:
            continue
        continues = out and (not raw_text[:1].isspace() or CONTRACTION_PART_RE.match(text))
        if continues:
            prev = out[-1]
            prev["word"] += text
            prev["end"] = max(prev["end"], w["end"])
            prev["probability"] = min(prev["probability"], w["probability"])
            prev["source"].append(first_index + k)
        else:
            out.append({"word": text, "start": w["start"], "end": w["end"], "probability": w["probability"],
                        "source": [first_index + k], "punctuated": punctuated})
    return out


# --- 3. Cleaning ----------------------------------------------------------------

def _drop(words: List[Dict], i: int, j: int) -> None:
    """Remove words[i..j]. A sentence end on the last removed word moves to the word before."""
    end = SENTENCE_END_RE.search(words[j]["word"])
    del words[i:j + 1]
    if end and i > 0 and not SENTENCE_END_RE.search(words[i - 1]["word"]):
        words[i - 1]["word"] = words[i - 1]["word"].rstrip(",;:") + end.group(0)


def _clean(words: List[Dict], s: Dict) -> Tuple[int, int]:
    """Remove non-speech annotations and fillers in place. Returns (annotations, fillers) removed."""
    fillers = [re.compile(p, re.I) for p in s["filler_patterns"]] if s["remove_fillers"] else []
    protected = set(s["protected_words"])
    annotations = set(s["non_speech_annotations"])
    n_annotations = n_fillers = 0
    i = 0
    while i < len(words):
        text = words[i]["word"]
        opener = text.lstrip("\"'")[:1]
        if opener in "[(":
            closer = "]" if opener == "[" else ")"
            j = next((k for k in range(i, min(i + 8, len(words))) if closer in words[k]["word"]), None)
            if j is not None:
                inner = _plain(" ".join(w["word"] for w in words[i:j + 1]))
                if opener == "[" or inner in annotations:
                    _drop(words, i, j)
                    n_annotations += 1
                    continue
        if MUSIC_SYMBOLS_RE.search(text):
            text = MUSIC_SYMBOLS_RE.sub("", text).strip()
            if not re.search(r"\w", text):
                _drop(words, i, i)
                continue
            words[i]["word"] = text
        plain = _plain(text)
        if fillers and plain not in protected and any(p.fullmatch(plain) for p in fillers):
            _drop(words, i, i)
            n_fillers += 1
            continue
        i += 1
    return n_annotations, n_fillers


# --- 4. Numbers to words ------------------------------------------------------------

def _words(n) -> List[str]:
    return num2words(n, lang="en").replace(",", "").split()


def _time_words(hour: int, minute: int) -> List[str]:
    if minute == 0:
        return _words(hour)
    return _words(hour) + (["oh"] + _words(minute) if minute < 10 else _words(minute))


def _plural(word: str) -> str:
    return word[:-1] + "ies" if word.endswith("y") else word + "s"


def number_to_words(core: str, next_core: str = "") -> Optional[List[str]]:
    """Spoken words for a number token, or None if it isn't one we can read safely.

    next_core is the following word (lowercase, no punctuation), used for "1.5 million"
    (a decimal, not a time) and "50 percent".
    """
    c = core.replace(",", "") if re.fullmatch(r"[$£€]?\d{1,3}(,\d{3})+(\.\d+)?%?", core) else core
    m = re.fullmatch(r"([$£€])(\d+)(?:\.(\d{2}))?", c)
    if m:
        one, many = CURRENCIES[m.group(1)]
        amount = int(m.group(2))
        if next_core in SCALE_WORDS:
            return _words(amount)  # "$5 million": the unit goes after "million"
        out = _words(amount) + [one if amount == 1 else many]
        if m.group(3) and int(m.group(3)):
            out += _words(int(m.group(3)))
        return out
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", c)
    if m:
        return _words(Decimal(m.group(1))) + ["percent"]
    m = re.fullmatch(r"(\d+)(st|nd|rd|th)", c, re.I)
    if m:
        return num2words(int(m.group(1)), lang="en", to="ordinal").split()
    m = re.fullmatch(r"'?(\d0)s", c)
    if m:
        return [_plural(_words(int(m.group(1)))[-1])]
    m = re.fullmatch(r"(\d{3}0)s", c)
    if m:
        out = num2words(int(m.group(1)), lang="en", to="year").split()
        return out[:-1] + [_plural(out[-1])]
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", c)
    if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
        return _time_words(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d+)\.(\d+)", c)
    if m:
        hour, frac = m.group(1), m.group(2)
        # Whisper writes spoken clock times as "10.30" / "11.00".
        if len(frac) == 2 and int(hour) <= 23 and int(frac) <= 59 and next_core not in SCALE_WORDS | {"percent"}:
            return _time_words(int(hour), int(frac))
        return _words(Decimal(c))
    if re.fullmatch(r"\d+", c):
        if len(c) > 1 and c.startswith("0"):
            return None  # "007", codes
        if len(c) >= 8 and "," not in core:
            return None  # phone numbers, IDs
        n = int(c)
        if len(c) == 4 and 1100 <= n <= 2099:
            return num2words(n, lang="en", to="year").split()
        return _words(n)
    return None


def _numbers_to_words(words: List[Dict]) -> List[str]:
    """Replace number tokens in place. Returns the tokens left as written (ambiguous)."""
    left_as_is = []
    out: List[Dict] = []
    pending_unit: Optional[Tuple[str, str]] = None  # ("dollars", ...) to place after "million"
    for idx, w in enumerate(words):
        lead, core, trail = TOKEN_RE.match(w["word"]).groups()
        if pending_unit and core.lower() in SCALE_WORDS:
            out.append(dict(w, word=lead + core))
            out.append(dict(w, word=pending_unit[1] + trail, source=list(w["source"])))
            pending_unit = None
            continue
        pending_unit = None
        if not re.search(r"\d", core):
            out.append(w)
            continue
        nxt = words[idx + 1]["word"] if idx + 1 < len(words) else ""
        next_core = TOKEN_RE.match(nxt).group(2).lower() if nxt else ""
        spoken = number_to_words(core, next_core)
        if spoken is None:
            left_as_is.append(w["word"])
            out.append(w)
            continue
        if core[:1] in CURRENCIES and next_core in SCALE_WORDS:
            pending_unit = CURRENCIES[core[:1]]
        # Every word made from one token keeps that token's start and end.
        for k, part in enumerate(spoken):
            text = (lead if k == 0 else "") + part + (trail if k == len(spoken) - 1 else "")
            out.append(dict(w, word=text, source=list(w["source"])))
    words[:] = out
    for token in left_as_is:
        log.info("Number left as written (ambiguous): %r", token)
    return left_as_is


# --- 5. Sentence segmentation ----------------------------------------------------

def _ends_sentence(w: Dict, nxt: Optional[Dict], s: Dict) -> bool:
    text = w["word"]
    if not SENTENCE_END_RE.search(text):
        return False
    next_upper = nxt is not None and nxt["word"].lstrip("\"'(")[:1].isupper()
    core = text.lower().rstrip("\"')]")
    if core in s["title_abbreviations"]:
        return False
    if core in s["abbreviations"] or ELLIPSIS_END_RE.search(text):
        return nxt is None or next_upper  # "etc. And then" ends; "p.m. on Friday" doesn't
    return True


def _gap(a: Dict, b: Dict) -> float:
    return b["start"] - a["end"]


def _split_point(piece: List[Dict], s: Dict) -> Optional[int]:
    """Where to split an over-long piece (see SENTENCE_SPLIT_* in config for the order)."""
    n, min_words = len(piece), s["sentence_min_words"]
    allowed = range(min_words, n - min_words + 1)  # index of the first word of the second part
    if not allowed:
        return None
    middle = n / 2
    commas = [k for k in allowed if piece[k - 1]["word"].rstrip("\"')]").endswith((",", ";", ":"))]
    if commas:
        return min(commas, key=lambda k: abs(k - middle))
    pauses = [k for k in allowed if _gap(piece[k - 1], piece[k]) >= s["sentence_split_pause_seconds"]]
    if pauses:
        return max(pauses, key=lambda k: (_gap(piece[k - 1], piece[k]), -abs(k - middle)))
    joining = s["sentence_split_before_words"]
    # Before the first of consecutive joining words: "girl | but if you're", not "girl but | if".
    joiners = [k for k in allowed
               if _plain(piece[k]["word"]) in joining and _plain(piece[k - 1]["word"]) not in joining]
    if joiners:
        return min(joiners, key=lambda k: abs(k - middle))
    return max(allowed, key=lambda k: (_gap(piece[k - 1], piece[k]), -abs(k - middle)))


def _too_long(piece: List[Dict], s: Dict) -> bool:
    return (len(piece) > s["sentence_max_words"]
            or piece[-1]["end"] - piece[0]["start"] > s["sentence_max_seconds"])


def _split_long(piece: List[Dict], s: Dict) -> List[List[Dict]]:
    if not _too_long(piece, s):
        return [piece]
    k = _split_point(piece, s)
    if k is None:
        return [piece]
    return _split_long(piece[:k], s) + _split_long(piece[k:], s)


def _segment_run(run: List[Dict], s: Dict) -> List[List[Dict]]:
    """Split one run of words (no excluded segment inside it) into sentences."""
    pieces: List[List[Dict]] = []
    cur: List[Dict] = []
    for i, w in enumerate(run):
        if cur:
            gap = _gap(cur[-1], w)
            if gap >= s["sentence_long_silence_seconds"] or (
                    gap >= s["sentence_pause_seconds"] and not cur[-1]["punctuated"]):
                pieces.append(cur)
                cur = []
        cur.append(w)
        if _ends_sentence(w, run[i + 1] if i + 1 < len(run) else None, s):
            pieces.append(cur)
            cur = []
    if cur:
        pieces.append(cur)

    pieces = [part for p in pieces for part in _split_long(p, s)]

    # Merge fragments that are too short into the closer neighbour, but keep complete
    # short sentences ("Yes.", "Wow!") and never merge across a long silence.
    i = 0
    while i < len(pieces):
        p = pieces[i]
        if len(p) >= s["sentence_min_words"] or SENTENCE_END_RE.search(p[-1]["word"]) or len(pieces) == 1:
            i += 1
            continue
        limit = s["sentence_long_silence_seconds"]
        gap_before = _gap(pieces[i - 1][-1], p[0]) if i > 0 else None
        gap_after = _gap(p[-1], pieces[i + 1][0]) if i + 1 < len(pieces) else None
        options = [(g, side) for g, side in ((gap_before, -1), (gap_after, 1)) if g is not None and g < limit]
        # A piece ending a sentence can't take words after it.
        options = [(g, side) for g, side in options
                   if not (side == -1 and SENTENCE_END_RE.search(pieces[i - 1][-1]["word"]))]
        if not options:
            i += 1
            continue
        _, side = min(options)
        if side == -1:
            pieces[i - 1] = pieces[i - 1] + p
            del pieces[i]
        else:
            pieces[i + 1] = p + pieces[i + 1]
            del pieces[i]
    return pieces


# --- Pipeline ---------------------------------------------------------------------

def process_transcript(raw: Dict, settings: Optional[Dict] = None) -> Dict:
    """Raw transcriber result -> training transcript with timed sentences.

    settings: overrides for default_settings() (lowercase config names).
    """
    s = default_settings()
    for key, value in (settings or {}).items():
        if key not in s:
            raise ValueError(f"Unknown processing setting: {key}")
        s[key] = value

    segments = raw.get("segments") or []
    flagged = find_hallucinations(segments, s)

    # Runs of words between excluded segments; an excluded segment is a hard sentence break.
    runs: List[List[Dict]] = [[]]
    first_index = 0
    for i, seg in enumerate(segments):
        if i in flagged:
            runs.append([])
        else:
            runs[-1].extend(_normalize_segment_words(seg, first_index, _has_sentence_punct(seg["text"])))
        first_index += len(seg.get("words") or [])

    n_annotations = n_fillers = 0
    left_as_is: List[str] = []
    pieces: List[List[Dict]] = []
    for run in runs:
        a, f = _clean(run, s)
        n_annotations += a
        n_fillers += f
        if s["numbers_to_words"]:
            left_as_is += _numbers_to_words(run)
        pieces += _segment_run(run, s) if run else []

    sentences = []
    for p in pieces:
        if not any(re.search(r"\w", w["word"]) for w in p):
            continue  # punctuation only: nothing to type
        sentences.append({
            "index": len(sentences),
            "text": " ".join(w["word"] for w in p),
            "start": p[0]["start"],
            "end": p[-1]["end"],
            "words": [{k: w[k] for k in ("word", "start", "end", "probability", "source")} for w in p],
        })

    excluded = [{"segment": i, "start": segments[i]["start"], "end": segments[i]["end"],
                 "text": segments[i]["text"].strip(), "reasons": reasons} for i, reasons in flagged.items()]
    if not segments:
        empty_reason = "no_speech"
    elif len(flagged) == len(segments):
        empty_reason = "all_excluded"
    elif not sentences:
        empty_reason = "nothing_left"
    else:
        empty_reason = None
    details = ", ".join("#%d: %s" % (e["segment"], "+".join(e["reasons"])) for e in excluded)
    log.info("Processed transcript: %d segments, %d excluded as hallucinations%s, %d annotations and %d fillers "
             "removed, %d numbers left as written, %d sentences",
             len(segments), len(excluded), f" ({details})" if details else "",
             n_annotations, n_fillers, len(left_as_is), len(sentences))

    return {
        "language": raw.get("language"),
        "language_probability": raw.get("language_probability"),
        "model": raw.get("model"),
        "device": raw.get("device"),
        "compute_type": raw.get("compute_type"),
        "asr_settings": raw.get("asr_settings"),
        "processing_settings": s,
        "processing_version": config.PROCESSING_VERSION,
        "original_transcript": " ".join(seg["text"].strip() for seg in segments),
        "training_transcript": " ".join(st["text"] for st in sentences),
        "excluded_segments": len(excluded),
        "excluded": excluded,
        "numbers_left_as_written": left_as_is,
        "empty_reason": empty_reason,
        "sentences": sentences,
    }
