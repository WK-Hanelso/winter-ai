from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.core import CompanionCore
from companion.work_orchestration import WorkRequest
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


class CapturingOwnedWork:
    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[WorkRequest] = []
        self.fail = fail

    def execute(self, request: WorkRequest) -> ReadOnlyWorkerResult:
        self.requests.append(request)
        if self.fail:
            from companion.worker import WorkerUnavailableError

            raise WorkerUnavailableError("typed Worker unavailable")
        return ReadOnlyWorkerResult(
            answer="내가 확인해보니 STT 이후 구간을 먼저 나눠 측정하는 게 맞아.",
            evidence=("docs/realtime-readiness.md:80",),
            limitations=("표본은 네 턴뿐이야.",),
            worker="codex-typed-project",
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


def test_core_routes_clear_project_diagnosis_without_worker_name() -> None:
    owned_work = CapturingOwnedWork()
    repository = InMemoryConversationRepository()
    core = CompanionCore(
        FakeChatModel(),
        repository,
        owned_work_executor=owned_work,
    )

    response = core.respond_to_text(
        "현재 코드와 저장된 latency 근거로 가장 큰 지연 원인을 진단해줘"
    )

    assert len(owned_work.requests) == 1
    assert "코덱스" not in response.text
    assert response.text.startswith("내가 확인해보니")
    assert "표본은 네 턴뿐이야" in response.text
    assert response.worker_name == "codex-typed-project"
    assert response.worker_evidence == ("docs/realtime-readiness.md:80",)
    assert repository.list_messages()[-1].content == response.text


def test_owned_work_stream_speaks_as_winter_before_worker_result() -> None:
    owned_work = CapturingOwnedWork()
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        owned_work_executor=owned_work,
    )
    statuses: list[str] = []
    sentences: list[str] = []

    response = core.respond_to_text_streaming(
        "현재 코드와 latency를 분석해서 개선 하나를 추천해줘",
        sentences.append,
        on_status=statuses.append,
    )

    assert sentences[0] == "잠깐만, 내가 현재 코드와 기록을 확인해볼게."
    assert statuses == ["내가 코드와 기록을 확인하고 있어."]
    assert "코덱스" not in " ".join(sentences)
    assert "내가 확인해보니" in response.text


def test_owned_work_never_routes_write_private_or_ordinary_turns() -> None:
    owned_work = CapturingOwnedWork()
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        owned_work_executor=owned_work,
    )

    for text in (
        "오늘 운동이 너무 힘들었어",
        "이 코드를 고쳐줘",
        "내 대화 기록과 코드를 분석해줘",
    ):
        response = core.respond_to_text(text)
        assert response.worker_name is None

    assert owned_work.requests == []


def test_explicit_codex_route_keeps_precedence_over_owned_canary() -> None:
    explicit = CapturingWorker()
    owned_work = CapturingOwnedWork()
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        read_only_worker=explicit,
        owned_work_executor=owned_work,
    )

    response = core.respond_to_text("코덱스로 현재 코드를 분석해줘")

    assert len(explicit.requests) == 1
    assert owned_work.requests == []
    assert response.text.startswith("코덱스로 확인했어.")


def test_owned_work_failure_is_truthful_and_does_not_fallback() -> None:
    owned_work = CapturingOwnedWork(fail=True)
    core = CompanionCore(
        FakeChatModel(prefix="should-not-appear: "),
        InMemoryConversationRepository(),
        owned_work_executor=owned_work,
    )

    response = core.respond_to_text("현재 코드 latency 원인을 분석해줘")

    assert response.text == (
        "요청을 안전하게 정리하거나 코드 근거를 확인하지 못했어. "
        "확인한 척하지 않을게."
    )
    assert "should-not-appear" not in response.text
    assert response.worker_error is True
