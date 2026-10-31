"""ai.transcript_processor on hand-made raw results and on real transcriber output (tests/fixtures)."""

import copy
import json
import re
from pathlib import Path

import pytest

import config
from ai.transcript_processor import find_hallucinations, default_settings, number_to_words, process_transcript

FIXTURES = Path(__file__).parent / "fixtures"


def seg(tokens, nsp=0.01, logprob=-0.2, cr=1.3):
    """A raw segment from (word, start, end) or (word, start, end, probability) tuples."""
    words = [{"word": t[0], "start": t[1], "end": t[2], "probability": t[3] if len(t) > 3 else 0.9} for t in tokens]
    return {"id": 0, "start": words[0]["start"] if words else 0.0, "end": words[-1]["end"] if words else 0.0,
            "text": "".join(t[0] for t in tokens), "avg_logprob": logprob, "no_speech_prob": nsp,
            "compression_ratio": cr, "temperature": 0.0, "words": words}


def timed(text, start=0.0, step=0.3, gap=0.0):
    """Tokens for a space-separated text, one word every `step` seconds plus `gap` between words."""
    out, t = [], start
    for w in text.split(" "):
        out.append((" " + w, round(t, 3), round(t + step, 3)))
        t += step + gap
    return out


def raw_of(*segments):
    return {"language": "en", "language_probability": 0.99, "model": "small", "device": "cpu",
            "compute_type": "int8", "asr_settings": {}, "segments": list(segments)}


def texts(result):
    return [s["text"] for s in result["sentences"]]


def all_words(result):
    return [w for s in result["sentences"] for w in s["words"]]


# --- Cleaning -------------------------------------------------------------------

def test_non_speech_annotations_are_removed_but_spoken_parentheses_kept():
    r = process_transcript(raw_of(seg(timed("[Music] So I said (laughs) that it was (and I mean it) great."))))
    assert texts(r) == ["So I said that it was (and I mean it) great."]


def test_multi_token_annotation_and_music_symbols():
    r = process_transcript(raw_of(seg(timed("♪ Run for your life ♪ [ Applause ] okay then."))))
    assert texts(r) == ["Run for your life okay then."]


def test_fillers_removed_by_default_and_kept_when_disabled():
    raw = raw_of(seg(timed("Um, so I was, uh, thinking er about it erm today. Uhh.")))
    assert texts(process_transcript(raw)) == ["so I was, thinking about it today."]
    kept = process_transcript(raw, {"remove_fillers": False})
    assert " ".join(texts(kept)) == "Um, so I was, uh, thinking er about it erm today. Uhh."


def test_real_words_that_look_like_fillers_are_kept():
    r = process_transcript(raw_of(seg(timed("To err is human, uh-huh, mm-hmm, I agree."))))
    assert texts(r) == ["To err is human, uh-huh, mm-hmm, I agree."]


def test_meaningful_expressions_are_never_removed():
    raw = raw_of(seg(timed("Wow! Hey, oh, well, yeah. Yes. No.")))
    # Even a filler list that would match them can't remove them.
    r = process_transcript(raw, {"filler_patterns": [r"wow", r"hey", r"oh", r"well", r"yeah", r"yes", r"no"]})
    assert " ".join(texts(r)) == "Wow! Hey, oh, well, yeah. Yes. No."


def test_removing_a_sentence_final_filler_keeps_the_sentence_end():
    r = process_transcript(raw_of(seg(timed("I went there, um. Then I left."))))
    assert texts(r) == ["I went there.", "Then I left."]


def test_cleaning_never_shifts_timestamps():
    tokens = [(" Um,", 0.0, 0.4), (" I", 0.5, 0.6), (" [laughs]", 0.6, 1.2), (" like", 1.3, 1.5), (" it.", 1.5, 1.9)]
    r = process_transcript(raw_of(seg(tokens)))
    words = all_words(r)
    assert [(w["word"], w["start"], w["end"], w["source"]) for w in words] == [
        ("I", 0.5, 0.6, [1]), ("like", 1.3, 1.5, [3]), ("it.", 1.5, 1.9, [4])]
    assert (r["sentences"][0]["start"], r["sentences"][0]["end"]) == (0.5, 1.9)


