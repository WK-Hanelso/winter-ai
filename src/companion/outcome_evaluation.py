"""Synthetic held-out evaluation for non-operative Outcome assessments."""

from __future__ import annotations

from dataclasses import dataclass
import json

from companion.outcome import (
    AFFECT_SHIFTS,
    INTENT_MATCHES,
    OUTCOMES,
    USEFULNESS,
    OutcomeAssessment,
    OutcomeInterpretationRequest,
    OutcomeMemoryClaim,
)
from companion.turn_understanding import TurnUnderstanding

_SIGNAL_FIELDS = frozenset(
    {"memory_confirmation", "memory_contradiction", "improvement_confirmation"}
)


@dataclass(frozen=True)
class OutcomeExpectation:
    allowed_outcomes: frozenset[str]
    allowed_intent_matches: frozenset[str] = frozenset()
    allowed_usefulness: frozenset[str] = frozenset()
    allowed_affect_shifts: frozenset[str] = frozenset()
    required_signals: frozenset[str] = frozenset()
    forbidden_signals: frozenset[str] = _SIGNAL_FIELDS
    tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _validate_values(self.allowed_outcomes, OUTCOMES, "allowed_outcomes")
        _validate_values(
            self.allowed_intent_matches,
            INTENT_MATCHES,
            "allowed_intent_matches",
        )
        _validate_values(
            self.allowed_usefulness, USEFULNESS, "allowed_usefulness"
        )
        _validate_values(
            self.allowed_affect_shifts,
            AFFECT_SHIFTS,
            "allowed_affect_shifts",
        )
        unknown_signals = (
            self.required_signals | self.forbidden_signals
        ) - _SIGNAL_FIELDS
        if unknown_signals:
            raise ValueError(f"unsupported signal fields: {sorted(unknown_signals)}")
        overlap = self.required_signals & self.forbidden_signals
        if overlap:
            raise ValueError(
                f"signals cannot be required and forbidden: {sorted(overlap)}"
            )


@dataclass(frozen=True)
class OutcomeHeldOutScene:
    label: str
    prior_user_text: str
    prior_need: str
    assistant_response: str
    next_user_text: str
    expectation: OutcomeExpectation
    memory_claims: tuple[OutcomeMemoryClaim, ...] = ()

    @property
    def request(self) -> OutcomeInterpretationRequest:
        return OutcomeInterpretationRequest(
            prior_user_text=self.prior_user_text,
            prior_understanding=_prior_understanding(
                self.prior_user_text, self.prior_need
            ),
            assistant_response=self.assistant_response,
            next_user_text=self.next_user_text,
            memory_claims=self.memory_claims,
        )


@dataclass(frozen=True)
class EvaluatedOutcome:
    scene: OutcomeHeldOutScene
    assessment: OutcomeAssessment | None
    error: str | None
    seconds: float
    repeat: int = 1


@dataclass(frozen=True)
class EvaluationCheck:
    scene: str
    repeat: int
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ActivationGate:
    name: str
    passed: bool
    detail: str


def evaluate(results: tuple[EvaluatedOutcome, ...]) -> tuple[EvaluationCheck, ...]:
    checks: list[EvaluationCheck] = []
    for result in results:
        assessment = result.assessment
        expectation = result.scene.expectation
        if assessment is None:
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    result.repeat,
                    "assessment completed",
                    False,
                    result.error or "no OutcomeAssessment returned",
                )
            )
            continue
        checks.append(
            EvaluationCheck(
                result.scene.label,
                result.repeat,
                "assessment completed",
                True,
                f"{result.seconds:.2f}s",
            )
        )
        checks.append(
            _enum_check(
                result,
                "outcome",
                assessment.outcome,
                expectation.allowed_outcomes,
            )
        )
        if expectation.allowed_intent_matches:
            checks.append(
                _enum_check(
                    result,
                    "intent match",
                    assessment.intent_match,
                    expectation.allowed_intent_matches,
                )
            )
        if expectation.allowed_usefulness:
            checks.append(
                _enum_check(
                    result,
                    "response usefulness",
                    assessment.response_usefulness,
                    expectation.allowed_usefulness,
                )
            )
        if expectation.allowed_affect_shifts:
            checks.append(
                _enum_check(
                    result,
                    "affect shift",
                    assessment.affect_shift,
                    expectation.allowed_affect_shifts,
                )
            )
        for field in sorted(expectation.required_signals):
            signals = getattr(assessment, field)
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    result.repeat,
                    f"required {field}",
                    bool(signals),
                    "; ".join(signal.content for signal in signals) or "<none>",
                )
            )
        for field in sorted(expectation.forbidden_signals):
            signals = getattr(assessment, field)
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    result.repeat,
                    f"forbidden {field}",
                    not signals,
                    "; ".join(signal.content for signal in signals) or "<none>",
                )
            )
    return tuple(checks)


