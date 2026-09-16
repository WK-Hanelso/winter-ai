import threading

from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.core import CompanionCore
from companion.owned_work import AsyncOwnedWorkCoordinator
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


def test_legacy_core_does_not_route_codex_text_or_execute_worker() -> None:
    worker = CapturingWorker()
    repository = InMemoryConversationRepository()
    core = CompanionCore(FakeChatModel(), repository, read_only_worker=worker)
    for prompt in ("오늘 운동이 너무 힘들었어", "codex로 조사해줘"):
        response = core.respond_to_text(prompt)
        assert response.worker_request is None
        assert repository.list_messages()[-1].content == response.text
    assert worker.requests == []


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


class BlockingOwnedWork:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request: WorkRequest) -> ReadOnlyWorkerResult:
        self.started.set()
        assert self.release.wait(timeout=2)
        return ReadOnlyWorkerResult(
            answer="확인이 끝났어.",
            evidence=("README.md:1",),
            limitations=(),
            worker="typed-worker",
            model="test",
        )


def test_background_owned_work_ack_does_not_wait_and_chat_continues() -> None:
    executor = BlockingOwnedWork()
    coordinator = AsyncOwnedWorkCoordinator(executor)
    model = FakeChatModel(prefix="일반 대화: ")
    core = CompanionCore(
        model,
        InMemoryConversationRepository(),
        owned_work_submitter=coordinator,
    )

    ack = core.respond_to_text("현재 코드 latency 원인을 분석해줘")
    assert executor.started.wait(timeout=1)
    ordinary = core.respond_to_text("그동안 오늘 운동 이야기하자")

    assert ack.text == "응, 내가 확인해볼게. 끝나면 내가 다시 말해줄게."
    assert ack.worker_name == "winter-owned-work-background"
    assert ordinary.text.startswith("일반 대화:")
    executor.release.set()
    assert coordinator.wait_for_idle(timeout=2)
    assert coordinator.ready()[0].text == "확인이 끝났어."
