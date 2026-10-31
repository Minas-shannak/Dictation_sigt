"""Compare transcriber fixtures against caption references (test tooling, not app code).

    python tests/tools/evaluate_fixtures.py

tests/reference/*.json hold YouTube captions for the test clips. They are used
here only as a reference for what was said; the app never reads captions.

For each fixture: word error rate of the processed training transcript against
the reference (both normalized the same way), punctuation counts in the raw
output, timing agreement of matched words with the reference lines, segments
the hallucination filter flagged, and kept text with no reference speech near it.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ai.transcript_processor import TOKEN_RE, number_to_words, process_transcript  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
REFERENCE = ROOT / "tests" / "reference"
CONTRACTIONS = {"gonna": "going to", "wanna": "want to", "gotta": "got to", "ok": "okay", "cos": "because"}
FILLERS = re.compile(r"(u+m+|u+h+|e+r+|e+r+m+|h+m+|m+h*m+)")


def norm_tokens(text):
    """Lowercase words without punctuation; numbers as words; fillers dropped."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|>>|♪", " ", text).translate(str.maketrans("‘’“”", "''\"\""))
    out = []
    for tok in text.split():
        core = TOKEN_RE.match(tok).group(2)
        spoken = number_to_words(core) if re.search(r"\d", core) else None
        for part in (spoken or [core]):
            for piece in re.split(r"[-–—/]", part.lower()):
                w = re.sub(r"[^\w']", "", piece).strip("'")
                if w and not FILLERS.fullmatch(w):
                    out.extend(CONTRACTIONS.get(w, w).split())
    return out


def align(ref, hyp):
    """Word-level Levenshtein alignment: (substitutions, deletions, insertions, matched (ref_i, hyp_j) pairs)."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, s, dl, ins, pairs = n, m, 0, 0, 0, []
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] == hyp[j - 1]:
                pairs.append((i - 1, j - 1))
            else:
                s += 1
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            dl, i = dl + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return s, dl, ins, pairs


def evaluate(name, raw, ref):
    p = process_transcript(raw)
    words = [w for s in raw["segments"] for w in s["words"]]
    row = {
        "fixture": name,
        "model": raw["model"],
        "prompt": bool(raw["asr_settings"].get("initial_prompt")),
        "vad": raw["asr_settings"]["vad_filter"],
        "seconds": raw["transcribe_seconds"],
        "sentence_ends": sum(1 for w in words if re.search(r"[.?!…][\"')]*$", w["word"])),
        "commas": sum(1 for w in words if w["word"].rstrip("\"')").endswith(",")),
        "lowercase_i": sum(1 for w in words if re.fullmatch(r"i('m|'ll|'ve|'d)?[,.?!]*", w["word"].strip())),
        "sentences": len(p["sentences"]),
        "flagged": [(round(e["start"], 1), e["text"], "+".join(e["reasons"])) for e in p["excluded"]],
    }
    if not ref:
        return row, p
    lines = ref["lines"]
    first, last = lines[0]["start"], lines[-1]["start"] + lines[-1]["duration"]
    # Reference tokens, each tagged with its line's time window.
    ref_tok, ref_win = [], []
    for ln in lines:
        for t in norm_tokens(ln["text"]):
            ref_tok.append(t)
            ref_win.append((ln["start"], ln["start"] + ln["duration"]))
    # Hypothesis: training words inside the reference's time span.
    hyp_tok, hyp_time = [], []
    for st in p["sentences"]:
        for w in st["words"]:
            if first - 1.0 <= w["start"] <= last + 1.0:
                for t in norm_tokens(w["word"]):
                    hyp_tok.append(t)
                    hyp_time.append((w["start"], w["end"]))
    s, dl, ins, pairs = align(ref_tok, hyp_tok)
    # Timing: a matched word should overlap its reference line (captions are approximate: ±1.5 s).
    off = [j for i, j in pairs if not (hyp_time[j][1] >= ref_win[i][0] - 1.5 and hyp_time[j][0] <= ref_win[i][1] + 1.5)]
    # Kept sentences with no reference line within 2 s: possible hallucinations that slipped through.
    lonely = [(round(st["start"], 1), st["text"]) for st in p["sentences"]
              if not any(st["start"] <= ln["start"] + ln["duration"] + 2 and st["end"] >= ln["start"] - 2 for ln in lines)]
    # Flagged segments overlapping a reference line that contains their words: real speech wrongly flagged?
    wrong = []
    for e in p["excluded"]:
        etoks = norm_tokens(e["text"])
        for ln in lines:
            if e["start"] <= ln["start"] + ln["duration"] + 1 and e["end"] >= ln["start"] - 1 and etoks and \
                    all(t in norm_tokens(ln["text"]) for t in etoks):
                wrong.append((round(e["start"], 1), e["text"], ln["text"]))
                break
    row.update({
        "ref_words": len(ref_tok), "wer": round(100 * (s + dl + ins) / len(ref_tok), 1), "sub": s, "del": dl, "ins": ins,
        "timing_matched": len(pairs), "timing_off": len(off),
        "longest_word": round(max((w["end"] - w["start"] for w in words), default=0), 2),
        "unreferenced_sentences": lonely, "flagged_but_in_reference": wrong,
    })
    return row, p


def main():
    rows = []
    for path in sorted(FIXTURES.glob("*.json")) + sorted((FIXTURES / "experiments").glob("*.json")):
        clip = path.stem.split("_")[0]
        ref_path = REFERENCE / f"{clip}.json"
        ref = json.loads(ref_path.read_text(encoding="utf-8")) if ref_path.exists() else None
        raw = json.loads(path.read_text(encoding="utf-8"))["raw"]
        row, _ = evaluate(str(path.relative_to(FIXTURES)), raw, ref)
        row["reference"] = ref["caption_source"] if ref else None
        rows.append(row)
    json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