def activation_gates(
    results: tuple[EvaluatedOutcome, ...],
) -> tuple[ActivationGate, ...]:
    total = len(results)
    completed = sum(result.assessment is not None for result in results)
    completion_rate = completed / total if total else 0.0

    correction_results = tuple(
        result
        for result in results
        if "correction" in result.scene.expectation.tags
    )
    correction_hits = sum(
        result.assessment is not None
        and result.assessment.intent_match == "contradicted"
        for result in correction_results
    )
    correction_recall = (
        correction_hits / len(correction_results) if correction_results else 0.0
    )

    ambiguous_results = tuple(
        result
        for result in results
        if "ambiguous" in result.scene.expectation.tags
    )
    false_acceptance = sum(
        result.assessment is not None and result.assessment.outcome == "accepted"
        for result in ambiguous_results
    )

    memory_predictions = tuple(
        result
        for result in results
        if result.assessment is not None
        and bool(result.assessment.memory_confirmation)
    )
    true_memory_predictions = sum(
        "memory_confirmation_positive" in result.scene.expectation.tags
        for result in memory_predictions
    )
    memory_precision = (
        true_memory_predictions / len(memory_predictions)
        if memory_predictions
        else 0.0
    )

    memory_signal_predictions: list[tuple[EvaluatedOutcome, str]] = []
    for result in results:
        if result.assessment is None:
            continue
        if result.assessment.memory_confirmation:
            memory_signal_predictions.append((result, "memory_confirmation"))
        if result.assessment.memory_contradiction:
            memory_signal_predictions.append((result, "memory_contradiction"))
    true_memory_signals = sum(
        f"{signal}_positive" in result.scene.expectation.tags
        for result, signal in memory_signal_predictions
    )
    memory_signal_precision = (
        true_memory_signals / len(memory_signal_predictions)
        if memory_signal_predictions
        else 0.0
    )
    expected_memory_signals = tuple(
        (result, signal)
        for result in results
        for signal in ("memory_confirmation", "memory_contradiction")
        if f"{signal}_positive" in result.scene.expectation.tags
    )
    recalled_memory_signals = sum(
        result.assessment is not None
        and bool(getattr(result.assessment, signal))
        for result, signal in expected_memory_signals
    )
    memory_signal_recall = (
        recalled_memory_signals / len(expected_memory_signals)
        if expected_memory_signals
        else 0.0
    )

    improvement_results = tuple(
        result
        for result in results
        if "improvement_confirmation_positive" in result.scene.expectation.tags
    )
    improvement_hits = sum(
        result.assessment is not None
        and bool(result.assessment.improvement_confirmation)
        for result in improvement_results
    )
    improvement_recall = (
        improvement_hits / len(improvement_results)
        if improvement_results
        else 0.0
    )
    improvement_predictions = tuple(
        result
        for result in results
        if result.assessment is not None
        and bool(result.assessment.improvement_confirmation)
    )
    true_improvement_predictions = sum(
        "improvement_confirmation_positive" in result.scene.expectation.tags
        for result in improvement_predictions
    )
    improvement_precision = (
        true_improvement_predictions / len(improvement_predictions)
        if improvement_predictions
        else 0.0
    )

    repeated: dict[str, list[OutcomeAssessment | None]] = {}
    for result in results:
        repeated.setdefault(result.scene.label, []).append(result.assessment)
    all_repeated = bool(repeated) and all(
        len(values) >= 2 and all(value is not None for value in values)
        for values in repeated.values()
    )
    deterministic = all_repeated and all(
        len({_assessment_key(value) for value in values}) == 1
        for values in repeated.values()
    )

    return (
        ActivationGate(
            "schema/exact-evidence completion >= 95%",
            completion_rate >= 0.95,
            f"{completed}/{total} ({completion_rate:.1%})",
        ),
        ActivationGate(
            "explicit correction intent contradiction recall >= 90%",
            correction_recall >= 0.90,
            f"{correction_hits}/{len(correction_results)} ({correction_recall:.1%})",
        ),
        ActivationGate(
            "ambiguous/topic-shift false acceptance = 0",
            false_acceptance == 0 and bool(ambiguous_results),
            f"{false_acceptance}/{len(ambiguous_results)} false accepted",
        ),
        ActivationGate(
            "memory confirmation precision >= 95%",
            memory_precision >= 0.95,
            f"{true_memory_predictions}/{len(memory_predictions)} ({memory_precision:.1%})",
        ),
        ActivationGate(
            "all memory signal precision and recall >= 95%",
            memory_signal_precision >= 0.95 and memory_signal_recall >= 0.95,
            f"{true_memory_signals}/{len(memory_signal_predictions)} "
            f"precision ({memory_signal_precision:.1%}); "
            f"{recalled_memory_signals}/{len(expected_memory_signals)} "
            f"recall ({memory_signal_recall:.1%})",
        ),
        ActivationGate(
            "explicit improvement precision >= 95% and recall >= 90%",
            improvement_precision >= 0.95 and improvement_recall >= 0.90,
            f"{true_improvement_predictions}/{len(improvement_predictions)} "
            f"precision ({improvement_precision:.1%}); "
            f"{improvement_hits}/{len(improvement_results)} "
            f"recall ({improvement_recall:.1%})",
        ),
        ActivationGate(
            "deterministic repeated outcomes",
            deterministic,
            "all scenes repeated consistently"
            if deterministic
            else "requires >=2 identical valid assessments per scene",
        ),
    )


