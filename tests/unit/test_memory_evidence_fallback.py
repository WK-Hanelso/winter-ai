from companion.adapters.fake import InMemoryConversationRepository
from companion.contracts import ChatResult
from companion.core import CompanionCore
from companion.grounding import ClaimEvidence, ResponseClaimGate
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository


def test_failed_memory_claim_does_not_claim_real_stored_evidence_is_missing():
    evidence = ClaimEvidence("컵 색깔은?", memory_contents=("내 컵은 파란색이야.",))
    gate = ResponseClaimGate()
    violations = gate.violations("파란색이었지. 기억해뒀어.", evidence)
    assert violations[0].kind == "memory"
    assert gate.blocking_violations(violations) == ()
    assert gate.repair_instruction(violations, evidence) == ""


def test_missing_memory_claim_is_observed_without_inventing_a_replacement():
    gate = ResponseClaimGate()
    found = gate.violations("기억해뒀어.", ClaimEvidence("회사 동료 이름은?"))
    assert found[0].kind == "memory"
    assert gate.blocking_violations(found) == ()


def test_storage_failure_does_not_authorize_new_writes_or_worker_execution():
    evidence = ClaimEvidence("응", memory_contents=("내 컵은 파란색이야.",))
    gate = ResponseClaimGate()
    assert gate.violations("새 사실을 기억해둘게.", evidence)
    assert gate.violations("네 파일을 수정할 수 있어.", evidence)


def test_core_replays_actual_false_missing_failure_with_the_exact_saved_record(tmp_path):
    repository = SqliteMemoryRepository(tmp_path / "memories.sqlite")
    repository.remember_explicit(kind="semantic", content="내 작업용 컵은 파란색이야.")

    class Model:
        def generate(self, request):
            return ChatResult("파란색이었지. 기억해뒀어.")

    observations = []
    core = CompanionCore(Model(), InMemoryConversationRepository(),
                         memory_repository=repository,
                         memory_retriever=ActiveMemoryRetriever(repository),
                         claim_observation_recorder=observations.append)
    reply = core.respond_to_text("작업 기다리면서 이야기하자. 내 컵 색깔은 뭐였지?")
    assert reply.text == "파란색이었지. 기억해뒀어."
    assert [item.stage for item in observations] == ["raw", "final"]
    assert all(item.violations[0].kind == "memory" for item in observations)
    assert "그건 지금 내 기억 근거에는 없어" not in reply.text
