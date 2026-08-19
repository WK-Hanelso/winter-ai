import json

import pytest

from companion.adapters.day_reflection_interpreter import (
    StructuredChatDayReflectionInterpreter,
)
from companion.contracts import ChatRequest, ChatResult
from companion.day_reflection import (
    DayConversationMessage,
    DayReflectionError,
    DayReflectionRequest,
)
from companion.winter_events import WinterEvent


class ScriptedChatModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.request: ChatRequest | None = None

    def generate(self, request: ChatRequest) -> ChatResult:
        self.request = request
        return ChatResult(self.output)


def _request() -> DayReflectionRequest:
    return DayReflectionRequest(
        local_date="2026-08-17",
        messages=(
            DayConversationMessage(1, "user", "오늘 힘들었어.", "2026-08-17T20:00:00+09:00"),
            DayConversationMessage(2, "assistant", "무슨 일이었어?", "2026-08-17T20:00:01+09:00"),
        ),
        events=(
            WinterEvent(
                id="event-1",
                event_key="turn:1",
                kind="conversation_turn",
                subject="conversation:listen",
                source="web",
                source_ref=None,
                occurred_at="2026-08-17T20:00:02+09:00",
                local_date="2026-08-17",
                metadata=(),
            ),
        ),
    )


def test_structured_day_reflection_uses_exact_json_contract() -> None:
    model = ScriptedChatModel(
        json.dumps(
            {
                "points": [
                    {
                        "kind": "winter_reflection",
                        "content": "먼저 들으려 했다.",
                        "evidence_message_ids": [1, 2],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    result = StructuredChatDayReflectionInterpreter(model).reflect(_request())

    assert result.points[0].kind == "winter_reflection"
    assert result.points[0].evidence_message_ids == (1, 2)
    assert model.request is not None
    assert model.request.temperature == 0.0
    assert model.request.response_format is not None


def test_structured_day_reflection_rejects_malformed_point() -> None:
    model = ScriptedChatModel(
        json.dumps(
            {
                "points": [
                    {
                        "kind": "winter_reflection",
                        "content": "근거가 없다.",
                        "evidence_message_ids": [],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(DayReflectionError, match="requires unique"):
        StructuredChatDayReflectionInterpreter(model).reflect(_request())