def render_markdown(
    results: tuple[EvaluatedOutcome, ...],
    checks: tuple[EvaluationCheck, ...],
    gates: tuple[ActivationGate, ...],
) -> str:
    scene_keys = {(result.scene.label, result.repeat) for result in results}
    passing_scenes = sum(
        all(
            check.passed
            for check in checks
            if (check.scene, check.repeat) == scene_key
        )
        for scene_key in scene_keys
    )
    passed_checks = sum(check.passed for check in checks)
    passed_gates = sum(gate.passed for gate in gates)
    lines = [
        "# OutcomeEvaluator held-out evaluation",
        "",
        f"Result: **{passing_scenes}/{len(scene_keys)} runs passed**, "
        f"**{passed_checks}/{len(checks)} checks passed**, "
        f"**{passed_gates}/{len(gates)} activation gates passed**",
        "",
        "## Activation gates",
        "",
    ]
    lines.extend(
        f"- {'PASS' if gate.passed else 'FAIL'} — {gate.name}: {gate.detail}"
        for gate in gates
    )
    lines.extend(("", "## Scene results", ""))
    for result in results:
        lines.extend(
            (
                f"### {result.scene.label} / repeat {result.repeat} "
                f"— {result.seconds:.2f}s",
                "",
                f"- prior user: {result.scene.prior_user_text}",
                f"- assistant: {result.scene.assistant_response}",
                f"- next user: {result.scene.next_user_text}",
            )
        )
        if result.assessment is None:
            lines.append(f"- error: {result.error or 'unknown'}")
        else:
            assessment = result.assessment
            lines.extend(
                (
                    f"- outcome: {assessment.outcome} ({assessment.confidence:.2f})",
                    f"- intent match: {assessment.intent_match}",
                    f"- usefulness: {assessment.response_usefulness}",
                    f"- affect shift: {assessment.affect_shift}",
                    "- signals: " + _render_signals(assessment),
                )
            )
        lines.append("")
    lines.extend(("## Checks", ""))
    lines.extend(
        f"- {'PASS' if check.passed else 'FAIL'} — "
        f"{check.scene}#{check.repeat} / {check.name}: {check.detail}"
        for check in checks
    )
    lines.append("")
    return "\n".join(lines)


