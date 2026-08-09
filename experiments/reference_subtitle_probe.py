"""CLI for a text-only Korean subtitle quality probe."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from companion.reference_storage import ReferenceStorageError, load_reference_storage
from companion.reference_subtitle_probe import (
    ReferenceSubtitleProbeError,
    YtDlpSubtitleFetcher,
    dump_subtitle_probe_report,
    probe_candidate_subtitles,
    public_subtitle_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure Korean subtitle quality without downloading media.",
    )
    parser.add_argument("--storage-root", type=Path, default=Path("/reference-data"))
    parser.add_argument(
        "--expected-storage-id",
        default=os.environ.get("REFERENCE_STORAGE_ID"),
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--report-relative-path", required=True)
    parser.add_argument(
        "--duration",
        action="append",
        default=[],
        metavar="SOURCE_ID=SECONDS",
        help="known source duration used for subtitle coverage ratio",
    )
    parser.add_argument("--repository-root", type=Path, default=Path("/workspace"))
    parser.add_argument("--yt-dlp-executable", default="yt-dlp")
    return parser


def parse_durations(values: list[str]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for value in values:
        source_id, _, seconds = value.partition("=")
        if not source_id or not seconds:
            raise SystemExit("--duration must use SOURCE_ID=SECONDS form")
        try:
            durations[source_id] = float(seconds)
        except ValueError:
            raise SystemExit("--duration seconds must be a number") from None
    return durations


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.expected_storage_id:
        raise SystemExit("REFERENCE_STORAGE_ID or --expected-storage-id is required")
    durations = parse_durations(arguments.duration)
    try:
        storage = load_reference_storage(
            arguments.storage_root,
            arguments.repository_root,
            expected_storage_id=arguments.expected_storage_id,
        )
        report = probe_candidate_subtitles(
            storage,
            arguments.candidate_id,
            YtDlpSubtitleFetcher(arguments.yt_dlp_executable),
            generated_at=datetime.now(UTC).isoformat(),
            durations=durations,
        )
        dump_subtitle_probe_report(storage, arguments.report_relative_path, report)
    except (ReferenceStorageError, ReferenceSubtitleProbeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    summary = public_subtitle_summary(report)
    summary["status"] = "ok" if not report.failures else "partial_failure"
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
