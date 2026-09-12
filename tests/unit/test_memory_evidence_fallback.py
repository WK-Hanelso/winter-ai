from companion.adapters.fake import InMemoryConversationRepository
from companion.contracts import ChatResult
from companion.core import CompanionCore
from companion.grounding import ClaimEvidence, ClaimViolation, ResponseClaimGate
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository


def test_failed_memory_claim_does_not_claim_real_stored_evidence_is_missing():
    evidence = ClaimEvidence("컵 색깔은?", memory_contents=("내 컵은 파란색이야.",))
    gate = ResponseClaimGate()
    violations = gate.violations("파란색이었지. 기억해뒀어.", evidence)
    assert violations  # The guard was not relaxed to pass this example.
    reply = gate.fallback(violations, evidence)
    assert "“내 컵은 파란색이야.”" in reply
    assert "근거에는 없어" not in reply
    assert "정확히 확인하지 못했어" in reply


def test_missing_memory_fallback_does_not_invent_evidence():
    reply = ResponseClaimGate().fallback((ClaimViolation("memory", "기억해뒀어."),),
                                        ClaimEvidence("회사 동료 이름은?"))
    assert reply == "그건 지금 내 기억 근거에는 없어."


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

    core = CompanionCore(Model(), InMemoryConversationRepository(),
                         memory_repository=repository,
                         memory_retriever=ActiveMemoryRetriever(repository))
    reply = core.respond_to_text("작업 기다리면서 이야기하자. 내 컵 색깔은 뭐였지?")
    assert "“내 작업용 컵은 파란색이야.”" in reply.text
    assert "그건 지금 내 기억 근거에는 없어" not in reply.text
