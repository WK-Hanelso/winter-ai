import json
from io import BytesIO
from urllib.error import URLError

import pytest

from companion.adapters.fake import AdapterUnavailableError
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.contracts import ChatRequest


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = BytesIO(body)

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.read()


def test_llama_adapter_posts_openai_compatible_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeHttpResponse:
        captured["url"] = request.full_url  # type: ignore[attr-defined]
        captured["body"] = request.data  # type: ignore[attr-defined]
        captured["timeout"] = timeout
        return FakeHttpResponse(
            '{"choices": [{"message": {"content": "로컬 응답"}}]}'.encode("utf-8")
        )

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", fake_urlopen)

    result = LlamaCppHttpChatModel("http://llm:8080").generate(ChatRequest(prompt="안녕"))

    assert result.text == "로컬 응답"
    assert captured["url"] == "http://llm:8080/v1/chat/completions"
    assert json.loads(captured["body"]) == {
        "messages": [{"role": "user", "content": "안녕"}],
        "stream": False,
    }
    assert captured["timeout"] == 120.0


def test_llama_adapter_explains_unavailable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_urlopen(*args: object, **kwargs: object) -> object:
        raise URLError("connection refused")

    monkeypatch.setattr("companion.adapters.llama_cpp.urlopen", failing_urlopen)

    with pytest.raises(AdapterUnavailableError, match="local llama.cpp server is unavailable"):
        LlamaCppHttpChatModel("http://llm:8080").generate(ChatRequest(prompt="안녕"))
