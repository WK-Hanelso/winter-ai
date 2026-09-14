"""Persistent user memories with conservative direct-statement capture."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from configs.dialogue.memory import CURRENT_STATE_SLOTS, MAX_CHARACTERS, RANKED_MEMORY_SLOTS

_EXPLICIT_MEMORY_REQUEST = re.compile(r"^\s*기억해(?:\s*줘)?[.!,:]?\s+(.+?)\s*$")
_SUFFIX_MEMORY_REQUEST = re.compile(r"^\s*(.+?)\s+기억해(?:\s*줘)?[.!]?\s*$")
_EXPLICIT_FORGET_REQUEST = re.compile(r"^\s*잊어\s*줘[.!,:]?\s+(.+?)\s*$")
_SUFFIX_FORGET_REQUEST = re.compile(r"^\s*(.+?)\s+잊어(?:\s*줘)?[.!]?\s*$")
_RECALL_REQUEST = re.compile(
    r"기억(?:나|해)|내가\s*(?:전에|예전에|뭐|뭘|어떤)|"
    r"나에\s*대해|내\s*(?:취향|선호)|뭐였|뭐라고\s*했"
)
_PREFERENCE_WORDING = re.compile(r"좋아|싫어|선호|원해|편해|마음에\s*들")
_DECISION_WORDING = re.compile(r"결정|하기로\s*했|가기로\s*했|정했어|정했다")
_PROJECT_WORDING = re.compile(r"프로젝트|작업|진행|blocker|다음\s*단계|겨울이")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")
_TEMPORARY_WORDING = re.compile(
    r"오늘|지금|방금|당장|요즘|아까|어제|내일|이번\s*(?:엔|에는|만)"
)
_UNCERTAIN_WORDING = re.compile(
    r"것\s*같|듯(?:해|하다)?|아마|모르겠|일지도|수도\s*있|확실하지"
)
_DIRECT_PREFERENCE = re.compile(
    r"^(?:나는|난|내가|저는|전|제가)\s+(?P<object>.+?)\s*"
    r"(?P<verb>좋아하지\s*않아(?:요)?|원하지\s*않아(?:요)?|"
    r"좋아해(?:요)?|좋아한다|좋아합니다|좋아(?:요)?|"
    r"싫어해(?:요)?|싫어한다|싫어합니다|싫어(?:요)?|"
    r"선호해(?:요)?|선호한다|선호합니다|"
    r"원해(?:요)?|원한다|원합니다|편해(?:요)?|편하다|좋겠어(?:요)?)$"
)
_DIRECT_SEMANTIC_FIELD = re.compile(
    r"^(?:내|제)\s*(?P<field>이름|생일|직업|전공|고향|사는\s*곳|거주지|"
    r"혈액형|MBTI)\s*(?:은|는|이|가)?\s*"
    r"(?P<value>.+?(?:이야|야|예요|이에요|입니다|이다))$",
    re.IGNORECASE,
)
_DIRECT_DECISION = re.compile(
    r"^(?:나는|난|우리는|우리|이건|그건|이번\s*프로젝트(?:에서는|는)?|"
    r"겨울이\s*프로젝트(?:에서는|는)?)\s+.+?"
    r"(?:(?:하|가)기로\s*했어|(?:하|가)기로\s*했다|결정했어|결정했다|"
    r"정했어|정했다)$"
)
_SUBJECT_PARTICLES = (
    "으로",
    "에서",
    "에게",
    "한테",
    "부터",
    "까지",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "도",
    "만",
    "와",
    "과",
)
_RETRIEVAL_STOP = {
    "나는",
    "난",
    "내가",
    "내",
    "저는",
    "전",
    "제가",
    "제",
    "뭐",
    "뭘",
    "어떤",
    "했지",
    "였지",
    "기억",
}
_CURRENT_STATE_PREFIX = "current_state:"
_CURRENT_STATE_TOPIC = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_CONVERSATION_EVIDENCE = re.compile(r"^conversation:[1-9][0-9]*$")
_TEMPORAL_SCOPES = frozenset({"unknown", "durable", "episodic"})
_STATE_HISTORY_TIME = re.compile(r"예전|과거|전에는|그때|이전|변화|달라|비교")
_STATE_HISTORY_CONDITION = re.compile(
    r"상태|기분|마음|멘탈|힘들|괜찮|나아|회복|건강|스트레스|어땠"
)

_CREATE_MEMORIES_TABLE = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    importance INTEGER NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_accessed_at TEXT,
    supersedes TEXT,
    evidence_ref TEXT,
    temporal_scope TEXT NOT NULL DEFAULT 'unknown'
)
"""


class MemoryRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Memory:
    id: str
    kind: str
    content: str
    importance: int
    confidence: float
    status: str
    source: str
    created_at: str
    updated_at: str
    last_accessed_at: str | None
    supersedes: str | None
    evidence_ref: str | None
    temporal_scope: str


@dataclass(frozen=True)
class DirectMemoryStatement:
    """A stable fact the user stated directly, without model inference."""

    kind: str
    content: str
    conflict_key: str | None
    polarity: str | None


class SqliteMemoryRepository:
    _STATUSES = ("candidate", "approved", "active", "deprecated", "rejected")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def add_candidate(
        self,
        *,
        kind: str,
        content: str,
        source: str = "user_explicit",
        importance: int = 5,
        confidence: float = 1.0,
        evidence_ref: str | None = None,
        temporal_scope: str = "unknown",
    ) -> Memory:
        if not kind.strip() or not content.strip():
            raise MemoryRepositoryError("memory kind and content are required")
        _validate_provenance(evidence_ref, temporal_scope)
        now = _now()
        memory = Memory(
            str(uuid4()),
            kind,
            content,
            importance,
            confidence,
            "candidate",
            source,
            now,
            now,
            None,
            None,
            evidence_ref,
            temporal_scope,
        )
        self._write(
            """
            INSERT INTO memories
            (id, kind, content, importance, confidence, status, source,
             created_at, updated_at, last_accessed_at, supersedes,
             evidence_ref, temporal_scope)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _values(memory),
        )
        return memory

    def remember_explicit(
        self,
        *,
        kind: str,
        content: str,
        evidence_ref: str | None = None,
    ) -> Memory:
        """Activate a memory the user explicitly commanded us to remember.

        The lifecycle is still recorded candidate -> approved -> active.  The
        command itself is the approval; inferred memories never use this path.
        """
        _validate_provenance(evidence_ref, "unknown")
        normalized = content.strip()
        for memory in self.list_active():
            if memory.kind == kind and memory.content == normalized:
                return memory
        candidate = self.add_candidate(
            kind=kind,
            content=normalized,
            source="user_explicit",
            importance=5,
            confidence=1.0,
            evidence_ref=evidence_ref,
            temporal_scope="unknown",
        )
        self.transition(candidate.id, "approved")
        return self.transition(candidate.id, "active")

    def remember_direct_statement(
        self,
        statement: DirectMemoryStatement,
        *,
        evidence_ref: str,
        temporal_scope: str,
    ) -> Memory | None:
        """Activate a stable first-person statement, returning only new writes.

        This path never contains a model inference: ``statement.content`` is a
        verbatim user sentence. An exact repeat is ignored. A clear replacement
        (the same named fact, or the opposite preference about the same object)
        supersedes the old active value so retrieval cannot return both.
        """
        _validate_provenance(evidence_ref, temporal_scope)
        normalized = statement.content.strip()
        active = self.list_active()
        if any(
            memory.kind == statement.kind and memory.content == normalized
            for memory in active
        ):
            return None
        superseded = next(
            (
                memory
                for memory in reversed(active)
                if _direct_statements_conflict(statement, memory)
            ),
            None,
        )
        candidate = self.add_candidate(
            kind=statement.kind,
            content=normalized,
            source="user_direct",
            importance=5,
            confidence=1.0,
            evidence_ref=evidence_ref,
            temporal_scope=temporal_scope,
        )
        if superseded is not None:
            self._write(
                "UPDATE memories SET supersedes = ? WHERE id = ?",
                (superseded.id, candidate.id),
            )
        self.transition(candidate.id, "approved")
        return self.transition(candidate.id, "active")

    def remember_reviewed(
        self, *, kind: str, content: str, source: str = "reflection_reviewed",
    ) -> Memory:
        """Activate a reviewed candidate or one selected by the Host's auto policy."""
        normalized = content.strip()
        if not kind.strip() or not normalized:
            raise MemoryRepositoryError("reviewed memory kind and content are required")
        existing = self.list()
        for memory in existing:
            if memory.kind == kind and memory.content == normalized and memory.status == "active":
                return memory
        for memory in existing:
            if memory.kind != kind or memory.content != normalized:
                continue
            if memory.status == "candidate":
                if source == "reflection_auto":
                    self._write("UPDATE memories SET source = ? WHERE id = ?", (source, memory.id))
                self.transition(memory.id, "approved")
                return self.transition(memory.id, "active")
            if memory.status == "approved":
                if source == "reflection_auto":
                    self._write("UPDATE memories SET source = ? WHERE id = ?", (source, memory.id))
                return self.transition(memory.id, "active")
        candidate = self.add_candidate(
            kind=kind,
            content=normalized,
            source=source,
            importance=5,
            confidence=1.0,
        )
        self.transition(candidate.id, "approved")
        return self.transition(candidate.id, "active")

    def remember_current_state(
        self, *, topic: str, content: str, source: str = "user_current_state",
        importance: int = 7, confidence: float = 1.0,
        evidence_ref: str | None = None, temporal_scope: str = "episodic",
        connection: sqlite3.Connection | None = None,
    ) -> Memory:
        """Keep state History; optionally join an extraction's atomic transaction."""
        normalized_topic = topic.strip().lower()
        normalized_content = content.strip()
        if not _CURRENT_STATE_TOPIC.fullmatch(normalized_topic):
            raise MemoryRepositoryError(
                "current-state topic must use lowercase letters, digits and underscores"
            )
        if not normalized_content:
            raise MemoryRepositoryError("current-state content is required")
        _validate_provenance(evidence_ref, temporal_scope)
        if connection is None:
            try:
                with self._connect() as own:
                    own.execute("BEGIN IMMEDIATE")
                    return self.remember_current_state(
                        topic=normalized_topic, content=normalized_content, source=source,
                        importance=importance, confidence=confidence, evidence_ref=evidence_ref,
                        temporal_scope=temporal_scope, connection=own,
                    )
            except sqlite3.Error as error:
                raise MemoryRepositoryError(f"could not save current state: {error}") from error
        kind = f"{_CURRENT_STATE_PREFIX}{normalized_topic}"
        row = connection.execute(
            "SELECT * FROM memories WHERE kind = ? AND status = 'active' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (kind,),
        ).fetchone()
        current = _from_row(row) if row is not None else None
        if current is not None and current.content == normalized_content:
            return current
        now = _now()
        memory = Memory(
            str(uuid4()), kind, normalized_content, importance, confidence, "active", source,
            now, now, None, current.id if current else None, evidence_ref, temporal_scope,
        )
        connection.execute(
            "INSERT INTO memories (id, kind, content, importance, confidence, status, source, "
            "created_at, updated_at, last_accessed_at, supersedes, evidence_ref, temporal_scope) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", _values(memory),
        )
        if current is not None:
            connection.execute(
                "UPDATE memories SET status = 'deprecated', updated_at = ? WHERE id = ?",
                (now, current.id),
            )
        return memory

    def forget_explicit(self, content: str) -> Memory | None:
        """Deprecate one active memory using whitespace tokens, without stemming."""
        terms = {term for term in content.split() if len(term) >= 2}
        scored = []
        for memory in self.list_active():
            words = {word for word in memory.content.split() if len(word) >= 2}
            score = sum(any(term in word or word in term for word in words) for term in terms)
            if score:
                scored.append((score, memory.created_at, memory.id))
        if not scored:
            return None
        return self.transition(max(scored)[2], "deprecated")

    def transition(self, memory_id: str, status: str) -> Memory:
        if status not in self._STATUSES:
            raise MemoryRepositoryError(f"unsupported memory status: {status}")
        current = self.get(memory_id)
        allowed = {
            "candidate": ("approved", "rejected"),
            "approved": ("active", "rejected"),
            "active": ("deprecated",),
        }
        if status not in allowed.get(current.status, ()):
            raise MemoryRepositoryError(
                f"cannot transition memory {memory_id} from {current.status} to {status}"
            )
        self._write(
            "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), memory_id),
        )
        updated = self.get(memory_id)
        if status == "active" and updated.supersedes:
            old = self.get(updated.supersedes)
            if old.status == "active":
                self._write(
                    "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
                    ("deprecated", _now(), old.id),
                )
        return updated

    def replace(self, memory_id: str, content: str) -> Memory:
        old = self.get(memory_id)
        replacement = self.add_candidate(
            kind=old.kind,
            content=content,
            source="user_edit",
            importance=old.importance,
            confidence=old.confidence,
            temporal_scope=old.temporal_scope,
        )
        self._write(
            "UPDATE memories SET supersedes = ? WHERE id = ?",
            (old.id, replacement.id),
        )
        return self.get(replacement.id)

    def delete(self, memory_id: str) -> Memory:
        memory = self.get(memory_id)
        try:
            with self._connect() as c:
                reference = c.execute(
                    "SELECT id FROM memories WHERE supersedes = ? LIMIT 1",
                    (memory_id,),
                ).fetchone()
                if reference is not None:
                    raise MemoryRepositoryError(
                        f"cannot delete memory {memory_id}: superseded by {reference['id']}"
                    )
                c.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        except sqlite3.Error as e:
            raise MemoryRepositoryError(f"could not delete memory: {e}") from e
        return memory

    def get(self, memory_id: str) -> Memory:
        try:
            with self._connect() as c:
                row = c.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
        except sqlite3.Error as e:
            raise MemoryRepositoryError(f"could not read memory: {e}") from e
        if row is None:
            raise MemoryRepositoryError(f"memory not found: {memory_id}")
        return _from_row(row)

    def list(self) -> tuple[Memory, ...]:
        try:
            with self._connect() as c:
                return tuple(
                    _from_row(row)
                    for row in c.execute("SELECT * FROM memories ORDER BY created_at")
                )
        except sqlite3.Error as e:
            raise MemoryRepositoryError(f"could not list memories: {e}") from e

    def list_active(self) -> tuple[Memory, ...]:
        return tuple(memory for memory in self.list() if memory.status == "active")

    def list_current_state_history(
        self,
        *,
        topic: str | None = None,
    ) -> tuple[Memory, ...]:
        kind = None
        if topic is not None:
            normalized_topic = topic.strip().lower()
            if not _CURRENT_STATE_TOPIC.fullmatch(normalized_topic):
                raise MemoryRepositoryError(
                    "current-state topic must use lowercase letters, digits and underscores"
                )
            kind = f"{_CURRENT_STATE_PREFIX}{normalized_topic}"
        return tuple(
            memory
            for memory in self.list()
            if memory.kind.startswith(_CURRENT_STATE_PREFIX)
            and (kind is None or memory.kind == kind)
            and memory.status in {"active", "deprecated"}
        )

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as c:
                c.execute(_CREATE_MEMORIES_TABLE)
                columns = {
                    str(row["name"])
                    for row in c.execute("PRAGMA table_info(memories)").fetchall()
                }
                if "evidence_ref" not in columns:
                    c.execute("ALTER TABLE memories ADD COLUMN evidence_ref TEXT")
                if "temporal_scope" not in columns:
                    c.execute(
                        "ALTER TABLE memories ADD COLUMN temporal_scope "
                        "TEXT NOT NULL DEFAULT 'unknown'"
                    )
        except (OSError, sqlite3.Error) as e:
            raise MemoryRepositoryError(
                f"could not initialize memory database {self._path}: {e}"
            ) from e

    def _write(self, sql: str, values: tuple[object, ...]) -> None:
        try:
            with self._connect() as c:
                c.execute(sql, values)
        except sqlite3.Error as e:
            raise MemoryRepositoryError(f"could not write memory: {e}") from e

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path)
        c.row_factory = sqlite3.Row
        return c


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _values(m: Memory) -> tuple[object, ...]:
    return (
        m.id,
        m.kind,
        m.content,
        m.importance,
        m.confidence,
        m.status,
        m.source,
        m.created_at,
        m.updated_at,
        m.last_accessed_at,
        m.supersedes,
        m.evidence_ref,
        m.temporal_scope,
    )


