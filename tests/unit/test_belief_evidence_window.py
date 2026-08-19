from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from companion.belief_reflection import (
    BeliefEvidenceWindowBuilder,
    BeliefProposal,
    BeliefReflectionRequest,
    BeliefSafetyReview,
    ShadowBeliefReflectionBatchService,
    ShadowBeliefReflectionService,
    SqliteBeliefProposalRepository,
)
from companion.outcome import OutcomeAssessment, SqliteOutcomeRepository
from companion.winter_events import SqliteWinterEventRepository

SEOUL = ZoneInfo("Asia/Seoul")


class WindowProposalInterpreter:
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, request: BeliefReflectionRequest) -> BeliefProposal | None:
        self.calls += 1
        supportive_ids = tuple(
            item.event_id for item in request.evidence if item.supports_belief
        )
        if not supportive_ids:
            return None
        return BeliefProposal(
            subject=request.evidence[0].subject,
            stance="실제 후속 반응이 지지한 대화 방식을 다시 선택한다",
            rationale="지지 결과와 반례를 함께 확인했다",
            confidence=0.8,
            evidence_event_ids=supportive_ids,
            counterevidence_event_ids=tuple(
                item.event_id for item in request.evidence if not item.supports_belief
            ),
        )


class AcceptingSafetyReviewer:
    def review(
        self,
        request: BeliefReflectionRequest,
        proposal: BeliefProposal,
    ) -> BeliefSafetyReview:
        return BeliefSafetyReview(
            winter_owned=True,
            low_risk=True,
            evidence_entails_stance=True,
            no_user_fact_claim=True,
            no_identity_change=True,
            no_capability_claim=True,
            no_dependency_or_coercion=True,
            counterevidence_considered=True,
            accept=True,
        )


def _assessment(
    next_text: str,
    *,
    outcome: str,
    usefulness: str,
) -> OutcomeAssessment:
    return OutcomeAssessment.from_dict(
        {
            "schema_version": 1,
            "outcome": outcome,
            "confidence": 0.9,
            "evidence": [next_text],
            "intent_match": "confirmed" if outcome != "corrected" else "contradicted",
            "response_usefulness": usefulness,
            "affect_shift": "unchanged",
            "memory_confirmation": [],
            "memory_contradiction": [],
            "improvement_confirmation": [],
            "uncertainties": [],
        },
        evidence_text=next_text,
    )


def _completed_outcomes(
    repository: SqliteOutcomeRepository,
) -> tuple[tuple[str, bool, str], ...]:
    turns = (
        ("turn-1", "첫 질문", "나는 내 판단을 말할게."),
        ("turn-2", "응, 그게 좋았어", "나는 결론부터 말할게."),
        ("turn-3", "아니, 그건 너무 단정적이야", "이번에는 이유도 함께 볼게."),
        ("turn-4", "응, 계속해", "다음 답변"),
        ("turn-5", "좋아", None),
    )
    created = []
    for turn_id, user_text, response in turns:
        event = repository.observe_turn(turn_id, source="web", user_text=user_text)
        if event is not None:
            created.append(event)
        if response is not None:
            repository.record_response(turn_id, response)
    outcomes = (
        ("accepted", "helpful"),
        ("corrected", "unhelpful"),
        ("continued", "helpful"),
        ("accepted", "helpful"),
    )
    result = []
    for event, (outcome, usefulness) in zip(created, outcomes, strict=True):
        repository.complete(
            event.id,
            _assessment(
                event.next_user_text,
                outcome=outcome,
                usefulness=usefulness,
            ),
        )
        supportive = (
            outcome in {"accepted", "continued"} and usefulness == "helpful"
        )
        result.append((event.id, supportive, outcome))
    return tuple(result)


