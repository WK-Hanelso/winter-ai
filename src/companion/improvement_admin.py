"""Privacy-minimal audit view of Winter's persistent Improvement Ledger."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
import os
from pathlib import Path
import sys

from companion.improvements import ImprovementLedgerError, SqliteImprovementLedger


def build_parser() -> argparse.ArgumentParser:
    data = Path(os.environ.get("WINTER_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=data / "improvements.sqlite")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        items = SqliteImprovementLedger(arguments.ledger).list()
    except ImprovementLedgerError as error:
        print(f"improvement ledger unavailable: {error}", file=sys.stderr)
        return 1
    statuses = Counter(item.status for item in items)
    print(f"improvements: total={len(items)}")
    for status in sorted(statuses):
        print(f"{status}: {statuses[status]}")
    print(f"proposal_versions: {sum(len(item.proposals) for item in items)}")
    print(f"implementation_approvals: {sum(len(item.decisions) for item in items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