# --- Word normalization ------------------------------------------------------------

def test_leading_spaces_stripped_and_fragments_merged_with_combined_timing():
    tokens = [(" I", 0.0, 0.2, 0.9), (" don", 0.2, 0.4, 0.8), ("'t", 0.4, 0.5, 0.7), (" think", 0.5, 0.8),
              (" he's", 0.8, 1.0), (" a", 1.0, 1.1), (" grown", 1.1, 1.4), ("-up.", 1.4, 1.7, 0.6)]
    r = process_transcript(raw_of(seg(tokens)))
    words = all_words(r)
    assert [w["word"] for w in words] == ["I", "don't", "think", "he's", "a", "grown-up."]
    assert (words[1]["start"], words[1]["end"], words[1]["probability"], words[1]["source"]) == (0.2, 0.5, 0.7, [1, 2])
    assert (words[5]["start"], words[5]["end"], words[5]["source"]) == (1.1, 1.7, [6, 7])


def test_contraction_split_with_a_space_is_merged():
    tokens = [(" They", 0.0, 0.2), (" can", 0.2, 0.4), (" 't", 0.4, 0.5), (" stay", 0.5, 0.9), (" we", 0.9, 1.0), (" 're", 1.0, 1.1), (" told.", 1.1, 1.5)]
    assert texts(process_transcript(raw_of(seg(tokens)))) == ["They can't stay we're told."]


def test_curly_quotes_and_apostrophes_become_straight():
    tokens = [(" It’s", 0.0, 0.3), (" “fine”", 0.3, 0.7), (" isn’t", 0.7, 1.0), (" it?", 1.0, 1.2)]
    assert texts(process_transcript(raw_of(seg(tokens)))) == ['It\'s "fine" isn\'t it?']


def test_fragments_are_not_merged_across_segments():
    r = process_transcript(raw_of(seg(timed("I said yes.")), seg([("-and", 1.5, 1.8), (" then", 1.8, 2.0), (" left.", 2.0, 2.3)])))
    assert [w["word"] for w in all_words(r)][:4] == ["I", "said", "yes.", "-and"]


# --- Numbers ------------------------------------------------------------------------

@pytest.mark.parametrize("token, following, spoken", [
    ("5", "", "five"),
    ("150", "", "one hundred and fifty"),
    ("1,000", "", "one thousand"),
    ("3.5", "", "three point five"),
    ("3.125", "", "three point one two five"),
    ("1st", "", "first"),
    ("22nd", "", "twenty-second"),
    ("1990", "", "nineteen ninety"),
    ("2024", "", "twenty twenty-four"),
    ("1990s", "", "nineteen nineties"),
    ("'90s", "", "nineties"),
    ("80s", "", "eighties"),
    ("$20", "", "twenty dollars"),
    ("$1", "", "one dollar"),
    ("£5.50", "", "five pounds fifty"),
    ("$5", "million", "five"),
    ("50%", "", "fifty percent"),
    ("2.5%", "", "two point five percent"),
    ("10.30", "", "ten thirty"),
    ("11.00", "", "eleven"),
    ("12:05", "", "twelve oh five"),
    ("1.25", "million", "one point two five"),
])
def test_number_forms(token, following, spoken):
    assert " ".join(number_to_words(token, following)) == spoken


@pytest.mark.parametrize("token", ["10-30", "MP3", "COVID-19", "24/7", "007", "07700900123", "4K"])
def test_ambiguous_numbers_are_left_as_written(token):
    assert number_to_words(token) is None


def test_number_words_keep_the_token_timing_and_punctuation():
    tokens = [(" It", 0.0, 0.2), (" cost", 0.2, 0.5), (" $20,", 0.5, 1.1), (" about", 1.1, 1.4), (" 50%", 1.4, 2.0), (" more.", 2.0, 2.4)]
    words = all_words(process_transcript(raw_of(seg(tokens))))
    assert [w["word"] for w in words] == ["It", "cost", "twenty", "dollars,", "about", "fifty", "percent", "more."]
    assert [(w["start"], w["end"], w["source"]) for w in words[2:4]] == [(0.5, 1.1, [2]), (0.5, 1.1, [2])]
    assert [(w["start"], w["end"]) for w in words[5:7]] == [(1.4, 2.0), (1.4, 2.0)]


