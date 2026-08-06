"""SQLite implementation of the shared conversation repository port."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from companion.contracts import ConversationMessage


class ConversationRepositoryError(RuntimeError):
    """Raised when the selected conversation store cannot be used."""


class SqliteConversationRepository:
    """Stores ordered conversation messages in a local SQLite database."""

    _SCHEMA_VERSION = 1

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize()

    def append(self, message: ConversationMessage) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO conversation_messages (role, content) VALUES (?, ?)",
                    (message.role, message.content),
                )
        except sqlite3.Error as error:
            raise ConversationRepositoryError(
                f"could not save conversation to {self._database_path}: {error}"
            ) from error

    def list_messages(self) -> tuple[ConversationMessage, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT role, content FROM conversation_messages ORDER BY id"
                ).fetchall()
        except sqlite3.Error as error:
            raise ConversationRepositoryError(
                f"could not read conversation from {self._database_path}: {error}"
            ) from error
        return tuple(ConversationMessage(role=row["role"], content=row["content"]) for row in rows)

    def _initialize(self) -> None:
        try:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER NOT NULL)"
                )
                version_row = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
                ).fetchone()
                if version_row is not None and version_row["version"] > self._SCHEMA_VERSION:
                    raise ConversationRepositoryError(
                        f"conversation database {self._database_path} uses unsupported "
                        f"schema version {version_row['version']}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                if version_row is None:
                    connection.execute(
                        "INSERT INTO schema_migrations (version) VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
        except ConversationRepositoryError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ConversationRepositoryError(
                f"could not initialize conversation database {self._database_path}: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection
