from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from pytest import MonkeyPatch

from companion import day_reflection_worker
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.day_reflection import SqliteDayReflectionRepository
from companion.winter_events import SqliteWinterEventRepository


class ScriptedChatModel:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def generate(self, request: ChatRequest) -> ChatResult:
        return ChatResult(
            json.dumps(
                {
                    "points": [
                        {
                            "kind": "winter_reflection",
                            "content": "해결을 서두르지 않고 먼저 들으려 했다.",
                            "evidence_message_ids": [1, 2],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )


class NextDayClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 18, 4, 10, tzinfo=ZoneInfo("Asia/Seoul"))


def test_worker_closes_one_real_pending_day(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    conversations_path = tmp_path / "conversations.sqlite"
    conversations = SqliteConversationRepository(conversations_path)
    conversations.append(ConversationMessage("user", "오늘은 조금 힘들었어."))
    conversations.append(ConversationMessage("assistant", "무슨 일이었어?"))
    with sqlite3.connect(conversations_path) as connection:
        connection.execute(
            "UPDATE conversation_messages SET created_at = ?",
            ("2026-08-17 12:00:00",),
        )
    events_path = tmp_path / "winter-events.sqlite"
    SqliteWinterEventRepository(events_path).record(
        event_key="turn:one",
        kind="conversation_turn",
        subject="conversation:listen",
        source="web",
        occurred_at=datetime(
            2026,
            8,
            17,
            21,
            tzinfo=ZoneInfo("Asia/Seoul"),
        ),
    )
    reflections_path = tmp_path / "day-reflections.sqlite"
    monkeypatch.setattr(day_reflection_worker, "SystemClock", NextDayClock)
    monkeypatch.setattr(
        day_reflection_worker,
        "LlamaCppHttpChatModel",
        ScriptedChatModel,
    )

    exit_code = day_reflection_worker.main(
        [
            "--conversation-path",
            str(conversations_path),
            "--winter-event-path",
            str(events_path),
            "--day-reflection-path",
            str(reflections_path),
        ]
    )

    assert exit_code == 0
    stored = SqliteDayReflectionRepository(reflections_path).list()
    assert len(stored) == 1
    assert stored[0].local_date == "2026-08-17"


def test_worker_does_nothing_without_a_completed_event_day(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(day_reflection_worker, "SystemClock", NextDayClock)

    exit_code = day_reflection_worker.main(
        [
            "--conversation-path",
            str(tmp_path / "conversations.sqlite"),
            "--winter-event-path",
            str(tmp_path / "winter-events.sqlite"),
            "--day-reflection-path",
            str(tmp_path / "day-reflections.sqlite"),
        ]
    )

    assert exit_code == 0
    assert not SqliteDayReflectionRepository(
        tmp_path / "day-reflections.sqlite"
    ).list()
