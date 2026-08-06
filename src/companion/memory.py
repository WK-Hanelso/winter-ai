"""Explicitly approved persistent memories; no automatic extraction."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4
import re

class MemoryRepositoryError(RuntimeError): pass

@dataclass(frozen=True)
class Memory:
    id: str; kind: str; content: str; importance: int; confidence: float; status: str
    source: str; created_at: str; updated_at: str; last_accessed_at: str | None; supersedes: str | None

class SqliteMemoryRepository:
    _STATUSES = ("candidate", "approved", "active", "deprecated", "rejected")
    def __init__(self, path: Path) -> None:
        self._path = path; self._initialize()
    def add_candidate(self, *, kind: str, content: str, source: str = "user_explicit", importance: int = 5, confidence: float = 1.0) -> Memory:
        if not kind.strip() or not content.strip(): raise MemoryRepositoryError("memory kind and content are required")
        now = _now(); memory = Memory(str(uuid4()), kind, content, importance, confidence, "candidate", source, now, now, None, None)
        self._write("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", _values(memory)); return memory
    def transition(self, memory_id: str, status: str) -> Memory:
        if status not in self._STATUSES: raise MemoryRepositoryError(f"unsupported memory status: {status}")
        current = self.get(memory_id)
        allowed = {"candidate": ("approved", "rejected"), "approved": ("active", "rejected"), "active": ("deprecated",)}
        if status not in allowed.get(current.status, ()):
            raise MemoryRepositoryError(f"cannot transition memory {memory_id} from {current.status} to {status}")
        self._write("UPDATE memories SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), memory_id))
        updated = self.get(memory_id)
        if status == "active" and updated.supersedes:
            old = self.get(updated.supersedes)
            if old.status == "active": self._write("UPDATE memories SET status = ?, updated_at = ? WHERE id = ?", ("deprecated", _now(), old.id))
        return updated
    def replace(self, memory_id: str, content: str) -> Memory:
        old = self.get(memory_id)
        replacement = self.add_candidate(kind=old.kind, content=content, source="user_edit", importance=old.importance, confidence=old.confidence)
        self._write("UPDATE memories SET supersedes = ? WHERE id = ?", (old.id, replacement.id))
        return self.get(replacement.id)
    def get(self, memory_id: str) -> Memory:
        try:
            with self._connect() as c: row = c.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        except sqlite3.Error as e: raise MemoryRepositoryError(f"could not read memory: {e}") from e
        if row is None: raise MemoryRepositoryError(f"memory not found: {memory_id}")
        return _from_row(row)
    def list(self) -> tuple[Memory, ...]:
        try:
            with self._connect() as c: return tuple(_from_row(row) for row in c.execute("SELECT * FROM memories ORDER BY created_at"))
        except sqlite3.Error as e: raise MemoryRepositoryError(f"could not list memories: {e}") from e
    def list_active(self) -> tuple[Memory, ...]:
        return tuple(memory for memory in self.list() if memory.status == "active")
    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as c: c.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, importance INTEGER NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_accessed_at TEXT, supersedes TEXT)")
        except (OSError, sqlite3.Error) as e: raise MemoryRepositoryError(f"could not initialize memory database {self._path}: {e}") from e
    def _write(self, sql: str, values: tuple[object, ...]) -> None:
        try:
            with self._connect() as c: c.execute(sql, values)
        except sqlite3.Error as e: raise MemoryRepositoryError(f"could not write memory: {e}") from e
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path); c.row_factory = sqlite3.Row; return c

def _now() -> str: return datetime.now(timezone.utc).isoformat()
def _values(m: Memory) -> tuple[object, ...]: return (m.id, m.kind, m.content, m.importance, m.confidence, m.status, m.source, m.created_at, m.updated_at, m.last_accessed_at, m.supersedes)
def _from_row(row: sqlite3.Row) -> Memory: return Memory(**dict(row))

class ActiveMemoryRetriever:
    def __init__(self, repository: SqliteMemoryRepository, *, max_memories: int = 3, max_characters: int = 1000) -> None:
        self._repository = repository; self._max_memories = max_memories; self._max_characters = max_characters
    def retrieve(self, query: str) -> tuple[Memory, ...]:
        query_terms = _terms(query)
        ranked = []
        for memory in self._repository.list_active():
            overlap = len(query_terms & _terms(memory.content))
            if overlap: ranked.append((overlap, memory.importance, memory.created_at, memory))
        selected: list[Memory] = []; length = 0
        for _, _, _, memory in sorted(ranked, key=lambda item: (-item[0], -item[1], item[2], item[3].id)):
            if len(selected) == self._max_memories or length + len(memory.content) > self._max_characters: continue
            selected.append(memory); length += len(memory.content)
        return tuple(selected)

def memory_context(memories: tuple[Memory, ...]) -> str:
    return "Relevant active user memories:\n" + "\n".join(f"- [{m.id} | {m.kind}] {m.content}" for m in memories)
def _terms(text: str) -> set[str]: return set(re.findall(r"[0-9A-Za-z가-힣]+", text.lower()))
