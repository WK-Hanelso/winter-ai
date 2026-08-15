"""Persistent unfinished topics explicitly signalled by the user.

Open loops are not inferred facts about 천우.  They are verbatim conversational
commitments such as "내일 다시 보자" or unresolved topics such as "아직 고민
중이야".  That makes them safe to persist automatically while still giving a
later session something concrete to continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

_TERMS = re.compile(r"[0-9A-Za-z가-힣_+-]+")
_FUTURE_THREAD = re.compile(
    r"(?:내일|나중에|다음에|아침에|끝나면|하고\s*나서).{0,80}"
    r"(?:하자|보자|얘기|이야기|확인|알려|이어|계속)"
)
_UNRESOLVED = re.compile(
    r"고민\s*중|결정.{0,20}못|해결.{0,20}(?:안|못)|"
    r"아직.{0,60}(?:고민|결정|해결|문제|해야|모르)|"
    r"계속.{0,60}(?:문제|실패|막혀|안\s*돼)"
)
_KEEP_CUE = re.compile(r"잊지\s*마|이어서\s*(?:얘기|이야기)|다시\s*꺼내")
_CONTINUATION_CUE = re.compile(
    r"아까|그\s*얘기|그\s*이야기|이어서|이어\s*가|계속\s*(?:하자|해|보자)|"
    r"어떻게\s*됐|어디까지"
)
_GREETING_CUE = re.compile(r"^(?:안녕|좋은\s*아침|일어났어|나\s*왔어|돌아왔어)[.!?\s]*$")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS open_loops (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_recalled_at TEXT
)
"""


class OpenLoopRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenLoop:
    id: str
    content: str
    status: str
    source: str
    created_at: str
    updated_at: str
    last_recalled_at: str | None


class SqliteOpenLoopRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def remember(self, content: str, *, source: str = "user_explicit") -> OpenLoop:
        content = content.strip()
        source = source.strip()
        if not content or not source:
            raise OpenLoopRepositoryError("open loop content and source are required")
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT * FROM open_loops
                    WHERE content = ? AND status = 'open'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (content,),
                ).fetchone()
                if existing is not None:
                    return _from_row(existing)
                now = _now()
                loop = OpenLoop(
                    id=str(uuid4()),
                    content=content,
                    status="open",
                    source=source,
                    created_at=now,
                    updated_at=now,
                    last_recalled_at=None,
                )
                connection.execute(
                    "INSERT INTO open_loops VALUES (?, ?, ?, ?, ?, ?, ?)",
                    _values(loop),
                )
                return loop
        except sqlite3.Error as error:
            raise OpenLoopRepositoryError(f"could not remember open loop: {error}") from error

    def transition(self, loop_id: str, status: str) -> OpenLoop:
        if status not in ("resolved", "dismissed"):
            raise OpenLoopRepositoryError(f"unsupported open loop status: {status}")
        current = self.get(loop_id)
        if current.status != "open":
            raise OpenLoopRepositoryError(
                f"cannot transition open loop {loop_id} from {current.status} to {status}"
            )
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE open_loops SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), loop_id),
                )
        except sqlite3.Error as error:
            raise OpenLoopRepositoryError(f"could not update open loop: {error}") from error
        return self.get(loop_id)

    def get(self, loop_id: str) -> OpenLoop:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM open_loops WHERE id = ?", (loop_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise OpenLoopRepositoryError(f"could not read open loop: {error}") from error
        if row is None:
            raise OpenLoopRepositoryError(f"open loop not found: {loop_id}")
        return _from_row(row)

    def list(self) -> tuple[OpenLoop, ...]:
        try:
            with self._connect() as connection:
                return tuple(
                    _from_row(row)
                    for row in connection.execute(
                        "SELECT * FROM open_loops ORDER BY created_at DESC, id"
                    )
                )
        except sqlite3.Error as error:
            raise OpenLoopRepositoryError(f"could not list open loops: {error}") from error

    def list_open(self) -> tuple[OpenLoop, ...]:
        return tuple(loop for loop in self.list() if loop.status == "open")

    def mark_recalled(self, loops: tuple[OpenLoop, ...]) -> None:
        if not loops:
            return
        try:
            with self._connect() as connection:
                now = _now()
                connection.executemany(
                    "UPDATE open_loops SET last_recalled_at = ? WHERE id = ?",
                    ((now, loop.id) for loop in loops),
                )
        except sqlite3.Error as error:
            raise OpenLoopRepositoryError(f"could not mark open loops recalled: {error}") from error

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(_CREATE_TABLE)
        except (OSError, sqlite3.Error) as error:
            raise OpenLoopRepositoryError(
                f"could not initialize open loop storage: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


class ActiveOpenLoopRetriever:
    def __init__(self, repository: SqliteOpenLoopRepository, *, max_loops: int = 2) -> None:
        self._repository = repository
        self._max_loops = max_loops

    def retrieve(self, query: str) -> tuple[OpenLoop, ...]:
        open_loops = self._repository.list_open()
        if not open_loops:
            return ()
        if _CONTINUATION_CUE.search(query):
            selected = open_loops[: self._max_loops]
        elif _GREETING_CUE.search(query.strip()):
            selected = open_loops[:1]
        else:
            query_terms = _terms(query)
            ranked = [
                (len(query_terms & _terms(loop.content)), loop)
                for loop in open_loops
            ]
            selected = tuple(
                loop
                for overlap, loop in sorted(
                    ranked, key=lambda item: (-item[0], item[1].created_at, item[1].id)
                )
                if overlap
            )[: self._max_loops]
        self._repository.mark_recalled(selected)
        return selected


def detect_open_loop(text: str) -> str | None:
    stripped = text.strip()
    if len(stripped) < 5:
        return None
    if any(pattern.search(stripped) for pattern in (_FUTURE_THREAD, _UNRESOLVED, _KEEP_CUE)):
        return stripped
    return None


def open_loop_context(loops: tuple[OpenLoop, ...]) -> str:
    lines = [
        "다음은 천우가 직접 남긴 아직 끝나지 않은 이야기야.",
        "현재 말과 관련 있을 때만 자연스럽게 이어가고, 적힌 내용 밖을 기억한다고 하지 마.",
    ]
    lines.extend(f"- [{loop.id}] {loop.content}" for loop in loops)
    return "\n".join(lines)


def open_loop_topic(content: str) -> str:
    """A short verbatim-derived label for a deterministic continuity fallback."""
    subject = re.split(
        r"내일|나중에|다음에|아침에|끝나면|하고\s*나서", content, maxsplit=1
    )[0]
    subject = re.sub(r"^(?:아직|계속)\s*", "", subject).strip(" .,!?:")
    subject = re.sub(r"(?:은|는|이|가|을|를)$", "", subject).strip()
    words = subject.split()
    if not words:
        words = content.strip().split()
    return " ".join(words[:4]).strip(" .,!?:")


def _terms(text: str) -> set[str]:
    ignored = {"그", "이", "저", "것", "거", "얘기", "이야기", "하자", "해"}
    return {
        match.group(0).lower()
        for match in _TERMS.finditer(text)
        if match.group(0).lower() not in ignored
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _values(loop: OpenLoop) -> tuple[object, ...]:
    return (
        loop.id,
        loop.content,
        loop.status,
        loop.source,
        loop.created_at,
        loop.updated_at,
        loop.last_recalled_at,
    )


def _from_row(row: sqlite3.Row) -> OpenLoop:
    return OpenLoop(**dict(row))
