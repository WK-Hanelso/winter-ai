"""One-time-safe merge for conversation databases split by old interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from companion.adapters.sqlite_repository import SqliteConversationRepository


@dataclass(frozen=True)
class MergeResult:
    canonical_before: int
    legacy_before: int
    merged: int
    duplicates: int


def merge_conversation_databases(legacy: Path, canonical: Path) -> MergeResult:
    """Merge exact unique messages chronologically, retaining both source files.

    The old web and CLI stores assigned unrelated integer ids, so ids cannot be
    used to establish order.  Their UTC ``created_at`` values can.  An exact
    role/content/timestamp duplicate is one event; repeated words at different
    times are kept because they are different conversation events.
    """
    if legacy.resolve() == canonical.resolve():
        raise ValueError("legacy and canonical conversation paths must differ")
    if not legacy.exists():
        SqliteConversationRepository(canonical)
        count = len(_read(canonical))
        return MergeResult(count, 0, count, 0)

    SqliteConversationRepository(canonical)
    canonical_rows = _read(canonical)
    legacy_rows = _read(legacy)
    combined = canonical_rows + legacy_rows
    unique = sorted(set(combined), key=lambda row: (row[2], row[0], row[1]))

    try:
        with sqlite3.connect(canonical) as connection:
            connection.execute("DELETE FROM conversation_messages")
            connection.execute(
                "DELETE FROM sqlite_sequence WHERE name = 'conversation_messages'"
            )
            connection.executemany(
                """
                INSERT INTO conversation_messages (role, content, created_at)
                VALUES (?, ?, ?)
                """,
                unique,
            )
    except sqlite3.Error as error:
        raise RuntimeError(f"could not merge conversations into {canonical}: {error}") from error

    return MergeResult(
        canonical_before=len(canonical_rows),
        legacy_before=len(legacy_rows),
        merged=len(unique),
        duplicates=len(combined) - len(unique),
    )


def _read(path: Path) -> list[tuple[str, str, str]]:
    try:
        with sqlite3.connect(path) as connection:
            return [
                (str(role), str(content), str(created_at))
                for role, content, created_at in connection.execute(
                    """
                    SELECT role, content, created_at
                    FROM conversation_messages
                    ORDER BY created_at, id
                    """
                )
            ]
    except sqlite3.Error as error:
        raise RuntimeError(f"could not read conversation database {path}: {error}") from error
