"""Conservative autonomous activation for Winter-owned low-risk beliefs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from companion.beliefs import (
    Belief,
    BeliefActivationDecision,
    SqliteBeliefRepository,
)
from companion.winter_events import SqliteWinterEventRepository, WinterEventRepositoryError

AUTONOMOUS_SOURCE = "winter_reflection"
MINIMUM_CONFIDENCE = 0.80
MINIMUM_EVIDENCE = 3
MINIMUM_DAYS = 2
_ALLOWED_SUBJECT_PREFIXES = ("conversation:", "relationship:", "project:")
_ALLOWED_EVIDENCE_KINDS = (
    "conversation_turn",
    "evaluation_result",
    "outcome_observation",
)
_EVALUATIVE_EVIDENCE_KINDS = ("evaluation_result", "outcome_observation")


@dataclass(frozen=True)
class BeliefActivationResult:
    candidate_id: str
    activated: bool
    reasons: tuple[str, ...]
    belief: Belief


class AutonomousBeliefActivator:
    """Activate only evidence-backed Winter judgements in approved namespaces."""

    def __init__(
        self,
        beliefs: SqliteBeliefRepository,
        events: SqliteWinterEventRepository,
    ) -> None:
        self._beliefs = beliefs
        self._events = events

    def evaluate(self, candidate_id: str, *, occurred_at: datetime) -> BeliefActivationResult:
        decision = self._beliefs.get_activation_decision(candidate_id)
        if decision is None:
            candidate = self._beliefs.get(candidate_id)
            belief, decision = self._beliefs.decide_activation(
                candidate.id,
                reasons=self._reasons(candidate),
                occurred_at=occurred_at,
            )
        else:
            belief = self._beliefs.get(candidate_id)
        self._mirror_decision(belief, decision)
        return BeliefActivationResult(
            candidate_id,
            decision.activated,
            decision.reasons,
            belief,
        )

    def _mirror_decision(
        self,
        belief: Belief,
        decision: BeliefActivationDecision,
    ) -> None:
        outcome = "activated" if decision.activated else "blocked"
        self._events.record(
            event_key=f"belief-activation:{belief.id}",
            kind=f"belief_activation_{outcome}",
            subject=belief.subject,
            source="autonomous_belief_policy_v1",
            source_ref=belief.id,
            occurred_at=datetime.fromisoformat(decision.occurred_at),
            metadata={
                "activated": decision.activated,
                "confidence": belief.confidence,
                "evidence_count": len(belief.evidence),
                "reason_count": len(decision.reasons),
                "reason_codes": ",".join(decision.reasons),
            },
        )

    def _reasons(self, candidate: Belief) -> tuple[str, ...]:
        reasons: list[str] = []
        if candidate.status != "candidate":
            reasons.append("candidate_required")
        if candidate.source != AUTONOMOUS_SOURCE:
            reasons.append("autonomous_source_required")
        if not candidate.subject.startswith(_ALLOWED_SUBJECT_PREFIXES):
            reasons.append("subject_not_low_risk")
        if candidate.confidence < MINIMUM_CONFIDENCE:
            reasons.append("confidence_below_gate")
        event_ids, malformed = _event_ids(candidate.evidence)
        if malformed or len(event_ids) != len(candidate.evidence):
            reasons.append("event_evidence_required")
        if len(set(event_ids)) < MINIMUM_EVIDENCE:
            reasons.append("insufficient_independent_evidence")
        evidence_events = []
        for event_id in dict.fromkeys(event_ids):
            try:
                evidence_events.append(self._events.get(event_id))
            except WinterEventRepositoryError:
                reasons.append("missing_event_evidence")
                break
        if evidence_events:
            if any(event.kind not in _ALLOWED_EVIDENCE_KINDS for event in evidence_events):
                reasons.append("unsupported_event_kind")
            if any(event.subject != candidate.subject for event in evidence_events):
                reasons.append("evidence_subject_mismatch")
            if len({event.local_date for event in evidence_events}) < MINIMUM_DAYS:
                reasons.append("insufficient_distinct_days")
            supportive_outcomes = tuple(
                event
                for event in evidence_events
                if event.kind in _EVALUATIVE_EVIDENCE_KINDS
                and dict(event.metadata).get("supports_belief") is True
            )
            if not supportive_outcomes:
                reasons.append("supportive_outcome_required")
        active_same_subject = tuple(
            belief
            for belief in self._beliefs.list_active()
            if belief.subject == candidate.subject
        )
        if active_same_subject and candidate.supersedes not in {
            belief.id for belief in active_same_subject
        }:
            reasons.append("revision_required")
        return tuple(dict.fromkeys(reasons))


def _event_ids(evidence: tuple[str, ...]) -> tuple[list[str], bool]:
    prefix = "winter-event:"
    ids: list[str] = []
    malformed = False
    for item in evidence:
        if not item.startswith(prefix) or not item.removeprefix(prefix).strip():
            malformed = True
            continue
        ids.append(item.removeprefix(prefix).strip())
    return ids, malformed
