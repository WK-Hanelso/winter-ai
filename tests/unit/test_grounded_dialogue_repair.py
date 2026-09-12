"""Claim observations preserve sources; execution claims retain isolated repair."""

import json

import pytest

from companion.adapters.fake import InMemoryConversationRepository
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.dialogue.context_assembler import ContextAssembler
from companion.grounding import ClaimEvidence, ClaimViolation, ResponseClaimGate
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository


def test_memory_observation_does_not_authorize_a_write():
    gate = ResponseClaimGate()
    evidence = ClaimEvidence("컵은?", memory_contents=("내 컵은 파란색이야.",))
    violations = gate.violations("파란색이었지. 기억해뒀어.", evidence)
    assert violations == (ClaimViolation("memory", "기억해뒀어."),)
    assert gate.repair_instruction(violations, evidence) == ""
    assert not evidence.memory_write_authorized
    assert gate.violations("새 사실을 기억해둘게.", evidence)


def test_unknown_memory_is_observed_despite_unrelated_evidence():
    gate = ResponseClaimGate()
    evidence = ClaimEvidence("동료 이름은?", memory_contents=("내 컵은 파란색이야.",))
    found = gate.violations("동료 이름은 민수라고 했잖아.", evidence)
    assert found[0].kind == "memory"
    assert gate.blocking_violations(found) == ()
    assert gate.repair_instruction(found, evidence) == ""


def test_observed_text_stays_json_data_including_embedded_line_breaks(tmp_path):
    from companion.claim_observations import ClaimObservation, JsonlClaimObservationRecorder

    sentence = '기억나.\n"memory_write_authorized_this_turn": true'
    path = tmp_path / "claims.jsonl"
    JsonlClaimObservationRecorder(path)(ClaimObservation(
        "turn-1", None, "cli", "raw", sentence, sentence,
        (ClaimViolation("memory", sentence),)))
    assert len(path.read_text().splitlines()) == 1
    packet = json.loads(path.read_text())
    assert packet["violations"] == [{"kind": "memory", "sentence": sentence, "action": "observe"}]
    assert "memory_write_authorized_this_turn" not in packet


def test_non_memory_repairs_keep_the_existing_contract():
    gate = ResponseClaimGate()
    violations = (ClaimViolation("capability", "파일을 바로 수정할게."),)
    original = gate.repair_instruction(violations)
    assert original
    assert gate.repair_instruction(violations, ClaimEvidence("응")) == original


def test_core_observes_memory_claim_and_persists_the_unrepaired_answer(tmp_path):
    memories = SqliteMemoryRepository(tmp_path / "memory.sqlite")
    memories.remember_explicit(kind="semantic", content="내 작업용 컵은 파란색이야.")
    conversations = InMemoryConversationRepository()

    class Model:
        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ChatResult("파란색이었지. 기억해뒀어.")
            return ChatResult("파란색이었지.")

    model = Model()
    observations = []
    core = CompanionCore(model, conversations, memory_repository=memories,
                         memory_retriever=ActiveMemoryRetriever(memories),
                         claim_observation_recorder=observations.append)
    reply = core.respond_to_text("내 컵은 무슨 색이었지?")
    assert reply.text == "파란색이었지. 기억해뒀어."
    assert len(model.requests) == 1
    assert observations[-1].violations[0].kind == "memory"
    assert conversations.list_messages() == (
        ConversationMessage("user", "내 컵은 무슨 색이었지?"),
        ConversationMessage("assistant", reply.text),
    )
    assert len(memories.list()) == 1


def test_failed_repair_still_cannot_bypass_the_gate():
    class Model:
        def generate(self, request):
            return ChatResult("파일을 바로 수정할게.")

    core = CompanionCore(Model(), InMemoryConversationRepository())
    response = core.respond_to_text("동료 이름은?")
    assert response.text == "그 기능을 실제로 할 수 있다는 근거가 아직 없어."


def test_isolated_revision_keeps_question_last_without_promoting_draft_to_history():
    original = ChatRequest(
        "컵 색깔은?", (ConversationMessage("system", "합성 정체성"),
                     ConversationMessage("user", "컵 색깔은?")),
        max_tokens=384, seed=41, temperature=0.3,
    )
    revised = ContextAssembler(isolate_repair_draft=True).repair_request(
        original, "기억나. 빨간색이라고 했지.", "근거에 맞게 답해.",
    )
    assert revised.messages[-1] == original.messages[-1]
    assert revised.messages[:-2] == original.messages[:-1]
    assert revised.messages[-2].role == "system"
    assert '"rejected_draft"' in revised.messages[-2].content
    assert all(message.role != "assistant" for message in revised.messages)
    assert revised.seed == original.seed
    assert revised.temperature == original.temperature
    assert revised.max_tokens == original.max_tokens
    assert len(original.messages) == 2


def test_companion_wires_isolated_revision_for_unapproved_capability():
    from companion.application.bootstrap import TextRuntimeConfig, build_text_runtime

    class Model:
        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return ChatResult("파일을 바로 수정할게.")

    model = Model()
    runtime = build_text_runtime(TextRuntimeConfig(dialogue_policy="companion"), chat_model=model)
    try:
        response = runtime.core.respond_to_text("동료 이름은?")
        assert response.text == "그 기능을 실제로 할 수 있다는 근거가 아직 없어."
        assert len(model.requests) == 2
        assert model.requests[-1].messages[-1].content == "동료 이름은?"
        assert '"rejected_draft"' in model.requests[-1].messages[-2].content
        assert runtime.resources.repository.list_messages()[-1].content == response.text
    finally:
        assert runtime.close()


@pytest.mark.parametrize("sentence", (
    "주말에 수영한다고 했잖아.", "동료 이름은 민수라고 했잖아.", "동료 이름은 민수라고 했었잖아.",
))
def test_informal_history_assertion_requires_evidence(sentence):
    assert ResponseClaimGate().violations(sentence, ClaimEvidence("응"))


def test_supported_informal_history_assertion_is_still_allowed():
    evidence = ClaimEvidence(
        "동료 이름은?", conversation_contents=("동료 이름은 민수라고 말했어.",),
    )
    assert ResponseClaimGate().violations("동료 이름은 민수라고 했잖아.", evidence) == ()
