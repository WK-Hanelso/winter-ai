"""CLI for the audio-only pilot ingest of one approved source."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from companion.reference_audio_ingest import (
    ReferenceAudioIngestError,
    YtDlpAudioDownloader,
    dump_ingest_record,
    ingest_source_audio,
    public_ingest_summary,
)
from companion.reference_storage import ReferenceStorageError, load_reference_storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download one approved source's audio stream without re-encoding.",
    )
    parser.add_argument("--storage-root", type=Path, default=Path("/reference-data"))
    parser.add_argument(
        "--expected-storage-id",
        default=os.environ.get("REFERENCE_STORAGE_ID"),
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument(
        "--source-id",
        required=True,
        help="exactly one source; a mistake then costs one file",
    )
    parser.add_argument("--report-relative-path", required=True)
    parser.add_argument(
        "--expected-duration-seconds",
        type=float,
        help="known duration used to reject an implausibly small download",
    )
    parser.add_argument("--repository-root", type=Path, default=Path("/workspace"))
    parser.add_argument("--yt-dlp-executable", default="yt-dlp")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.expected_storage_id:
        raise SystemExit("REFERENCE_STORAGE_ID or --expected-storage-id is required")
    try:
        storage = load_reference_storage(
            arguments.storage_root,
            arguments.repository_root,
            expected_storage_id=arguments.expected_storage_id,
        )
        storage, record = ingest_source_audio(
            storage,
            arguments.candidate_id,
            arguments.source_id,
            YtDlpAudioDownloader(arguments.yt_dlp_executable),
            generated_at=datetime.now(UTC).isoformat(),
            expected_duration_seconds=arguments.expected_duration_seconds,
        )
        dump_ingest_record(storage, arguments.report_relative_path, record)
    except (ReferenceStorageError, ReferenceAudioIngestError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    summary = public_ingest_summary(record)
    summary["status"] = "ok"
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
