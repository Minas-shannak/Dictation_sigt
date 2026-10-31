"""Transcribe a file from the command line, without Flask.

    python -m ai.cli sample.mp3 [--language en|auto] [--model small] [--words] [--twice]
    python -m ai.cli tests/fixtures/numbers.json      # reprocess a saved raw result, no transcription

Prints the device used, timings and the resulting dictation sentences.
"""

import argparse
import json
import logging
import sys
import time

import config
from ai.transcriber import TranscriptionError, transcribe_audio
from ai.transcript_processor import process_transcript


def print_sentences(processed: dict, words: bool) -> None:
    for st in processed["sentences"]:
        print(f"{st['index']:4} [{st['start']:7.2f} -> {st['end']:7.2f}] {st['text']}")
        if words:
            print("       " + " ".join(f"{w['word']}({w['start']:.2f}-{w['end']:.2f})" for w in st["words"]))
    for e in processed["excluded"]:
        print(f"  excluded segment {e['segment']} [{e['start']:.2f}-{e['end']:.2f}] ({', '.join(e['reasons'])}): {e['text']}")
    if processed["numbers_left_as_written"]:
        print(f"  numbers left as written: {', '.join(processed['numbers_left_as_written'])}")
    if processed["empty_reason"]:
        print(f"  no sentences: {processed['empty_reason']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai.cli", description=__doc__.splitlines()[0])
    parser.add_argument("audio", help="audio or video file, or a saved raw result (.json)")
    parser.add_argument("--language", default=config.DEFAULT_LANGUAGE, help='language code, or "auto" (default: %(default)s)')
    parser.add_argument("--model", default=None, help=f"Whisper model (default: {config.WHISPER_MODEL})")
    parser.add_argument("--words", action="store_true", help="also print every word with its timestamps")
    parser.add_argument("--twice", action="store_true", help="transcribe twice, to check the model is loaded only once")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "huggingface_hub"):  # one line per HTTP request otherwise
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.audio.endswith(".json"):
        with open(args.audio, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("raw", data)  # test fixtures wrap the raw result
        print(f"Raw result from {args.audio}: model={raw['model']} device={raw['device']} compute_type={raw['compute_type']}")
        print_sentences(process_transcript(raw), args.words)
        return 0

    language = None if args.language in ("", "auto") else args.language
    last_shown = [-1]

    def on_progress(fraction: float) -> None:
        pct = int(fraction * 100)
        if pct // 10 != last_shown[0] // 10 or pct == 100:
            last_shown[0] = pct
            print(f"  progress {pct}%", file=sys.stderr)

    for run in range(2 if args.twice else 1):
        t0 = time.monotonic()
        try:
            raw = transcribe_audio(args.audio, language=language, model_name=args.model,
                                   on_progress=on_progress, on_stage=lambda s: print(f"  stage: {s}", file=sys.stderr))
        except TranscriptionError as e:
            print(f"Error: {e.user_message}", file=sys.stderr)
            return 1
        processed = process_transcript(raw)
        total = time.monotonic() - t0
        print(f"\nRun {run + 1}: model={raw['model']} device={raw['device']} compute_type={raw['compute_type']}")
        print(f"language={raw['language']} ({raw['language_probability']:.2f})  audio={raw['duration']:.1f}s "
              f"(after VAD {raw['duration_after_vad']:.1f}s)  transcribe={raw['transcribe_seconds']:.1f}s  total={total:.1f}s")
        print_sentences(processed, args.words)
    return 0


if __name__ == "__main__":
    sys.exit(main())
