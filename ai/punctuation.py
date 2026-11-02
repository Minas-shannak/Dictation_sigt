"""Punctuation restoration for stretches Whisper left unpunctuated (EXPERIMENT, not used by the app).

A restorer is any callable that takes a list of lowercase words and returns one string per
word, punctuated and capitalized as it sees fit (tests/tools/punctuation_experiment.py wraps
an ONNX model in one). Whatever it returns, only two things are taken from it:

- capitalization: the first letter of a word that starts a sentence may become uppercase
  (elsewhere the model title-cases freely: "Mother Forbidden Road Trip"); nothing is lowercased;
- one punctuation mark (. , ? !) after a word that has none.

The restorer also sees up to CONTEXT_WORDS words on each side of the stretch (without them it
ends every stretch with a period); its suggestions for those are ignored.

Every word is rebuilt from the original word's own characters, so a restorer cannot change,
add or remove words, and `only_punctuation_and_case_added` checks that on the result.
"""

import logging
import re
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

Restorer = Callable[[List[str]], List[str]]

ADDABLE = ".,?!"
CONTEXT_WORDS = 8
SENTENCE_END_RE = re.compile(r"[.?!…][\"')\]]*$")
# Leading punctuation, the word, trailing punctuation (same split as the transcript processor).
TOKEN_RE = re.compile(r"^([\"'(\[]*)(.*?)([.,?!;:…\"')\]]*)$", re.S)


def _letters(text: str) -> str:
    return re.sub(r"[^\w']", "", text)


def _restyle(original: str, suggested: str, starts_sentence: bool) -> str:
    """original with the suggested capital (sentence start only) and final mark, nothing else."""
    lead, core, trail = TOKEN_RE.match(original).groups()
    theirs = _letters(suggested)
    if starts_sentence and core[:1].islower() and theirs[:1] == core[:1].upper() and theirs.lower() == _letters(core).lower():
        core = core[0].upper() + core[1:]
    mark = suggested.rstrip()[-1:]
    if not trail and mark and mark in ADDABLE:
        trail = mark
    return lead + core + trail


def only_punctuation_and_case_added(before: str, after: str) -> bool:
    """True if `after` is `before` with letters uppercased and/or one mark from ADDABLE appended."""
    if after == before:
        return True
    body = after[:-1] if after[-1:] in ADDABLE and len(after) == len(before) + 1 else after
    return len(body) == len(before) and all(a == b or (b.islower() and a == b.upper()) for a, b in zip(body, before))


def restore_unpunctuated(words: List[Dict], restorer: Restorer) -> int:
    """Punctuate the stretches of words Whisper left unpunctuated, in place.

    words: the processor's word dicts ("word", "punctuated", ...). A restored stretch is
    marked punctuated, so its pauses no longer end sentences; the punctuation does.
    Returns the number of words changed.
    """
    changed = 0
    i = 0
    while i < len(words):
        if words[i]["punctuated"]:
            i += 1
            continue
        j = i
        while j < len(words) and not words[j]["punctuated"]:
            j += 1
        stretch = words[i:j]
        lo, hi = max(0, i - CONTEXT_WORDS), min(len(words), j + CONTEXT_WORDS)
        sent = [_letters(TOKEN_RE.match(w["word"]).group(2)).lower() for w in words[lo:hi]]
        suggested: Optional[List[str]] = restorer(sent)
        if suggested is None or len(suggested) != len(sent):
            log.warning("Punctuation restorer returned %s words for %d; stretch left as it was",
                        None if suggested is None else len(suggested), len(sent))
            i = j
            continue
        for k, (w, s) in enumerate(zip(stretch, suggested[i - lo:j - lo])):
            prev = words[i + k - 1]["word"] if i + k > 0 else None
            new = _restyle(w["word"], s, prev is None or bool(SENTENCE_END_RE.search(prev)))
            if not only_punctuation_and_case_added(w["word"], new):  # can't happen; checked anyway
                raise AssertionError(f"punctuation restoration changed a word: {w['word']!r} -> {new!r}")
            if new != w["word"]:
                changed += 1
                w["word"] = new
            w["punctuated"] = True
        i = j
    return changed
