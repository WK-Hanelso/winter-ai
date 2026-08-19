from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.core import CompanionCore
from companion.winter_events import (
    SqliteWinterEventRepository,
    WinterEventError,
)


def test_event_is_idempotent_and_groups_a_real_korean_day(tmp_path: Path) -> None:
    repository = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    occurred = datetime(2026, 8, 18, 0, 30, tzinfo=ZoneInfo("Asia/Seoul"))

    first = repository.record(
        event_key="turn:1",
        kind="conversation_turn",
        subject="conversation:self_reciprocate",
        source="web",
        source_ref="shadow-1",
        occurred_at=occurred,
        metadata={"dialogue_behavior": "self_reciprocate", "has_response": True},
    )
    repeated = repository.record(
        event_key="turn:1",
        kind="conversation_turn",
        subject="conversation:self_reciprocate",
        source="web",
        source_ref="shadow-1",
        occurred_at=occurred,
        metadata={"dialogue_behavior": "self_reciprocate", "has_response": True},
    )

    assert repeated == first
    assert repository.list_day("2026-08-18") == (first,)
    day = repository.summarize_day("2026-08-18")
    assert day.event_count == 1
    assert day.kind_counts == (("conversation_turn", 1),)
    assert day.source_counts == (("web", 1),)
    assert repository.find_by_source_ref("shadow-1") == first
    assert repository.find_by_source_ref("missing") is None
    assert repository.list_dates_before("2026-08-19") == ("2026-08-18",)
    assert repository.list_dates_before("2026-08-18") == ()


def test_event_rejects_key_reuse_and_conversation_content(tmp_path: Path) -> None:
    repository = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    occurred = datetime(2026, 8, 18, tzinfo=ZoneInfo("Asia/Seoul"))
    repository.record(
        event_key="turn:1",
        kind="conversation_turn",
        subject="conversation:engage",
        source="web",
        occurred_at=occurred,
    )

    with pytest.raises(WinterEventError, match="reused"):
        repository.record(
            event_key="turn:1",
            kind="conversation_turn",
            subject="conversation:listen",
            source="web",
            occurred_at=occurred,
        )
    with pytest.raises(WinterEventError, match="must not duplicate"):
        repository.record(
            event_key="turn:2",
            kind="conversation_turn",
            subject="conversation:engage",
            source="web",
            occurred_at=occurred,
            metadata={"user_text": "private conversation"},
        )


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


def test_core_records_completed_turn_without_copying_conversation_text(
    tmp_path: Path,
) -> None:
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        turn_source="web",
        winter_event_repository=events,
        clock=FixedClock(datetime(2026, 8, 18, 9, tzinfo=ZoneInfo("Asia/Seoul"))),
    )

    response = core.respond_to_text("나도 해볼 테니까 너도 해보자")

    day = events.list_day("2026-08-18")
    assert response.dialogue_behavior == "self_reciprocate"
    assert len(day) == 1
    assert day[0].subject == "conversation:self_reciprocate"
    assert day[0].source == "web"
    metadata = dict(day[0].metadata)
    assert metadata["dialogue_behavior"] == "self_reciprocate"
    assert "text" not in " ".join(metadata)


def test_core_requires_clock_when_event_recording_is_enabled(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit Clock"):
        CompanionCore(
            FakeChatModel(),
            InMemoryConversationRepository(),
            winter_event_repository=SqliteWinterEventRepository(
                tmp_path / "winter-events.sqlite"
            ),
        )
