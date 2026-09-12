from companion.adapters.fake import InMemoryConversationRepository
from companion.context import ConversationContextBuilder
from companion.contracts import ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.grounding import ClaimEvidence, ResponseClaimGate


def test_actual_recent_user_statement_supports_recall_without_repair():
    class Model:
        calls = 0

        def generate(self, request):
            self.calls += 1
            return ChatResult("주말에 기타 수업 처음 가기로 했다고 했지.")

    repository = InMemoryConversationRepository()
    repository.append(ConversationMessage("user", "주말에는 기타 수업 처음 가보려고."))
    repository.append(ConversationMessage("assistant", "응, 처음이구나."))
    model = Model()
    core = CompanionCore(model, repository, ConversationContextBuilder())
    result = core.respond_to_text("아까 내가 주말에 뭐 한다고 했지?")
    assert result.text == "주말에 기타 수업 처음 가기로 했다고 했지."
    assert model.calls == 1


def test_conversation_evidence_does_not_authorize_a_memory_write():
    evidence = ClaimEvidence(
        user_text="응",
        conversation_contents=("주말에 기타 수업에 가.",),
    )
    assert ResponseClaimGate().violations("기타 수업을 기억해둘게.", evidence)


def test_an_assistant_invention_is_not_user_memory_evidence():
    class Model:
        def generate(self, request):
            return ChatResult("전에 동료 이름이 민수라고 말했지.")

    repository = InMemoryConversationRepository()
    repository.append(ConversationMessage("assistant", "동료 이름이 민수라고 말했지."))
    observations = []
    core = CompanionCore(Model(), repository, ConversationContextBuilder(),
                         claim_observation_recorder=observations.append)
    assert core.respond_to_text("동료 이름 기억나?").text == "전에 동료 이름이 민수라고 말했지."
    assert observations[-1].violations[0].kind == "memory"


def test_prior_question_cannot_authorize_its_unconfirmed_premise():
    class Model:
        def generate(self, request):
            return ChatResult("전에 동료 이름이 민수라고 말했지.")

    repository = InMemoryConversationRepository()
    repository.append(ConversationMessage("user", "동료 이름이 민수라고 말했지?"))
    observations = []
    core = CompanionCore(Model(), repository, ConversationContextBuilder(),
                         claim_observation_recorder=observations.append)
    assert core.respond_to_text("동료 이름 기억나?").text == "전에 동료 이름이 민수라고 말했지."
    assert observations[-1].violations[0].kind == "memory"
