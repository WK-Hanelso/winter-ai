"""SQLite implementation of the shared conversation repository port."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from companion.contracts import ConversationAppendReceipt, ConversationMessage


class ConversationRepositoryError(RuntimeError):
    """Raised when the selected conversation store cannot be used."""


class SqliteConversationRepository:
    """Stores ordered conversation messages in a local SQLite database."""

    _SCHEMA_VERSION = 1

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize()

    def append(self, message: ConversationMessage) -> ConversationAppendReceipt:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO conversation_messages (role, content) VALUES (?, ?)",
                    (message.role, message.content),
                )
                if cursor.lastrowid is None:
                    raise ConversationRepositoryError(
                        "conversation insert did not return a row id"
                    )
                return ConversationAppendReceipt(id=int(cursor.lastrowid))
        except ConversationRepositoryError:
            raise
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

    def append_once(self, key: str, message: ConversationMessage) -> ConversationAppendReceipt:
        """Add a notification receipt atomically, preserving all existing message rows."""
        if not key or len(key) > 200:
            raise ValueError("invalid conversation delivery key")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS conversation_deliveries "
                    "(delivery_key TEXT PRIMARY KEY, message_id INTEGER NOT NULL)"
                )
                row = connection.execute(
                    "SELECT m.id, m.role, m.content, m.created_at FROM conversation_deliveries d "
                    "JOIN conversation_messages m ON m.id = d.message_id WHERE d.delivery_key = ?",
                    (key,),
                ).fetchone()
                if row is not None:
                    if (row["role"], row["content"]) != (message.role, message.content):
                        raise ConversationRepositoryError(
                            "delivery key reused with different content"
                        )
                    return ConversationAppendReceipt(int(row["id"]), str(row["created_at"]))
                cursor = connection.execute(
                    "INSERT INTO conversation_messages (role, content) VALUES (?, ?)",
                    (message.role, message.content),
                )
                assert cursor.lastrowid is not None
                connection.execute("INSERT INTO conversation_deliveries VALUES (?, ?)",
                                   (key, cursor.lastrowid))
                timestamp = connection.execute(
                    "SELECT created_at FROM conversation_messages WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                assert timestamp is not None
                return ConversationAppendReceipt(
                    int(cursor.lastrowid), str(timestamp["created_at"]),
                )
        except sqlite3.Error as error:
            raise ConversationRepositoryError("could not persist notification") from error

    def recent_messages(self, limit: int) -> tuple[ConversationMessage, ...]:
        if limit < 1:
            raise ValueError("recent message limit must be positive")
        try:
            with self._connect() as connection:
                visible = self._context_visibility(connection)
                rows = connection.execute(
                    "SELECT role, content FROM conversation_messages "
                    f"WHERE {visible} ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as error:
            raise ConversationRepositoryError("could not read recent conversation") from error
        return tuple(ConversationMessage(row["role"], row["content"]) for row in reversed(rows))

    def search_messages(
        self, terms: tuple[str, ...], *, exclude_latest: int, limit: int,
    ) -> tuple[ConversationMessage, ...]:
        if exclude_latest < 0 or not 1 <= limit <= 8:
            raise ValueError("invalid conversation search bounds")
        terms = tuple(dict.fromkeys(term for term in terms if term))[:12]
        if not terms:
            return ()
        patterns = tuple(
            "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            for term in terms
        )
        score = " + ".join("CASE WHEN content LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END" for _ in terms)
        try:
            with self._connect() as connection:
                visible = self._context_visibility(connection)
                boundary = 9223372036854775807
                if exclude_latest:
                    row = connection.execute(
                        "SELECT id FROM conversation_messages "
                        f"WHERE {visible} ORDER BY id DESC LIMIT 1 OFFSET ?",
                        (exclude_latest - 1,),
                    ).fetchone()
                    if row is None:
                        return ()
                    boundary = row["id"]
                anchors = connection.execute(
                    f"SELECT id, ({score}) AS score FROM conversation_messages "
                    f"WHERE {visible} AND role = 'user' AND id < ? AND score > 0 "
                    "ORDER BY score DESC, id DESC LIMIT ?",
                    (*patterns, boundary, limit),
                ).fetchall()
                selected: list[ConversationMessage] = []
                for anchor in sorted(anchors, key=lambda item: item["id"]):
                    # Exact user turn and at most its next reply; never take another user's reply.
                    rows = connection.execute(
                        "SELECT role, content FROM conversation_messages "
                        f"WHERE {visible} AND id >= ? "
                        "AND id < COALESCE((SELECT MIN(id) FROM conversation_messages "
                        "WHERE role = 'user' AND id > ?), 9223372036854775807) "
                        "ORDER BY id LIMIT 2", (anchor["id"], anchor["id"]),
                    ).fetchall()
                    selected.extend(
                        ConversationMessage(row["role"], row["content"]) for row in rows
                    )
                return tuple(selected)
        except sqlite3.Error as error:
            raise ConversationRepositoryError("could not search conversation") from error

    @staticmethod
    def _context_visibility(connection: sqlite3.Connection) -> str:
        # Do not create a table or cache its absence: reviewed exclusions may be
        # installed after this repository instance was constructed.
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'conversation_context_exclusions'"
        ).fetchone()
        if exists is None:
            return "1 = 1"
        return (
            "NOT EXISTS (SELECT 1 FROM conversation_context_exclusions e "
            "WHERE e.message_id = conversation_messages.id)"
        )

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
