"""HTTP adapter for an explicitly selected local llama.cpp server."""

from __future__ import annotations

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

    def generate(self, request: ChatRequest) -> ChatResult:
        endpoint = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        payload = json.dumps(
            {
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": False,
            }
        ).encode("utf-8")
        http_request = Request(
            endpoint,
            data=payload,
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
