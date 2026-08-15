import pytest

from companion.adapters.fake import (
    AdapterUnavailableError,
    FailingChatModel,
    FakeChatModel,
    InMemoryConversationRepository,
)
from companion.beliefs import ActiveBeliefRetriever, SqliteBeliefRepository
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.identity import CompanionIdentity
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository
from companion.open_loops import ActiveOpenLoopRetriever, SqliteOpenLoopRepository
from companion.verbal_style import VerbalStylePlanner, load_verbal_style


class CapturingChatModel:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult(text="captured")


def test_core_returns_structured_response_and_persists_a_turn() -> None:
    repository = InMemoryConversationRepository()
    core = CompanionCore(FakeChatModel(), repository)

    response = core.respond_to_text("안녕")

    assert response.text == "fake: 안녕"
    assert response.dialogue_act == "answer"
    assert response.prosody.emotion == "neutral"
    assert [(message.role, message.content) for message in repository.list_messages()] == [
        ("user", "안녕"),
        ("assistant", "fake: 안녕"),
    ]


def test_core_does_not_persist_an_assistant_message_when_model_fails() -> None:
    repository = InMemoryConversationRepository()
    core = CompanionCore(FailingChatModel(), repository)

    with pytest.raises(AdapterUnavailableError):
        core.respond_to_text("안녕")

    assert [(message.role, message.content) for message in repository.list_messages()] == [
        ("user", "안녕")
    ]


def test_core_sends_bounded_repository_context_to_the_chat_model() -> None:
    repository = InMemoryConversationRepository(
        _messages=[
            ConversationMessage(role="user", content="첫 대화"),
            ConversationMessage(role="assistant", content="첫 응답"),
        ]
    )
    chat_model = CapturingChatModel()
    core = CompanionCore(
        chat_model,
        repository,
        ConversationContextBuilder(max_messages=2, max_characters=100),
    )

    core.respond_to_text("현재 질문")

    # System blocks are asserted elsewhere; this test is about the conversation
    # window staying inside its budget.
    conversation = tuple(
        message for message in chat_model.requests[0].messages if message.role != "system"
    )
    assert conversation == (
        ConversationMessage(role="assistant", content="첫 응답"),
        ConversationMessage(role="user", content="현재 질문"),
    )


def test_core_injects_related_old_turn_outside_recent_context() -> None:
    old = [
        ConversationMessage("user", "겨울이 목소리는 질문 억양이 아직 어색해"),
        ConversationMessage("assistant", "질문 끝을 올리는 앞단부터 다시 보자."),
    ]
    recent = [ConversationMessage("user", f"최근 다른 대화 {index}") for index in range(12)]
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(_messages=old + recent),
        ConversationContextBuilder(max_messages=12, max_characters=4000),
    )

    core.respond_to_text("목소리 질문은 지금 어떻게 됐어?")

    system = [
        message.content
        for message in model.requests[0].messages
        if message.role == "system"
    ]
    assert any("오래된 실제 대화" in text for text in system)
    assert any("질문 끝을 올리는 앞단" in text for text in system)


def test_core_prefixes_identity_as_system_message() -> None:
    model = CapturingChatModel()
    identity = CompanionIdentity(
        "Winter",
        "companion",
        ("calm",),
        ("honesty",),
        ("respect",),
        ("no impersonation",),
        "1",
    )
    core = CompanionCore(model, InMemoryConversationRepository(), identity=identity)
    core.respond_to_text("안녕")
    # Identity leads: it frames every other system block.
    assert model.requests[0].messages[0].role == "system"
    assert "너는 Winter야." in model.requests[0].messages[0].content


def test_core_injects_selected_active_memory_as_distinct_system_message(tmp_path) -> None:
    repository = InMemoryConversationRepository()
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memory = memory_repository.add_candidate(
        kind="preference", content="천우는 Python config를 선호한다"
    )
    memory_repository.transition(memory.id, "approved")
    memory_repository.transition(memory.id, "active")
    model = CapturingChatModel()
    core = CompanionCore(
        model, repository, memory_retriever=ActiveMemoryRetriever(memory_repository)
    )
    core.respond_to_text("Python config는?")
    system_messages = [
        message for message in model.requests[0].messages if message.role == "system"
    ]
    assert any(memory.id in message.content for message in system_messages)

    recall = core.respond_to_text("내가 어떤 설정 방식을 좋아한다고 했지?")
    assert recall.dialogue_behavior == "recall_memory"


