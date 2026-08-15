from pathlib import Path

from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.core import CompanionCore
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository
from companion.outcome import SqliteOutcomeRepository
from companion.turn_understanding import SqliteTurnUnderstandingRepository


def test_core_links_actual_response_to_next_turn_without_changing_answers(
    tmp_path: Path,
) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    observed = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        turn_understanding_repository=turns,
        outcome_repository=outcomes,
        turn_source="cli",
    )
    control = CompanionCore(FakeChatModel(), InMemoryConversationRepository())

    first = observed.respond_to_text("첫 질문")
    first_control = control.respond_to_text("첫 질문")
    assert first.text == first_control.text
    assert outcomes.list() == ()

    second = observed.respond_to_text("아니, 그 뜻이 아니야")
    second_control = control.respond_to_text("아니, 그 뜻이 아니야")

    assert second.text == second_control.text
    events = outcomes.list()
    assert len(events) == 1
    assert events[0].prior_turn_id == first.shadow_turn_id
    assert events[0].next_turn_id == second.shadow_turn_id
    assert events[0].assistant_response == first.text
    assert events[0].next_user_text == "아니, 그 뜻이 아니야"


def test_core_records_memory_acknowledgement_as_the_actual_response(
    tmp_path: Path,
) -> None:
    from companion.memory import SqliteMemoryRepository

    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        memory_repository=SqliteMemoryRepository(tmp_path / "memory.sqlite"),
        turn_understanding_repository=turns,
        outcome_repository=outcomes,
        turn_source="voice",
    )

    first = core.respond_to_text("기억해. 나는 결론부터 듣는 걸 좋아해")
    core.respond_to_text("응, 맞아")

    assert first.text == "기억해둘게."
    assert outcomes.list()[0].assistant_response == "기억해둘게."


def test_core_records_only_memories_actually_selected_for_the_response(
    tmp_path: Path,
) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    memories = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    selected = memories.add_candidate(
        kind="preference", content="천우는 Python config를 선호한다"
    )
    memories.transition(selected.id, "approved")
    memories.transition(selected.id, "active")
    unrelated = memories.add_candidate(
        kind="semantic", content="천우의 생일은 3월 12일이다"
    )
    memories.transition(unrelated.id, "approved")
    memories.transition(unrelated.id, "active")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        memory_retriever=ActiveMemoryRetriever(memories),
        turn_understanding_repository=turns,
        outcome_repository=outcomes,
        turn_source="cli",
    )

    core.respond_to_text("내가 선호하는 Python config가 뭐였지?")
    core.respond_to_text("응, 그 기억이 맞아")

    claims = outcomes.list()[0].prior_memory_claims
    assert [(claim.id, claim.kind, claim.content) for claim in claims] == [
        (selected.id, selected.kind, selected.content)
    ]
