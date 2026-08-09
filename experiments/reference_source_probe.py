"""CLI for a no-download Human Reference source metadata probe."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from companion.reference_source_probe import (
    DEFAULT_DURATION_CAP_SECONDS,
    ReferenceSourceProbeError,
    YtDlpMetadataRunner,
    dump_private_probe_report,
    probe_candidate_sources,
    public_probe_summary,
)
from companion.reference_storage import ReferenceStorageError, load_reference_storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe approved private source metadata without downloading media.",
    )
    parser.add_argument("--storage-root", type=Path, default=Path("/reference-data"))
    parser.add_argument(
        "--expected-storage-id",
        default=os.environ.get("REFERENCE_STORAGE_ID"),
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--report-relative-path", required=True)
    parser.add_argument(
        "--duration-cap-seconds",
        type=int,
        default=DEFAULT_DURATION_CAP_SECONDS,
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
        report = probe_candidate_sources(
            storage,
            arguments.candidate_id,
            YtDlpMetadataRunner(arguments.yt_dlp_executable),
            generated_at=datetime.now(UTC).isoformat(),
            duration_cap_seconds=arguments.duration_cap_seconds,
        )
        dump_private_probe_report(storage, arguments.report_relative_path, report)
    except (ReferenceStorageError, ReferenceSourceProbeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    summary = public_probe_summary(report)
    summary["status"] = "cap_exceeded" if report.cap_exceeded else "ok"
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if report.failures:
        return 2
    return 3 if report.cap_exceeded else 0


if __name__ == "__main__":
    raise SystemExit(main())
