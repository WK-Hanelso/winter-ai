import threading
import time

from companion.contracts import ChatRequest, ChatResult
from companion.model_priority import ForegroundPriorityChatScheduler


class _OrderedModel:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def generate(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request.prompt)
        if request.prompt == "foreground-1":
            self.first_started.set()
            assert self.release_first.wait(timeout=2)
        return ChatResult(request.prompt)


def test_waiting_foreground_enters_before_background() -> None:
    model = _OrderedModel()
    scheduler = ForegroundPriorityChatScheduler(
        model,
        background_quiet_seconds=0,
    )
    foreground = scheduler.foreground()
    background = scheduler.background()

    first = threading.Thread(
        target=lambda: foreground.generate(ChatRequest("foreground-1"))
    )
    background_thread = threading.Thread(
        target=lambda: background.generate(ChatRequest("background"))
    )
    second = threading.Thread(
        target=lambda: foreground.generate(ChatRequest("foreground-2"))
    )
    first.start()
    assert model.first_started.wait(timeout=1)
    background_thread.start()
    second.start()
    time.sleep(0.05)
    model.release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    background_thread.join(timeout=2)

    assert model.calls == ["foreground-1", "foreground-2", "background"]


def test_background_waits_for_quiet_window_after_foreground() -> None:
    model = _OrderedModel()
    scheduler = ForegroundPriorityChatScheduler(
        model,
        background_quiet_seconds=0.08,
    )
    foreground = scheduler.foreground()
    background = scheduler.background()

    foreground.generate(ChatRequest("foreground-now"))
    started = time.perf_counter()
    background.generate(ChatRequest("background-later"))

    assert time.perf_counter() - started >= 0.06
    assert model.calls == ["foreground-now", "background-later"]


def test_explicit_user_activity_defers_background_before_model_generation() -> None:
    model = _OrderedModel()
    scheduler = ForegroundPriorityChatScheduler(
        model,
        background_quiet_seconds=0.08,
    )

    scheduler.mark_foreground_activity()
    started = time.perf_counter()
    scheduler.background().generate(ChatRequest("background-after-recording"))

    assert time.perf_counter() - started >= 0.06


class _StreamingModel:
    def generate(self, request: ChatRequest) -> ChatResult:
        return ChatResult("fallback")

    def generate_stream(self, request: ChatRequest):  # type: ignore[no-untyped-def]
        yield "첫째."
        yield "둘째."


def test_foreground_wrapper_preserves_streaming() -> None:
    scheduler = ForegroundPriorityChatScheduler(
        _StreamingModel(),  # type: ignore[arg-type]
        background_quiet_seconds=0,
    )

    assert list(scheduler.foreground().generate_stream(ChatRequest("대화"))) == [  # type: ignore[attr-defined]
        "첫째.",
        "둘째.",
    ]
