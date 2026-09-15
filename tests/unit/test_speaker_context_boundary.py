"""Speaker provenance is context, not a new fact, canned answer or memory write."""

from dataclasses import dataclass, field

from configs.dialogue import companion_examples

from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime
from companion.contracts import ChatRequest, ChatResult, ConversationMessage


@dataclass
class CaptureModel:
    requests: list[ChatRequest] = field(default_factory=list)

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult("응.")


def test_provenance_boundary_precedes_exact_real_turns_and_is_not_saved() -> None:
    model = CaptureModel()
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    history = (ConversationMessage("user", "새 머그컵을 샀는데 상자를 안 열었어."),
               ConversationMessage("assistant", "머그컵이 아직 잠꾸러기네ㅋㅋ"))
    try:
        for message in history:
            runtime.resources.repository.append(message)
        query = ConversationMessage("user", "내가 머그컵에 대해 뭐라고 했더라?")
        runtime.core.respond_to_text(query.content)
        assert len(model.requests) == 1
        messages = model.requests[0].messages
        start = messages.index(history[0])
        assert messages[start:start + 2] == history
        assert messages[-1] == query
        boundary = ConversationMessage(
            "system", companion_examples.REAL_DIALOGUE_INSTRUCTION,
        )
        assert messages.index(boundary) < len(messages) - 3
        assert messages[messages.index(boundary) + 1].role == "system"
        assert runtime.resources.repository.list_messages() == history + (
            query, ConversationMessage("assistant", "응."),
        )
    finally:
        runtime.close()


def test_boundary_is_generic_not_a_recall_answer_or_an_emotion_override() -> None:
    instruction = companion_examples.REAL_DIALOGUE_INSTRUCTION
    assert "user" in instruction and "assistant" in instruction
    assert "농담·비유·추측" in instruction
    assert not any(word in instruction for word in ("운동복", "텀블러", "머그컵", "토요일"))


def test_legacy_does_not_receive_new_boundary() -> None:
    model = CaptureModel()
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="legacy"), chat_model=model)
    try:
        runtime.core.respond_to_text("안녕")
        assert all(message.content != companion_examples.REAL_DIALOGUE_INSTRUCTION
                   for message in model.requests[0].messages)
    finally:
        runtime.close()
