from http.client import RemoteDisconnected
from io import BytesIO
import json
from urllib.error import URLError

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.contracts import ChatRequest, ConversationMessage


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = BytesIO(body)

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.read()

    def __iter__(self):
        """Line by line, the way urlopen's response is read when streaming."""
        return iter(self._body)


def test_llama_adapter_posts_openai_compatible_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeHttpResponse:
        captured["url"] = request.full_url  # type: ignore[attr-defined]
        captured["body"] = request.data  # type: ignore[attr-defined]
        captured["timeout"] = timeout
        return FakeHttpResponse(
            '{"choices": [{"message": {"content": "로컬 응답"}}]}'.encode()
        )

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", fake_urlopen)

    result = LlamaCppHttpChatModel("http://llm:8080").generate(ChatRequest(prompt="안녕"))

    assert result.text == "로컬 응답"
    assert captured["url"] == "http://llm:8080/v1/chat/completions"
    assert json.loads(captured["body"]) == {
        "messages": [{"role": "user", "content": "안녕"}],
        "stream": False,
        "max_tokens": 300,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "presence_penalty": 1.0,
    }
    assert captured["timeout"] == 120.0


def test_llama_adapter_sends_structured_context_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeHttpResponse:
        captured["body"] = request.data  # type: ignore[attr-defined]
        return FakeHttpResponse('{"choices": [{"message": {"content": "응답"}}]}'.encode())

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", fake_urlopen)
    request = ChatRequest(
        prompt="현재 질문",
        messages=(
            ConversationMessage(role="user", content="첫 질문"),
            ConversationMessage(role="assistant", content="첫 답변"),
            ConversationMessage(role="user", content="현재 질문"),
        ),
    )

    LlamaCppHttpChatModel("http://llm:8080").generate(request)

    assert json.loads(captured["body"])["messages"] == [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 답변"},
        {"role": "user", "content": "현재 질문"},
    ]


def test_llama_adapter_uses_the_turn_specific_token_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeHttpResponse:
        captured["body"] = request.data  # type: ignore[attr-defined]
        return FakeHttpResponse('{"choices": [{"message": {"content": "응답"}}]}'.encode())

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", fake_urlopen)

    LlamaCppHttpChatModel("http://llm:8080").generate(
        ChatRequest(prompt="안녕", max_tokens=120)
    )

    assert json.loads(captured["body"])["max_tokens"] == 120


def test_llama_adapter_forwards_an_explicit_structured_output_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeHttpResponse:
        captured["body"] = request.data  # type: ignore[attr-defined]
        return FakeHttpResponse(b'{"choices": [{"message": {"content": "{}"}}]}')

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", fake_urlopen)
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "probe",
            "strict": True,
            "schema": {"type": "object", "additionalProperties": False},
        },
    }

    LlamaCppHttpChatModel("http://llm:8080").generate(
        ChatRequest(
            prompt="안녕",
            response_format=response_format,
            temperature=0.0,
            seed=42,
        )
    )

    body = json.loads(captured["body"])
    assert body["response_format"] == response_format
    assert body["temperature"] == 0.0
    assert body["seed"] == 42


def test_llama_adapter_explains_unavailable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_urlopen(*args: object, **kwargs: object) -> object:
        raise URLError("connection refused")

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", failing_urlopen)

    with pytest.raises(AdapterUnavailableError, match="local llama.cpp server is unavailable"):
        LlamaCppHttpChatModel("http://llm:8080").generate(ChatRequest(prompt="안녕"))


def test_llama_adapter_wraps_remote_disconnect_without_crashing_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_urlopen(*args: object, **kwargs: object) -> object:
        raise RemoteDisconnected("server closed connection")

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", failing_urlopen)

    with pytest.raises(AdapterUnavailableError, match="disconnected"):
        LlamaCppHttpChatModel("http://llm:8080").generate(ChatRequest(prompt="안녕"))


def test_llama_adapter_sends_sampling_on_the_streaming_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both paths, because the spoken turn only ever uses the streaming one."""
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeHttpResponse:
        captured["body"] = request.data  # type: ignore[attr-defined]
        return FakeHttpResponse(b"data: [DONE]\n")

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", fake_urlopen)

    list(LlamaCppHttpChatModel("http://llm:8080").generate_stream(ChatRequest(prompt="안녕")))

    body = json.loads(captured["body"])
    assert body["stream"] is True
    assert body["temperature"] == 0.7
    assert body["presence_penalty"] == 1.0
    assert body["max_tokens"] == 300
