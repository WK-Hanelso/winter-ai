#!/usr/bin/env python3
"""Merge the retired web conversation DB into the shared canonical DB."""

from __future__ import annotations

import argparse
from pathlib import Path

from companion.conversation_migration import merge_conversation_databases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, default=Path("data/conversation.sqlite"))
    parser.add_argument("--canonical", type=Path, default=Path("data/conversations.sqlite"))
    arguments = parser.parse_args()

    result = merge_conversation_databases(arguments.legacy, arguments.canonical)
    print(
        f"canonical={result.canonical_before} legacy={result.legacy_before} "
        f"merged={result.merged} duplicates={result.duplicates}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
