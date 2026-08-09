"""Offline coverage for the grounding policy."""

from __future__ import annotations

from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.contracts import ChatRequest, ChatResult
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


def test_default_policy_forbids_invented_experience_and_memory() -> None:
    instruction = GroundingPolicy().instruction()

    assert instruction is not None
    assert "겪지 않은 일" in instruction
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


def test_grounding_message_is_last_so_it_is_least_likely_to_be_dropped() -> None:
    model = CapturingChatModel()

    CompanionCore(model, InMemoryConversationRepository()).respond_to_text("안녕")

    assert "지어내지 마" in _system_contents(model)[-1]


def test_core_still_answers_normally_with_grounding_enabled() -> None:
    response = CompanionCore(
        FakeChatModel(), InMemoryConversationRepository()
    ).respond_to_text("안녕")

    assert response.text == "fake: 안녕"