def test_core_injects_related_active_belief_as_a_distinct_system_message(tmp_path) -> None:
    repository = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    belief = repository.add_candidate(
        subject="음성 개발 방향",
        stance="추가 VC 학습보다 앞단 TTS 개선이 우선이다",
        rationale="질문 억양 실패가 반복됐다",
        confidence=0.78,
        evidence=("voice-test-22",),
    )
    repository.transition(belief.id, "active")
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        belief_retriever=ActiveBeliefRetriever(repository),
    )

    core.respond_to_text("음성 TTS 방향은 어떻게 생각해?")

    system_messages = [
        message for message in model.requests[0].messages if message.role == "system"
    ]
    assert any(belief.id in message.content for message in system_messages)
    assert any("고정된 사실이 아니라" in message.content for message in system_messages)


def test_core_activates_memory_only_for_explicit_memory_request(tmp_path) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        memory_repository=memory_repository,
    )

    response = core.respond_to_text("기억해. 나는 Python config를 선호해")

    assert len(response.memory_candidate_ids) == 1
    assert response.dialogue_act == "memory_candidate"
    assert response.prosody.emotion == "warm"
    assert response.verbal_style.tone == "warm"
    assert "기억해둘게." in response.text
    memory = memory_repository.get(response.memory_candidate_ids[0])
    assert (memory.kind, memory.content, memory.status, memory.source) == (
        "preference",
        "나는 Python config를 선호해",
        "active",
        "user_explicit",
    )
    assert memory_repository.list_active() == (memory,)
    assert model.requests == []


def test_core_does_not_create_candidate_for_ordinary_or_empty_request(tmp_path) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        memory_repository=memory_repository,
    )

    assert core.respond_to_text("Python config를 선호해").memory_candidate_ids == ()
    assert core.respond_to_text("기억해").memory_candidate_ids == ()
    assert memory_repository.list() == ()


def test_core_automatically_remembers_a_direct_stable_preference(tmp_path) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    model = CapturingChatModel()
    repository = InMemoryConversationRepository()
    core = CompanionCore(
        model,
        repository,
        memory_retriever=ActiveMemoryRetriever(memory_repository),
        memory_repository=memory_repository,
    )

    response = core.respond_to_text("나는 결론부터 듣는 설명을 좋아해")

    assert response.memory_candidate_ids == ()
    assert len(response.automatic_memory_ids) == 1
    assert response.text == "captured"
    assert "기억해둘게" not in response.text
    memory = memory_repository.get(response.automatic_memory_ids[0])
    assert (memory.kind, memory.status, memory.source) == (
        "preference",
        "active",
        "user_direct",
    )

    restarted_model = CapturingChatModel()
    restarted = CompanionCore(
        restarted_model,
        repository,
        memory_retriever=ActiveMemoryRetriever(memory_repository),
        memory_repository=memory_repository,
    )
    recall = restarted.respond_to_text("내가 어떤 설명을 좋아한다고 했지?")

    assert recall.dialogue_behavior == "recall_memory"
    assert any(
        memory.id in message.content
        for message in restarted_model.requests[0].messages
        if message.role == "system"
    )


def test_core_injects_state_timeline_only_for_a_change_question(tmp_path) -> None:
    memories = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    previous = memories.remember_current_state(
        topic="wellbeing",
        content="천우는 마음이 힘들어 운동을 시작했다.",
    )
    current = memories.remember_current_state(
        topic="wellbeing",
        content="천우는 운동을 하며 마음이 조금 회복됐다.",
    )
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        memory_retriever=ActiveMemoryRetriever(memories),
        memory_repository=memories,
    )

    core.respond_to_text("오늘은 뭐 할까?")
    ordinary_system = "\n".join(
        message.content
        for message in model.requests[0].messages
        if message.role == "system"
    )
    assert current.content in ordinary_system
    assert previous.content not in ordinary_system

    core.respond_to_text("예전과 지금 내 마음 상태가 어떻게 달라?")
    comparison_system = "\n".join(
        message.content
        for message in model.requests[1].messages
        if message.role == "system"
    )
    assert "현재 상태 Timeline" in comparison_system
    assert previous.content in comparison_system
    assert current.content in comparison_system
    assert comparison_system.count(current.content) == 1
    assert "원인을 천우가 직접 말하지 않았다면 추측하지 마" in comparison_system