def _from_row(row: sqlite3.Row) -> Memory:
    return Memory(**dict(row))


class ActiveMemoryRetriever:
    def __init__(
        self,
        repository: SqliteMemoryRepository,
        *,
        max_memories: int | None = None,
        max_characters: int = MAX_CHARACTERS,
    ) -> None:
        if (max_memories is not None and max_memories < 0) or max_characters < 0:
            raise ValueError("memory budgets must be nonnegative")
        self._repository = repository
        self._max_memories = max_memories
        self._max_characters = max_characters

    def retrieve(self, query: str) -> tuple[Memory, ...]:
        active = self._repository.list_active()
        query_terms = _terms(query)
        recall = bool(_RECALL_REQUEST.search(query))

        def latest(memory: Memory) -> tuple[float, str, str]:
            return memory.confidence, memory.updated_at, memory.id

        def rank(memory: Memory) -> tuple[int, float, str, str]:
            return (len(query_terms & _terms(memory.content)), *latest(memory))

        profiles = sorted(
            (m for m in active if m.kind in {"semantic", "relationship"}),
            key=latest, reverse=True,
        )
        states = sorted(
            (m for m in active if m.kind.startswith(_CURRENT_STATE_PREFIX)),
            key=rank, reverse=True,
        )
        remaining = [m for m in active if m.kind in {
            "preference", "project", "decision", "procedural", "episodic",
        }]
        matched = sorted(
            (m for m in remaining if query_terms & _terms(m.content)), key=rank, reverse=True,
        )
        fallback = sorted(
            (m for m in remaining if m.kind in {"preference", "project"}
             and not query_terms & _terms(m.content)),
            key=lambda m: (m.updated_at, m.confidence, m.id), reverse=True,
        )
        if not recall:
            fallback = fallback[:RANKED_MEMORY_SLOTS]
        if recall:
            overview_kinds = {"preference", "project"}
            relevant = ([m for m in matched if m.kind in overview_kinds] + fallback
                        + [m for m in matched if m.kind not in overview_kinds])
        else:
            relevant = matched + fallback
        selected: list[Memory] = []
        length = 0
        # Profile and recall inclusion are bounded by content budget, not ranked slots.
        for pool, slots in (
            (profiles, None),
            (relevant, None if recall else self._max_memories),
            (states, CURRENT_STATE_SLOTS),
        ):
            count = 0
            for memory in pool:
                if slots is not None and count == slots:
                    break
                if length + len(memory.content) > self._max_characters:
                    continue
                selected.append(memory)
                length += len(memory.content)
                count += 1
        return tuple(selected)

    def retrieve_current_state_history(self, query: str) -> tuple[Memory, ...]:
        if not is_current_state_history_query(query):
            return ()
        history = self._repository.list_current_state_history()
        if not history:
            return ()
        query_terms = _terms(query)
        topic_scores: dict[str, int] = {}
        for memory in history:
            topic_scores[memory.kind] = max(
                topic_scores.get(memory.kind, 0),
                len(query_terms & _terms(memory.content)),
            )
        related_topics = {
            topic for topic, score in topic_scores.items() if score > 0
        }
        selected = tuple(
            memory
            for memory in history
            if not related_topics or memory.kind in related_topics
        )
        return selected[-10:]


