from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from companion.belief_autonomy import AutonomousBeliefActivator
from companion.belief_reflection import SqliteBeliefProposalRepository
from companion.beliefs import SqliteBeliefRepository
from companion.lived_continuity import (
    render_lived_continuity,
    summarize_lived_continuity,
)
from companion.winter_events import SqliteWinterEventRepository


def test_monitor_reports_real_day_and_block_reasons_without_conversation_text(
    tmp_path: Path,
) -> None:
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    now = datetime(2026, 8, 18, 9, tzinfo=ZoneInfo("Asia/Seoul"))
    event = events.record(
        event_key="turn:1",
        kind="conversation_turn",
        subject="conversation:self_reciprocate",
        source="web",
        occurred_at=now,
        metadata={"dialogue_behavior": "self_reciprocate"},
    )
    candidate = beliefs.add_candidate(
        subject="conversation:self_reciprocate",
        stance="내 생각을 말한다",
        rationale="한 번의 실제 대화에서 관찰했다",
        confidence=0.9,
        evidence=(f"winter-event:{event.id}",),
        source="winter_reflection",
    )
    AutonomousBeliefActivator(beliefs, events).evaluate(candidate.id, occurred_at=now)
    proposals = SqliteBeliefProposalRepository(tmp_path / "belief-reflection.sqlite")
    proposals.record(
        window_id="window:one",
        proposal=None,
        safe=False,
        reason_codes=("semantic_risk",),
    )

    summary = summarize_lived_continuity(
        events,
        beliefs,
        "2026-08-18",
        proposals,
    )
    rendered = render_lived_continuity(summary)

    assert summary.day.event_count == 2
    assert summary.candidate_beliefs == 1
    assert summary.recent_blocks == 1
    assert "insufficient_independent_evidence" in summary.recent_block_reasons
    assert summary.blocked_shadow_proposals == 1
    assert summary.shadow_block_reasons == ("semantic_risk",)
    assert "실제 사건: 2" in rendered
    assert "Shadow 관점 제안 통과/차단: 0/1" in rendered
    assert "conversation text" not in rendered
