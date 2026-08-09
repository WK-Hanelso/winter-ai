"""CLI for extracting speech-style traits from a Reference transcript."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from companion.reference_speech_style import (
    ReferenceSpeechStyleError,
    SpeechStyleProfile,
    compare_profiles,
    profile_transcript,
    public_style_summary,
)
from companion.reference_subtitle_probe import (
    ReferenceSubtitleProbeError,
    parse_webvtt,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure speech-style traits from a Reference transcript.",
    )
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--transcript",
        type=Path,
        required=True,
        help="primary transcript (whisper VTT)",
    )
    parser.add_argument(
        "--secondary-transcript",
        type=Path,
        help="independent transcript used only to flag tool-specific artefacts",
    )
    parser.add_argument("--primary-label", default="whisper")
    parser.add_argument("--secondary-label", default="service")
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument(
        "--report-path",
        type=Path,
        help="write the full report here; must be outside the repository",
    )
    return parser


def _profile(path: Path, label: str, top_n: int) -> SpeechStyleProfile:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReferenceSpeechStyleError(f"could not read {label} transcript") from error
    return profile_transcript(label, parse_webvtt(document), top_n=top_n)


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        primary = _profile(arguments.transcript, arguments.primary_label, arguments.top_n)
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "source_id": arguments.source_id,
            "primary": public_style_summary(primary),
        }
        if arguments.secondary_transcript:
            secondary = _profile(
                arguments.secondary_transcript, arguments.secondary_label, arguments.top_n
            )
            payload["secondary"] = public_style_summary(secondary)
            payload["comparison"] = compare_profiles(primary, secondary)
        if arguments.report_path:
            destination = arguments.report_path
            descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
    except (ReferenceSpeechStyleError, ReferenceSubtitleProbeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    except OSError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    payload["status"] = "ok"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