def memory_context(memories: tuple[Memory, ...]) -> str:
    background: list[str] = []
    states: list[str] = []
    for memory in memories:
        if memory.source == "reflection_auto":
            background.append(f'천우가 말하길 “{memory.content}”.')
        elif memory.kind.startswith(_CURRENT_STATE_PREFIX) and memory.source != "extracted":
            states.append(memory.content)
        else:
            background.append(_memory_sentence(memory.content))
    if states:
        background.append(_memory_sentence("요즘은 " + ", ".join(states)))
    return MEMORY_BACKGROUND_INSTRUCTION + "\n" + " ".join(background)


MEMORY_BACKGROUND_INSTRUCTION = (
    "아래는 네가 이미 아는 천우의 배경이야. 물어보면 지금 대화에 맞는 한두 가지만 "
    "골라 네 말로 짧게 말하고, 목록처럼 읊거나 전부 말하지 마. "
    "안 물어보면 굳이 꺼내지 마."
)


def _memory_sentence(content: str) -> str:
    # Add separators only: never paraphrase, strip or truncate source text.
    # Embedded source newlines/punctuation remain intact for exact provenance.
    return content if content.rstrip().endswith((".", "!", "?", "。", "！", "？")) else (
        content + "."
    )


def is_current_state_history_query(text: str) -> bool:
    return bool(
        _STATE_HISTORY_TIME.search(text) and _STATE_HISTORY_CONDITION.search(text)
    )


