"""Merge per-chunk pair files into one dataset.

Chunks are produced independently — each identifies the Reference speaker on its
own — so the merged file is the first thing that can be split into train and
held-out across a whole source.

Duplicates are dropped. Re-running a chunk is normal while the pipeline is being
tuned, and the same exchange appearing twice would inflate every count that
follows.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path


class MergeError(RuntimeError):
    """Raised when pair files cannot be merged without guessing."""


def load_pairs(paths: list[Path]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(paths):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MergeError(f"could not read {path.name}") from error
        if not isinstance(rows, list):
            raise MergeError(f"{path.name} does not hold a list of pairs")
        for row in rows:
            key = (str(row.get("prompt", "")), str(row.get("response", "")))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            merged.append(row)
    if not merged:
        raise MergeError("no pairs found in the given files")
    return merged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-dir", type=Path, required=True)
    parser.add_argument("--pattern", default="pairs-*.json")
    parser.add_argument("--output-path", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        paths = [
            path
            for path in arguments.pairs_dir.glob(arguments.pattern)
            if path != arguments.output_path
        ]
        if not paths:
            raise MergeError("no pair files matched")
        merged = load_pairs(paths)
        descriptor = os.open(
            arguments.output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(merged, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
    except (MergeError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    responses = [str(row.get("response", "")) for row in merged]
    print(
        json.dumps(
            {
                "status": "ok",
                "generated_at": datetime.now(UTC).isoformat(),
                "source_files": len(paths),
                "pair_count": len(merged),
                "mean_response_words": round(
                    sum(len(text.split()) for text in responses) / len(responses), 2
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
