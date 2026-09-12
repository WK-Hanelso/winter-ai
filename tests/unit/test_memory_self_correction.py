"""A content-free error acknowledgement must not erase an evidenced answer."""

import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.grounding import ClaimEvidence, ResponseClaimGate


@pytest.mark.parametrize("text", ["내가 잘못 기억했어.", "잘못 기억했네.",
                                "제가 잘못 기억했습니다.", "제가 잘못 기억했어요."])
def test_content_free_self_correction_is_not_a_positive_memory_claim(text: str) -> None:
    assert ResponseClaimGate().violations(text, ClaimEvidence(user_text="아니야")) == ()


@pytest.mark.parametrize("text", [
    "내가 잘못 기억했어. 네가 수영을 좋아한다고 기억해.",
    "내가 잘못 기억했어, 네가 수영을 좋아한다고 기억해.",
    "내가 잘못 기억했지만 네가 수영을 좋아한다고 기억해.",
    "네가 수영을 좋아한다고 잘못 기억했어.",
    "내가 '수영을 좋아해'라고 잘못 기억했어.",
    "내가 잘못 기억했어 '수영을 좋아해'.",
    "네가 잘못 기억했어.",
    "내가 잘못 기억했어. 수영을 좋아한다는 걸 기억해둘게.",
])
def test_correction_cannot_license_an_added_fact_or_write(text: str) -> None:
    assert any(v.kind == "memory" for v in ResponseClaimGate().violations(
        text, ClaimEvidence(user_text="아니야"),
    ))


def test_actual_rejected_reply_is_preserved_without_repair_or_memory_write() -> None:
    class Model:
        calls = 0

        def generate(self, request: ChatRequest) -> ChatResult:
            self.calls += 1
            return ChatResult("토요일에 친구랑 전시회 가려고 했네. 내가 잘못 기억했어.")

    repository = InMemoryConversationRepository()
    repository.append(ConversationMessage("user", "토요일에 친구랑 전시회 가려고."))
    repository.append(ConversationMessage("assistant", "일요일에 동생이랑 영화 보러 가는구나."))
    model = Model()
    core = CompanionCore(model, repository, ConversationContextBuilder())
    response = core.respond_to_text(
        "아니, 토요일에 친구랑 전시회 간다고. 내가 언제 누구랑 어디 가는 거야?"
    )
    assert response.text == "토요일에 친구랑 전시회 가려고 했네. 내가 잘못 기억했어."
    assert model.calls == 1
    assert repository.list_messages()[-1].content == response.text