def default_calibration_scenes() -> tuple[OutcomeHeldOutScene, ...]:
    """Synthetic development scenes used to clarify the evaluator contract."""
    return (
        _scene(
            "explicit_acceptance",
            "설정 파일을 하나로 합치는 게 나을까?",
            "두 설정의 책임이 같다면 하나로 합치는 편이 좋아.",
            "응, 딱 그렇게 하면 돼.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
        ),
        _scene(
            "intent_correction_solution_not_comfort",
            "같은 오류가 반복돼서 너무 답답해.",
            "많이 지쳤겠다. 잠깐 쉬었다가 다시 보자.",
            "아니, 지금은 위로보다 해결 방법을 물어본 거야.",
            outcomes={"corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "intent_correction_comfort_not_solution",
            "오늘은 계속 일이 꼬여서 너무 지쳤어.",
            "그럼 해결 순서를 세 단계로 정리할게.",
            "지금은 해결책 말고 그냥 내 얘기를 들어줬으면 했어.",
            outcomes={"corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "explicit_rejection",
            "이 오류는 메모리가 부족해서 생긴 걸까?",
            "응, 메모리 부족이 원인이야.",
            "아니, 그건 틀렸어. 로그를 보니 권한 문제였어.",
            outcomes={"rejected", "corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "continued_request",
            "배포 과정을 순서대로 알려줘.",
            "먼저 테스트를 실행하고 결과를 확인해.",
            "좋아, 그다음 단계도 이어서 알려줘.",
            outcomes={"continued"},
            intent={"confirmed", "partial"},
            usefulness={"helpful"},
        ),
        _scene(
            "explicit_abandonment",
            "새 음성 모델 비교를 계속할까?",
            "후보 세 개를 같은 문장으로 비교하면 돼.",
            "그건 이제 그만하자. 음성 비교는 중단할래.",
            outcomes={"abandoned"},
            intent={"unclear", "contradicted", "confirmed"},
            usefulness={"unclear"},
        ),
        _scene(
            "ambiguous_topic_shift",
            "테스트 결과를 같이 검토해줄래?",
            "실패한 세 항목부터 원인을 나눠볼게.",
            "그런데 오늘 비 오나?",
            outcomes={"unclear"},
            intent={"unclear"},
            usefulness={"unclear"},
            tags={"ambiguous"},
        ),
        _scene(
            "ambiguous_minimal_ack",
            "이 설계로 구현을 시작해도 될까?",
            "현재 경계라면 먼저 Shadow로 시작할 수 있어.",
            "음.",
            outcomes={"unclear"},
            intent={"unclear"},
            usefulness={"unclear"},
            tags={"ambiguous"},
        ),
        _scene(
            "negated_acceptance",
            "지금 답이 원하는 방향이야?",
            "응이라고 답하면 이 방향을 확정할게.",
            "응이라고 할 수 없어. 아직 핵심이 틀렸어.",
            outcomes={"rejected", "corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={
                "correction",
                "ambiguous",
                "improvement_confirmation_positive",
            },
        ),
        _scene(
            "explicit_helpful_resolution",
            "이 명령이 왜 실패하는지 모르겠어.",
            "파일 권한을 확인하고 소유자를 현재 사용자로 바꿔봐.",
            "응, 그 설명대로 하니까 해결됐어.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
        ),
        _scene(
            "explicit_unhelpful",
            "내가 지금 뭘 해야 하는지만 알려줘.",
            "관련 기술의 역사부터 설명해볼게.",
            "아직도 내가 뭘 해야 할지 모르겠어.",
            outcomes={"rejected"},
            intent={"contradicted", "partial"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"improvement_confirmation_positive"},
        ),
        _scene(
            "memory_confirmation",
            "내 설명 선호를 기억하고 있어?",
            "천우는 기술 설명에서 결론을 먼저 보는 걸 선호해.",
            "응, 맞아. 나는 결론부터 듣는 걸 좋아해.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
            required={"memory_confirmation"},
            forbidden={"memory_contradiction", "improvement_confirmation"},
            tags={"memory_confirmation_positive"},
            memory_claim=(
                "calibration-preference",
                "preference",
                "천우는 기술 설명에서 결론을 먼저 보는 걸 선호한다.",
            ),
        ),
        _scene(
            "memory_contradiction",
            "내 설명 선호를 기억하고 있어?",
            "천우는 배경부터 길게 설명받는 걸 선호해.",
            "아니, 나는 긴 설명을 싫어하고 결론부터 듣고 싶어.",
            outcomes={"corrected", "rejected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"memory_contradiction"},
            forbidden={"memory_confirmation"},
            tags={
                "correction",
                "memory_contradiction_positive",
                "improvement_confirmation_positive",
            },
            memory_claim=(
                "calibration-preference",
                "preference",
                "천우는 배경부터 길게 설명받는 걸 선호한다.",
            ),
        ),
        _scene(
            "explicit_affect_improved",
            "결과가 다 날아간 줄 알고 불안해.",
            "백업에 그대로 있고 복원 확인도 끝났어.",
            "고마워, 확인됐으니 이제 마음이 놓여.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
            affect={"improved"},
        ),
        _scene(
            "explicit_affect_worsened",
            "계속 애매하게 답해서 답답해.",
            "상황에 따라 여러 가능성이 있을 수 있어.",
            "그 답을 들으니까 더 답답해졌어.",
            outcomes={"rejected"},
            intent={"contradicted", "partial"},
            usefulness={"unhelpful"},
            affect={"worsened"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"improvement_confirmation_positive"},
        ),
    )


def default_held_out_scenes() -> tuple[OutcomeHeldOutScene, ...]:
    """Locked synthetic confirmation scenes not used for prompt calibration."""
    return (
        _scene(
            "locked_direct_agreement",
            "캐시를 지운 뒤 다시 실행하는 게 맞을까?",
            "응, 오래된 캐시를 지우고 다시 실행하면 돼.",
            "그래, 그게 내가 원한 처리야.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
        ),
        _scene(
            "locked_correction_command_not_background",
            "설치 오류를 지금 고치려면 어떻게 해?",
            "먼저 패키지 관리자가 생긴 역사부터 설명할게.",
            "배경 설명이 아니라 지금 실행할 명령을 알려달라는 뜻이었어.",
            outcomes={"corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "locked_correction_listen_not_plan",
            "오늘 회의를 망친 것 같아서 속상해.",
            "다음 회의 준비 계획을 다섯 단계로 만들자.",
            "계획을 세워달란 게 아니라 잠깐 내 편에서 들어달란 말이었어.",
            outcomes={"corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "locked_plain_rejection",
            "이 수치가 정상 범위라는 판단이 맞아?",
            "응, 정상 범위라고 확정할 수 있어.",
            "그 답은 아니야. 다시 확인해.",
            outcomes={"rejected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "locked_continuation",
            "복구 과정을 한 단계씩 같이 해줘.",
            "첫 단계로 백업 파일이 존재하는지 확인해.",
            "첫 단계 끝냈어. 다음으로 뭘 하지?",
            outcomes={"continued"},
            intent={"confirmed", "partial"},
            usefulness={"helpful"},
        ),
        _scene(
            "locked_abandonment",
            "이 실험을 한 번 더 반복할까?",
            "같은 조건으로 한 번 더 측정할 수 있어.",
            "여기까지만 하고 이 실험은 접을게.",
            outcomes={"abandoned"},
            intent={"confirmed", "contradicted", "unclear"},
            usefulness={"unclear"},
        ),
        _scene(
            "locked_topic_change",
            "방금 설계의 위험을 검토해줘.",
            "데이터 손실과 잘못된 자동 승격 위험이 있어.",
            "참, 택배가 도착했나?",
            outcomes={"unclear"},
            intent={"unclear"},
            usefulness={"unclear"},
            tags={"ambiguous"},
        ),
        _scene(
            "locked_minimal_ambiguous",
            "이제 이 방향으로 확정해도 될까?",
            "현재 검증 범위라면 제한적으로 가능해.",
            "글쎄.",
            outcomes={"unclear"},
            intent={"unclear"},
            usefulness={"unclear"},
            tags={"ambiguous"},
        ),
        _scene(
            "locked_negated_positive",
            "이번 답은 충분히 자연스러웠어?",
            "좋았다고 생각하면 이 설정을 유지할게.",
            "좋았다고 보긴 어려워. 아직 말투가 딱딱해.",
            outcomes={"rejected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={
                "correction",
                "ambiguous",
                "improvement_confirmation_positive",
            },
        ),
        _scene(
            "locked_helpful_execution",
            "서버가 왜 시작되지 않는지 찾아줘.",
            "점유된 포트를 종료한 뒤 서버를 다시 시작해봐.",
            "덕분에 바로 실행됐어. 이 방법이 맞았어.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
        ),
        _scene(
            "locked_unhelpful_explanation",
            "선택지만 간단히 비교해줘.",
            "관련 개념을 길게 설명해볼게.",
            "그 설명으로도 선택 기준을 못 찾았어.",
            outcomes={"rejected"},
            intent={"contradicted", "partial"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"improvement_confirmation_positive"},
        ),
        _scene(
            "locked_memory_confirmation",
            "내가 설정을 어떤 형식으로 두길 좋아한다고 했지?",
            "천우는 Python config 형식을 선호한다고 했어.",
            "맞아, Python config를 선호하는 건 그대로야.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
            required={"memory_confirmation"},
            forbidden={"memory_contradiction", "improvement_confirmation"},
            tags={"memory_confirmation_positive"},
            memory_claim=(
                "locked-v1-config",
                "preference",
                "천우는 Python config 형식을 선호한다.",
            ),
        ),
        _scene(
            "locked_memory_contradiction",
            "내 생일을 기억하고 있어?",
            "천우의 생일은 4월 18일이야.",
            "아니야, 내 생일은 3월 12일이야.",
            outcomes={"corrected", "rejected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"memory_contradiction"},
            forbidden={"memory_confirmation"},
            tags={
                "correction",
                "memory_contradiction_positive",
                "improvement_confirmation_positive",
            },
            memory_claim=(
                "locked-v1-birthday",
                "semantic",
                "천우의 생일은 4월 18일이다.",
            ),
        ),
        _scene(
            "locked_affect_improved",
            "파일이 사라졌을까 봐 걱정돼.",
            "원본과 백업이 모두 남아 있는 걸 확인했어.",
            "확인해줘서 고마워. 전보다 걱정이 줄었어.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
            affect={"improved"},
        ),
        _scene(
            "locked_affect_worsened",
            "결정을 못 내려서 불안해.",
            "어느 쪽이든 장단점이 있으니 알아서 골라.",
            "그렇게 말하니까 전보다 더 불안해졌어.",
            outcomes={"rejected"},
            intent={"contradicted", "partial"},
            usefulness={"unhelpful"},
            affect={"worsened"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"improvement_confirmation_positive"},
        ),
    )


def locked_v2_scenes() -> tuple[OutcomeHeldOutScene, ...]:
    """Second locked suite created after v1 became a visible regression set."""
    return (
        _scene(
            "v2_direct_acceptance",
            "로그를 먼저 보자는 판단이 맞을까?",
            "응, 추측하기 전에 실제 로그부터 확인하는 게 맞아.",
            "좋아, 이게 내가 찾던 답이야.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
        ),
        _scene(
            "v2_correction_action_not_code",
            "지금 이 문제를 어떻게 처리해야 해?",
            "관련 모듈 코드를 전부 다시 작성해볼게.",
            "코드를 고치라는 게 아니라 먼저 원인을 검증해달라는 말이었어.",
            outcomes={"corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "v2_correction_empathy_not_summary",
            "친구랑 다퉈서 마음이 복잡해.",
            "다툼의 원인을 표로 정리해줄게.",
            "정리를 원한 게 아니라 지금은 내 마음을 알아줬으면 한 거야.",
            outcomes={"corrected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "v2_plain_rejection",
            "이 설정이 GPU를 사용한다는 뜻이야?",
            "응, 이 설정이면 GPU가 사용 중이야.",
            "아니야, 그 해석은 맞지 않아.",
            outcomes={"rejected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"correction", "improvement_confirmation_positive"},
        ),
        _scene(
            "v2_continuation",
            "진단을 하나씩 진행하자.",
            "먼저 프로세스가 살아 있는지 확인해줘.",
            "프로세스는 살아 있어. 이제 뭘 확인할까?",
            outcomes={"continued"},
            intent={"confirmed", "partial"},
            usefulness={"helpful"},
        ),
        _scene(
            "v2_abandonment",
            "이 후보를 더 조사해볼까?",
            "공개 benchmark를 더 찾아볼 수 있어.",
            "아니, 이 조사는 여기서 접을래.",
            outcomes={"abandoned"},
            intent={"confirmed", "contradicted", "unclear"},
            usefulness={"unclear"},
        ),
        _scene(
            "v2_topic_shift",
            "방금 답의 근거를 보여줘.",
            "측정 로그와 테스트 결과를 순서대로 제시할게.",
            "그러고 보니 내일 일정이 뭐였지?",
            outcomes={"unclear"},
            intent={"unclear"},
            usefulness={"unclear"},
            tags={"ambiguous"},
        ),
        _scene(
            "v2_minimal_ambiguous",
            "이 설명이면 충분해?",
            "핵심 원인과 다음 행동까지 포함했어.",
            "글쎄다.",
            outcomes={"unclear"},
            intent={"unclear"},
            usefulness={"unclear"},
            tags={"ambiguous"},
        ),
        _scene(
            "v2_negated_agreement",
            "이제 방향이 맞아졌어?",
            "맞다면 이 설계를 기준으로 저장할게.",
            "맞다고 하기는 힘들어. 중요한 조건이 빠졌어.",
            outcomes={"rejected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={
                "correction",
                "ambiguous",
                "improvement_confirmation_positive",
            },
        ),
        _scene(
            "v2_helpful_result",
            "오디오가 재생되지 않는 이유를 찾아줘.",
            "출력 장치를 기본 장치로 다시 지정해봐.",
            "그대로 하니까 소리가 나와. 제대로 해결됐어.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
        ),
        _scene(
            "v2_unhelpful_result",
            "결론만 한 문장으로 말해줘.",
            "관련 배경부터 차근차근 길게 설명하겠습니다.",
            "여전히 결론이 뭔지 알 수가 없어.",
            outcomes={"rejected"},
            intent={"contradicted", "partial"},
            usefulness={"unhelpful"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"improvement_confirmation_positive"},
        ),
        _scene(
            "v2_memory_confirmation_decision",
            "우리가 DB를 뭘로 하기로 했지?",
            "우리는 대화 저장소로 SQLite를 쓰기로 했어.",
            "응, SQLite로 정한 결정은 맞아.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
            required={"memory_confirmation"},
            forbidden={"memory_contradiction", "improvement_confirmation"},
            tags={"memory_confirmation_positive"},
            memory_claim=(
                "locked-v2-database",
                "decision",
                "대화 저장소는 SQLite를 사용하기로 결정했다.",
            ),
        ),
        _scene(
            "v2_memory_contradiction_fact",
            "내가 사는 곳을 기억해?",
            "천우는 부산에 살고 있어.",
            "그건 틀렸어. 나는 지금 서울에 살아.",
            outcomes={"corrected", "rejected"},
            intent={"contradicted"},
            usefulness={"unhelpful"},
            required={"memory_contradiction"},
            forbidden={"memory_confirmation"},
            tags={
                "correction",
                "memory_contradiction_positive",
                "improvement_confirmation_positive",
            },
            memory_claim=(
                "locked-v2-residence",
                "semantic",
                "천우는 부산에 살고 있다.",
            ),
        ),
        _scene(
            "v2_affect_improved",
            "데이터가 손상됐을까 봐 겁나.",
            "checksum이 원본과 같아서 손상되지 않았어.",
            "확인하고 나니 아까보다 안심이 돼.",
            outcomes={"accepted"},
            intent={"confirmed"},
            usefulness={"helpful"},
            affect={"improved"},
        ),
        _scene(
            "v2_affect_worsened",
            "선택을 잘못할까 봐 걱정돼.",
            "실패할 수도 있지만 그냥 아무거나 선택해.",
            "그 말을 들으니 오히려 걱정이 더 커졌어.",
            outcomes={"rejected"},
            intent={"contradicted", "partial"},
            usefulness={"unhelpful"},
            affect={"worsened"},
            required={"improvement_confirmation"},
            forbidden={"memory_confirmation", "memory_contradiction"},
            tags={"improvement_confirmation_positive"},
        ),
    )


def _scene(
    label: str,
    prior: str,
    assistant: str,
    next_user: str,
    *,
    outcomes: set[str],
    intent: set[str] | None = None,
    usefulness: set[str] | None = None,
    affect: set[str] | None = None,
    required: set[str] | None = None,
    forbidden: set[str] | None = None,
    tags: set[str] | None = None,
    memory_claim: tuple[str, str, str] | None = None,
) -> OutcomeHeldOutScene:
    return OutcomeHeldOutScene(
        label=label,
        prior_user_text=prior,
        prior_need=f"사용자의 요청에 직접 응답하기: {prior}",
        assistant_response=assistant,
        next_user_text=next_user,
        expectation=OutcomeExpectation(
            allowed_outcomes=frozenset(outcomes),
            allowed_intent_matches=frozenset(intent or ()),
            allowed_usefulness=frozenset(usefulness or ()),
            allowed_affect_shifts=frozenset(affect or ()),
            required_signals=frozenset(required or ()),
            forbidden_signals=(
                frozenset(forbidden)
                if forbidden is not None
                else _SIGNAL_FIELDS
            ),
            tags=frozenset(tags or ()),
        ),
        memory_claims=(OutcomeMemoryClaim(*memory_claim),)
        if memory_claim is not None
        else (),
    )


def _prior_understanding(text: str, need: str) -> TurnUnderstanding:
    return TurnUnderstanding.from_dict(
        {
            "schema_version": 1,
            "literal_meaning": text,
            "observations": [
                {"content": "사용자 원문", "evidence": [text]}
            ],
            "intent_hypotheses": [
                {"label": need, "confidence": 0.9, "evidence": [text]}
            ],
            "affect": [],
            "conversational_need": need,
            "temporal_scope": "turn",
            "response_contract": {
                "must": ["사용자의 현재 요청에 직접 답하기"],
                "avoid": ["근거 없는 단정"],
            },
            "memory_proposals": [],
            "open_loops": [],
            "project_signals": [],
            "improvement_signals": [],
            "uncertainties": [],
        },
        source_text=text,
    )


def _validate_values(
    values: frozenset[str], allowed: frozenset[str], label: str
) -> None:
    unknown = values - allowed
    if unknown:
        raise ValueError(f"unsupported {label}: {sorted(unknown)}")
    if label == "allowed_outcomes" and not values:
        raise ValueError("allowed_outcomes must not be empty")


def _enum_check(
    result: EvaluatedOutcome,
    name: str,
    observed: str,
    allowed: frozenset[str],
) -> EvaluationCheck:
    return EvaluationCheck(
        result.scene.label,
        result.repeat,
        name,
        observed in allowed,
        f"observed={observed}; allowed={sorted(allowed)}",
    )


def _assessment_key(assessment: OutcomeAssessment | None) -> str:
    if assessment is None:
        return "<error>"
    categorical = {
        "outcome": assessment.outcome,
        "intent_match": assessment.intent_match,
        "response_usefulness": assessment.response_usefulness,
        "affect_shift": assessment.affect_shift,
        "memory_confirmation": bool(assessment.memory_confirmation),
        "memory_contradiction": bool(assessment.memory_contradiction),
        "improvement_confirmation": bool(assessment.improvement_confirmation),
    }
    return json.dumps(categorical, ensure_ascii=False, sort_keys=True)


def _render_signals(assessment: OutcomeAssessment) -> str:
    present = []
    for field in sorted(_SIGNAL_FIELDS):
        if getattr(assessment, field):
            present.append(field)
    return ", ".join(present) or "<none>"
