"""Auditable working documents between Cheonu, Winter and specialist Workers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

SCHEMA_VERSION = 2
CONTEXT_VISIBILITIES = frozenset({"worker_shareable", "winter_only"})
CAPABILITY_KINDS = frozenset(
    {
        "project.inspect",
        "project.modify",
        "deployment.execute",
        "account.read",
        "network.access",
        "unknown",
    }
)
RESOURCE_KINDS = frozenset(
    {
        "project",
        "account",
        "external_service",
        "private_conversation",
        "identity",
        "memory",
        "human_reference",
        "unknown",
    }
)
AUTHORIZATION_STATUSES = frozenset({"ready", "clarify", "blocked"})
WORKER_ASSESSMENTS = frozenset(
    {"supported", "partial", "unsupported", "needs_user"}
)
CHECK_STATUSES = frozenset({"pass", "fail", "not_run"})


class WorkOrchestrationError(ValueError):
    """Raised when a working document is incomplete, ungrounded or unsafe."""


class WorkOrchestrationUnavailableError(WorkOrchestrationError):
    """A transient failure of the selected local Winter Core Model."""


@dataclass(frozen=True)
class WorkContext:
    content: str
    visibility: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise WorkOrchestrationError("work context must not be empty")
        if self.visibility not in CONTEXT_VISIBILITIES:
            raise WorkOrchestrationError(
                f"unsupported work context visibility: {self.visibility}"
            )


@dataclass(frozen=True)
class WorkRequest:
    user_text: str
    context: tuple[WorkContext, ...] = ()

    def __post_init__(self) -> None:
        if not self.user_text.strip():
            raise WorkOrchestrationError("work request user_text must not be empty")

    @property
    def worker_context(self) -> tuple[str, ...]:
        return tuple(
            item.content
            for item in self.context
            if item.visibility == "worker_shareable"
        )

    @property
    def evidence_corpus(self) -> tuple[str, ...]:
        return (self.user_text,) + self.worker_context


@dataclass(frozen=True)
class GroundedNeed:
    kind: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class IntentProposal:
    schema_version: int
    objective: str
    desired_outcome: str
    capabilities: tuple[GroundedNeed, ...]
    resources: tuple[GroundedNeed, ...]
    definition_of_done: tuple[str, ...]
    uncertainties: tuple[str, ...]
    clarification_question: str | None

    @classmethod
    def from_dict(
        cls, payload: object, *, request: WorkRequest
    ) -> IntentProposal:
        data = _mapping(payload, "IntentProposal")
        _exact_fields(
            data,
            {
                "schema_version",
                "objective",
                "desired_outcome",
                "capabilities",
                "resources",
                "definition_of_done",
                "uncertainties",
                "clarification_question",
            },
            "IntentProposal",
        )
        _schema_version(data["schema_version"])
        capabilities = tuple(
            _grounded_need(
                item,
                request.evidence_corpus,
                CAPABILITY_KINDS,
                f"capabilities[{index}]",
            )
            for index, item in enumerate(
                _sequence(data["capabilities"], "capabilities")
            )
        )
        resources = tuple(
            _grounded_need(
                item,
                request.evidence_corpus,
                RESOURCE_KINDS,
                f"resources[{index}]",
            )
            for index, item in enumerate(_sequence(data["resources"], "resources"))
        )
        if not capabilities:
            raise WorkOrchestrationError("IntentProposal requires capabilities")
        if not resources:
            raise WorkOrchestrationError("IntentProposal requires resources")
        definition_of_done = _strings(
            data["definition_of_done"], "definition_of_done"
        )
        if not definition_of_done:
            raise WorkOrchestrationError(
                "IntentProposal requires definition_of_done"
            )
        uncertainties = _strings(data["uncertainties"], "uncertainties")
        clarification = _optional_string(
            data["clarification_question"], "clarification_question"
        )
        if uncertainties and clarification is None:
            clarification = (
                f"{uncertainties[0]} 무엇을 기준으로 할지 조금 더 말해줄래?"
            )
        return cls(
            schema_version=SCHEMA_VERSION,
            objective=_string(data["objective"], "objective"),
            desired_outcome=_string(data["desired_outcome"], "desired_outcome"),
            capabilities=capabilities,
            resources=resources,
            definition_of_done=definition_of_done,
            uncertainties=uncertainties,
            clarification_question=clarification,
        )

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True)
class WorkPolicy:
    allowed_capabilities: frozenset[str] = frozenset({"project.inspect"})
    allowed_resources: frozenset[str] = frozenset({"project"})


@dataclass(frozen=True)
class WorkAuthorization:
    status: str
    reasons: tuple[str, ...]
    clarification_question: str | None

    def __post_init__(self) -> None:
        if self.status not in AUTHORIZATION_STATUSES:
            raise WorkOrchestrationError(
                f"unsupported authorization status: {self.status}"
            )


@dataclass(frozen=True)
class IntentBrief:
    schema_version: int
    objective: str
    desired_outcome: str
    capabilities: tuple[str, ...]
    resources: tuple[str, ...]
    definition_of_done: tuple[str, ...]
    uncertainties: tuple[str, ...]
    worker_context: tuple[str, ...]
    authorization: WorkAuthorization

    @property
    def ready_for_worker(self) -> bool:
        return self.authorization.status == "ready"

    @property
    def clarification_question(self) -> str | None:
        return self.authorization.clarification_question

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


def authorize_intent(
    proposal: IntentProposal,
    request: WorkRequest,
    policy: WorkPolicy | None = None,
) -> IntentBrief:
    active_policy = policy or WorkPolicy()
    capabilities = tuple(dict.fromkeys(need.kind for need in proposal.capabilities))
    resources = tuple(dict.fromkeys(need.kind for need in proposal.resources))
    denied_capabilities = tuple(
        kind
        for kind in capabilities
        if kind not in active_policy.allowed_capabilities
    )
    denied_resources = tuple(
        kind for kind in resources if kind not in active_policy.allowed_resources
    )
    if denied_capabilities or denied_resources:
        reasons = tuple(
            [f"capability_not_allowed:{kind}" for kind in denied_capabilities]
            + [f"resource_not_allowed:{kind}" for kind in denied_resources]
        )
        authorization = WorkAuthorization("blocked", reasons, None)
    elif proposal.uncertainties or proposal.clarification_question is not None:
        authorization = WorkAuthorization(
            "clarify",
            proposal.uncertainties or ("model_requested_clarification",),
            proposal.clarification_question,
        )
    else:
        authorization = WorkAuthorization("ready", (), None)
    return IntentBrief(
        schema_version=SCHEMA_VERSION,
        objective=proposal.objective,
        desired_outcome=proposal.desired_outcome,
        capabilities=capabilities,
        resources=resources,
        definition_of_done=proposal.definition_of_done,
        uncertainties=proposal.uncertainties,
        worker_context=request.worker_context,
        authorization=authorization,
    )


@dataclass(frozen=True)
class WorkerTaskPacket:
    schema_version: int
    objective: str
    desired_outcome: str
    capabilities: tuple[str, ...]
    resources: tuple[str, ...]
    context: tuple[str, ...]
    definition_of_done: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    required_report: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> WorkerTaskPacket:
        data = _mapping(payload, "WorkerTaskPacket")
        _exact_fields(
            data,
            {
                "schema_version",
                "objective",
                "desired_outcome",
                "capabilities",
                "resources",
                "context",
                "definition_of_done",
                "prohibited_actions",
                "required_report",
            },
            "WorkerTaskPacket",
        )
        _schema_version(data["schema_version"])
        capabilities = _strings(data["capabilities"], "capabilities")
        resources = _strings(data["resources"], "resources")
        definition_of_done = _strings(
            data["definition_of_done"], "definition_of_done"
        )
        if not capabilities or not resources or not definition_of_done:
            raise WorkOrchestrationError(
                "WorkerTaskPacket requires capabilities, resources and definition_of_done"
            )
        return cls(
            schema_version=SCHEMA_VERSION,
            objective=_string(data["objective"], "objective"),
            desired_outcome=_string(data["desired_outcome"], "desired_outcome"),
            capabilities=capabilities,
            resources=resources,
            context=_strings(data["context"], "context"),
            definition_of_done=definition_of_done,
            prohibited_actions=_strings(
                data["prohibited_actions"], "prohibited_actions"
            ),
            required_report=_strings(data["required_report"], "required_report"),
        )

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


_PRIVATE_BOUNDARIES = (
    "Identity, Memory, Conversation, account credential, Human Reference와 음성을 "
    "읽거나 요청하지 않는다.",
    "허용된 project text 밖의 경로와 네트워크를 사용하지 않는다.",
)
_REQUIRED_REPORT = (
    "summary",
    "evidence with stable ids",
    "claims referencing evidence ids",
    "actions_performed",
    "checks with pass/fail/not_run",
    "limitations",
    "unresolved",
)


def build_worker_task(brief: IntentBrief) -> WorkerTaskPacket:
    if not brief.ready_for_worker:
        raise WorkOrchestrationError("IntentBrief is not authorized for a Worker")
    return WorkerTaskPacket(
        schema_version=SCHEMA_VERSION,
        objective=brief.objective,
        desired_outcome=brief.desired_outcome,
        capabilities=brief.capabilities,
        resources=brief.resources,
        context=brief.worker_context,
        definition_of_done=brief.definition_of_done,
        prohibited_actions=_PRIVATE_BOUNDARIES,
        required_report=_REQUIRED_REPORT,
    )


@dataclass(frozen=True)
class ReportEvidence:
    evidence_id: str
    locator: str


@dataclass(frozen=True)
class ReportClaim:
    content: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReportCheck:
    name: str
    status: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceReport:
    schema_version: int
    summary: str
    evidence: tuple[ReportEvidence, ...]
    claims: tuple[ReportClaim, ...]
    actions_performed: tuple[str, ...]
    checks: tuple[ReportCheck, ...]
    limitations: tuple[str, ...]
    unresolved: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> EvidenceReport:
        data = _mapping(payload, "EvidenceReport")
        _exact_fields(
            data,
            {
                "schema_version",
                "summary",
                "evidence",
                "claims",
                "actions_performed",
                "checks",
                "limitations",
                "unresolved",
            },
            "EvidenceReport",
        )
        _schema_version(data["schema_version"])
        evidence = tuple(
            _report_evidence(item, index)
            for index, item in enumerate(_sequence(data["evidence"], "evidence"))
        )
        evidence_ids = tuple(item.evidence_id for item in evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise WorkOrchestrationError("EvidenceReport evidence ids must be unique")
        known = frozenset(evidence_ids)
        claims = tuple(
            _report_claim(item, index, known)
            for index, item in enumerate(_sequence(data["claims"], "claims"))
        )
        checks = tuple(
            _report_check(item, index, known)
            for index, item in enumerate(_sequence(data["checks"], "checks"))
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            summary=_string(data["summary"], "summary"),
            evidence=evidence,
            claims=claims,
            actions_performed=_strings(
                data["actions_performed"], "actions_performed"
            ),
            checks=checks,
            limitations=_strings(data["limitations"], "limitations"),
            unresolved=_strings(data["unresolved"], "unresolved"),
        )

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.evidence)

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True)
class ReviewProposal:
    schema_version: int
    worker_assessment: str
    goal_coverage: tuple[str, ...]
    judgment: str
    evidence_ids: tuple[str, ...]
    unresolved: tuple[str, ...]
    message: str

    @classmethod
    def from_dict(
        cls, payload: object, *, report: EvidenceReport
    ) -> ReviewProposal:
        data = _mapping(payload, "ReviewProposal")
        _exact_fields(
            data,
            {
                "schema_version",
                "worker_assessment",
                "goal_coverage",
                "judgment",
                "evidence_ids",
                "unresolved",
                "message",
            },
            "ReviewProposal",
        )
        _schema_version(data["schema_version"])
        assessment = _string(data["worker_assessment"], "worker_assessment")
        if assessment not in WORKER_ASSESSMENTS:
            raise WorkOrchestrationError(
                f"unsupported worker_assessment: {assessment}"
            )
        evidence_ids = _strings(data["evidence_ids"], "evidence_ids")
        unknown = set(evidence_ids) - report.evidence_ids
        if unknown:
            raise WorkOrchestrationError(
                f"ReviewProposal contains unknown evidence ids: {sorted(unknown)}"
            )
        coverage = _strings(data["goal_coverage"], "goal_coverage")
        if not coverage:
            raise WorkOrchestrationError("ReviewProposal requires goal_coverage")
        return cls(
            schema_version=SCHEMA_VERSION,
            worker_assessment=assessment,
            goal_coverage=coverage,
            judgment=_string(data["judgment"], "judgment"),
            evidence_ids=evidence_ids,
            unresolved=_strings(data["unresolved"], "unresolved"),
            message=_string(data["message"], "message"),
        )


@dataclass(frozen=True)
class AnswerAction:
    response: str
    type: Literal["answer"] = field(init=False, default="answer")


@dataclass(frozen=True)
class RetryAction:
    task: str
    type: Literal["retry"] = field(init=False, default="retry")


@dataclass(frozen=True)
class ClarifyAction:
    question: str
    type: Literal["clarify"] = field(init=False, default="clarify")


DecisionAction = AnswerAction | RetryAction | ClarifyAction


@dataclass(frozen=True)
class WinterDecision:
    schema_version: int
    worker_assessment: str
    goal_coverage: tuple[str, ...]
    judgment: str
    evidence_ids: tuple[str, ...]
    unresolved: tuple[str, ...]
    action: DecisionAction

    @classmethod
    def from_review(
        cls,
        proposal: ReviewProposal,
        *,
        brief: IntentBrief,
        report: EvidenceReport,
    ) -> WinterDecision:
        if not brief.ready_for_worker:
            raise WorkOrchestrationError(
                "WinterDecision cannot review an unauthorized brief"
            )
        failed_or_missing_check = any(
            check.status != "pass" for check in report.checks
        )
        if proposal.worker_assessment == "needs_user":
            action: DecisionAction = ClarifyAction(proposal.message)
        elif (
            proposal.worker_assessment != "supported"
            or proposal.unresolved
            or report.unresolved
            or report.limitations
            or failed_or_missing_check
        ):
            retry_reasons = tuple(
                f"{check.name}: {check.status}"
                for check in report.checks
                if check.status != "pass"
            ) + report.limitations + report.unresolved + proposal.unresolved
            if not retry_reasons:
                retry_reasons = (proposal.message,)
            action = RetryAction(
                "다음 Worker 작업: " + "; ".join(dict.fromkeys(retry_reasons))
            )
        else:
            action = AnswerAction(proposal.message)
        return cls(
            schema_version=SCHEMA_VERSION,
            worker_assessment=proposal.worker_assessment,
            goal_coverage=proposal.goal_coverage,
            judgment=proposal.judgment,
            evidence_ids=proposal.evidence_ids,
            unresolved=proposal.unresolved,
            action=action,
        )

    @property
    def next_action(self) -> str:
        return self.action.type

    @property
    def follow_up_task(self) -> str | None:
        if isinstance(self.action, RetryAction):
            return self.action.task
        return None

    @property
    def final_response(self) -> str | None:
        if isinstance(self.action, AnswerAction):
            return self.action.response
        if isinstance(self.action, ClarifyAction):
            return self.action.question
        return None

    @property
    def evidence_used(self) -> tuple[str, ...]:
        return self.evidence_ids

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


def _grounded_need(
    value: object,
    corpus: tuple[str, ...],
    allowed: frozenset[str],
    name: str,
) -> GroundedNeed:
    data = _mapping(value, name)
    _exact_fields(data, {"kind", "evidence"}, name)
    kind = _string(data["kind"], f"{name}.kind")
    if kind not in allowed:
        raise WorkOrchestrationError(f"unsupported {name} kind: {kind}")
    evidence = _strings(data["evidence"], f"{name}.evidence")
    if not evidence:
        raise WorkOrchestrationError(f"{name} requires evidence")
    for quote in evidence:
        if not any(quote in text for text in corpus):
            raise WorkOrchestrationError(
                f"{name} evidence must be an exact shareable substring: {quote!r}"
            )
    return GroundedNeed(kind, evidence)


def _report_evidence(value: object, index: int) -> ReportEvidence:
    name = f"evidence[{index}]"
    data = _mapping(value, name)
    _exact_fields(data, {"evidence_id", "locator"}, name)
    return ReportEvidence(
        _string(data["evidence_id"], f"{name}.evidence_id"),
        _string(data["locator"], f"{name}.locator"),
    )


def _report_claim(
    value: object, index: int, known_evidence: frozenset[str]
) -> ReportClaim:
    name = f"claims[{index}]"
    data = _mapping(value, name)
    _exact_fields(data, {"content", "evidence_ids"}, name)
    evidence_ids = _known_evidence_ids(data["evidence_ids"], name, known_evidence)
    if not evidence_ids:
        raise WorkOrchestrationError(f"{name} requires evidence_ids")
    return ReportClaim(_string(data["content"], f"{name}.content"), evidence_ids)


def _report_check(
    value: object, index: int, known_evidence: frozenset[str]
) -> ReportCheck:
    name = f"checks[{index}]"
    data = _mapping(value, name)
    _exact_fields(data, {"name", "status", "evidence_ids"}, name)
    status = _string(data["status"], f"{name}.status")
    if status not in CHECK_STATUSES:
        raise WorkOrchestrationError(f"unsupported check status: {status}")
    return ReportCheck(
        _string(data["name"], f"{name}.name"),
        status,
        _known_evidence_ids(data["evidence_ids"], name, known_evidence),
    )


def _known_evidence_ids(
    value: object, name: str, known: frozenset[str]
) -> tuple[str, ...]:
    evidence_ids = _strings(value, f"{name}.evidence_ids")
    unknown = set(evidence_ids) - known
    if unknown:
        raise WorkOrchestrationError(
            f"{name} contains unknown evidence ids: {sorted(unknown)}"
        )
    return evidence_ids


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkOrchestrationError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def _exact_fields(data: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise WorkOrchestrationError(
            f"{name} fields do not match schema; missing={missing}, extra={extra}"
        )


def _sequence(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise WorkOrchestrationError(f"{name} must be an array")
    return cast(list[object], value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkOrchestrationError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


def _strings(value: object, name: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{name}[{index}]")
        for index, item in enumerate(_sequence(value, name))
    )


def _schema_version(value: object) -> None:
    if value != SCHEMA_VERSION:
        raise WorkOrchestrationError(
            f"unsupported work orchestration schema_version: {value}"
        )
