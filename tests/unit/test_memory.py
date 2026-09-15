from pathlib import Path
import sqlite3

import pytest

from companion.memory import (
    ActiveMemoryRetriever,
    DirectMemoryStatement,
    MemoryRepositoryError,
    SqliteMemoryRepository,
    classify_explicit_memory_kind,
    current_state_history_context,
    extract_direct_memory_statements,
    is_current_state_history_query,
    memory_context,
)


def test_explicit_memory_kind_uses_only_observable_wording() -> None:
    assert classify_explicit_memory_kind("나는 짧은 설명을 좋아해") == "preference"
    assert classify_explicit_memory_kind("우리는 SQLite로 가기로 결정했어") == "decision"
    assert classify_explicit_memory_kind("겨울이 프로젝트는 기억부터 만들 거야") == "project"
    assert classify_explicit_memory_kind("내 생일은 3월이야") == "semantic"


def test_direct_stable_user_statements_are_extracted_without_a_memory_command() -> None:
    preference, semantic = extract_direct_memory_statements(
        "나는 해결책보다 상황을 이해해주는 대화를 좋아해. 내 생일은 3월 12일이야."
    )

    assert preference.kind == "preference"
    assert preference.content == "나는 해결책보다 상황을 이해해주는 대화를 좋아해"
    assert preference.conflict_key is not None
    assert preference.conflict_key.startswith("preference:해결책보다 상황")
    assert preference.polarity == "positive"
    assert semantic == DirectMemoryStatement(
        kind="semantic",
        content="내 생일은 3월 12일이야",
        conflict_key="semantic:생일",
        polarity=None,
    )
    assert extract_direct_memory_statements("우리는 SQLite로 하기로 했어") == (
        DirectMemoryStatement(
            kind="decision",
            content="우리는 SQLite로 하기로 했어",
            conflict_key=None,
            polarity=None,
        ),
    )


@pytest.mark.parametrize(
    "text",
    (
        "나는 낮잠 중이야",
        "나는 형편없는 사람이야",
        "나는 일정 때문에 지친 사람이야",
        "나는 개발자야",
    ),
)
def test_broad_identity_shaped_wording_is_not_a_durable_direct_memory(text: str) -> None:
    assert extract_direct_memory_statements(text) == ()


@pytest.mark.parametrize(
    "text",
    (
        "나는 지금 라면이 좋아",
        "나는 이 방식이 좋은 것 같아",
        "내가 어떤 설명을 좋아한다고 했지?",
        "민수는 짧은 설명을 좋아해",
        "기억해. 나는 짧은 설명을 좋아해",
    ),
)
def test_direct_memory_ignores_temporary_uncertain_or_non_user_claims(text: str) -> None:
    assert extract_direct_memory_statements(text) == ()


