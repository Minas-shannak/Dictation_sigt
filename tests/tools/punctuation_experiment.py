"""Punctuation-restoration experiment: before/after sentences for saved raw results.

Model: 1-800-BAD-CODE/punctuation_fullstop_truecase_english (Apache-2.0, ONNX, 210 MB).
Needs onnxruntime and sentencepiece, which are NOT in requirements.txt; run it with a
separate venv:

    uv venv --python 3.12 /tmp/punct && uv pip install --python /tmp/punct/bin/python \\
        "onnxruntime-gpu[cuda,cudnn]==1.31.1" sentencepiece num2words
    /tmp/punct/bin/python tests/tools/punctuation_experiment.py <model dir> raw1.json [raw2.json ...] [--cpu]

<model dir> holds punct_cap_seg_en.onnx, spe_32k_lc_en.model and config.yaml from the model repo.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ai.transcript_processor import process_transcript  # noqa: E402

POST_LABELS = ["<NULL>", "<ACRONYM>", ".", ",", "?"]  # from the model's config.yaml
MAX_TOKENS = 256  # the model was trained on up to 256 tokens (including BOS/EOS)
CONTEXT = 24      # words of context kept on each side of a window, predictions discarded


class OnnxRestorer:
    """Restorer for ai/punctuation.py: lowercase words in, one punctuated/cased string per word out."""

    def __init__(self, model_dir: str, gpu: bool):
        import onnxruntime as ort
        import sentencepiece as spm
        if gpu:
            ort.preload_dlls()
        options = ort.SessionOptions()
        options.log_severity_level = 3
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if gpu else ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(Path(model_dir) / "punct_cap_seg_en.onnx"), options, providers=providers)
        self.sp = spm.SentencePieceProcessor(model_file=str(Path(model_dir) / "spe_32k_lc_en.model"))
        self.providers = self.session.get_providers()
        self.seconds = 0.0
        self.words = 0

    def _run(self, words):
        """Model output for each word of one window (fits in MAX_TOKENS)."""
        pieces = [self.sp.encode(w) for w in words]
        ids = [self.sp.bos_id()] + [t for p in pieces for t in p] + [self.sp.eos_id()]
        _, post, cap, _ = self.session.run(None, {"input_ids": np.array([ids], dtype=np.int64)})
        out, pos = [], 1
        for p in pieces:
            text = ""
            for tok in p:
                piece = self.sp.id_to_piece(tok)
                first = 1 if piece.startswith("▁") else 0
                for ci in range(first, len(piece)):
                    text += piece[ci].upper() if ci < cap.shape[2] and cap[0, pos, ci] else piece[ci]
                pos += 1
            label = POST_LABELS[post[0, pos - 1]] if p else "<NULL>"
            out.append(text + (label if label in ".,?" else ""))
        return out

    def __call__(self, words):
        t0 = time.monotonic()
        lengths = [len(self.sp.encode(w)) for w in words]
        result = [None] * len(words)
        start = 0
        while start < len(words):
            # Window = CONTEXT words before + as many core words as fit + CONTEXT words after.
            lo = max(0, start - CONTEXT)
            budget = MAX_TOKENS - 2 - sum(lengths[lo:start])
            end = start
            while end < len(words) and budget - lengths[end] >= sum(lengths[end + 1:end + 1 + CONTEXT]):
                budget -= lengths[end]
                end += 1
            end = max(end, start + 1)
            hi = min(len(words), end + CONTEXT)
            out = self._run(words[lo:hi])
            result[start:end] = out[start - lo:end - lo]
            start = end
        self.seconds += time.monotonic() - t0
        self.words += len(words)
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model_dir")
    ap.add_argument("raw", nargs="+")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--range", nargs=2, type=float, default=None, help="only print sentences starting in this span (s)")
    args = ap.parse_args()
    t0 = time.monotonic()
    restorer = OnnxRestorer(args.model_dir, gpu=not args.cpu)
    print(f"model loaded in {time.monotonic() - t0:.2f} s; providers {restorer.providers}")
    report = {}
    for path in args.raw:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw = data.get("raw", data)
        before = process_transcript(raw)
        restorer.seconds = restorer.words = 0
        after = process_transcript(raw, punctuate=restorer)
        print(f"\n=== {Path(path).name}: {restorer.words} unpunctuated words restored in {restorer.seconds:.3f} s; "
              f"{len(before['sentences'])} -> {len(after['sentences'])} sentences")
        lo, hi = args.range or (-1, 1e9)
        show = lambda r: [(s["start"], s["split_reason"], s["text"]) for s in r["sentences"] if lo <= s["start"] <= hi]
        report[path] = {"before": show(before), "after": show(after), "words": restorer.words, "seconds": restorer.seconds}
        print("--- before"); [print(f"  {t:7.2f} <{why}> {x}") for t, why, x in report[path]["before"]]
        print("--- after"); [print(f"  {t:7.2f} <{why}> {x}") for t, why, x in report[path]["after"]]
    return report


if __name__ == "__main__":
    main()