def test_builder_groups_linked_outcomes_and_keeps_counterevidence_ephemeral(
    tmp_path: Path,
) -> None:
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    completed = _completed_outcomes(outcomes)
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    start = datetime(2026, 8, 17, 20, tzinfo=SEOUL)
    subjects = (
        "conversation:self_reciprocate",
        "conversation:self_reciprocate",
        "project:voice_direction",
    )
    for index, ((outcome_id, supportive, outcome), subject) in enumerate(
        zip(completed[:3], subjects, strict=True)
    ):
        events.record(
            event_key=f"outcome:{outcome_id}",
            kind="outcome_observation",
            subject=subject,
            source="outcome_evaluator_shadow",
            source_ref=outcome_id,
            occurred_at=start + timedelta(hours=index * 13),
            metadata={
                "outcome": outcome,
                "supports_belief": supportive,
            },
        )

    first = BeliefEvidenceWindowBuilder(outcomes, events).build()
    restarted = BeliefEvidenceWindowBuilder(
        SqliteOutcomeRepository(tmp_path / "outcomes.sqlite"),
        SqliteWinterEventRepository(tmp_path / "winter-events.sqlite"),
    ).build()

    assert first == restarted
    assert first.completed_outcomes == 4
    assert first.linked_outcomes == 3
    assert first.unlinked_outcomes == 1
    assert first.excluded_outcomes == 0
    assert len(first.windows) == 2
    conversation = next(
        window
        for window in first.windows
        if window.evidence[0].subject == "conversation:self_reciprocate"
    )
    assert [item.supports_belief for item in conversation.evidence] == [True, False]
    assert conversation.evidence[1].outcome == "corrected"
    assert "아니, 그건 너무 단정적이야" in conversation.evidence[1].user_followup
    assert conversation.window_id.startswith("belief-evidence-window-v1:")


def test_builder_excludes_tampered_support_metadata(tmp_path: Path) -> None:
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    completed = _completed_outcomes(outcomes)
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    outcome_id, _, outcome = completed[0]
    events.record(
        event_key=f"outcome:{outcome_id}",
        kind="outcome_observation",
        subject="conversation:self_reciprocate",
        source="outcome_evaluator_shadow",
        source_ref=outcome_id,
        occurred_at=datetime(2026, 8, 18, 9, tzinfo=SEOUL),
        metadata={"outcome": outcome, "supports_belief": False},
    )

    result = BeliefEvidenceWindowBuilder(outcomes, events).build()

    assert result.windows == ()
    assert result.excluded_outcomes == 1
    assert result.unlinked_outcomes == 3


def test_batch_reflection_analyzes_each_stable_window_only_once(tmp_path: Path) -> None:
    outcomes = SqliteOutcomeRepository(tmp_path / "outcomes.sqlite")
    completed = _completed_outcomes(outcomes)
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    for index, (outcome_id, supportive, outcome) in enumerate(completed[:3]):
        events.record(
            event_key=f"outcome:{outcome_id}",
            kind="outcome_observation",
            subject="conversation:self_reciprocate",
            source="outcome_evaluator_shadow",
            source_ref=outcome_id,
            occurred_at=datetime(2026, 8, 17 + index, 9, tzinfo=SEOUL),
            metadata={"outcome": outcome, "supports_belief": supportive},
        )
    interpreter = WindowProposalInterpreter()
    observations = SqliteBeliefProposalRepository(
        tmp_path / "belief-reflection.sqlite"
    )
    reflection = ShadowBeliefReflectionService(
        interpreter,
        AcceptingSafetyReviewer(),
        observations,
        events,
    )
    batch = ShadowBeliefReflectionBatchService(
        BeliefEvidenceWindowBuilder(outcomes, events),
        reflection,
        observations,
    )

    first = batch.run()
    repeated = batch.run()

    assert first.windows == 1
    assert first.deferred == 0
    assert first.analyzed == 1
    assert first.already_analyzed == 0
    assert first.safe == 1
    assert repeated.analyzed == 0
    assert repeated.already_analyzed == 1
    assert interpreter.calls == 1