def test_explicit_memory_requires_approval_then_activation(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    candidate = repo.add_candidate(kind="semantic", content="천우는 겨울이를 선택했다")
    assert candidate.status == "candidate"
    assert repo.transition(candidate.id, "approved").status == "approved"
    reopened = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    assert reopened.transition(candidate.id, "active").status == "active"

def test_memory_rejects_skipping_approval(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    candidate = repo.add_candidate(kind="semantic", content="테스트")
    with pytest.raises(MemoryRepositoryError, match="cannot transition"):
        repo.transition(candidate.id, "active")

def test_retriever_selects_only_related_active_memory(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    related = repo.add_candidate(kind="preference", content="천우는 Python config를 선호한다")
    unrelated = repo.add_candidate(kind="project", content="저녁에 Voice 검증을 한다")
    pending = repo.add_candidate(kind="semantic", content="천우는 Python config를 선호한다")
    for memory in (related, unrelated):
        repo.transition(memory.id, "approved")
        repo.transition(memory.id, "active")
    # Force the overflow branch; small stores now include every active item.
    retriever = ActiveMemoryRetriever(repo, max_characters=len(related.content))
    assert retriever.retrieve("Python config는 어떻게 관리해?") == (
        repo.get(related.id),
    )
    retrieved = retriever.retrieve("Python config")
    assert pending.id not in {memory.id for memory in retrieved}


def test_explicit_remember_is_immediately_active_and_idempotent(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")

    first = repo.remember_explicit(kind="preference", content="나는 긴 목록을 싫어해")
    repeated = repo.remember_explicit(kind="preference", content="나는 긴 목록을 싫어해")

    assert first.status == "active"
    assert repeated.id == first.id
    assert repo.list() == (first,)


def test_direct_statement_is_automatically_active_and_exact_repeat_is_idempotent(
    tmp_path: Path,
) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    statement = extract_direct_memory_statements("나는 긴 설명을 싫어해")[0]

    first = repo.remember_direct_statement(
        statement,
        evidence_ref="conversation:7",
        temporal_scope="durable",
    )
    repeated = repo.remember_direct_statement(
        statement,
        evidence_ref="conversation:7",
        temporal_scope="durable",
    )

    assert first is not None
    assert first.status == "active"
    assert first.source == "user_direct"
    assert first.evidence_ref == "conversation:7"
    assert first.temporal_scope == "durable"
    assert repeated is None
    assert repo.list_active() == (first,)


@pytest.mark.parametrize(
    ("evidence_ref", "temporal_scope", "message"),
    (
        ("message:7", "durable", "evidence_ref"),
        ("conversation:0", "durable", "evidence_ref"),
        ("conversation:7", "forever", "temporal_scope"),
    ),
)
def test_direct_memory_rejects_invalid_provenance_even_for_an_exact_repeat(
    tmp_path: Path,
    evidence_ref: str,
    temporal_scope: str,
    message: str,
) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    statement = extract_direct_memory_statements("나는 긴 설명을 싫어해")[0]
    repo.remember_direct_statement(
        statement,
        evidence_ref="conversation:1",
        temporal_scope="durable",
    )

    with pytest.raises(MemoryRepositoryError, match=message):
        repo.remember_direct_statement(
            statement,
            evidence_ref=evidence_ref,
            temporal_scope=temporal_scope,
        )


def test_clear_direct_preference_change_supersedes_the_old_memory(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    old = repo.remember_direct_statement(
        extract_direct_memory_statements("나는 긴 설명을 좋아해")[0],
        evidence_ref="conversation:1",
        temporal_scope="durable",
    )
    new = repo.remember_direct_statement(
        extract_direct_memory_statements("나는 긴 설명을 싫어해")[0],
        evidence_ref="conversation:3",
        temporal_scope="durable",
    )

    assert old is not None and new is not None
    assert new.status == "active"
    assert new.supersedes == old.id
    assert repo.get(old.id).status == "deprecated"


def test_named_direct_fact_change_supersedes_the_old_value(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    old = repo.remember_direct_statement(
        extract_direct_memory_statements("내 생일은 3월 12일이야")[0],
        evidence_ref="conversation:1",
        temporal_scope="durable",
    )
    new = repo.remember_direct_statement(
        extract_direct_memory_statements("내 생일은 4월 2일이야")[0],
        evidence_ref="conversation:3",
        temporal_scope="durable",
    )

    assert old is not None and new is not None
    assert new.supersedes == old.id
    assert repo.get(old.id).status == "deprecated"
    assert repo.list_active() == (new,)


def test_recall_question_can_retrieve_stable_memory_without_exact_word_overlap(
    tmp_path: Path,
) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memory = repo.remember_explicit(
        kind="preference", content="천우는 결론을 먼저 듣는 설명을 선호한다"
    )

    assert ActiveMemoryRetriever(repo).retrieve("내가 어떤 설명을 좋아한다고 했지?") == (
        memory,
    )


def test_current_state_replaces_previous_value_and_is_always_available(
    tmp_path: Path,
) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    previous = repo.remember_current_state(
        topic="wellbeing",
        content="천우는 요즘 마음이 힘들다.",
    )
    current = repo.remember_current_state(
        topic="wellbeing",
        content="천우는 요즘 운동을 하며 마음을 회복하고 있다.",
    )

    assert repo.get(previous.id).status == "deprecated"
    assert current.supersedes == previous.id
    assert current.source == "user_current_state"
    assert current.importance == 7
    assert previous.temporal_scope == "episodic"
    assert current.temporal_scope == "episodic"
    assert ActiveMemoryRetriever(repo).retrieve("오늘 뭐 할까?") == (current,)
    assert memory_context((current,)).endswith(") " + current.content)


def test_current_states_take_priority_over_multiple_matching_stable_memories(
    tmp_path: Path,
) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    wellbeing = repo.remember_current_state(
        topic="wellbeing",
        content="천우는 현재 운동으로 마음을 회복하고 있다.",
    )
    workload = repo.remember_current_state(
        topic="workload",
        content="천우는 현재 공부량과 업무 부담이 크다.",
    )
    repo.remember_explicit(kind="semantic", content="천우는 현재 업무 연구를 한다.")
    repo.remember_explicit(kind="project", content="천우는 현재 업무 모델을 개발한다.")

    retrieved = ActiveMemoryRetriever(repo).retrieve("현재 업무는 어때?")

    assert {wellbeing.id, workload.id}.issubset({memory.id for memory in retrieved})


def test_current_state_history_keeps_old_and_current_values_for_comparison(
    tmp_path: Path,
) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    previous = repo.remember_current_state(
        topic="wellbeing",
        content="천우는 마음이 힘들어 운동을 시작했다.",
    )
    current = repo.remember_current_state(
        topic="wellbeing",
        content="천우는 운동을 하며 마음이 조금 회복됐다.",
    )
    retriever = ActiveMemoryRetriever(repo)

    assert retriever.retrieve_current_state_history("오늘 뭐 할까?") == ()
    history = retriever.retrieve_current_state_history(
        "예전과 지금 내 마음 상태가 어떻게 달라?"
    )

    assert tuple(memory.id for memory in history) == (previous.id, current.id)
    assert repo.list_current_state_history(topic="wellbeing") == history
    context = current_state_history_context(history)
    assert "원인을 천우가 직접 말하지 않았다면 추측하지 마" in context
    assert previous.content in context
    assert current.content in context
    assert is_current_state_history_query("예전과 지금 내 마음 상태가 어떻게 달라?")
    assert not is_current_state_history_query("과거 E2E 모델 구조를 설명해줘")


def test_retrieval_uses_korean_content_words_not_generic_first_person_words(
    tmp_path: Path,
) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    name = repo.remember_direct_statement(
        extract_direct_memory_statements("내 이름은 천우야")[0],
        evidence_ref="conversation:1",
        temporal_scope="durable",
    )
    birthday = repo.remember_direct_statement(
        extract_direct_memory_statements("내 생일은 3월 12일이야")[0],
        evidence_ref="conversation:3",
        temporal_scope="durable",
    )

    assert name is not None and birthday is not None
    assert ActiveMemoryRetriever(repo, max_characters=len(birthday.content)).retrieve(
        "내 생일이 언제였지?"
    ) == (birthday,)


def test_legacy_memory_database_migrates_without_losing_lifecycle_or_history(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-memory.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE memories (
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
                supersedes TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old",
                "current_state:wellbeing",
                "과거 상태 fixture",
                7,
                1.0,
                "deprecated",
                "user_current_state",
                "2026-08-01T00:00:00+00:00",
                "2026-08-02T00:00:00+00:00",
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "new",
                "current_state:wellbeing",
                "현재 상태 fixture",
                7,
                1.0,
                "active",
                "user_current_state",
                "2026-08-02T00:00:00+00:00",
                "2026-08-02T00:00:00+00:00",
                None,
                "old",
            ),
        )

    repository = SqliteMemoryRepository(database_path)
    history = repository.list_current_state_history(topic="wellbeing")

    assert tuple(memory.id for memory in history) == ("old", "new")
    assert history[1].supersedes == "old"
    assert all(memory.evidence_ref is None for memory in history)
    assert all(memory.temporal_scope == "unknown" for memory in history)

def test_replacement_deprecates_old_memory_only_when_replacement_activates(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    old = repo.add_candidate(kind="preference", content="Python config를 선호한다")
    repo.transition(old.id, "approved")
    repo.transition(old.id, "active")
    replacement = repo.replace(old.id, "YAML config를 선호한다")
    assert replacement.status == "candidate" and replacement.supersedes == old.id
    assert repo.get(old.id).status == "active"
    repo.transition(replacement.id, "approved")
    repo.transition(replacement.id, "active")
    assert repo.get(old.id).status == "deprecated"
    assert repo.get(replacement.id).status == "active"


def test_delete_removes_unreferenced_memory_from_storage_and_retrieval(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memory = repo.add_candidate(kind="preference", content="Python config를 선호한다")
    repo.transition(memory.id, "approved")
    repo.transition(memory.id, "active")

    deleted = repo.delete(memory.id)

    assert deleted.id == memory.id
    assert repo.list() == ()
    assert ActiveMemoryRetriever(repo).retrieve("Python config") == ()
    with pytest.raises(MemoryRepositoryError, match="memory not found"):
        repo.get(memory.id)


def test_delete_refuses_memory_referenced_by_replacement(tmp_path: Path) -> None:
    repo = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    old = repo.add_candidate(kind="preference", content="Python config를 선호한다")
    replacement = repo.replace(old.id, "YAML config를 선호한다")

    with pytest.raises(MemoryRepositoryError, match=f"superseded by {replacement.id}"):
        repo.delete(old.id)
    assert repo.get(old.id).id == old.id
    assert repo.get(replacement.id).supersedes == old.id
