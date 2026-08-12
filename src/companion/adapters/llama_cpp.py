"""HTTP adapter for an explicitly selected local llama.cpp server."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import ChatRequest, ChatResult


@dataclass(frozen=True)
class LlamaCppHttpChatModel:
    """Calls an OpenAI-compatible endpoint exposed by local llama.cpp only."""

    base_url: str
    timeout_seconds: float = 120.0

    def _endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    def _payload(self, request: ChatRequest, *, stream: bool) -> bytes:
        return json.dumps(
            {
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ]
                or [{"role": "user", "content": request.prompt}],
                "stream": stream,
            }
        ).encode("utf-8")

    def generate_stream(self, request: ChatRequest) -> Iterator[str]:
        """Yield the answer in pieces, as the model writes it.

        Nothing downstream can start until a sentence exists, and a whole answer
        takes two and a half to four seconds here. The first sentence is ready
        long before that, and the voice path is already sentence by sentence, so
        streaming takes most of the model's thinking off the critical path.

        Pieces are whatever the server sends — not sentences. Cutting them into
        sentences belongs to the caller, which is where the rule already lives.
        """
        endpoint = self._endpoint()
        http_request = Request(
            endpoint,
            data=self._payload(request, stream=True),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                for line in response:
                    piece = _stream_piece(line)
                    if piece:
                        yield piece
        except HTTPError as error:
            raise AdapterUnavailableError(
                f"local llama.cpp server returned HTTP {error.code} at {endpoint}"
            ) from error
        except URLError as error:
            raise AdapterUnavailableError(
                f"local llama.cpp server is unavailable at {endpoint}: {error.reason}"
            ) from error
        except TimeoutError as error:
            raise AdapterUnavailableError(
                f"local llama.cpp server timed out after {self.timeout_seconds:g}s at {endpoint}"
            ) from error

    def generate(self, request: ChatRequest) -> ChatResult:
        endpoint = self._endpoint()
        http_request = Request(
            endpoint,
            data=self._payload(request, stream=False),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw_response = response.read()
        except HTTPError as error:
            raise AdapterUnavailableError(
                f"local llama.cpp server returned HTTP {error.code} at {endpoint}"
            ) from error
        except URLError as error:
            raise AdapterUnavailableError(
                f"local llama.cpp server is unavailable at {endpoint}: {error.reason}"
            ) from error
        except TimeoutError as error:
            raise AdapterUnavailableError(
                f"local llama.cpp server timed out after {self.timeout_seconds:g}s at {endpoint}"
            ) from error

        return ChatResult(text=_extract_text(raw_response, endpoint))


def _stream_piece(line: bytes) -> str:
    """One server-sent event's text, or empty for keep-alives and the end.

    Malformed events are skipped rather than raised on: a stream that has
    already produced half an answer should finish it, and the failure that
    matters — no text at all — surfaces as an empty answer.
    """
    raw = line.strip()
    if not raw.startswith(b"data:"):
        return ""
    body = raw[len(b"data:") :].strip()
    if not body or body == b"[DONE]":
        return ""
    try:
        choices = json.loads(body).get("choices") or []
        return str(choices[0].get("delta", {}).get("content") or "")
    except (ValueError, KeyError, IndexError, AttributeError):
        return ""


def _extract_text(raw_response: bytes, endpoint: str) -> str:
    try:
        response: dict[str, Any] = json.loads(raw_response)
        text = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise AdapterUnavailableError(
            f"local llama.cpp server returned an invalid chat response at {endpoint}"
        ) from error
    if not isinstance(text, str) or not text.strip():
        raise AdapterUnavailableError(
            f"local llama.cpp server returned an empty chat response at {endpoint}"
        )
    return text
