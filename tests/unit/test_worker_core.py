from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.core import CompanionCore
from companion.worker import ReadOnlyWorkerRequest, ReadOnlyWorkerResult


class CapturingWorker:
    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[ReadOnlyWorkerRequest] = []
        self.fail = fail

    def execute(self, request: ReadOnlyWorkerRequest) -> ReadOnlyWorkerResult:
        self.requests.append(request)
        if self.fail:
            from companion.worker import WorkerUnavailableError

            raise WorkerUnavailableError("Codex Worker를 사용할 수 없습니다.")
        return ReadOnlyWorkerResult(
            answer="원인은 설정 누락이야.",
            evidence=("src/app.py:12",),
            limitations=("수정은 하지 않았어.",),
            worker="codex",
            model="gpt-5.4",
        )


def test_core_delegates_only_explicit_codex_turn_and_preserves_worker_metadata() -> None:
    worker = CapturingWorker()
    model = FakeChatModel()
    repository = InMemoryConversationRepository()
    core = CompanionCore(model, repository, read_only_worker=worker)

    ordinary = core.respond_to_text("오늘 운동이 너무 힘들었어")
    delegated = core.respond_to_text("코덱스로 지금 코드 원인을 분석해줘")

    assert ordinary.worker_name is None
    assert len(worker.requests) == 1
    assert worker.requests[0].task == "코덱스로 지금 코드 원인을 분석해줘"
    assert delegated.text.startswith("코덱스로 확인했어. 원인은 설정 누락이야.")
    assert delegated.worker_name == "codex"
    assert delegated.worker_evidence == ("src/app.py:12",)
    assert delegated.worker_limitations == ("수정은 하지 않았어.",)
    assert repository.list_messages()[-1].content == delegated.text


def test_streaming_worker_reports_status_then_returns_same_core_answer() -> None:
    worker = CapturingWorker()
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        read_only_worker=worker,
    )
    statuses: list[str] = []
    sentences: list[str] = []

    response = core.respond_to_text_streaming(
        "코덱스로 README를 검토해줘",
        sentences.append,
        on_status=statuses.append,
    )

    assert statuses == ["코덱스로 확인하고 있어."]
    assert " ".join(sentences) == response.text
    assert response.worker_name == "codex"


def test_requested_worker_failure_is_truthful_and_does_not_use_local_fake() -> None:
    worker = CapturingWorker(fail=True)
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        read_only_worker=worker,
    )

    response = core.respond_to_text("코덱스로 프로젝트를 분석해줘")

    assert response.text == "지금은 Codex Worker를 사용할 수 없어. 로컬 답변으로 한 척하지 않을게."
    assert response.worker_name == "codex"
    assert response.worker_error is True
