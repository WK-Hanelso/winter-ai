"""Shadow-only proposals for evidence-backed Winter beliefs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Protocol
from uuid import uuid4

from companion.outcome import SqliteOutcomeRepository
from companion.winter_events import (
    SqliteWinterEventRepository,
    WinterEventRepositoryError,
)

ALLOWED_BELIEF_PREFIXES = ("conversation:", "relationship:", "project:")


class BeliefReflectionError(ValueError):
    pass


class BeliefReflectionUnavailableError(BeliefReflectionError):
    pass


class BeliefReflectionRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class BeliefEvidenceExcerpt:
    event_id: str
    subject: str
    local_date: str
    winter_response: str
    user_followup: str
    outcome: str
    response_usefulness: str
    supports_belief: bool

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.event_id,
                self.subject,
                self.local_date,
                self.winter_response,
                self.user_followup,
                self.outcome,
                self.response_usefulness,
            )
        ):
            raise BeliefReflectionError("belief evidence excerpt fields are required")


@dataclass(frozen=True)
class BeliefReflectionRequest:
    window_id: str
    evidence: tuple[BeliefEvidenceExcerpt, ...]

    def __post_init__(self) -> None:
        if not self.window_id.strip() or not self.evidence:
            raise BeliefReflectionError("belief reflection window and evidence are required")
        ids = [item.event_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise BeliefReflectionError("belief reflection evidence ids must be unique")


@dataclass(frozen=True)
class BeliefProposal:
    subject: str
    stance: str
    rationale: str
    confidence: float
    evidence_event_ids: tuple[str, ...]
    counterevidence_event_ids: tuple[str, ...] = ()
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.subject.strip() or not self.stance.strip() or not self.rationale.strip():
            raise BeliefReflectionError(
                "belief proposal subject, stance and rationale are required"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise BeliefReflectionError("belief proposal confidence must be between 0 and 1")
        if not self.evidence_event_ids:
            raise BeliefReflectionError("belief proposal requires event evidence")
        combined = self.evidence_event_ids + self.counterevidence_event_ids
        if any(not item.strip() for item in combined) or len(combined) != len(set(combined)):
            raise BeliefReflectionError("belief proposal event ids must be non-empty and unique")


@dataclass(frozen=True)
class BeliefSafetyReview:
    winter_owned: bool
    low_risk: bool
    evidence_entails_stance: bool
    no_user_fact_claim: bool
    no_identity_change: bool
    no_capability_claim: bool
    no_dependency_or_coercion: bool
    counterevidence_considered: bool
    accept: bool


class BeliefProposalInterpreter(Protocol):
    def propose(self, request: BeliefReflectionRequest) -> BeliefProposal | None: ...


class BeliefSafetyReviewer(Protocol):
    def review(
        self,
        request: BeliefReflectionRequest,
        proposal: BeliefProposal,
    ) -> BeliefSafetyReview: ...


@dataclass(frozen=True)
class BeliefProposalObservation:
    id: str
    window_id: str
    proposal: BeliefProposal | None
    safe: bool
    reason_codes: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class BeliefEvidenceWindowBuildResult:
    windows: tuple[BeliefReflectionRequest, ...]
    completed_outcomes: int
    linked_outcomes: int
    unlinked_outcomes: int
    excluded_outcomes: int


@dataclass(frozen=True)
class BeliefReflectionBatchResult:
    windows: int
    deferred: int
    analyzed: int
    already_analyzed: int
    safe: int
    blocked: int
    no_proposal: int
    completed_outcomes: int
    linked_outcomes: int
    unlinked_outcomes: int
    excluded_outcomes: int


class BeliefEvidenceWindowBuilder:
    """Build ephemeral model input from linked stores without copying dialogue."""

    _SCHEMA_VERSION = "belief-evidence-window-v1"

    def __init__(
        self,
        outcomes: SqliteOutcomeRepository,
        events: SqliteWinterEventRepository,
    ) -> None:
        self._outcomes = outcomes
        self._events = events

    def build(self, *, max_events_per_subject: int = 12) -> BeliefEvidenceWindowBuildResult:
        if max_events_per_subject < 1:
            raise BeliefReflectionError("belief evidence window limit must be positive")
        completed = self._outcomes.list(status="completed")
        grouped: dict[str, list[tuple[str, BeliefEvidenceExcerpt]]] = {}
        linked = 0
        unlinked = 0
        excluded = 0
        for outcome_event in completed:
            assessment = outcome_event.assessment
            observation = self._events.find_by_source_ref(outcome_event.id)
            if observation is None or observation.kind == "outcome_observation_unlinked":
                unlinked += 1
                continue
            if (
                assessment is None
                or observation.kind != "outcome_observation"
                or not observation.subject.startswith(ALLOWED_BELIEF_PREFIXES)
            ):
                excluded += 1
                continue
            metadata = dict(observation.metadata)
            expected_support = (
                assessment.outcome in {"accepted", "continued"}
                and assessment.response_usefulness == "helpful"
            )
            if metadata.get("supports_belief") is not expected_support:
                excluded += 1
                continue
            linked += 1
            excerpt = BeliefEvidenceExcerpt(
                event_id=observation.id,
                subject=observation.subject,
                local_date=observation.local_date,
                winter_response=outcome_event.assistant_response,
                user_followup=outcome_event.next_user_text,
                outcome=assessment.outcome,
                response_usefulness=assessment.response_usefulness,
                supports_belief=expected_support,
            )
            grouped.setdefault(observation.subject, []).append(
                (observation.occurred_at, excerpt)
            )
        windows = tuple(
            _build_window(
                subject,
                tuple(
                    excerpt
                    for _, excerpt in sorted(rows, key=lambda item: item[0])[
                        -max_events_per_subject:
                    ]
                ),
                self._SCHEMA_VERSION,
            )
            for subject, rows in sorted(grouped.items())
        )
        return BeliefEvidenceWindowBuildResult(
            windows=windows,
            completed_outcomes=len(completed),
            linked_outcomes=linked,
            unlinked_outcomes=unlinked,
            excluded_outcomes=excluded,
        )


class SqliteBeliefProposalRepository:
    """Stores conclusions and reason codes, never the conversation excerpts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialize()

    def record(
        self,
        *,
        window_id: str,
        proposal: BeliefProposal | None,
        safe: bool,
        reason_codes: tuple[str, ...],
    ) -> BeliefProposalObservation:
        observation = BeliefProposalObservation(
            id=str(uuid4()),
            window_id=window_id.strip(),
            proposal=proposal,
            safe=safe,
            reason_codes=tuple(reason_codes),
            created_at=datetime.now(UTC).isoformat(),
        )
        if not observation.window_id:
            raise BeliefReflectionRepositoryError("belief reflection window is required")
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO belief_proposal_observations "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    _observation_values(observation),
                )
                row = connection.execute(
                    "SELECT * FROM belief_proposal_observations WHERE window_id = ?",
                    (observation.window_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise BeliefReflectionRepositoryError(
                f"could not record belief proposal observation: {error}"
            ) from error
        if row is None:
            raise BeliefReflectionRepositoryError("belief proposal observation was not stored")
        stored = _observation_from_row(row)
        if _observation_identity(stored) != _observation_identity(observation):
            raise BeliefReflectionRepositoryError(
                "belief reflection window was reused with different evidence"
            )
        return stored

    def list(self) -> tuple[BeliefProposalObservation, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM belief_proposal_observations ORDER BY created_at, id"
                ).fetchall()
        except sqlite3.Error as error:
            raise BeliefReflectionRepositoryError(
                f"could not list belief proposal observations: {error}"
            ) from error
        return tuple(_observation_from_row(row) for row in rows)

    def get_by_window(self, window_id: str) -> BeliefProposalObservation | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM belief_proposal_observations WHERE window_id = ?",
                    (window_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise BeliefReflectionRepositoryError(
                f"could not read belief proposal observation: {error}"
            ) from error
        return _observation_from_row(row) if row is not None else None

    def _initialize(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS belief_proposal_observations (
                        id TEXT PRIMARY KEY,
                        window_id TEXT NOT NULL UNIQUE,
                        proposal_json TEXT,
                        safe INTEGER NOT NULL,
                        reason_codes_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
        except (OSError, sqlite3.Error) as error:
            raise BeliefReflectionRepositoryError(
                f"could not initialize belief proposal storage: {error}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


class ShadowBeliefReflectionService:
    """Observe model proposals without creating or activating a Belief."""

    def __init__(
        self,
        interpreter: BeliefProposalInterpreter,
        reviewer: BeliefSafetyReviewer,
        observations: SqliteBeliefProposalRepository,
        events: SqliteWinterEventRepository,
    ) -> None:
        self._interpreter = interpreter
        self._reviewer = reviewer
        self._observations = observations
        self._events = events

    def analyze(self, request: BeliefReflectionRequest) -> BeliefProposalObservation:
        existing = self._observations.get_by_window(request.window_id)
        if existing is not None:
            return existing
        proposal = self._interpreter.propose(request)
        if proposal is None:
            return self._observations.record(
                window_id=request.window_id,
                proposal=None,
                safe=False,
                reason_codes=("no_proposal",),
            )
        reasons = list(self._structural_reasons(request, proposal))
        review = self._reviewer.review(request, proposal)
        reasons.extend(_review_reasons(review))
        reasons = list(dict.fromkeys(reasons))
        return self._observations.record(
            window_id=request.window_id,
            proposal=proposal,
            safe=not reasons,
            reason_codes=tuple(reasons),
        )

    def _structural_reasons(
        self,
        request: BeliefReflectionRequest,
        proposal: BeliefProposal,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if not proposal.subject.startswith(ALLOWED_BELIEF_PREFIXES):
            reasons.append("subject_not_low_risk")
        request_ids = {item.event_id for item in request.evidence}
        proposal_ids = set(
            proposal.evidence_event_ids + proposal.counterevidence_event_ids
        )
        if not proposal_ids <= request_ids:
            reasons.append("evidence_outside_window")
        for event_id in proposal_ids & request_ids:
            try:
                event = self._events.get(event_id)
            except WinterEventRepositoryError:
                reasons.append("missing_event_evidence")
                break
            if event.subject != proposal.subject:
                reasons.append("evidence_subject_mismatch")
                break
        if not any(
            item.supports_belief and item.event_id in proposal.evidence_event_ids
            for item in request.evidence
        ):
            reasons.append("supportive_outcome_required")
        return tuple(dict.fromkeys(reasons))


class ShadowBeliefReflectionBatchService:
    """Analyze new evidence windows while leaving every result in Shadow."""

    def __init__(
        self,
        builder: BeliefEvidenceWindowBuilder,
        reflection: ShadowBeliefReflectionService,
        observations: SqliteBeliefProposalRepository,
    ) -> None:
        self._builder = builder
        self._reflection = reflection
        self._observations = observations

    def run(
        self,
        *,
        max_events_per_subject: int = 12,
        limit: int = 10,
    ) -> BeliefReflectionBatchResult:
        if limit < 1:
            raise BeliefReflectionError("belief reflection batch limit must be positive")
        built = self._builder.build(max_events_per_subject=max_events_per_subject)
        analyzed = 0
        already_analyzed = 0
        safe = 0
        blocked = 0
        no_proposal = 0
        pending = []
        for window in built.windows:
            if self._observations.get_by_window(window.window_id) is None:
                pending.append(window)
            else:
                already_analyzed += 1
        selected = pending[:limit]
        for window in selected:
            analyzed += 1
            observation = self._reflection.analyze(window)
            if observation.safe:
                safe += 1
            elif observation.reason_codes == ("no_proposal",):
                no_proposal += 1
            else:
                blocked += 1
        return BeliefReflectionBatchResult(
            windows=len(built.windows),
            deferred=len(pending) - len(selected),
            analyzed=analyzed,
            already_analyzed=already_analyzed,
            safe=safe,
            blocked=blocked,
            no_proposal=no_proposal,
            completed_outcomes=built.completed_outcomes,
            linked_outcomes=built.linked_outcomes,
            unlinked_outcomes=built.unlinked_outcomes,
            excluded_outcomes=built.excluded_outcomes,
        )


def _review_reasons(review: BeliefSafetyReview) -> tuple[str, ...]:
    checks = (
        (review.winter_owned, "not_winter_owned"),
        (review.low_risk, "semantic_risk"),
        (review.evidence_entails_stance, "stance_not_entailed"),
        (review.no_user_fact_claim, "user_fact_claim"),
        (review.no_identity_change, "identity_change_claim"),
        (review.no_capability_claim, "capability_claim"),
        (review.no_dependency_or_coercion, "dependency_or_coercion"),
        (review.counterevidence_considered, "counterevidence_ignored"),
        (review.accept, "review_rejected"),
    )
    return tuple(reason for passed, reason in checks if not passed)


def _build_window(
    subject: str,
    evidence: tuple[BeliefEvidenceExcerpt, ...],
    schema_version: str,
) -> BeliefReflectionRequest:
    identity = json.dumps(
        {
            "schema": schema_version,
            "subject": subject,
            "event_ids": [item.event_id for item in evidence],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return BeliefReflectionRequest(
        window_id=f"{schema_version}:{digest}",
        evidence=evidence,
    )


def _observation_values(observation: BeliefProposalObservation) -> tuple[object, ...]:
    proposal_json = (
        json.dumps(asdict(observation.proposal), ensure_ascii=False, sort_keys=True)
        if observation.proposal is not None
        else None
    )
    return (
        observation.id,
        observation.window_id,
        proposal_json,
        int(observation.safe),
        json.dumps(observation.reason_codes, ensure_ascii=False),
        observation.created_at,
    )


def _observation_from_row(row: sqlite3.Row) -> BeliefProposalObservation:
    raw = dict(row)
    try:
        proposal_json = raw.pop("proposal_json")
        proposal_payload = json.loads(proposal_json) if proposal_json else None
        reasons = json.loads(raw.pop("reason_codes_json"))
    except (TypeError, json.JSONDecodeError) as error:
        raise BeliefReflectionRepositoryError(
            "stored belief proposal observation is invalid JSON"
        ) from error
    proposal = _proposal_from_payload(proposal_payload)
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise BeliefReflectionRepositoryError("stored belief proposal reasons are invalid")
    safe = raw.pop("safe")
    if safe not in (0, 1):
        raise BeliefReflectionRepositoryError("stored belief proposal safety is invalid")
    return BeliefProposalObservation(
        proposal=proposal,
        safe=bool(safe),
        reason_codes=tuple(reasons),
        **raw,
    )


def _observation_identity(observation: BeliefProposalObservation) -> tuple[object, ...]:
    return (
        observation.window_id,
        observation.proposal,
        observation.safe,
        observation.reason_codes,
    )


def _proposal_from_payload(payload: object) -> BeliefProposal | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise BeliefReflectionRepositoryError("stored belief proposal must be an object")
    try:
        return BeliefProposal(
            subject=payload["subject"],
            stance=payload["stance"],
            rationale=payload["rationale"],
            confidence=payload["confidence"],
            evidence_event_ids=tuple(payload["evidence_event_ids"]),
            counterevidence_event_ids=tuple(payload["counterevidence_event_ids"]),
            supersedes=payload["supersedes"],
        )
    except (KeyError, TypeError, BeliefReflectionError) as error:
        raise BeliefReflectionRepositoryError(
            "stored belief proposal fields are invalid"
        ) from error
