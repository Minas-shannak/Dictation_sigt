"""Tests for the punctuation-restoration experiment (ai/punctuation.py). No model needed:
the restorers here are fakes, several of them trying to change words."""

import re

import pytest

from ai.punctuation import only_punctuation_and_case_added, restore_unpunctuated
from ai.transcript_processor import process_transcript
from tests.test_transcript_processor import raw_of, seg, texts, timed

MOVIE = "what now I'm only picking up bits and pieces of course overprotective mother forbidden road trip"


def plain_words(result):
    return [re.sub(r"[^\w']", "", w["word"]).lower() for s in result["sentences"] for w in s["words"]]


def scripted(answer):
    """A restorer that returns `answer` (space-separated, one entry per word)."""
    return lambda words: answer.split(" ")


def test_restored_punctuation_and_capitals_end_sentences():
    answer = "What now? I'm only picking up bits and pieces. Of course, overprotective mother, forbidden road trip."
    r = process_transcript(raw_of(seg(timed(MOVIE))), punctuate=scripted(answer))
    assert texts(r) == ["What now?", "I'm only picking up bits and pieces.",
                        "Of course, overprotective mother, forbidden road trip."]
    assert [s["split_reason"] for s in r["sentences"]] == ["punctuation"] * 3


@pytest.mark.parametrize("answer", [
    "what now I am only picking up bits and pieces of course overprotective mother forbidden road trip",  # I'm -> I am
    "what now i'm only picking up bits and peaces of course overprotective mother forbidden road trip.",  # spelling
    "What now? I'm only picking up bits and pieces. Of course! overprotective mother forbidden road-trip.",
    "WHAT NOW I'M ONLY PICKING UP BITS AND PIECES OF COURSE OVERPROTECTIVE MOTHER FORBIDDEN ROAD TRIP",
    "what. now. i'm. only. picking. up. bits. and. pieces. of. course. o.v.e.r. mother forbidden road trip",
])
def test_a_restorer_can_never_change_add_or_remove_words(answer):
    raw = raw_of(seg(timed(MOVIE)))
    before = plain_words(process_transcript(raw))
    r = process_transcript(raw, punctuate=scripted(answer))
    assert plain_words(r) == before
    words = [w for s in r["sentences"] for w in s["words"]]
    original = [w for s in process_transcript(raw)["sentences"] for w in s["words"]]
    for a, b in zip(original, words):
        assert only_punctuation_and_case_added(a["word"], b["word"]), (a["word"], b["word"])
        assert (a["start"], a["end"], a["source"]) == (b["start"], b["end"], b["source"])


def test_wrong_word_count_leaves_the_stretch_as_it_was():
    raw = raw_of(seg(timed(MOVIE)))
    for restorer in (scripted("What now?"), lambda words: words + ["extra."], lambda words: None):
        assert texts(process_transcript(raw, punctuate=restorer)) == texts(process_transcript(raw))


def test_capitals_are_only_ever_added():
    r = process_transcript(raw_of(seg(timed("I told Anna about it"))), punctuate=scripted("i told anna about it."))
    assert texts(r) == ["I told Anna about it."]


def test_only_stretches_whisper_left_unpunctuated_change_and_neighbours_are_context():
    seen = []

    def restorer(words):
        seen.append(list(words))
        return [w.upper() + "!" for w in words]  # tries to change every word it sees
    raw = raw_of(seg(timed("Whisper punctuated this one, didn't it?")), seg(timed("but not this one at all", 3.0)),
                 seg(timed("And this is punctuated.", 6.0)))
    r = process_transcript(raw, punctuate=restorer)
    # One call: the stretch with up to 8 words of context on each side.
    assert seen == [["whisper", "punctuated", "this", "one", "didn't", "it", "but", "not", "this", "one", "at", "all",
                     "and", "this", "is", "punctuated"]]
    assert texts(r) == ["Whisper punctuated this one, didn't it?", "But! Not! This! One! At! All!", "And this is punctuated."]


def test_capitals_only_at_a_sentence_start():
    r = process_transcript(raw_of(seg(timed(MOVIE))),
                           punctuate=scripted("What Now? I'm Only Picking Up Bits And Pieces. Of Course Overprotective "
                                              "Mother Forbidden Road Trip."))
    assert texts(r) == ["What now?", "I'm only picking up bits and pieces.",
                        "Of course overprotective mother forbidden road trip."]


def test_without_a_restorer_nothing_changes():
    raw = raw_of(seg(timed(MOVIE)))
    assert process_transcript(raw) == process_transcript(raw, punctuate=None)


def test_the_word_check_itself():
    assert only_punctuation_and_case_added("now", "Now?")
    assert only_punctuation_and_case_added("i'm", "I'm")
    assert not only_punctuation_and_case_added("Anna", "anna")      # lowercasing
    assert not only_punctuation_and_case_added("am", "a.m.")        # inside a word
    assert not only_punctuation_and_case_added("now", "now?!")      # two marks
    assert not only_punctuation_and_case_added("road", "road-trip")
    assert not only_punctuation_and_case_added("now", "how")
