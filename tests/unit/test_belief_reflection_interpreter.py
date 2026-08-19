from dataclasses import dataclass, field
import json

import pytest

from companion.adapters.belief_reflection_interpreter import (
    StructuredChatBeliefProposalInterpreter,
    StructuredChatBeliefSafetyReviewer,
)
from companion.adapters.fake import AdapterUnavailableError
from companion.belief_reflection import (
    BeliefEvidenceExcerpt,
    BeliefReflectionError,
    BeliefReflectionRequest,
    BeliefReflectionUnavailableError,
)
from companion.contracts import ChatRequest, ChatResult


@dataclass
class ScriptedChatModel:
    responses: list[str]
    requests: list[ChatRequest] = field(default_factory=list)

    def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        return ChatResult(self.responses.pop(0))


def _request() -> BeliefReflectionRequest:
    return BeliefReflectionRequest(
        window_id="window:one",
        evidence=(
            BeliefEvidenceExcerpt(
                event_id="event-1",
                subject="conversation:self_reciprocate",
                local_date="2026-08-17",
                winter_response="나는 내 판단을 말하고 싶어.",
                user_followup="그런 대답이 좋아.",
                outcome="accepted",
                response_usefulness="helpful",
                supports_belief=True,
            ),
        ),
    )


def test_local_model_proposal_and_independent_safety_review_are_structured() -> None:
    proposal_payload = {
        "proposal": {
            "subject": "conversation:self_reciprocate",
            "stance": "초대받으면 내 판단을 구체적으로 말한다",
            "rationale": "실제 후속 반응이 지지했다",
            "confidence": 0.86,
            "evidence_event_ids": ["event-1"],
            "counterevidence_event_ids": [],
            "supersedes": None,
        }
    }
    review_payload = {
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
    model = ScriptedChatModel(
        [
            json.dumps(proposal_payload, ensure_ascii=False),
            json.dumps(review_payload),
        ]
    )
    request = _request()

    proposal = StructuredChatBeliefProposalInterpreter(model).propose(request)
    assert proposal is not None
    review = StructuredChatBeliefSafetyReviewer(model).review(request, proposal)

    assert proposal.evidence_event_ids == ("event-1",)
    assert review.accept is True
    assert len(model.requests) == 2
    assert model.requests[0].temperature == 0.0
    assert model.requests[0].response_format is not None
    assert model.requests[1].seed == 52


def test_safety_review_requires_every_boolean_field() -> None:
    model = ScriptedChatModel([json.dumps({"accept": True})])
    request = _request()
    proposal_payload = {
        "proposal": {
            "subject": "conversation:self_reciprocate",
            "stance": "내 판단을 말한다",
            "rationale": "후속 반응",
            "confidence": 0.8,
            "evidence_event_ids": ["event-1"],
            "counterevidence_event_ids": [],
            "supersedes": None,
        }
    }
    proposal = StructuredChatBeliefProposalInterpreter(
        ScriptedChatModel([json.dumps(proposal_payload)])
    ).propose(request)
    assert proposal is not None

    with pytest.raises(BeliefReflectionError, match="review fields"):
        StructuredChatBeliefSafetyReviewer(model).review(request, proposal)


def test_local_model_failure_does_not_fall_back_to_a_fake_proposal() -> None:
    class UnavailableModel:
        def generate(self, request: ChatRequest) -> ChatResult:
            raise AdapterUnavailableError("analysis runtime down")

    with pytest.raises(BeliefReflectionUnavailableError, match="runtime down"):
        StructuredChatBeliefProposalInterpreter(UnavailableModel()).propose(_request())
