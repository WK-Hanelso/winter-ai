import base64
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from companion.contracts import AudioInput, AudioOutput, SpeechRequest, Transcript
from companion.owned_work import OwnedWorkUpdate
from companion.voice_latency import SqliteVoiceLatencyRepository
from companion.web import Companion, make_handler


class OneSentenceCore:
    def respond_to_text_streaming(
        self, text: str, on_sentence: object, on_status: object = None
    ) -> None:
        assert text == "안녕"
        on_sentence("응, 안녕.")  # type: ignore[operator]


class DirectVoice:
    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        assert request.text == "응, 안녕."
        return AudioOutput(b"direct-wav", "audio/wav")


class AnyVoice:
    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        return AudioOutput(request.text.encode(), "audio/wav")


class UnusedStt:
    def transcribe(self, audio: AudioInput) -> Transcript:
        return Transcript("안녕")


class WorkInbox:
    def __init__(self) -> None:
        self.updates = (
            OwnedWorkUpdate(
                id="job-1",
                status="completed",
                text="확인 끝났어. 다음은 실제 표본을 더 모으는 게 맞아.",
                evidence=("docs/realtime.md:1",),
                limitations=("표본은 네 턴뿐이야.",),
            ),
        )
        self.acknowledged: list[str] = []

    def ready(self) -> tuple[OwnedWorkUpdate, ...]:
        return self.updates

    def acknowledge(self, update_id: str) -> None:
        self.acknowledged.append(update_id)
        self.updates = tuple(
            update for update in self.updates if update.id != update_id
        )


def test_direct_voice_path_emits_stage_one_without_conversion() -> None:
    events: list[tuple[str, dict[str, str | float]]] = []
    companion = Companion(  # type: ignore[arg-type]
        OneSentenceCore(), DirectVoice(), None, UnusedStt()
    )

    companion.answer("안녕", lambda event, payload: events.append((event, payload)))

    audio = next(payload for event, payload in events if event == "audio")
    assert base64.b64decode(str(audio["wav"])) == b"direct-wav"


def test_web_delivers_and_acknowledges_background_winter_result(
    tmp_path: Path,
) -> None:
    inbox = WorkInbox()
    companion = Companion(  # type: ignore[arg-type]
        OneSentenceCore(), AnyVoice(), None, UnusedStt(), inbox
    )
    repository = SqliteVoiceLatencyRepository(tmp_path / "voice-latency.sqlite")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(companion, repository))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/work-updates")
        response = connection.getresponse()
        payload = json.loads(response.read())
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)

    assert response.status == 200
    assert payload["updates"][0]["id"] == "job-1"
    assert payload["updates"][0]["text"].startswith("확인 끝났어")
    assert base64.b64decode(payload["updates"][0]["wav"]).startswith(
        "확인 끝났어".encode()
    )
    assert payload["updates"][0]["evidence"] == ["docs/realtime.md:1"]
    assert payload["updates"][0]["limitations"] == ["표본은 네 턴뿐이야."]
    assert inbox.acknowledged == ["job-1"]


def test_background_update_is_not_rendered_twice_before_ack() -> None:
    inbox = WorkInbox()
    companion = Companion(  # type: ignore[arg-type]
        OneSentenceCore(), AnyVoice(), None, UnusedStt(), inbox
    )

    first = companion.work_updates()
    second = companion.work_updates()

    assert len(first) == 1
    assert second == ()
    companion.release_work_updates(("job-1",))
    assert len(companion.work_updates()) == 1


class FailingCore:
    def respond_to_text_streaming(
        self, text: str, on_sentence: object, on_status: object = None
    ) -> None:
        raise RuntimeError("local model stopped")


def test_core_failure_reaches_caller_without_done() -> None:
    events: list[tuple[str, dict[str, str | float]]] = []
    companion = Companion(  # type: ignore[arg-type]
        FailingCore(), AnyVoice(), None, UnusedStt()
    )

    with pytest.raises(RuntimeError, match="local model stopped"):
        companion.answer("안녕", lambda event, payload: events.append((event, payload)))

    assert all(event != "done" for event, _payload in events)


def test_core_failure_is_an_sse_error_not_blank_done(tmp_path: Path) -> None:
    companion = Companion(  # type: ignore[arg-type]
        FailingCore(), AnyVoice(), None, UnusedStt()
    )
    repository = SqliteVoiceLatencyRepository(tmp_path / "voice-latency.sqlite")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(companion, repository))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        body = json.dumps({"text": "안녕"}, ensure_ascii=False).encode()
        connection.request(
            "POST",
            "/turn",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        events = response.read().decode()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)

    assert response.status == 200
    assert "event: error" in events
    assert "local model stopped" in events
    assert "event: done" not in events


class ConcurrentCoreProbe:
    def __init__(self) -> None:
        self.first_entered = threading.Event()
        self.second_entered = threading.Event()
        self.release_first = threading.Event()
        self._state_lock = threading.Lock()
        self._calls = 0
        self._active = 0
        self.max_active = 0

    def respond_to_text_streaming(
        self, text: str, on_sentence: object, on_status: object = None
    ) -> None:
        with self._state_lock:
            self._calls += 1
            call = self._calls
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        if call == 1:
            self.first_entered.set()
            assert self.release_first.wait(timeout=2)
        else:
            self.second_entered.set()
        on_sentence(f"{text} 답")  # type: ignore[operator]
        with self._state_lock:
            self._active -= 1


def test_two_answers_never_run_shared_core_concurrently() -> None:
    core = ConcurrentCoreProbe()
    companion = Companion(core, AnyVoice(), None, UnusedStt())  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, str | float]]] = []

    def record(event: str, payload: dict[str, str | float]) -> None:
        events.append((event, payload))

    first = threading.Thread(target=lambda: companion.answer("첫째", record))
    second = threading.Thread(target=lambda: companion.answer("둘째", record))
    first.start()
    assert core.first_entered.wait(timeout=1)
    second.start()
    second_started_before_release = core.second_entered.wait(timeout=0.2)
    core.release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_started_before_release is False
    assert core.max_active == 1


class FailingVoice:
    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        raise RuntimeError("voice server stopped")


def test_voice_failure_never_emits_done() -> None:
    events: list[tuple[str, dict[str, str | float]]] = []
    companion = Companion(  # type: ignore[arg-type]
        OneSentenceCore(), FailingVoice(), None, UnusedStt()
    )

    with pytest.raises(RuntimeError, match="voice server stopped"):
        companion.answer("안녕", lambda event, payload: events.append((event, payload)))

    assert all(event != "done" for event, _payload in events)
