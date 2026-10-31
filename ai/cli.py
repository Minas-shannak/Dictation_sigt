"""Transcribe a file from the command line, without Flask.

    python -m ai.cli sample.mp3 [--language en|auto] [--model small] [--words] [--twice]

Prints the device used, timings and the recognized segments. Sentence
segmentation comes with ai/transcript_processor.py (Phase 3).
"""

import argparse
import logging
import sys
import time

import config
from ai.transcriber import TranscriptionError, transcribe_audio


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai.cli", description=__doc__.splitlines()[0])
    parser.add_argument("audio", help="audio or video file")
    parser.add_argument("--language", default=config.DEFAULT_LANGUAGE, help='language code, or "auto" (default: %(default)s)')
    parser.add_argument("--model", default=None, help=f"Whisper model (default: {config.WHISPER_MODEL})")
    parser.add_argument("--words", action="store_true", help="also print every word with its timestamps")
    parser.add_argument("--twice", action="store_true", help="transcribe twice, to check the model is loaded only once")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "huggingface_hub"):  # one line per HTTP request otherwise
        logging.getLogger(noisy).setLevel(logging.WARNING)

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
        total = time.monotonic() - t0
        print(f"\nRun {run + 1}: model={raw['model']} device={raw['device']} compute_type={raw['compute_type']}")
        print(f"language={raw['language']} ({raw['language_probability']:.2f})  audio={raw['duration']:.1f}s "
              f"(after VAD {raw['duration_after_vad']:.1f}s)  transcribe={raw['transcribe_seconds']:.1f}s  total={total:.1f}s")
        for seg in raw["segments"]:
            print(f"[{seg['start']:7.2f} -> {seg['end']:7.2f}] {seg['text'].strip()}")
            if args.words:
                print("    " + " ".join(f"{w['word'].strip()}({w['start']:.2f}-{w['end']:.2f})" for w in seg["words"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
