"""CLI for registering approved candidates and sources in the private manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from companion.reference_registry import (
    ReferenceRegistryError,
    next_source_id,
    public_registry_summary,
    register_candidate,
    register_source,
)
from companion.reference_storage import ReferenceStorageError, load_reference_storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register approved reference candidates and sources.",
    )
    parser.add_argument("--storage-root", type=Path, default=Path("/reference-data"))
    parser.add_argument(
        "--expected-storage-id",
        default=os.environ.get("REFERENCE_STORAGE_ID"),
    )
    parser.add_argument("--repository-root", type=Path, default=Path("/workspace"))
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--list", action="store_true", help="print IDs and counts only")
    actions.add_argument("--add-candidate", metavar="CANDIDATE_ID")
    actions.add_argument("--add-source", metavar="CANDIDATE_ID")
    parser.add_argument("--identity-ref", help="private identity reference for a candidate")
    parser.add_argument(
        "--source-uri",
        help="private source URI; never written to Git, stdout or a report",
    )
    parser.add_argument(
        "--source-id",
        help="explicit source ID; the next free source-NNN is used when omitted",
    )
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
        assigned_source_id = None
        if arguments.add_candidate:
            if not arguments.identity_ref:
                raise ReferenceRegistryError("--identity-ref is required for a candidate")
            storage = register_candidate(
                storage, arguments.add_candidate, arguments.identity_ref
            )
        elif arguments.add_source:
            if not arguments.source_uri:
                raise ReferenceRegistryError("--source-uri is required for a source")
            assigned_source_id = arguments.source_id or next_source_id(storage.manifest)
            storage = register_source(
                storage,
                arguments.add_source,
                assigned_source_id,
                arguments.source_uri,
            )
    except (ReferenceStorageError, ReferenceRegistryError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    summary = public_registry_summary(storage.manifest)
    summary["status"] = "ok"
    if assigned_source_id:
        summary["registered_source_id"] = assigned_source_id
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