def test_currency_with_scale_word_puts_the_unit_after_it():
    tokens = [(" A", 0.0, 0.2), (" $5", 0.2, 0.6), (" million", 0.6, 1.0), (" deal.", 1.0, 1.4)]
    assert texts(process_transcript(raw_of(seg(tokens)))) == ["A five million dollars deal."]


def test_time_split_into_tokens_is_merged_then_read_as_a_time():
    tokens = [(" At", 0.0, 0.2), (" 10", 0.2, 0.5), (".30?", 0.5, 0.9)]
    assert texts(process_transcript(raw_of(seg(tokens)))) == ["At ten thirty?"]


def test_ambiguous_numbers_are_reported_and_conversion_can_be_switched_off():
    raw = raw_of(seg(timed("British people say 10-30 and 5 more.")))
    r = process_transcript(raw)
    assert texts(r) == ["British people say 10-30 and five more."]
    assert r["numbers_left_as_written"] == ["10-30"]
    assert texts(process_transcript(raw, {"numbers_to_words": False})) == ["British people say 10-30 and 5 more."]


# --- Segmentation ------------------------------------------------------------------

def test_splits_at_sentence_punctuation_with_word_timing():
    r = process_transcript(raw_of(seg(timed("I came home. Was it late? Yes! Then we slept."))))
    assert texts(r) == ["I came home.", "Was it late?", "Yes!", "Then we slept."]
    first = r["sentences"][0]
    assert (first["start"], first["end"]) == (first["words"][0]["start"], first["words"][-1]["end"])
    assert [s["index"] for s in r["sentences"]] == [0, 1, 2, 3]


def test_abbreviations_and_decimals_do_not_end_sentences():
    r = process_transcript(raw_of(seg(timed("Mr. Smith met Dr. Jones at 3 p.m. on Friday, then flew to the U.S. etc. And that was 2.5 hours."))),
                           {"numbers_to_words": False})
    assert texts(r) == ["Mr. Smith met Dr. Jones at 3 p.m. on Friday, then flew to the U.S. etc.", "And that was 2.5 hours."]


def test_ellipsis_ends_a_sentence_only_before_a_capital():
    r = process_transcript(raw_of(seg(timed("I was going to... well, never mind... You know what I mean."))))
    assert texts(r) == ["I was going to... well, never mind...", "You know what I mean."]


def test_pause_ends_a_sentence_only_where_whisper_left_no_punctuation():
    unpunctuated = timed("so we went to the shop", 0.0) + timed("and then we came home", 2.5)  # 0.7+ s pause
    assert texts(process_transcript(raw_of(seg(unpunctuated)))) == ["so we went to the shop", "and then we came home"]
    punctuated = timed("So we went to the shop", 0.0) + timed("and then we came home.", 2.5)
    assert texts(process_transcript(raw_of(seg(punctuated)))) == ["So we went to the shop and then we came home."]


def test_long_silence_always_ends_a_sentence():
    tokens = timed("So we went to the shop", 0.0) + timed("and then we came home.", 4.0)  # 2.2 s silence
    assert texts(process_transcript(raw_of(seg(tokens)))) == ["So we went to the shop", "and then we came home."]


def test_long_sentence_split_at_comma_nearest_the_middle():
    text = "When we got there, the shop was closed, so we walked all the way back home in the rain again."
    r = process_transcript(raw_of(seg(timed(text))), {"sentence_max_words": 12})
    assert texts(r) == ["When we got there, the shop was closed,", "so we walked all the way back home in the rain again."]


def test_long_sentence_without_comma_split_at_longest_real_pause():
    tokens = timed("we walked to the old shop on the corner", 0.0, 0.25) + timed("to buy some bread and milk for the morning", 3.0, 0.25)
    r = process_transcript(raw_of(seg(tokens)), {"sentence_max_words": 12})
    assert texts(r) == ["we walked to the old shop on the corner", "to buy some bread and milk for the morning"]