def test_system_messages_are_ordered_identity_memory_belief_style_grounded_director(
    tmp_path,
) -> None:
    memory_repository = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memory = memory_repository.add_candidate(
        kind="preference", content="천우는 Python config를 선호한다"
    )
    memory_repository.transition(memory.id, "approved")
    memory_repository.transition(memory.id, "active")
    belief_repository = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    belief = belief_repository.add_candidate(
        subject="Python config",
        stance="명시적인 설정이 숨은 기본값보다 낫다",
        rationale="설정 변경을 검토하기 쉽다",
        confidence=0.75,
        evidence=("config-review-1",),
    )
    belief_repository.transition(belief.id, "active")
    identity = CompanionIdentity(
        "Winter", "companion", ("calm",), ("honesty",), ("respect",), ("no impersonation",), "1"
    )
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        identity=identity,
        memory_retriever=ActiveMemoryRetriever(memory_repository),
        belief_retriever=ActiveBeliefRetriever(belief_repository),
    )

    core.respond_to_text("Python config는?")

    system_messages = [
        message for message in model.requests[0].messages if message.role == "system"
    ]
    assert len(system_messages) == 5
    # Identity frames everything; grounding sits closest to the generated turn
    # because it is the constraint that must not be dropped.
    assert "너는 Winter야." in system_messages[0].content
    assert memory.id in system_messages[1].content
    assert belief.id in system_messages[2].content
    # Not "반말": the register is drawn per turn, so asserting on it would fail
    # whenever the polite draw came up. The length rule is in every style block.
    assert "문장 이내로" in system_messages[3].content
    assert "지어내지 마" in system_messages[4].content
    assert "질문에 먼저 직접 답해" in system_messages[4].content


def test_core_remembers_and_recalls_an_explicit_unfinished_thread(tmp_path) -> None:
    open_loop_repository = SqliteOpenLoopRepository(tmp_path / "dialogue-state.sqlite")
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        open_loop_repository=open_loop_repository,
        open_loop_retriever=ActiveOpenLoopRetriever(open_loop_repository),
    )

    first = core.respond_to_text("질문 억양은 내일 다시 들어보자")
    second = core.respond_to_text("아까 그 얘기 이어가자")

    assert len(first.opened_loop_ids) == 1
    assert second.dialogue_behavior == "continue_thread"
    assert second.recalled_loop_ids == first.opened_loop_ids
    second_system = [
        message.content
        for message in model.requests[1].messages
        if message.role == "system"
    ]
    assert any("질문 억양은 내일 다시 들어보자" in text for text in second_system)
    assert any("막연히 '어떤 얘기?'" in text for text in second_system)


def test_core_directs_distress_toward_listening_not_generic_problem_solving() -> None:
    model = CapturingChatModel()
    core = CompanionCore(model, InMemoryConversationRepository())

    response = core.respond_to_text("같은 실패를 반복해서 너무 답답해")

    assert response.dialogue_behavior == "listen"
    instructions = [
        message.content
        for message in model.requests[0].messages
        if message.role == "system"
    ]
    assert any("해결책부터 내놓지 마" in text for text in instructions)


def test_non_explanation_response_is_bounded_to_two_sentences() -> None:
    class LongModel:
        def generate(self, request: ChatRequest) -> ChatResult:
            return ChatResult(text="첫 문장이야. 둘째 문장이야. 셋째 문장이야. 넷째 문장이야.")

    repository = InMemoryConversationRepository()
    response = CompanionCore(  # type: ignore[arg-type]
        LongModel(),
        repository,
        verbal_style_planner=VerbalStylePlanner(
            load_verbal_style("reference_conversation")
        ),
    ).respond_to_text("오늘 어때?")

    assert response.text == "첫 문장이야. 둘째 문장이야."
    assert repository.list_messages()[-1].content == response.text