def current_state_history_context(memories: tuple[Memory, ...]) -> str:
    lines = [
        "천우의 현재 상태 Timeline이야. 관찰된 변화만 비교하고 변화의 원인을 "
        "천우가 직접 말하지 않았다면 추측하지 마. 최신 active가 현재 상태야:"
    ]
    for memory in memories:
        topic = memory.kind.removeprefix(_CURRENT_STATE_PREFIX)
        valid_to = "현재" if memory.status == "active" else memory.updated_at
        lines.append(
            f"- [{topic} | {memory.status} | {memory.created_at} ~ {valid_to}] "
            f"{memory.content}"
        )
    return "\n".join(lines)


def extract_explicit_forget_content(text: str) -> str | None:
    """Recognize explicit prefix/suffix forget commands without a model."""
    match = _EXPLICIT_FORGET_REQUEST.match(text) or _SUFFIX_FORGET_REQUEST.match(text)
    return match.group(1).strip() if match else None


def extract_explicit_memory_content(text: str) -> str | None:
    """Return content only for an unambiguous Korean memory request."""
    match = _EXPLICIT_MEMORY_REQUEST.match(text)
    if match:
        return match.group(1).strip()
    # Natural request order: preserve the user's exact content, not an LLM extraction.
    # A question, quotation, negation or unresolved pronoun is not a write instruction.
    match = _SUFFIX_MEMORY_REQUEST.match(text)
    if match is None:
        return None
    content = match.group(1).strip()
    if ("?" in content or any(mark in text for mark in ('"', "“", "”", "‘", "’"))
            or content.rstrip(".! ") in {"그거", "이거", "저거", "그것", "이것", "뭘", "뭐"}
            or re.search(r"(?:말고|마|않아|않고)\s*[.!]?\s*$", content)):
        return None
    return content