def test_long_sentence_without_comma_or_pause_split_before_a_joining_word():
    text = "I was a good girl at school because I did all the clubs and took part in everything"
    r = process_transcript(raw_of(seg(timed(text))), {"sentence_max_words": 12})
    assert texts(r) == ["I was a good girl at school", "because I did all the clubs and took part in everything"]


def test_max_seconds_also_splits():
    r = process_transcript(raw_of(seg(timed("one two three four five six seven eight", step=1.5))),
                           {"sentence_max_seconds": 6.0, "numbers_to_words": False})
    assert all(s["end"] - s["start"] <= 6.0 for s in r["sentences"])
    assert " ".join(texts(r)) == "one two three four five six seven eight"


def test_short_standalone_expressions_are_kept():
    r = process_transcript(raw_of(seg(timed("Wow! That was a great game. Yes. I agree with you."))))
    assert texts(r) == ["Wow!", "That was a great game.", "Yes.", "I agree with you."]


def test_short_fragment_merges_into_the_closer_neighbour():
    tokens = timed("so I went to the market", 0.0) + timed("and bought", 2.6) + [(" apples", 3.3, 3.6), (" there", 3.6, 3.9)]
    r = process_transcript(raw_of(seg(tokens)))
    assert texts(r) == ["so I went to the market", "and bought apples there"]


def test_short_fragment_is_not_merged_across_a_long_silence():
    tokens = timed("I really liked it.", 0.0) + timed("well", 5.0)
    assert texts(process_transcript(raw_of(seg(tokens)))) == ["I really liked it.", "well"]


def test_word_order_and_timestamps_are_preserved_across_segments():
    raw = raw_of(seg(timed("Good morning everyone. Today we", 0.0)), seg(timed("talk about trains.", 1.8)))
    r = process_transcript(raw)
    assert texts(r) == ["Good morning everyone.", "Today we talk about trains."]
    raw_words = [w for s in raw["segments"] for w in s["words"]]
    for w in all_words(r):
        assert w["start"] == raw_words[w["source"][0]]["start"] and w["end"] == raw_words[w["source"][-1]]["end"]


# --- Hallucination filter ----------------------------------------------------------

def test_hallucination_rules_flag_the_right_segments():
    segments = [
        seg(timed("This is fine."), nsp=0.01, logprob=-0.2),
        seg(timed("Thank you."), nsp=0.8, logprob=-1.2),                        # no speech
        seg(timed("la la la la la la la la"), cr=3.1),                          # compression ratio
        seg([(" Mm", 0, 0.2, 0.1), (" ok", 0.2, 0.4, 0.2)]),                    # low word probability
        seg(timed("you know what I mean you know what I mean you know what I mean you know what I mean")),
        seg(timed("Thanks for watching!"), nsp=0.5, logprob=-0.3),              # known phrase, low confidence
        seg(timed("Thanks for watching!"), nsp=0.05, logprob=-0.2),             # known phrase, confident: kept
    ]
    assert find_hallucinations(segments, default_settings()) == {
        1: ["no_speech"], 2: ["compression_ratio", "repetition_loop"], 3: ["low_word_probability"],
        4: ["repetition_loop"], 5: ["known_hallucination"]}


def test_the_conservative_cases_are_not_flagged():
    segments = [
        seg([(" Perfect.", 0, 0.5, 0.34)]),                                     # short reply, low-ish probability
        seg(timed("no, no, no, no, no!")),                                      # single word, 5 times
        seg(timed("Hang on. Let me check. Hang on. Let me check.")),            # a line genuinely said twice
        seg(timed("my nature child of the wild"), nsp=0.48, logprob=-0.49),     # sung, low-ish confidence
    ]
    assert find_hallucinations(segments, default_settings()) == {}


def test_the_same_segment_repeated_more_than_three_times_is_a_loop():
    segments = [seg(timed("I love you.", i * 2.0)) for i in range(6)]
    flagged = find_hallucinations(segments, default_settings())
    assert sorted(flagged) == [3, 4, 5]  # the first three are kept


