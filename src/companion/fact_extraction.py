"""Post-session fact extraction and atomic, resumable storage in the memory DB."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
import re
import sqlite3
from typing import Protocol
from uuid import uuid4

from companion.contracts import ConversationMessage
from companion.memory import (
    _CURRENT_STATE_TOPIC,
    _TEMPORAL_SCOPES,
    _UNCERTAIN_WORDING,
    Memory,
    SqliteMemoryRepository,
    _from_row,
    _now,
    _terms,
    _validate_provenance,
    _values,
)
from companion.reflection import SqliteConversationHistoryReader

VERSION = 1
FACT_KINDS = frozenset({
    "semantic", "preference", "project", "decision", "procedural", "relationship", "episodic",
})


class FactExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    kind: str
    content: str
    confidence: float
    temporal_scope: str
    evidence: str


@dataclass(frozen=True)
class FactExtractionRequest:
    message_id: int
    user_text: str
    recent_context: tuple[ConversationMessage, ...] = ()
    active_memories: tuple[str, ...] = ()
    current_state_topics: tuple[str, ...] = ()


class FactExtractor(Protocol):
    def extract(self, request: FactExtractionRequest) -> tuple[Fact, ...]: ...

    def verify(self, request: FactExtractionRequest,
               facts: tuple[Fact, ...]) -> tuple[bool, ...]: ...


class FakeFactExtractor:
    def extract(self, request: FactExtractionRequest) -> tuple[Fact, ...]:
        return ()

    def verify(self, request: FactExtractionRequest, facts: tuple[Fact, ...]) -> tuple[bool, ...]:
        return (False,) * len(facts)


@dataclass(frozen=True)
class FactProcessingResult:
    processed: int = 0
    stored: int = 0
    rejected: int = 0
    failed: int = 0
    lexical_rejected: int = 0
    lexical_demoted: int = 0


def validate_fact(fact: Fact) -> None:
    topic = fact.kind.removeprefix("current_state:")
    if fact.kind not in FACT_KINDS and not (
        fact.kind.startswith("current_state:") and _CURRENT_STATE_TOPIC.fullmatch(topic)
    ):
        raise FactExtractionError("invalid fact kind")
    if not fact.content.strip() or not fact.evidence.strip():
        raise FactExtractionError("fact content and evidence are required")
    if (isinstance(fact.confidence, bool) or not isinstance(fact.confidence, (float, int))
            or not 0 <= fact.confidence <= 1):
        raise FactExtractionError("fact confidence must be between 0 and 1")
    if fact.temporal_scope not in _TEMPORAL_SCOPES:
        raise FactExtractionError("invalid fact temporal scope")


_SEMANTIC_EXCLUDED = re.compile(r"느낀다|느끼|생각한다|불만|의도|말한다|원한다|바란다|싶어|싶다")
_WINTER_TOPIC = re.compile(r"communication|conversation|reply|style|winter|겨울", re.I)
# 2026-09-14 dry-run 2: a bare "?" or "왜" also dropped "…알고 있지 않았어?" facts; keep only
# the interrogative endings that mark a question about Winter.
_QUESTION = re.compile(r"(?:가능해|있어|알아|뭐야)(?:요)?[?？.!\s]*$")
# Bare 아니/않/없 also dropped "…아니라 감정 있는 AI를 만들려고" style facts;
# match intent negation only.
_NEGATED_INTENT = re.compile(r"(?:하려는|할)\s*(?:게|것이|건)\s*아니|의도(?:가|는)?\s*없|않는다$")
_WINTER_FEEDBACK = re.compile(
    r"말투|태도|성의|대화\s*방식|응답\s*방식|답변\s*방식|(?:그만|계속)\s*반복|반복하라고"
)
_WINTER_REQUEST = re.compile(
    r"(?:말해|말하|물어|질문해|답해|대답해|답변해|영어\s*선생|친구가?\s*되|친구이길)"
    r".*(?:줘|주라|주세요|원해|원한다|바라|바란다|되길|이길)"
)
_HANGUL = re.compile(r"[가-힣]")
_ABOUT_WINTER = re.compile(r"겨울이|묻고|있는지|가능한지|검색|기능")
_KIND_PRIORITY = {kind: rank for rank, kind in enumerate((
    "semantic", "project", "relationship", "preference", "decision", "procedural", "episodic",
))}
_LEXICAL_SUFFIXES = (
    "에서", "으로", "이다", "했다", "한다", "은", "는", "이", "가", "을", "를",
    "의", "에", "로", "와", "과", "도", "다",
)
_LEXICAL_STOP = frozenset(
    "천우 겨울이 겨울 현재 요즘 지금 오늘 매우 더욱 조금 있다 없다 하다 되다 중이다 "
    "위해 대해 관한 통해 같은 것을 것이 수 및".split()
)


def _kind_policy(fact: Fact) -> Fact | None:
    """Discard transient feedback and questions even when the verifier would accept them."""
    if fact.kind == "semantic" and _SEMANTIC_EXCLUDED.search(fact.content):
        return None
    if fact.kind.startswith("current_state:") and _WINTER_TOPIC.search(fact.kind.split(":", 1)[1]):
        return None
    # A question ending discards the fact only when the fact itself is about Winter's
    # abilities; "…노력중이지 그런데 …이유가 뭐야?" still carries a fact about the user.
    if _QUESTION.search(fact.evidence.strip()) and _ABOUT_WINTER.search(fact.content):
        return None
    if _NEGATED_INTENT.search(fact.content):
        return None
    if _WINTER_REQUEST.search(fact.evidence):
        return replace(fact, kind="preference")
    if any(_WINTER_FEEDBACK.search(text) for text in (fact.evidence, fact.content)):
        return None
    if fact.kind.startswith("current_state:") and "겨울" in fact.content:
        return None
    return fact


def _lexical_tokens(text: str) -> set[str]:
    """Whitespace tokens with one approximate suffix removed; no morphology dependency."""
    tokens = set()
    for raw in text.split():
        token = raw.strip(".,!?;:'\"()[]{}<>“”‘’…。，！？")
        if token in _LEXICAL_STOP:
            continue
        for suffix in _LEXICAL_SUFFIXES:
            if token.endswith(suffix):
                token = token[:-len(suffix)]
                break
        if len(token) >= 2 and token not in _LEXICAL_STOP:
            tokens.add(token)
    return tokens


def _unsupported_tokens(fact: Fact, request: FactExtractionRequest) -> set[str]:
    # The rest of the current utterance and active memories are deliberately not evidence.
    source = "\n".join((fact.evidence, *(m.content for m in request.recent_context[-6:])))
    evidence_tokens = _lexical_tokens(fact.evidence)
    content_tokens = _lexical_tokens(fact.content)
    # 최합 has no shared two-character prefix with 최종 or 합격: a single explicit alias.
    abbreviation_support: set[str] = set()
    if any(t.startswith("최합") for t in evidence_tokens) and "최종 합격" in fact.content:
        abbreviation_support.update(("최종", "합격"))
    return {token for token in content_tokens
            if token not in source
            and not any(token.startswith(alias) for alias in abbreviation_support)
            and not any(token[:2] == evidence[:2] for evidence in evidence_tokens)}


def _similar(left_text: str, right_text: str, threshold: float) -> bool:
    if left_text == right_text:
        return True
    left, right = _terms(left_text), _terms(right_text)
    union = left | right
    return bool(union) and len(left & right) / len(union) >= threshold


def _deduplicate(facts: tuple[Fact, ...]) -> tuple[Fact, ...]:
    # Choose by adjusted confidence, then kind; retain original order for unrelated facts.
    selected: list[int] = []
    for index in sorted(range(len(facts)), key=lambda i: (
        -facts[i].confidence, _KIND_PRIORITY.get(facts[i].kind, len(_KIND_PRIORITY)), i,
    )):
        fact = facts[index]
        if any(_similar(fact.content, facts[i].content, .6) or (
            _WINTER_REQUEST.search(fact.evidence) and _WINTER_REQUEST.search(facts[i].evidence)
        ) for i in selected):
            continue
        selected.append(index)
    return tuple(facts[i] for i in sorted(selected))


def _duplicate(fact: Fact, memory: Memory) -> bool:
    return _similar(fact.content, memory.content, .8)


class SqliteFactExtractionRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.memories = SqliteMemoryRepository(path)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS fact_extractions (
                    message_id INTEGER PRIMARY KEY, version INTEGER, status TEXT,
                    facts INTEGER, stored INTEGER, rejected INTEGER, error TEXT, created_at TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def completed_ids(self) -> set[int]:
        with closing(self._connect()) as connection:
            return {int(row[0]) for row in connection.execute(
                "SELECT message_id FROM fact_extractions "
                "WHERE version = ? AND status = 'completed'",
                (VERSION,),
            )}

    def record_failure(self, message_id: int, error: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                INSERT INTO fact_extractions VALUES (?, ?, 'failed', 0, 0, 0, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET version = excluded.version,
                    status = 'failed', facts = 0, stored = 0, rejected = 0,
                    error = excluded.error, created_at = excluded.created_at
                WHERE fact_extractions.status != 'completed' OR fact_extractions.version != ?
            """, (message_id, VERSION, error, _now(), VERSION))

    def store(self, request: FactExtractionRequest, facts: tuple[Fact, ...], *,
              total: int, rejected: int) -> int:
        """Commit all accepted facts and completion together, including state History."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM fact_extractions WHERE message_id = ? AND version = ? "
                "AND status = 'completed'", (request.message_id, VERSION),
            ).fetchone():
                return 0
            stored = 0
            for fact in _deduplicate(facts):
                validate_fact(fact)
                if fact.evidence not in request.user_text:
                    raise FactExtractionError("fact evidence must occur in user text")
                _validate_provenance(f"conversation:{request.message_id}", fact.temporal_scope)
                active = tuple(_from_row(row) for row in connection.execute(
                    "SELECT * FROM memories WHERE status = 'active'",
                ))
                if any(_duplicate(fact, memory) for memory in active):
                    continue
                evidence_ref = f"conversation:{request.message_id}"
                if fact.kind.startswith("current_state:") and fact.confidence >= 0.6:
                    self.memories.remember_current_state(
                        topic=fact.kind.removeprefix("current_state:"), content=fact.content,
                        source="extracted", confidence=fact.confidence, importance=5,
                        evidence_ref=evidence_ref, temporal_scope=fact.temporal_scope,
                        connection=connection,
                    )
                else:
                    now = _now()
                    memory = Memory(
                        str(uuid4()), fact.kind, fact.content, 5, fact.confidence,
                        "active" if fact.confidence >= 0.6 else "candidate", "extracted",
                        now, now, None, None, evidence_ref, fact.temporal_scope,
                    )
                    connection.execute(
                        "INSERT INTO memories (id, kind, content, importance, confidence, status, "
                        "source, created_at, updated_at, last_accessed_at, supersedes, "
                        "evidence_ref, "
                        "temporal_scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        _values(memory),
                    )
                stored += 1
            connection.execute("""
                INSERT INTO fact_extractions VALUES (?, ?, 'completed', ?, ?, ?, NULL, ?)
                ON CONFLICT(message_id) DO UPDATE SET version = excluded.version,
                    status = 'completed', facts = excluded.facts, stored = excluded.stored,
                    rejected = excluded.rejected, error = NULL, created_at = excluded.created_at
            """, (request.message_id, VERSION, total, stored, rejected, _now()))
            return stored


class FactExtractionService:
    def __init__(self, history: SqliteConversationHistoryReader,
                 repository: SqliteFactExtractionRepository, extractor: FactExtractor) -> None:
        self._history = history
        self._repository = repository
        self._extractor = extractor

    def process_pending(self, *, limit: int | None = 40,
                        progress: Callable[[int, int], None] | None = None) -> FactProcessingResult:
        if limit is not None and limit < 1:
            raise ValueError("fact extraction limit must be at least 1")
        records = self._history.list_records()
        completed = self._repository.completed_ids()
        pending = [(i, record) for i, record in enumerate(records)
                   if record.role == "user" and record.id not in completed][:limit]
        stored = rejected = failed = lexical_rejected = lexical_demoted = 0
        for index, (position, record) in enumerate(pending, start=1):
            try:
                active = sorted(self._repository.memories.list_active(),
                                key=lambda m: (m.updated_at, m.id), reverse=True)
                request = FactExtractionRequest(
                    record.id, record.content,
                    tuple(ConversationMessage(m.role, m.content)
                          for m in records[max(0, position - 6):position]),
                    tuple(m.content for m in active[:30]),
                    tuple(sorted({m.kind.removeprefix("current_state:") for m in active
                                  if m.kind.startswith("current_state:")})),
                )
                # Recheck before calling the model if another run completed this message.
                if record.id not in self._repository.completed_ids():
                    raw = self._extractor.extract(request)
                    if len(raw) > 5:
                        raise FactExtractionError("at most five facts are allowed")
                    grounded = []
                    count_lexical_rejected = count_lexical_demoted = 0
                    for fact in raw:
                        validate_fact(fact)
                        if fact.evidence not in request.user_text:
                            continue
                        checked = _kind_policy(fact)
                        if checked is None:
                            continue
                        fact = checked
                        # A non-Korean evidence clause (e.g. English) cannot be grounded
                        # lexically against a Korean fact sentence; the verifier decides.
                        # Non-Korean evidence (e.g. English) cannot be grounded lexically
                        # against a Korean fact sentence: demote it to a candidate instead.
                        unsupported = (1 if not _HANGUL.search(fact.evidence)
                                       else len(_unsupported_tokens(fact, request)))
                        if unsupported >= 2:
                            count_lexical_rejected += 1
                            continue
                        if unsupported == 1:
                            count_lexical_demoted += 1
                            fact = replace(fact, confidence=min(.55, fact.confidence))
                        if _UNCERTAIN_WORDING.search(fact.evidence):
                            fact = replace(fact, confidence=min(0.8, fact.confidence))
                        grounded.append(fact)
                    facts = tuple(grounded)
                    accept = self._extractor.verify(request, facts) if facts else ()
                    if len(accept) != len(facts) or any(type(a) is not bool for a in accept):
                        raise FactExtractionError("verification must return one boolean per fact")
                    accepted = tuple(f for f, a in zip(facts, accept, strict=True) if a)
                    count_rejected = len(raw) - len(accepted)
                    stored += self._repository.store(
                        request, accepted, total=len(raw), rejected=count_rejected,
                    )
                    rejected += count_rejected
                    # Like stored/rejected, counters describe completed message decisions.
                    lexical_rejected += count_lexical_rejected
                    lexical_demoted += count_lexical_demoted
            except Exception as error:  # noqa: BLE001 - persist failure and continue backfill
                # Model/provider exceptions may contain private payloads; never copy them to logs.
                detail = (str(error) if isinstance(error, FactExtractionError)
                          else type(error).__name__)
                self._repository.record_failure(record.id, detail)
                failed += 1
            if progress is not None:
                progress(index, len(pending))
        return FactProcessingResult(len(pending), stored, rejected, failed,
                                    lexical_rejected, lexical_demoted)
