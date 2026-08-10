"""Build (prompt, response) pairs from a word-level transcript and a speaker timeline.

This is the last step that was blocking conversational training data. Everything
it needs already exists on disk:

* word-level cues from whisper (``--word-timestamps``);
* a speaker timeline from the diarization probe;
* which speaker is the Reference, from the speaker probe.

No model runs here, so it works in the plain dev image. Pair text stays in
external storage; stdout carries counts only.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from companion.diarization import (
    DiarizationError,
    assign_words_to_speakers,
    build_exchanges,
    parse_spans,
    public_exchange_summary,
)
from companion.reference_subtitle_probe import parse_webvtt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--words-vtt", type=Path, required=True)
    parser.add_argument("--spans-path", type=Path, required=True)
    parser.add_argument("--reference-speaker", type=int, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--minimum-words", type=int, default=2)
    parser.add_argument(
        "--pairs-path",
        type=Path,
        help="write the pairs themselves here; must be outside the repository",
    )
    parser.add_argument("--report-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        words = parse_webvtt(arguments.words_vtt.read_text(encoding="utf-8"))
        raw = json.loads(arguments.spans_path.read_text(encoding="utf-8"))
        spans = parse_spans(tuple((row[0], row[1], int(row[2])) for row in raw["spans"]))
        turns = assign_words_to_speakers(words, spans)
        exchanges = build_exchanges(
            turns, arguments.reference_speaker, minimum_words=arguments.minimum_words
        )
        if not exchanges:
            raise DiarizationError("no exchanges could be built from this segment")
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "source_id": arguments.source_id,
            "reference_speaker": arguments.reference_speaker,
            "word_count": len(words),
            **public_exchange_summary(turns, exchanges, arguments.reference_speaker),
        }
    except (DiarizationError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    # Writing is inside the reporting path too: an existing file or a bad
    # directory used to raise past the handler and leave the caller with a
    # traceback and no explanation.
    try:
        _write_outputs(arguments, exchanges, payload)
    except OSError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    payload["status"] = "ok"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _write_outputs(arguments: argparse.Namespace, exchanges: tuple, payload: dict) -> None:
    for path, content in (
        (arguments.pairs_path, [
            {
                "prompt_speaker": exchange.prompt_speaker,
                "prompt": exchange.prompt_text,
                "response": exchange.response_text,
                "prompt_seconds": round(exchange.prompt_seconds, 2),
                "response_seconds": round(exchange.response_seconds, 2),
            }
            for exchange in exchanges
        ]),
        (arguments.report_path, payload),
    ):
        if path is None:
            continue
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(content, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