def test_streaming_never_hands_over_more_than_planned_sentence_limit() -> None:
    class LongStreamingModel:
        def generate(self, request: ChatRequest) -> ChatResult:
            raise AssertionError("streaming path should not fall back")

        def generate_stream(self, request: ChatRequest):  # type: ignore[no-untyped-def]
            yield from ("첫 문장이야. ", "둘째 문장이야. ", "셋째 문장이야. ")

    handed: list[str] = []
    response = CompanionCore(  # type: ignore[arg-type]
        LongStreamingModel(),
        InMemoryConversationRepository(),
        verbal_style_planner=VerbalStylePlanner(
            load_verbal_style("reference_conversation")
        ),
    ).respond_to_text_streaming("오늘 어때?", handed.append)

    assert handed == ["첫 문장이야.", "둘째 문장이야."]
    assert response.text == "첫 문장이야. 둘째 문장이야."


def test_core_repairs_a_stance_that_evades_the_choice() -> None:
    class RepairingModel:
        def __init__(self) -> None:
            self.requests: list[ChatRequest] = []

        def generate(self, request: ChatRequest) -> ChatResult:
            self.requests.append(request)
            if len(self.requests) == 1:
                return ChatResult(text="상황에 따라 달라. 둘 다 중요해.")
            return ChatResult(
                text="나는 대화 기억을 먼저 만들겠어. 관계의 연속성이 지금 더 부족하니까."
            )

    model = RepairingModel()
    core = CompanionCore(
        model,  # type: ignore[arg-type]
        InMemoryConversationRepository(),
        verbal_style_planner=VerbalStylePlanner(
            load_verbal_style("reference_conversation")
        ),
    )

    response = core.respond_to_text("둘 중 뭐가 더 낫다고 생각해?")

    assert len(model.requests) == 2
    assert response.text.startswith("나는 대화 기억을 먼저")
    assert any(
        message.role == "assistant" and "상황에 따라" in message.content
        for message in model.requests[1].messages
    )


def test_asking_for_an_explanation_widens_the_length_the_model_is_given() -> None:
    # The point of the whole thing: the cap 겨울이 is told about has to change
    # with the request, not only the metadata we report afterwards.
    from companion.verbal_style import VerbalStylePlanner, load_verbal_style

    model = CapturingChatModel()
    core = CompanionCore(
        model,  # type: ignore[arg-type]
        InMemoryConversationRepository(),
        verbal_style_planner=VerbalStylePlanner(
            load_verbal_style("reference_conversation")
        ),
    )

    short = core.respond_to_text("오늘 날씨는 어때?")
    long = core.respond_to_text("이거 좀 설명해줄래?")

    assert (short.dialogue_act, long.dialogue_act) == ("answer", "explain")
    instructions = [
        message.content
        for request in model.requests
        for message in request.messages
        if message.role == "system"
    ]
    assert any("2문장 이내" in text for text in instructions)
    assert any("4문장 이내" in text for text in instructions)


def test_streaming_hands_over_each_sentence_as_it_is_written() -> None:
    # The point of streaming: stage 1 starts on the first sentence while the
    # model is still writing the second.
    class StreamingChatModel:
        def generate(self, request: ChatRequest) -> ChatResult:
            raise AssertionError("streaming path should not fall back")

        def generate_stream(self, request: ChatRequest):  # type: ignore[no-untyped-def]
            yield from ("응, ", "그랬구나. ", "근데 ", "괜찮아?")

    handed: list[str] = []
    core = CompanionCore(  # type: ignore[arg-type]
        StreamingChatModel(),
        InMemoryConversationRepository(),
        verbal_style_planner=VerbalStylePlanner(
            load_verbal_style("reference_conversation")
        ),
    )

    response = core.respond_to_text_streaming("발표 망쳤어", handed.append)

    assert handed == ["응, 그랬구나.", "근데 괜찮아?"]
    assert response.text == "응, 그랬구나. 근데 괜찮아?"


def test_streaming_falls_back_when_the_model_cannot_stream() -> None:
    handed: list[str] = []
    core = CompanionCore(FakeChatModel(), InMemoryConversationRepository())

    response = core.respond_to_text_streaming("안녕", handed.append)

    assert handed == [response.text]
