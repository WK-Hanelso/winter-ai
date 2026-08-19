from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.belief_autonomy import AutonomousBeliefActivator
from companion.beliefs import SqliteBeliefRepository
from companion.core import CompanionCore
from companion.lived_continuity import summarize_lived_continuity
from companion.winter_events import SqliteWinterEventRepository

SEOUL = ZoneInfo("Asia/Seoul")


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


def test_actual_turns_and_a_supportive_outcome_can_activate_a_low_risk_view(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    clock = MutableClock(datetime(2026, 8, 17, 20, tzinfo=SEOUL))
    core = CompanionCore(
        FakeChatModel(),
        conversations,
        turn_source="web",
        winter_event_repository=events,
        clock=clock,
    )

    core.respond_to_text("겨울이도 오늘 대화 안에 함께 있었으면 해")
    clock.current = datetime(2026, 8, 18, 8, tzinfo=SEOUL)
    core.respond_to_text("너도 여기서 네 모습을 조금 보여줘")
    core.respond_to_text("겨울이는 반응만 하지 말고 이 대화에 함께 있어줘")

    evidence_events = tuple(
        event
        for day in ("2026-08-17", "2026-08-18")
        for event in events.list_day(day)
    )
    assert len(evidence_events) == 3
    assert {event.subject for event in evidence_events} == {
        "conversation:self_reciprocate"
    }
    supportive_outcome = events.record(
        event_key="outcome:self-reciprocate:one",
        kind="outcome_observation",
        subject="conversation:self_reciprocate",
        source="outcome_evaluator",
        occurred_at=datetime(2026, 8, 18, 8, 30, tzinfo=SEOUL),
        metadata={"supports_belief": True},
    )
    candidate = beliefs.add_candidate(
        subject="conversation:self_reciprocate",
        stance="천우가 나를 대화에 포함할 때 내 판단을 구체적으로 밝힌다",
        rationale="서로 다른 날의 실제 대화 세 번에서 같은 관계 행동이 선택됐다",
        confidence=0.84,
        evidence=tuple(
            f"winter-event:{event.id}"
            for event in (*evidence_events, supportive_outcome)
        ),
        source="winter_reflection",
    )

    result = AutonomousBeliefActivator(beliefs, events).evaluate(
        candidate.id,
        occurred_at=datetime(2026, 8, 18, 9, tzinfo=SEOUL),
    )
    summary = summarize_lived_continuity(events, beliefs, "2026-08-18")

    assert result.activated is True
    assert beliefs.get(candidate.id).status == "active"
    assert summary.day.event_count == 4
    assert summary.active_beliefs == 1
    assert summary.recent_activations == 1
    assert len(conversations.list_messages()) == 6
