"""Persistent queue and delivery inbox for Winter-owned read-only work."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

from companion.owned_work import OwnedWorkUpdate
from companion.work_orchestration import WorkRequest

_RESULT_STATUSES = ("completed", "partial", "failed")


class OwnedWorkRepositoryError(RuntimeError):
    """Raised when a background work job cannot be persisted exactly."""


class SqliteOwnedWorkRepository:
    """Keep one active request and undelivered results across process restarts."""

    _SCHEMA_VERSION = 1

    def __init__(self, database_path: Path, *, recovery: str = "resume") -> None:
        if recovery not in {"resume", "fail", "none"}:
            raise ValueError("invalid work recovery policy")
        self._database_path = database_path
        self._recovery = recovery
        self._initialize()

    def enqueue(self, work_id: str, request: WorkRequest) -> None:
        if request.context or len(request.user_text) > 800:
            raise OwnedWorkRepositoryError(
                "persistent owned work accepts only one bounded current request"
            )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                active = connection.execute(
                    "SELECT COUNT(*) AS count FROM owned_work_jobs "
                    "WHERE status IN ('pending', 'running')"
                ).fetchone()
                undelivered = connection.execute(
                    "SELECT COUNT(*) AS count FROM owned_work_jobs "
                    "WHERE status IN ('completed', 'partial', 'failed') "
                    "AND delivered_at IS NULL"
                ).fetchone()
                if active is None or int(active["count"]) > 0:
                    raise OwnedWorkRepositoryError(
                        "an owned work job is already active"
                    )
                if undelivered is None or int(undelivered["count"]) >= 10:
                    raise OwnedWorkRepositoryError(
                        "too many owned work results are waiting for delivery"
                    )
                connection.execute(
                    """
                    INSERT INTO owned_work_jobs (
                        id, user_text, status, evidence_json, limitations_json
                    ) VALUES (?, ?, 'pending', '[]', '[]')
                    """,
                    (work_id, request.user_text),
                )
        except OwnedWorkRepositoryError:
            raise
        except sqlite3.Error as error:
            raise OwnedWorkRepositoryError(
                f"could not enqueue owned work in {self._database_path}"
            ) from error

    def claim_pending(self, *, expected_id: str | None = None) -> tuple[str, WorkRequest] | None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT id, user_text FROM owned_work_jobs
                    WHERE status = 'pending' AND (? IS NULL OR id = ?)
                    ORDER BY created_at, id
                    LIMIT 1
                    """, (expected_id, expected_id),
                ).fetchone()
                if row is None:
                    return None
                updated = connection.execute(
                    """
                    UPDATE owned_work_jobs
                    SET status = 'running', owner_pid = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                    """,
                    (_owner_pid(), row["id"]),
                )
                if updated.rowcount != 1:
                    raise OwnedWorkRepositoryError(
                        "pending owned work changed before it could be claimed"
                    )
                return str(row["id"]), WorkRequest(str(row["user_text"]))
        except OwnedWorkRepositoryError:
            raise
        except sqlite3.Error as error:
            raise OwnedWorkRepositoryError(
                f"could not claim owned work in {self._database_path}"
            ) from error

    def complete(self, update: OwnedWorkUpdate) -> None:
        if update.status not in _RESULT_STATUSES:
            raise OwnedWorkRepositoryError(
                f"unsupported owned work result status: {update.status}"
            )
        try:
            with self._connect() as connection:
                updated = connection.execute(
                    """
                    UPDATE owned_work_jobs
                    SET status = ?, result_text = ?, evidence_json = ?,
                        limitations_json = ?, owner_pid = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                      AND status IN (
                          'pending', 'running', 'completed', 'partial', 'failed'
                      )
                      AND delivered_at IS NULL
                    """,
                    (
                        update.status,
                        update.text,
                        json.dumps(update.evidence, ensure_ascii=False),
                        json.dumps(update.limitations, ensure_ascii=False),
                        update.id,
                    ),
                )
                if updated.rowcount != 1:
                    raise OwnedWorkRepositoryError(
                        "owned work result does not match an active job"
                    )
        except OwnedWorkRepositoryError:
            raise
        except sqlite3.Error as error:
            raise OwnedWorkRepositoryError(
                f"could not complete owned work in {self._database_path}"
            ) from error

    def ready(self) -> tuple[OwnedWorkUpdate, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, status, result_text, evidence_json, limitations_json
                    FROM owned_work_jobs
                    WHERE status IN ('completed', 'partial', 'failed')
                      AND delivered_at IS NULL
                    ORDER BY updated_at, id
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise OwnedWorkRepositoryError(
                f"could not list owned work results in {self._database_path}"
            ) from error
        try:
            return tuple(
                OwnedWorkUpdate(
                    id=str(row["id"]),
                    status=str(row["status"]),
                    text=str(row["result_text"]),
                    evidence=_string_tuple(row["evidence_json"]),
                    limitations=_string_tuple(row["limitations_json"]),
                )
                for row in rows
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise OwnedWorkRepositoryError(
                "owned work result contains invalid JSON"
            ) from error

    def acknowledge(self, update_id: str) -> None:
        try:
            with self._connect() as connection:
                updated = connection.execute(
                    """
                    UPDATE owned_work_jobs
                    SET delivered_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                      AND status IN ('completed', 'partial', 'failed')
                      AND delivered_at IS NULL
                    """,
                    (update_id,),
                )
                if updated.rowcount not in (0, 1):
                    raise OwnedWorkRepositoryError(
                        "owned work acknowledgement changed multiple rows"
                    )
        except OwnedWorkRepositoryError:
            raise
        except sqlite3.Error as error:
            raise OwnedWorkRepositoryError(
                f"could not acknowledge owned work in {self._database_path}"
            ) from error

    def status(self, work_id: str) -> str | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT status FROM owned_work_jobs WHERE id = ?",
                    (work_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise OwnedWorkRepositoryError(
                f"could not read owned work in {self._database_path}"
            ) from error
        return None if row is None else str(row["status"])

    def assign_pending_owner(self, work_id: str, pid: int) -> None:
        if pid < 1:
            raise ValueError("invalid pending worker PID")
        with self._connect() as connection:
            connection.execute(
                "UPDATE owned_work_jobs SET owner_pid = ? WHERE id = ? AND status = 'pending'",
                (pid, work_id),
            )

    def _initialize(self) -> None:
        try:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS owned_work_schema "
                    "(version INTEGER NOT NULL)"
                )
                version = connection.execute(
                    "SELECT version FROM owned_work_schema "
                    "ORDER BY version DESC LIMIT 1"
                ).fetchone()
                if version is not None and int(version["version"]) > self._SCHEMA_VERSION:
                    raise OwnedWorkRepositoryError(
                        "owned work database uses a newer schema"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS owned_work_jobs (
                        id TEXT PRIMARY KEY,
                        user_text TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN (
                                'pending', 'running', 'completed', 'partial', 'failed'
                            )
                        ),
                        result_text TEXT,
                        evidence_json TEXT NOT NULL,
                        limitations_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        delivered_at TEXT,
                        owner_pid INTEGER
                    )
                    """
                )
                if version is None:
                    connection.execute(
                        "INSERT INTO owned_work_schema (version) VALUES (?)",
                        (self._SCHEMA_VERSION,),
                    )
                running = connection.execute(
                    "SELECT id, owner_pid FROM owned_work_jobs "
                    "WHERE status = 'running'"
                ).fetchall()
                for row in running:
                    owner_pid = row["owner_pid"]
                    if self._recovery == "none":
                        continue
                    if owner_pid is None or not _process_alive(int(owner_pid)):
                        if self._recovery == "fail":
                            connection.execute(
                                "UPDATE owned_work_jobs SET status = 'failed', owner_pid = NULL, "
                                "result_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                ("이전 작업이 중단됐어. 자동 재실행하지 않았어.", row["id"]),
                            )
                            continue
                        connection.execute(
                            """
                            UPDATE owned_work_jobs
                            SET status = 'pending', owner_pid = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND status = 'running'
                            """,
                            (row["id"],),
                        )
                if self._recovery == "fail":
                    pending = connection.execute(
                        "SELECT id, owner_pid FROM owned_work_jobs WHERE status = 'pending'"
                    ).fetchall()
                    for row in pending:
                        pid = row["owner_pid"]
                        if pid is not None and _process_alive(int(pid)):
                            continue
                        connection.execute(
                            "UPDATE owned_work_jobs SET status = 'failed', result_text = ?, "
                            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            ("이전 작업의 실행 여부를 몰라. 자동 재실행하지 않았어.", row["id"]),
                        )
        except OwnedWorkRepositoryError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise OwnedWorkRepositoryError(
                f"could not initialize owned work database {self._database_path}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection


def _string_tuple(raw: object) -> tuple[str, ...]:
    decoded = json.loads(str(raw))
    if not isinstance(decoded, list) or any(
        not isinstance(item, str) for item in decoded
    ):
        raise ValueError("expected a JSON string array")
    return tuple(decoded)


def _owner_pid() -> int:
    return os.getpid()


def _process_alive(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
