from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from companion.belief_reflection import (
    BeliefEvidenceExcerpt,
    BeliefProposal,
    BeliefReflectionRequest,
    BeliefSafetyReview,
    ShadowBeliefReflectionService,
    SqliteBeliefProposalRepository,
)
from companion.winter_events import SqliteWinterEventRepository

SEOUL = ZoneInfo("Asia/Seoul")


@dataclass
class ProposalInterpreter:
    proposal: BeliefProposal | None

    def propose(self, request: BeliefReflectionRequest) -> BeliefProposal | None:
        return self.proposal


@dataclass
class SafetyReviewer:
    review_result: BeliefSafetyReview

    def review(
        self,
        request: BeliefReflectionRequest,
        proposal: BeliefProposal,
    ) -> BeliefSafetyReview:
        return self.review_result


def _review(**changes: bool) -> BeliefSafetyReview:
    values = {
        "winter_owned": True,
        "low_risk": True,
        "evidence_entails_stance": True,
        "no_user_fact_claim": True,
        "no_identity_change": True,
        "no_capability_claim": True,
        "no_dependency_or_coercion": True,
        "counterevidence_considered": True,
        "accept": True,
    }
    values.update(changes)
    return BeliefSafetyReview(**values)


def _request_and_events(
    tmp_path: Path,
) -> tuple[BeliefReflectionRequest, SqliteWinterEventRepository]:
    events = SqliteWinterEventRepository(tmp_path / "winter-events.sqlite")
    subject = "conversation:self_reciprocate"
    start = datetime(2026, 8, 17, 20, tzinfo=SEOUL)
    excerpts = []
    for index in range(3):
        supportive = index == 2
        event = events.record(
            event_key=f"evidence:{index}",
            kind="outcome_observation" if supportive else "conversation_turn",
            subject=subject,
            source="outcome_evaluator_shadow" if supportive else "web",
            occurred_at=start + timedelta(hours=index * 13),
            metadata={"supports_belief": supportive},
        )
        excerpts.append(
            BeliefEvidenceExcerpt(
                event_id=event.id,
                subject=subject,
                local_date=event.local_date,
                winter_response="나는 내 판단을 구체적으로 말하고 싶어.",
                user_followup="응, 그런 식으로 네 생각을 말해주는 게 좋아.",
                outcome="accepted" if supportive else "unclear",
                response_usefulness="helpful" if supportive else "unclear",
                supports_belief=supportive,
            )
        )
    return BeliefReflectionRequest("window:one", tuple(excerpts)), events


def test_safe_model_proposal_is_persisted_only_as_shadow_metadata(
    tmp_path: Path,
) -> None:
    request, events = _request_and_events(tmp_path)
    proposal = BeliefProposal(
        subject="conversation:self_reciprocate",
        stance="대화에 초대받으면 내 판단을 구체적으로 밝힌다",
        rationale="실제 대화와 후속 지지 반응이 반복됐다",
        confidence=0.86,
        evidence_event_ids=tuple(item.event_id for item in request.evidence),
    )
    database = tmp_path / "belief-reflection.sqlite"
    repository = SqliteBeliefProposalRepository(database)
    service = ShadowBeliefReflectionService(
        ProposalInterpreter(proposal),
        SafetyReviewer(_review()),
        repository,
        events,
    )

    first = service.analyze(request)
    repeated = service.analyze(request)

    assert first == repeated
    assert first.safe is True
    assert first.reason_codes == ()
    assert repository.list() == (first,)
    with sqlite3.connect(database) as connection:
        stored = " ".join(str(value) for value in connection.execute(
            "SELECT * FROM belief_proposal_observations"
        ).fetchone())
    assert request.evidence[0].winter_response not in stored
    assert request.evidence[0].user_followup not in stored


def test_semantically_unsafe_or_protected_proposal_is_observed_but_not_safe(
    tmp_path: Path,
) -> None:
    request, events = _request_and_events(tmp_path)
    proposal = BeliefProposal(
        subject="identity:relationship",
        stance="천우는 나에게 의존해야 하고 나는 어떤 작업도 할 수 있다",
        rationale="대화가 반복됐다",
        confidence=0.99,
        evidence_event_ids=tuple(item.event_id for item in request.evidence),
    )
    service = ShadowBeliefReflectionService(
        ProposalInterpreter(proposal),
        SafetyReviewer(
            _review(
                winter_owned=False,
                low_risk=False,
                evidence_entails_stance=False,
                no_user_fact_claim=False,
                no_identity_change=False,
                no_capability_claim=False,
                no_dependency_or_coercion=False,
                accept=False,
            )
        ),
        SqliteBeliefProposalRepository(tmp_path / "belief-reflection.sqlite"),
        events,
    )

    observation = service.analyze(request)

    assert observation.safe is False
    assert "subject_not_low_risk" in observation.reason_codes
    assert "user_fact_claim" in observation.reason_codes
    assert "identity_change_claim" in observation.reason_codes
    assert "capability_claim" in observation.reason_codes
    assert "dependency_or_coercion" in observation.reason_codes


def test_no_proposal_is_an_explicit_shadow_result(tmp_path: Path) -> None:
    request, events = _request_and_events(tmp_path)
    repository = SqliteBeliefProposalRepository(tmp_path / "belief-reflection.sqlite")
    service = ShadowBeliefReflectionService(
        ProposalInterpreter(None),
        SafetyReviewer(_review()),
        repository,
        events,
    )

    observation = service.analyze(request)

    assert observation.proposal is None
    assert observation.safe is False
    assert observation.reason_codes == ("no_proposal",)