def classify_explicit_memory_kind(content: str) -> str:
    """Classify only the user's explicit wording; do not infer new content."""
    if _DECISION_WORDING.search(content):
        return "decision"
    if _PREFERENCE_WORDING.search(content):
        return "preference"
    if _PROJECT_WORDING.search(content):
        return "project"
    return "semantic"


def extract_direct_memory_statements(text: str) -> tuple[DirectMemoryStatement, ...]:
    """Extract only stable, first-person statements without interpreting them.

    Temporary state, uncertainty, questions and third-party claims are excluded.
    The returned content remains the user's own sentence; no LLM paraphrase is
    promoted to a fact.
    """
    if _EXPLICIT_MEMORY_REQUEST.match(text):
        return ()
    extracted: list[DirectMemoryStatement] = []
    for raw in _SENTENCE_BOUNDARY.split(text.strip()):
        sentence = raw.strip()
        if not sentence or "?" in sentence:
            continue
        sentence = sentence.rstrip(".!").strip()
        if (
            len(sentence) < 4
            or len(sentence) > 300
            or _TEMPORARY_WORDING.search(sentence)
            or _UNCERTAIN_WORDING.search(sentence)
        ):
            continue
        preference = _DIRECT_PREFERENCE.match(sentence)
        if preference:
            verb = preference.group("verb")
            polarity = (
                "negative"
                if "싫" in verb or "않아" in verb
                else "positive"
            )
            subject = _normalize_subject(preference.group("object"))
            extracted.append(
                DirectMemoryStatement(
                    "preference",
                    sentence,
                    f"preference:{subject}" if subject else None,
                    polarity,
                )
            )
            continue
        semantic = _DIRECT_SEMANTIC_FIELD.match(sentence)
        if semantic:
            field = re.sub(r"\s+", "", semantic.group("field").lower())
            extracted.append(
                DirectMemoryStatement(
                    "semantic", sentence, f"semantic:{field}", None
                )
            )
            continue
        if _DIRECT_DECISION.match(sentence):
            extracted.append(DirectMemoryStatement("decision", sentence, None, None))
            continue
    return tuple(extracted)


