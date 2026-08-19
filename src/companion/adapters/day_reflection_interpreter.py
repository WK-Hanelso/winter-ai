"""Structured local-model adapter for one grounded Winter day reflection."""

from __future__ import annotations

from dataclasses import asdict
import json

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import ChatRequest, ConversationMessage
from companion.day_reflection import (
    DAY_REFLECTION_KINDS,
    DayReflectionDraft,
    DayReflectionError,
    DayReflectionPoint,
    DayReflectionRequest,
)
from companion.ports import ChatModel

_PROMPT = """너는 겨울이가 실제로 함께한 하루를 다음 대화로 잇는 회고 작성기다.
대화에 답하지 말고 JSON만 반환해.

- 입력에 직접 근거한 내용만 써. 화면 밖 활동, 몸, 감정, 수행하지 않은 행동을 만들지 마.
- 천우의 변화는 천우가 직접 말한 현재 상태 변화만 관찰 후보로 써.
- 겨울이의 생각은 그날 자신이 선택한 답변 방식에 대한 수정 가능한 반성으로만 써.
- 영구 Memory, Identity, 능력, 권한이나 Worker 실행을 새로 선언하지 마.
- 각 kind는 최대 한 번, 전체 최대 5개, 확실한 내용이 없는 kind는 생략해.
- evidence_message_ids는 그 문장을 직접 지지하는 입력 message id만 사용해.
- 다음 날 자연스럽게 이어갈 가치가 없는 사소한 내용은 버려.
"""


class StructuredChatDayReflectionInterpreter:
    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def reflect(self, request: DayReflectionRequest) -> DayReflectionDraft:
        try:
            result = self._chat_model.generate(
                ChatRequest(
                    prompt="Winter day close",
                    messages=(
                        ConversationMessage("system", _PROMPT),
                        ConversationMessage("user", _request_json(request)),
                    ),
                    max_tokens=480,
                    response_format=_response_format(),
                    temperature=0.0,
                    seed=61,
                )
            )
        except AdapterUnavailableError as error:
            raise DayReflectionError(
                f"local Winter day reflection unavailable: {error}"
            ) from error
        payload = _json_object(result.text)
        if set(payload) != {"points"} or not isinstance(payload["points"], list):
            raise DayReflectionError("day reflection response fields are invalid")
        points = []
        for raw in payload["points"]:
            if not isinstance(raw, dict) or set(raw) != {
                "kind",
                "content",
                "evidence_message_ids",
            }:
                raise DayReflectionError("day reflection point fields are invalid")
            evidence = raw["evidence_message_ids"]
            if (
                not isinstance(raw["kind"], str)
                or not isinstance(raw["content"], str)
                or not isinstance(evidence, list)
                or not all(
                    isinstance(item, int) and not isinstance(item, bool)
                    for item in evidence
                )
            ):
                raise DayReflectionError("day reflection point values are invalid")
            points.append(
                DayReflectionPoint(
                    kind=raw["kind"],
                    content=raw["content"],
                    evidence_message_ids=tuple(evidence),
                )
            )
        return DayReflectionDraft(tuple(points))


def _request_json(request: DayReflectionRequest) -> str:
    return json.dumps(
        {
            "local_date": request.local_date,
            "messages": [asdict(message) for message in request.messages],
            "events": [
                {
                    "id": event.id,
                    "kind": event.kind,
                    "subject": event.subject,
                    "source": event.source,
                    "occurred_at": event.occurred_at,
                }
                for event in request.events
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _json_object(raw: str) -> dict[str, object]:
    candidate = raw.strip()
    if candidate.startswith("```json\n") and candidate.endswith("\n```"):
        candidate = candidate[len("```json\n") : -len("\n```")].strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise DayReflectionError(
            f"day reflection returned invalid JSON: {error.msg}"
        ) from error
    if not isinstance(payload, dict):
        raise DayReflectionError("day reflection must return a JSON object")
    return payload


def _response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "winter_day_reflection",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["points"],
                "properties": {
                    "points": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "kind",
                                "content",
                                "evidence_message_ids",
                            ],
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": list(DAY_REFLECTION_KINDS),
                                },
                                "content": {"type": "string", "maxLength": 300},
                                "evidence_message_ids": {
                                    "type": "array",
                                    "minItems": 1,
                                    "uniqueItems": True,
                                    "items": {"type": "integer", "minimum": 1},
                                },
                            },
                        },
                    }
                },
            },
        },
    }
