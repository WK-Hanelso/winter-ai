"""Offline coverage for the grounding policy."""

from __future__ import annotations

from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.grounding import GroundingPolicy
from companion.verbal_style import VerbalStylePlanner, load_verbal_style


class CapturingChatModel:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult(text="captured")


def _system_contents(model: CapturingChatModel) -> list[str]:
    return [
        message.content
        for message in model.requests[0].messages
        if message.role == "system"
    ]


def test_default_policy_forbids_inventing_the_world_but_not_a_self() -> None:
    # The distinction this policy exists to draw. Forbidding experience outright
    # had 겨울이 answering "오늘 하루 어땠어?" with "지금은 하루를 보내지 않아",
    # which is not grounding — it is a companion with no inside.
    instruction = GroundingPolicy().instruction()

    assert instruction is not None
    assert ("바깥일(날씨·뉴스·시세·회사 정보)은 검색 결과가 있을 때만, 출처를 붙여 말해."
            in instruction)
    assert "결과가 없으면 지어내지 말고 없다고 해. 천우가 오늘 뭘 했는지는 물어봐." in instruction
    assert "네 생각이나 기분은 네 것" in instruction
    assert "기억한다고 하지 마" in instruction


def test_policy_can_be_emptied_but_is_not_empty_by_default() -> None:
    empty = GroundingPolicy(
        forbid_invented_experience=False,
        forbid_invented_memory=False,
        admit_missing_information=False,
    )

    assert empty.instruction() is None
    assert GroundingPolicy().instruction() is not None


def test_grounding_is_applied_without_an_identity_file() -> None:
    # Not fabricating must not depend on the user having configured anything.
    model = CapturingChatModel()

    CompanionCore(model, InMemoryConversationRepository()).respond_to_text("안녕")

    assert any("지어내지 마" in content for content in _system_contents(model))


def test_grounding_is_applied_under_every_verbal_style_profile() -> None:
    for profile in ("base", "reference_broadcast"):
        model = CapturingChatModel()
        core = CompanionCore(
            model,
            InMemoryConversationRepository(),
            verbal_style_planner=VerbalStylePlanner(load_verbal_style(profile)),
        )

        core.respond_to_text("안녕")

        assert any("지어내지 마" in content for content in _system_contents(model))


def test_grounding_precedes_history_act_is_in_variable_tail_and_current_user_is_last() -> None:
    model = CapturingChatModel()
    repository = InMemoryConversationRepository()
    history = (ConversationMessage("user", "나 왔어"), ConversationMessage("assistant", "어서 와"))
    for message in history:
        repository.append(message)

    CompanionCore(model, repository, ConversationContextBuilder()).respond_to_text("안녕")

    messages = model.requests[0].messages
    instruction = GroundingPolicy().instruction()
    assert instruction is not None
    grounding = ConversationMessage("system", instruction)
    history_at = messages.index(history[0])
    assert messages.index(grounding) < history_at
    assert messages[history_at:history_at + len(history)] == history
    tail = messages[history_at + len(history):-1]
    assert len(tail) == 1 and tail[0].role == "system"
    assert tail[0].content.startswith("천우가 말한 내용 중 정서나 의도가 실린 부분에 반응해.")
    assert messages[-1] == ConversationMessage("user", "안녕")


def test_core_still_answers_normally_with_grounding_enabled() -> None:
    response = CompanionCore(
        FakeChatModel(), InMemoryConversationRepository()
    ).respond_to_text("안녕")

    assert response.text == "fake: 안녕"
