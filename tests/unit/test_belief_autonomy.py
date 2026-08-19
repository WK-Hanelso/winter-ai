from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from companion.belief_autonomy import AutonomousBeliefActivator
from companion.beliefs import Belief, BeliefRepositoryError, SqliteBeliefRepository
from companion.winter_events import (
    SqliteWinterEventRepository,
    WinterEventRepositoryError,
)

SEOUL = ZoneInfo("Asia/Seoul")


def _evidence(
    events: SqliteWinterEventRepository,
    subject: str,
    *,
    same_day: bool = False,
) -> tuple[str, ...]:
    start = datetime(2026, 8, 17, 20, tzinfo=SEOUL)
    ids = []
    for index in range(3):
        event = events.record(
            event_key=f"turn:{index}",
            kind="outcome_observation" if index == 2 else "conversation_turn",
            subject=subject,
            source="outcome_evaluator" if index == 2 else "web",
            occurred_at=start + timedelta(hours=index if same_day else index * 13),
            metadata={
                "dialogue_behavior": "self_reciprocate",
                "supports_belief": True if index == 2 else None,
            },
        )
        ids.append(f"winter-event:{event.id}")
    return tuple(ids)


def _candidate(
    beliefs: SqliteBeliefRepository,
    evidence: tuple[str, ...],
    *,
    subject: str = "conversation:self_reciprocate",
    source: str = "winter_reflection",
    confidence: float = 0.84,
    supersedes: str | None = None,
) -> Belief:
    return beliefs.add_candidate(
        subject=subject,
        stance="맞장구보다 내 관점을 구체적으로 말하는 쪽을 택한다",
        rationale="서로 다른 날의 실제 대화에서 자기 내용이 있는 답이 더 적합했다",
        confidence=confidence,
        evidence=evidence,
        source=source,
        supersedes=supersedes,
    )


def test_low_risk_belief_activates_from_three_events_across_two_days(
    tmp_path: Path,
) -> None:
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    candidate = _candidate(beliefs, _evidence(events, "conversation:self_reciprocate"))

    result = AutonomousBeliefActivator(beliefs, events).evaluate(
        candidate.id,
        occurred_at=datetime(2026, 8, 19, 9, tzinfo=SEOUL),
    )

    assert result.activated is True
    assert result.reasons == ()
    assert beliefs.get(candidate.id).status == "active"
    audit = events.list_day("2026-08-19")[-1]
    assert audit.kind == "belief_activation_activated"
    assert "stance" not in dict(audit.metadata)


def test_same_day_or_protected_subject_stays_candidate(tmp_path: Path) -> None:
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    subject = "identity:personality"
    candidate = _candidate(
        beliefs,
        _evidence(events, subject, same_day=True),
        subject=subject,
    )

    result = AutonomousBeliefActivator(beliefs, events).evaluate(
        candidate.id,
        occurred_at=datetime(2026, 8, 18, 9, tzinfo=SEOUL),
    )

    assert result.activated is False
    assert "subject_not_low_risk" in result.reasons
    assert "insufficient_distinct_days" in result.reasons
    assert beliefs.get(candidate.id).status == "candidate"


def test_manual_or_unrelated_evidence_cannot_use_autonomous_path(tmp_path: Path) -> None:
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    evidence = _evidence(events, "conversation:engage")
    candidate = _candidate(beliefs, evidence, source="manual")

    result = AutonomousBeliefActivator(beliefs, events).evaluate(
        candidate.id,
        occurred_at=datetime(2026, 8, 19, 9, tzinfo=SEOUL),
    )

    assert result.activated is False
    assert "autonomous_source_required" in result.reasons
    assert "evidence_subject_mismatch" in result.reasons


def test_repetition_without_a_supportive_outcome_stays_candidate(
    tmp_path: Path,
) -> None:
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    subject = "conversation:self_reciprocate"
    start = datetime(2026, 8, 17, 20, tzinfo=SEOUL)
    evidence_items = []
    for index in range(3):
        event = events.record(
            event_key=f"turn:{index}",
            kind="conversation_turn",
            subject=subject,
            source="web",
            occurred_at=start + timedelta(hours=index * 13),
        )
        evidence_items.append(f"winter-event:{event.id}")
    evidence = tuple(evidence_items)
    candidate = _candidate(beliefs, evidence)

    result = AutonomousBeliefActivator(beliefs, events).evaluate(
        candidate.id,
        occurred_at=datetime(2026, 8, 19, 9, tzinfo=SEOUL),
    )

    assert result.activated is False
    assert "supportive_outcome_required" in result.reasons


def test_evidence_backed_revision_deprecates_the_old_view(tmp_path: Path) -> None:
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    old = beliefs.add_candidate(
        subject="conversation:self_reciprocate",
        stance="현재 반응을 먼저 말한다",
        rationale="초기 대화 근거",
        confidence=0.9,
        evidence=("manual-review",),
    )
    beliefs.transition(old.id, "active")
    evidence = _evidence(events, old.subject)
    revision = _candidate(beliefs, evidence, supersedes=old.id)

    result = AutonomousBeliefActivator(beliefs, events).evaluate(
        revision.id,
        occurred_at=datetime(2026, 8, 19, 9, tzinfo=SEOUL),
    )

    assert result.activated is True
    assert beliefs.get(old.id).status == "deprecated"
    assert beliefs.get(revision.id).status == "active"


def test_activation_and_audit_survive_event_mirror_failure_and_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    beliefs = SqliteBeliefRepository(tmp_path / "beliefs.sqlite")
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    candidate = _candidate(
        beliefs,
        _evidence(events, "conversation:self_reciprocate"),
    )
    original_record = events.record

    def fail_mirror(**kwargs: Any) -> Any:
        if str(kwargs["event_key"]).startswith("belief-activation:"):
            raise WinterEventRepositoryError("mirror unavailable")
        return original_record(**kwargs)

    monkeypatch.setattr(events, "record", fail_mirror)
    activator = AutonomousBeliefActivator(beliefs, events)
    occurred_at = datetime(2026, 8, 19, 9, tzinfo=SEOUL)

    with pytest.raises(WinterEventRepositoryError, match="mirror unavailable"):
        activator.evaluate(candidate.id, occurred_at=occurred_at)

    assert beliefs.get(candidate.id).status == "active"
    stored = beliefs.get_activation_decision(candidate.id)
    assert stored is not None and stored.activated is True

    monkeypatch.setattr(events, "record", original_record)
    retried = activator.evaluate(
        candidate.id,
        occurred_at=datetime(2026, 8, 20, 9, tzinfo=SEOUL),
    )

    assert retried.activated is True
    mirrored = events.list_day("2026-08-19")[-1]
    assert mirrored.kind == "belief_activation_activated"


def test_belief_state_rolls_back_when_source_audit_cannot_commit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "beliefs.sqlite"
    beliefs = SqliteBeliefRepository(database)
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    candidate = _candidate(
        beliefs,
        _evidence(events, "conversation:self_reciprocate"),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_activation_audit
            BEFORE INSERT ON belief_activation_decisions
            BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END
            """
        )

    with pytest.raises(BeliefRepositoryError, match="could not decide"):
        beliefs.decide_activation(
            candidate.id,
            reasons=(),
            occurred_at=datetime(2026, 8, 19, 9, tzinfo=SEOUL),
        )

    assert beliefs.get(candidate.id).status == "candidate"
    assert beliefs.get_activation_decision(candidate.id) is None
