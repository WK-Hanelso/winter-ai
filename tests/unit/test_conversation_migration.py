from pathlib import Path
import sqlite3

from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.conversation_migration import merge_conversation_databases


def add(path: Path, role: str, content: str, created_at: str) -> None:
    SqliteConversationRepository(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO conversation_messages (role, content, created_at)
            VALUES (?, ?, ?)
            """,
            (role, content, created_at),
        )


def rows(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as connection:
        return list(
            connection.execute(
                "SELECT role, content, created_at FROM conversation_messages ORDER BY id"
            )
        )


def test_merge_preserves_chronology_and_removes_only_exact_duplicates(tmp_path: Path) -> None:
    legacy = tmp_path / "conversation.sqlite"
    canonical = tmp_path / "conversations.sqlite"
    add(canonical, "user", "나중 CLI", "2026-08-14 17:00:00")
    add(legacy, "user", "먼저 웹", "2026-08-13 09:00:00")
    add(legacy, "user", "나중 CLI", "2026-08-14 17:00:00")
    add(legacy, "user", "나중 CLI", "2026-08-14 18:00:00")

    result = merge_conversation_databases(legacy, canonical)

    assert result.merged == 3
    assert result.duplicates == 1
    assert rows(canonical) == [
        ("user", "먼저 웹", "2026-08-13 09:00:00"),
        ("user", "나중 CLI", "2026-08-14 17:00:00"),
        ("user", "나중 CLI", "2026-08-14 18:00:00"),
    ]
    assert legacy.exists()

    first_merge = rows(canonical)
    repeated = merge_conversation_databases(legacy, canonical)
    assert repeated.merged == 3
    assert rows(canonical) == first_merge