def test_excluded_segments_stay_in_raw_and_act_as_sentence_breaks():
    raw = raw_of(seg(timed("I went to the", 0.0)), seg(timed("Thank you.", 1.3), nsp=0.9, logprob=-1.5),
                 seg(timed("shop yesterday.", 2.0)))
    before = copy.deepcopy(raw)
    r = process_transcript(raw)
    assert raw == before
    assert texts(r) == ["I went to the", "shop yesterday."]
    assert r["excluded_segments"] == 1
    assert r["excluded"] == [{"segment": 1, "start": 1.3, "end": 1.9, "text": "Thank you.", "reasons": ["no_speech"]}]
    assert "Thank you." in r["original_transcript"] and "Thank you." not in r["training_transcript"]


def test_empty_results_say_why():
    assert process_transcript(raw_of())["empty_reason"] == "no_speech"
    all_bad = process_transcript(raw_of(seg(timed("Thank you."), nsp=0.9, logprob=-1.5)))
    assert all_bad["empty_reason"] == "all_excluded" and all_bad["sentences"] == []
    assert process_transcript(raw_of(seg(timed("[Music]"))))["empty_reason"] == "nothing_left"
    assert process_transcript(raw_of(seg(timed("Hello there."))))["empty_reason"] is None


# --- Settings and output ----------------------------------------------------------

def test_unknown_setting_is_rejected():
    with pytest.raises(ValueError):
        process_transcript(raw_of(), {"remove_filers": False})


def test_output_records_settings_versions_and_both_transcripts():
    r = process_transcript(raw_of(seg(timed("Um, hello there."))), {"remove_fillers": False})
    assert r["processing_settings"]["remove_fillers"] is False
    assert r["processing_version"] == config.PROCESSING_VERSION
    assert r["original_transcript"] == "Um, hello there."
    assert r["training_transcript"] == "Um, hello there."
    json.dumps(r)  # serializable as-is


# --- Real transcriber output (tests/fixtures) -------------------------------------

@pytest.fixture(scope="module", params=["conversation", "music", "numbers"])
def real(request):
    raw = json.loads((FIXTURES / f"{request.param}.json").read_text(encoding="utf-8"))["raw"]
    before = copy.deepcopy(raw)
    result = process_transcript(raw)
    assert raw == before, "the raw result must not be modified"
    return request.param, raw, result


def test_real_clips_have_no_false_hallucination_flags(real):
    _, _, r = real
    assert r["excluded_segments"] == 0


def test_real_clips_keep_every_word_with_its_original_timestamps(real):
    _, raw, r = real
    raw_words = [w for s in raw["segments"] for w in s["words"]]
    used = set()
    for w in all_words(r):
        first, last = raw_words[w["source"][0]], raw_words[w["source"][-1]]
        assert (w["start"], w["end"]) == (first["start"], max(x["end"] for x in (raw_words[i] for i in w["source"])))
        assert first["start"] <= last["start"]
        used.update(w["source"])
    assert used == set(range(len(raw_words)))  # nothing dropped (no fillers or annotations in these clips)


def test_real_clips_sentences_are_ordered_and_sized(real):
    _, _, r = real
    s = default_settings()
    prev_end = -1.0
    for st in r["sentences"]:
        assert st["start"] <= st["end"]
        assert st["start"] >= prev_end
        prev_end = st["end"]
        assert len(st["words"]) <= s["sentence_max_words"] + s["sentence_min_words"]  # merges may overshoot slightly


def test_real_numbers_clip_reads_times_as_words():
    raw = json.loads((FIXTURES / "numbers.json").read_text(encoding="utf-8"))["raw"]
    r = process_transcript(raw)
    text = r["training_transcript"]
    assert "ten thirty" in text and "twelve thirty" in text and "forty-five minutes" in text
    assert "one p.m., two p.m., three p.m., etc." in text
    assert re.findall(r"\S*\d\S*", text) == ["10-30.", "10-30."]  # the only ambiguous tokens, left as said
