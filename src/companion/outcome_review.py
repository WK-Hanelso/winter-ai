"""Blind human review and conservative readiness summaries for Shadow outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TextIO

from companion.outcome import (
    OutcomeAssessment,
    OutcomeReview,
    ReviewedMemoryRelation,
    ShadowOutcomeEvent,
    SqliteOutcomeRepository,
)

AUDIT_TARGET = 30

_OUTCOME_CHOICES = (
    ("accepted", "맞다 — 겨울이 답이 원하는 방향이었다"),
    ("corrected", "바로잡았다 — 내가 원한 방향을 다시 설명했다"),
    ("rejected", "거절했다 — 답이 틀렸거나 도움이 안 됐다고 했다"),
    ("continued", "이어갔다 — 같은 일을 다음 단계로 진행했다"),
    ("abandoned", "그만뒀다 — 작업이나 주제를 중단했다"),
    ("unclear", "판단하기 어렵다"),
)
_INTENT_CHOICES = (
    ("confirmed", "잘 이해했다"),
    ("contradicted", "잘못 이해했다"),
    ("partial", "일부만 이해했다"),
    ("unclear", "판단하기 어렵다"),
)
_USEFULNESS_CHOICES = (
    ("helpful", "도움이 됐다"),
    ("unhelpful", "도움이 안 됐다"),
    ("mixed", "일부만 도움이 됐다"),
    ("unclear", "판단하기 어렵다"),
)
_AFFECT_CHOICES = (
    ("improved", "기분이나 상태가 나아졌다"),
    ("worsened", "더 나빠졌다"),
    ("unchanged", "달라지지 않았다"),
    ("unclear", "대화만으로 판단하기 어렵다"),
)
_MEMORY_CHOICES = (
    ("confirmed", "그 기억이 맞다고 확인했다"),
    ("contradicted", "그 기억이 틀렸다고 바로잡았다"),
    ("none", "그 기억에 관해 말하지 않았다"),
    ("unclear", "판단하기 어렵다"),
)
_IMPROVEMENT_CHOICES = (
    ("confirmed", "고쳐야 할 문제가 드러났다"),
    ("none", "고쳐야 할 문제는 드러나지 않았다"),
    ("unclear", "판단하기 어렵다"),
)
_OUTCOME_LABELS = dict(_OUTCOME_CHOICES)
_INTENT_LABELS = dict(_INTENT_CHOICES)
_USEFULNESS_LABELS = dict(_USEFULNESS_CHOICES)
_AFFECT_LABELS = dict(_AFFECT_CHOICES)
_MEMORY_LABELS = dict(_MEMORY_CHOICES)
_IMPROVEMENT_LABELS = dict(_IMPROVEMENT_CHOICES)


@dataclass(frozen=True)
class FieldAgreement:
    matched: int
    total: int


@dataclass(frozen=True)
class DetectionScore:
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float | None:
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else None

    @property
    def recall(self) -> float | None:
        actual = self.true_positive + self.false_negative
        return self.true_positive / actual if actual else None


@dataclass(frozen=True)
class OutcomeAudit:
    reviewed: int
    target: int
    outcome: FieldAgreement
    intent_match: FieldAgreement
    response_usefulness: FieldAgreement
    affect_shift: FieldAgreement
    memory_detection: DetectionScore
    improvement_detection: DetectionScore


class _ReviewControl(Exception):
    def __init__(self, action: str) -> None:
        super().__init__(action)
        self.action = action


def run_blind_review(
    repository: SqliteOutcomeRepository,
    *,
    reviewer: str,
    limit: int,
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    events = repository.list_reviewable(reviewer=reviewer, limit=limit)
    if not events:
        print("지금 검토할 대화가 없습니다.", file=stdout)
        print(
            "일상 대화를 더 한 뒤 분석을 실행하면 새 검토 항목이 생깁니다.",
            file=stdout,
        )
        print("  ./winter chat --analyze-pending-turns", file=stdout)
        print("  ./winter chat --analyze-pending-outcomes", file=stdout)
        print("  ./winter chat --review-outcomes", file=stdout)
        return 0
    print(
        f"검토할 대화 {len(events)}개입니다. 숫자로 고르고, s는 넘기기, q는 종료입니다.",
        file=stdout,
    )
    saved = 0
    for index, event in enumerate(events, start=1):
        print("\n" + "=" * 64, file=stdout)
        print(f"대화 {index}/{len(events)}", file=stdout)
        print(f"천우의 질문: {event.prior_user_text}", file=stdout)
        print(f"겨울이의 답: {event.assistant_response}", file=stdout)
        print(f"천우의 다음 반응: {event.next_user_text}", file=stdout)
        if event.prior_memory_claims:
            print("이 답에 사용된 기억:", file=stdout)
            for claim in event.prior_memory_claims:
                print(f"- {claim.content}", file=stdout)
        try:
            review = _collect_review(
                event,
                reviewer=reviewer,
                stdin=stdin,
                stdout=stdout,
            )
        except _ReviewControl as control:
            if control.action == "quit":
                break
            print("이 대화는 넘겼습니다.", file=stdout)
            continue
        saved_review = repository.add_review(
            event.id,
            reviewer=reviewer,
            outcome=review.outcome,
            intent_match=review.intent_match,
            response_usefulness=review.response_usefulness,
            affect_shift=review.affect_shift,
            memory_relations=review.memory_relations,
            improvement_confirmation=review.improvement_confirmation,
        )
        saved += 1
        _print_comparison(event, saved_review, stdout)
    total = len(repository.list_reviews(reviewer=reviewer))
    print(
        f"\n이번에 {saved}개 저장했습니다. "
        f"누적 검토는 {total}/{AUDIT_TARGET}개입니다.",
        file=stdout,
    )
    print("이 결과는 아직 겨울이의 답변이나 기억을 자동으로 바꾸지 않습니다.", file=stdout)
    return 0


def build_audit(
    repository: SqliteOutcomeRepository, *, reviewer: str
) -> OutcomeAudit:
    reviews = repository.list_reviews(reviewer=reviewer)
    field_matches = {
        "outcome": 0,
        "intent_match": 0,
        "response_usefulness": 0,
        "affect_shift": 0,
    }
    memory_counts = [0, 0, 0]
    improvement_counts = [0, 0, 0]
    for review in reviews:
        event = repository.get(review.event_id)
        assessment = event.assessment
        if assessment is None:
            continue
        for field in field_matches:
            if getattr(review, field) == getattr(assessment, field):
                field_matches[field] += 1
        _accumulate_memory_score(event, review, memory_counts)
        human_improvement = review.improvement_confirmation
        if human_improvement != "unclear":
            _accumulate_detection(
                predicted=bool(assessment.improvement_confirmation),
                actual=human_improvement == "confirmed",
                counts=improvement_counts,
            )
    total = len(reviews)
    return OutcomeAudit(
        reviewed=total,
        target=AUDIT_TARGET,
        outcome=FieldAgreement(field_matches["outcome"], total),
        intent_match=FieldAgreement(field_matches["intent_match"], total),
        response_usefulness=FieldAgreement(
            field_matches["response_usefulness"], total
        ),
        affect_shift=FieldAgreement(field_matches["affect_shift"], total),
        memory_detection=DetectionScore(*memory_counts),
        improvement_detection=DetectionScore(*improvement_counts),
    )


def print_audit(audit: OutcomeAudit, stdout: TextIO) -> None:
    print(f"실제 대화 검토: {audit.reviewed}/{audit.target}", file=stdout)
    if audit.reviewed < audit.target:
        remaining = audit.target - audit.reviewed
        print(
            f"아직 운영 반영을 판단하지 않습니다. "
            f"{remaining}개 검토가 더 필요합니다.",
            file=stdout,
        )
    else:
        print(
            "최소 검토 수를 채웠습니다. 수치를 보고 운영 반영 여부를 결정해야 합니다.",
            file=stdout,
        )
    print("\n겨울이 판단과 천우 판단의 일치:", file=stdout)
    _print_agreement("전체 반응", audit.outcome, stdout)
    _print_agreement("의도 이해", audit.intent_match, stdout)
    _print_agreement("도움 여부", audit.response_usefulness, stdout)
    _print_agreement("감정 변화", audit.affect_shift, stdout)
    print("\n중요 신호 탐지:", file=stdout)
    _print_detection("기억 확인·반박", audit.memory_detection, stdout)
    _print_detection("고쳐야 할 문제", audit.improvement_detection, stdout)
    print("\n이 보고서는 관찰용이며 기억·성격·답변 정책을 자동 변경하지 않습니다.", file=stdout)


def _collect_review(
    event: ShadowOutcomeEvent,
    *,
    reviewer: str,
    stdin: TextIO,
    stdout: TextIO,
) -> OutcomeReview:
    outcome = _ask_choice("1. 다음 반응의 의미는?", _OUTCOME_CHOICES, stdin, stdout)
    intent = _ask_choice("2. 겨울이가 의도를 이해했나?", _INTENT_CHOICES, stdin, stdout)
    usefulness = _ask_choice("3. 겨울이 답이 도움이 됐나?", _USEFULNESS_CHOICES, stdin, stdout)
    affect = _ask_choice("4. 천우의 기분이나 상태가 달라졌나?", _AFFECT_CHOICES, stdin, stdout)
    relations = tuple(
        ReviewedMemoryRelation(
            claim.id,
            _ask_choice(
                f"5. 사용된 기억 판단: {claim.content}",
                _MEMORY_CHOICES,
                stdin,
                stdout,
            ),
        )
        for claim in event.prior_memory_claims
    )
    improvement = _ask_choice(
        "6. 이 대화에서 겨울이가 고쳐야 할 문제가 드러났나?",
        _IMPROVEMENT_CHOICES,
        stdin,
        stdout,
    )
    return OutcomeReview(
        id="pending",
        event_id=event.id,
        reviewer=reviewer,
        outcome=outcome,
        intent_match=intent,
        response_usefulness=usefulness,
        affect_shift=affect,
        memory_relations=relations,
        improvement_confirmation=improvement,
        created_at="pending",
    )


def _ask_choice(
    prompt: str,
    choices: tuple[tuple[str, str], ...],
    stdin: TextIO,
    stdout: TextIO,
) -> str:
    print(f"\n{prompt}", file=stdout)
    for index, (_, label) in enumerate(choices, start=1):
        print(f"  {index}. {label}", file=stdout)
    while True:
        print("선택> ", end="", file=stdout, flush=True)
        answer = stdin.readline()
        if not answer:
            raise _ReviewControl("quit")
        answer = answer.strip().lower()
        if answer == "q":
            raise _ReviewControl("quit")
        if answer == "s":
            raise _ReviewControl("skip")
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1][0]
        print(f"1~{len(choices)} 중 하나나 s, q를 입력해 주세요.", file=stdout)


def _print_comparison(
    event: ShadowOutcomeEvent, review: OutcomeReview, stdout: TextIO
) -> None:
    assessment = event.assessment
    if assessment is None:
        return
    print("\n저장했습니다. 이제 겨울이의 사후 판단을 공개합니다.", file=stdout)
    comparisons = (
        ("전체 반응", review.outcome, assessment.outcome, _OUTCOME_LABELS),
        ("의도 이해", review.intent_match, assessment.intent_match, _INTENT_LABELS),
        (
            "도움 여부",
            review.response_usefulness,
            assessment.response_usefulness,
            _USEFULNESS_LABELS,
        ),
        ("감정 변화", review.affect_shift, assessment.affect_shift, _AFFECT_LABELS),
    )
    for label, human, model, labels in comparisons:
        result = "일치" if human == model else "다름"
        print(
            f"- {label}: 천우={labels[human]}, 겨울이={labels[model]} → {result}",
            file=stdout,
        )
    model_memory = _model_memory_relations(event, assessment)
    for relation in review.memory_relations:
        model = model_memory[relation.claim_id]
        result = "일치" if relation.relation == model else "다름"
        print(
            f"- 기억 판단: 천우={_MEMORY_LABELS[relation.relation]}, "
            f"겨울이={_MEMORY_LABELS[model]} → {result}",
            file=stdout,
        )
    human_improvement = review.improvement_confirmation
    model_improvement = "confirmed" if assessment.improvement_confirmation else "none"
    result = "일치" if human_improvement == model_improvement else "다름"
    print(
        f"- 개선 필요: 천우={_IMPROVEMENT_LABELS[human_improvement]}, "
        f"겨울이={_IMPROVEMENT_LABELS[model_improvement]} → {result}",
        file=stdout,
    )


def _model_memory_relations(
    event: ShadowOutcomeEvent, assessment: OutcomeAssessment
) -> dict[str, str]:
    confirmed = {signal.content for signal in assessment.memory_confirmation}
    contradicted = {signal.content for signal in assessment.memory_contradiction}
    return {
        claim.id: (
            "confirmed"
            if claim.content in confirmed
            else "contradicted"
            if claim.content in contradicted
            else "none"
        )
        for claim in event.prior_memory_claims
    }


def _accumulate_memory_score(
    event: ShadowOutcomeEvent, review: OutcomeReview, counts: list[int]
) -> None:
    assessment = event.assessment
    if assessment is None:
        return
    predicted = _model_memory_relations(event, assessment)
    for relation in review.memory_relations:
        if relation.relation == "unclear":
            continue
        predicted_relation = predicted[relation.claim_id]
        positive = {"confirmed", "contradicted"}
        if predicted_relation in positive:
            if predicted_relation == relation.relation:
                counts[0] += 1
            else:
                counts[1] += 1
        if relation.relation in positive and predicted_relation != relation.relation:
            counts[2] += 1


def _accumulate_detection(*, predicted: bool, actual: bool, counts: list[int]) -> None:
    if predicted and actual:
        counts[0] += 1
    elif predicted:
        counts[1] += 1
    elif actual:
        counts[2] += 1


def _print_agreement(label: str, agreement: FieldAgreement, stdout: TextIO) -> None:
    if agreement.total == 0:
        print(f"- {label}: 아직 데이터 없음", file=stdout)
        return
    percentage = agreement.matched / agreement.total * 100
    print(
        f"- {label}: {agreement.matched}/{agreement.total} ({percentage:.1f}%)",
        file=stdout,
    )


def _print_detection(label: str, score: DetectionScore, stdout: TextIO) -> None:
    precision = "판단 불가" if score.precision is None else f"{score.precision * 100:.1f}%"
    recall = "판단 불가" if score.recall is None else f"{score.recall * 100:.1f}%"
    print(
        f"- {label}: 정확도={precision}, 놓치지 않은 비율={recall} "
        f"(잘 찾음 {score.true_positive}, 잘못 감지 {score.false_positive}, "
        f"놓침 {score.false_negative})",
        file=stdout,
    )
