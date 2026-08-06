from pathlib import Path
import sqlite3

import pytest

from companion.adapters.sqlite_repository import (
    ConversationRepositoryError,
    SqliteConversationRepository,
)
from companion.contracts import ConversationMessage


def test_sqlite_repository_persists_ordered_messages_across_instances(tmp_path: Path) -> None:
    database_path = tmp_path / "conversations.sqlite"
    repository = SqliteConversationRepository(database_path)
    first = ConversationMessage(role="user", content="첫 메시지")
    second = ConversationMessage(role="assistant", content="둘째 메시지")

    repository.append(first)
    repository.append(second)

    reopened_repository = SqliteConversationRepository(database_path)
    assert reopened_repository.list_messages() == (first, second)


def test_sqlite_repository_creates_schema_version(tmp_path: Path) -> None:
    database_path = tmp_path / "conversations.sqlite"
    SqliteConversationRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]


def test_sqlite_repository_rejects_newer_schema_version(tmp_path: Path) -> None:
    database_path = tmp_path / "conversations.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_migrations (version) VALUES (999)")

    with pytest.raises(ConversationRepositoryError, match="unsupported schema version 999"):
        SqliteConversationRepository(database_path)