def _validate_provenance(evidence_ref: str | None, temporal_scope: str) -> None:
    if evidence_ref is not None and not _CONVERSATION_EVIDENCE.fullmatch(evidence_ref):
        raise MemoryRepositoryError(
            "memory evidence_ref must be an exact conversation:<message_id> reference"
        )
    if temporal_scope not in _TEMPORAL_SCOPES:
        raise MemoryRepositoryError(
            f"unsupported memory temporal_scope: {temporal_scope}"
        )


def _direct_statements_conflict(
    new: DirectMemoryStatement, active: Memory
) -> bool:
    if not new.conflict_key or active.kind != new.kind:
        return False
    previous = extract_direct_memory_statements(active.content)
    if len(previous) != 1 or previous[0].conflict_key != new.conflict_key:
        return False
    old = previous[0]
    if new.kind == "semantic":
        return True
    return bool(new.polarity and old.polarity and new.polarity != old.polarity)


def _normalize_subject(text: str) -> str:
    words: list[str] = []
    for raw in re.findall(r"[0-9A-Za-z가-힣_+-]+", text.lower()):
        word = raw
        for particle in _SUBJECT_PARTICLES:
            if word.endswith(particle) and len(word) > len(particle):
                word = word[: -len(particle)]
                break
        if word:
            words.append(word)
    return " ".join(words)


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()):
        if raw in _RETRIEVAL_STOP:
            continue
        terms.add(raw)
        for particle in _SUBJECT_PARTICLES:
            if raw.endswith(particle) and len(raw) - len(particle) >= 2:
                stem = raw[: -len(particle)]
                if stem not in _RETRIEVAL_STOP:
                    terms.add(stem)
                break
    return terms
