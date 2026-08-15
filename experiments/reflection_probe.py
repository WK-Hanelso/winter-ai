"""Show both local-model decisions behind one compact memory extraction."""

from __future__ import annotations

import argparse

from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.reflection_interpreter import (
    StructuredChatReflectionInterpreter,
)
from companion.contracts import ChatRequest, ChatResult
from companion.reflection import ReflectionExtractionRequest


class TracingChatModel:
    def __init__(self, inner: LlamaCppHttpChatModel) -> None:
        self._inner = inner
        self._call = 0

    def generate(self, request: ChatRequest) -> ChatResult:
        self._call += 1
        result = self._inner.generate(request)
        stage = "proposal" if self._call == 1 else "verification"
        print(f"[{stage}] {result.text}")
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--model-url", default="http://127.0.0.1:18080")
    args = parser.parse_args()
    model = TracingChatModel(LlamaCppHttpChatModel(args.model_url))
    extraction = StructuredChatReflectionInterpreter(model).extract(
        ReflectionExtractionRequest(1, args.text)
    )
    print(f"[accepted] {extraction.proposals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
