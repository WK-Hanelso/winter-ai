from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.day_reflection import (
    ActiveDayReflectionRetriever,
    DayReflectionDraft,
    DayReflectionError,
    DayReflectionPoint,
    DayReflectionRequest,
    DayReflectionService,
    SqliteConversationDayReader,
    SqliteDayReflectionRepository,
    day_reflection_context,
)
from companion.winter_events import SqliteWinterEventRepository

SEOUL = ZoneInfo("Asia/Seoul")


@dataclass
class ScriptedDayInterpreter:
    draft: DayReflectionDraft
    calls: int = 0
    request: DayReflectionRequest | None = None

    def reflect(self, request: DayReflectionRequest) -> DayReflectionDraft:
        self.calls += 1
        self.request = request
        return self.draft


class CapturingChatModel:
    def __init__(self) -> None:
        self.request: ChatRequest | None = None

    def generate(self, request: ChatRequest) -> ChatResult:
        self.request = request
        return ChatResult("어제 이야기부터 이어가자.")


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 18, 9, tzinfo=SEOUL)


def _day_sources(
    tmp_path: Path,
) -> tuple[Path, SqliteWinterEventRepository]:
    conversation_path = tmp_path / "conversations.sqlite"
    conversations = SqliteConversationRepository(conversation_path)
    conversations.append(ConversationMessage("user", "오늘 회사 일이 조금 힘들었어."))
    conversations.append(
        ConversationMessage("assistant", "해결책보다 어떤 일이었는지 먼저 들을게.")
    )
    conversations.append(ConversationMessage("user", "응, 내일 이어서 이야기하자."))
    with sqlite3.connect(conversation_path) as connection:
        connection.execute(
            "UPDATE conversation_messages SET created_at = ?",
            ("2026-08-17 12:00:00",),
        )
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    events.record(
        event_key="turn:one",
        kind="conversation_turn",
        subject="conversation:listen",
        source="web",
        occurred_at=datetime(2026, 8, 17, 21, tzinfo=SEOUL),
    )
    return conversation_path, events


def test_closes_real_past_day_once_and_retrieves_it_as_fallible_context(
    tmp_path: Path,
) -> None:
    conversation_path, events = _day_sources(tmp_path)
    interpreter = ScriptedDayInterpreter(
        DayReflectionDraft(
            (
                DayReflectionPoint(
                    "shared_moment",
                    "천우가 회사에서 힘들었던 일을 꺼냈고 나는 먼저 들으려 했다.",
                    (1, 2),
                ),
                DayReflectionPoint(
                    "open_thread",
                    "회사에서 있었던 일을 다음 대화에서 이어 듣기로 했다.",
                    (1, 3),
                ),
                DayReflectionPoint(
                    "winter_reflection",
                    "해결책을 서두르지 않고 먼저 듣는 선택을 계속 확인하고 싶다.",
                    (1, 2),
                ),
            )
        )
    )
    database = tmp_path / "day-reflections.sqlite"
    repository = SqliteDayReflectionRepository(database)
    assert database.stat().st_mode & 0o777 == 0o600
    service = DayReflectionService(
        SqliteConversationDayReader(conversation_path),
        events,
        interpreter,
        repository,
    )

    first = service.close(
        "2026-08-17",
        now=datetime(2026, 8, 18, 0, 5, tzinfo=SEOUL),
    )
    repeated = service.close(
        "2026-08-17",
        now=datetime(2026, 8, 18, 8, tzinfo=SEOUL),
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.reflection == first.reflection
    assert interpreter.calls == 1
    assert interpreter.request is not None
    assert tuple(message.id for message in interpreter.request.messages) == (1, 2, 3)
    restarted = ActiveDayReflectionRetriever(
        SqliteDayReflectionRepository(database)
    ).retrieve(before_date="2026-08-18")
    assert restarted == (first.reflection,)
    context = day_reflection_context(restarted)
    assert "수정 가능한 회고" in context
    assert "지금 발화를 우선" in context
    assert "회사에서 있었던 일을 다음 대화" in context


def test_refuses_today_missing_events_and_evidence_from_another_day(
    tmp_path: Path,
) -> None:
    conversation_path, events = _day_sources(tmp_path)
    repository = SqliteDayReflectionRepository(tmp_path / "day-reflections.sqlite")
    interpreter = ScriptedDayInterpreter(
        DayReflectionDraft(
            (
                DayReflectionPoint(
                    "shared_moment",
                    "근거가 범위 밖이다.",
                    (999,),
                ),
            )
        )
    )
    service = DayReflectionService(
        SqliteConversationDayReader(conversation_path),
        events,
        interpreter,
        repository,
    )

    with pytest.raises(DayReflectionError, match="completed past"):
        service.close(
            "2026-08-17",
            now=datetime(2026, 8, 17, 23, tzinfo=SEOUL),
        )
    with pytest.raises(DayReflectionError, match="same day"):
        service.close(
            "2026-08-17",
            now=datetime(2026, 8, 18, 8, tzinfo=SEOUL),
        )
    empty_events = SqliteWinterEventRepository(tmp_path / "empty-events.sqlite")
    empty_service = DayReflectionService(
        SqliteConversationDayReader(conversation_path),
        empty_events,
        interpreter,
        repository,
    )
    with pytest.raises(DayReflectionError, match="both actual conversation"):
        empty_service.close(
            "2026-08-17",
            now=datetime(2026, 8, 18, 8, tzinfo=SEOUL),
        )


def test_core_automatically_reads_prior_day_reflection(tmp_path: Path) -> None:
    repository = SqliteDayReflectionRepository(tmp_path / "day-reflections.sqlite")
    repository.save(
        local_date="2026-08-17",
        input_fingerprint="fingerprint",
        event_ids=("event-1",),
        message_ids=(1, 2),
        points=(
            DayReflectionPoint(
                "open_thread",
                "회사에서 있었던 일을 이어 듣기로 했다.",
                (1, 2),
            ),
        ),
    )
    model = CapturingChatModel()
    core = CompanionCore(
        model,
        SqliteConversationRepository(tmp_path / "new-conversations.sqlite"),
        clock=FixedClock(),
        day_reflection_retriever=ActiveDayReflectionRetriever(repository),
    )

    core.respond_to_text("안녕")

    assert model.request is not None
    context = "\n".join(
        message.content
        for message in model.request.messages
        if message.role == "system"
    )
    assert "[겨울이의 지난날 회고]" in context
    assert "회사에서 있었던 일을 이어 듣기로 했다" in context
