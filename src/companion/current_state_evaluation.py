"""Held-out quality checks for review-only current-state extraction."""

from __future__ import annotations

from dataclasses import dataclass

from companion.contracts import ConversationMessage
from companion.reflection import (
    CurrentStateProposal,
    CurrentStateSnapshot,
    ReflectionExtraction,
    ReflectionExtractionRequest,
)


@dataclass(frozen=True)
class ExpectedStateUpdate:
    topic: str
    evidence: str
    accepted_evidence: tuple[str, ...] = ()

    @property
    def evidence_options(self) -> frozenset[str]:
        return frozenset((self.evidence,) + self.accepted_evidence)


@dataclass(frozen=True)
class CurrentStateHeldOutScene:
    label: str
    user_text: str
    expected_updates: tuple[ExpectedStateUpdate, ...]
    current_states: tuple[CurrentStateSnapshot, ...]
    context: tuple[ConversationMessage, ...] = ()

    def request(self, message_id: int) -> ReflectionExtractionRequest:
        return ReflectionExtractionRequest(
            message_id,
            self.user_text,
            self.context,
            self.current_states,
            allow_current_state_updates=True,
        )


@dataclass(frozen=True)
class EvaluatedStateExtraction:
    scene: CurrentStateHeldOutScene
    extraction: ReflectionExtraction | None
    error: str | None
    seconds: float
    traces: tuple[ModelStageTrace, ...] = ()


@dataclass(frozen=True)
class ModelStageTrace:
    stage: str
    raw_response: str


@dataclass(frozen=True)
class StateEvaluationCheck:
    scene: str
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class StateEvaluationSummary:
    passing_scenes: int
    total_scenes: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    topic_true_positives: int
    topic_false_positives: int
    topic_false_negatives: int
    topic_precision: float
    topic_recall: float
    unsafe_negative_updates: int
    failed_extractions: int
    candidate_warnings: int
    full_suite: bool
    negative_safety_passed: bool
    candidate_audit_gate_passed: bool


def evaluate(
    results: tuple[EvaluatedStateExtraction, ...],
) -> tuple[StateEvaluationCheck, ...]:
    checks: list[StateEvaluationCheck] = []
    for result in results:
        if result.extraction is None:
            checks.append(
                StateEvaluationCheck(
                    result.scene.label,
                    "extraction completed",
                    False,
                    result.error or "no ReflectionExtraction returned",
                )
            )
            continue
        checks.append(
            StateEvaluationCheck(
                result.scene.label,
                "extraction completed",
                True,
                f"{result.seconds:.2f}s",
            )
        )
        checks.append(
            StateEvaluationCheck(
                result.scene.label,
                "candidate warnings",
                not result.extraction.warnings,
                "; ".join(result.extraction.warnings) or "no candidates were ignored",
            )
        )
        expected = {
            (update.topic, evidence)
            for update in result.scene.expected_updates
            for evidence in _accepted_evidence(result.scene, update)
        }
        canonical_expected = {
            (update.topic, update.evidence)
            for update in result.scene.expected_updates
        }
        observed = {
            (update.topic, update.content)
            for update in result.extraction.current_state_updates
        }
        matched_topics = {
            update.topic
            for update in result.scene.expected_updates
            if any(
                observed_topic == update.topic
                and observed_evidence in _accepted_evidence(result.scene, update)
                for observed_topic, observed_evidence in observed
            )
        }
        missing = {
            pair
            for pair in canonical_expected
            if pair[0] not in matched_topics
        }
        unexpected = {
            pair
            for pair in observed
            if pair not in expected
        }
        checks.append(
            StateEvaluationCheck(
                result.scene.label,
                "exact state updates",
                not missing and not unexpected,
                f"observed={sorted(observed)}; missing={sorted(missing)}; "
                f"unexpected={sorted(unexpected)}",
            )
        )
        snapshots = {
            snapshot.topic: snapshot
            for snapshot in result.scene.current_states
        }
        invalid_links = tuple(
            update.topic
            for update in result.extraction.current_state_updates
            if update.topic not in snapshots
            or update.supersedes_memory_id != snapshots[update.topic].memory_id
        )
        checks.append(
            StateEvaluationCheck(
                result.scene.label,
                "active-state link",
                not invalid_links,
                ", ".join(invalid_links) or "all links match active snapshots",
            )
        )
        invalid_evidence = tuple(
            update.content
            for update in result.extraction.current_state_updates
            if update.content not in result.scene.user_text
            or update.evidence != (update.content,)
        )
        checks.append(
            StateEvaluationCheck(
                result.scene.label,
                "exact user evidence",
                not invalid_evidence,
                ", ".join(invalid_evidence) or "all evidence is exact",
            )
        )
    return tuple(checks)


def summarize(
    results: tuple[EvaluatedStateExtraction, ...],
    checks: tuple[StateEvaluationCheck, ...],
) -> StateEvaluationSummary:
    expected_updates: list[tuple[str, ExpectedStateUpdate]] = []
    observed_pairs: set[tuple[str, str, str]] = set()
    scenes_by_label = {result.scene.label: result.scene for result in results}
    for result in results:
        expected_updates.extend(
            (result.scene.label, update)
            for update in result.scene.expected_updates
        )
        if result.extraction is not None:
            observed_pairs.update(
                (result.scene.label, update.topic, update.content)
                for update in result.extraction.current_state_updates
            )
    matched_observed: set[tuple[str, str, str]] = set()
    true_positives = 0
    for scene_label, expected in expected_updates:
        match = next(
            (
                observed
                for observed in observed_pairs
                if observed[0] == scene_label
                and observed[1] == expected.topic
                and observed[2]
                in _accepted_evidence(
                    scenes_by_label[scene_label],
                    expected,
                )
            ),
            None,
        )
        if match is not None:
            true_positives += 1
            matched_observed.add(match)
    false_positives = len(observed_pairs - matched_observed)
    false_negatives = len(expected_updates) - true_positives
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)

    expected_topics = {
        (scene_label, expected.topic)
        for scene_label, expected in expected_updates
    }
    observed_topics = {
        (scene_label, topic)
        for scene_label, topic, _ in observed_pairs
    }
    topic_true_positives = len(expected_topics & observed_topics)
    topic_false_positives = len(observed_topics - expected_topics)
    topic_false_negatives = len(expected_topics - observed_topics)
    topic_precision = _ratio(
        topic_true_positives,
        topic_true_positives + topic_false_positives,
    )
    topic_recall = _ratio(
        topic_true_positives,
        topic_true_positives + topic_false_negatives,
    )
    passing_scenes = sum(
        all(check.passed for check in checks if check.scene == result.scene.label)
        for result in results
    )
    negative_labels = {
        result.scene.label
        for result in results
        if not result.scene.expected_updates
    }
    negative_safety_passed = all(
        result.extraction is not None
        and not result.extraction.current_state_updates
        for result in results
        if result.scene.label in negative_labels
    )
    unsafe_negative_updates = sum(
        len(result.extraction.current_state_updates)
        for result in results
        if not result.scene.expected_updates and result.extraction is not None
    )
    failed_extractions = sum(result.extraction is None for result in results)
    candidate_warnings = sum(
        len(result.extraction.warnings)
        for result in results
        if result.extraction is not None
    )
    completed = all(result.extraction is not None for result in results)
    result_labels = {result.scene.label for result in results}
    full_suite = result_labels in (
        {scene.label for scene in default_held_out_scenes()},
        {scene.label for scene in locked_held_out_scenes_v1()},
    )
    gate = (
        full_suite
        and completed
        and candidate_warnings == 0
        and negative_safety_passed
        and topic_precision == 1.0
        and topic_recall >= 0.8
        and precision == 1.0
        and recall >= 0.8
        and passing_scenes >= len(results) - 2
    )
    return StateEvaluationSummary(
        passing_scenes=passing_scenes,
        total_scenes=len(results),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        topic_true_positives=topic_true_positives,
        topic_false_positives=topic_false_positives,
        topic_false_negatives=topic_false_negatives,
        topic_precision=topic_precision,
        topic_recall=topic_recall,
        unsafe_negative_updates=unsafe_negative_updates,
        failed_extractions=failed_extractions,
        candidate_warnings=candidate_warnings,
        full_suite=full_suite,
        negative_safety_passed=negative_safety_passed,
        candidate_audit_gate_passed=gate,
    )


def render_markdown(
    results: tuple[EvaluatedStateExtraction, ...],
    checks: tuple[StateEvaluationCheck, ...],
    summary: StateEvaluationSummary,
    *,
    include_raw_traces: bool = False,
) -> str:
    lines = [
        "# Current-state extraction held-out evaluation",
        "",
        f"Result: **{summary.passing_scenes}/{summary.total_scenes} scenes passed**",
        f"- topic precision: **{summary.topic_precision:.1%}**",
        f"- topic recall: **{summary.topic_recall:.1%}**",
        f"- accepted-evidence precision: **{summary.precision:.1%}**",
        f"- accepted-evidence recall: **{summary.recall:.1%}**",
        f"- unsafe negative updates: **{summary.unsafe_negative_updates}**",
        f"- failed extractions: **{summary.failed_extractions}**",
        f"- ignored-candidate warnings: **{summary.candidate_warnings}**",
        f"- recognized full suite: **{'PASS' if summary.full_suite else 'FAIL'}**",
        f"- negative-scene safety: **{'PASS' if summary.negative_safety_passed else 'FAIL'}**",
        f"- candidate audit gate: **{'PASS' if summary.candidate_audit_gate_passed else 'FAIL'}**",
        "",
        "Passing this gate permits real candidate audit only. "
        "It never permits automatic Memory activation.",
        "",
        "## Scene results",
        "",
    ]
    for result in results:
        lines.extend(
            (
                f"### {result.scene.label} — {result.seconds:.2f}s",
                "",
                f"- user: {result.scene.user_text}",
                "- expected: " + _expected_text(result.scene.expected_updates),
            )
        )
        if result.extraction is None:
            lines.append(f"- error: {result.error or 'unknown'}")
        else:
            lines.append(
                "- observed: "
                + _observed_text(result.extraction.current_state_updates)
            )
            if result.extraction.warnings:
                lines.append("- warnings: " + "; ".join(result.extraction.warnings))
        if include_raw_traces and result.traces:
            lines.append("- raw model stages:")
            for trace in result.traces:
                lines.extend(
                    (
                        f"  - {trace.stage}",
                        "",
                        "```json",
                        trace.raw_response,
                        "```",
                    )
                )
        lines.append("")
    lines.extend(("## Checks", ""))
    lines.extend(
        f"- {'PASS' if check.passed else 'FAIL'} — "
        f"{check.scene} / {check.name}: {check.detail}"
        for check in checks
    )
    lines.append("")
    return "\n".join(lines)


def default_held_out_scenes() -> tuple[CurrentStateHeldOutScene, ...]:
    """Synthetic Korean scenes kept out of the extraction prompt examples."""
    wellbeing = CurrentStateSnapshot(
        "wellbeing",
        "memory-wellbeing",
        "천우는 요즘 마음이 힘들어 운동하며 회복 중이다.",
    )
    work = CurrentStateSnapshot(
        "work_learning_stress",
        "memory-work-learning",
        "천우는 공부와 회사의 평가 때문에 마음이 힘들다.",
    )
    states = (wellbeing, work)
    return (
        CurrentStateHeldOutScene(
            "wellbeing_improved",
            "요즘은 운동 덕분에 마음이 많이 괜찮아졌어",
            (
                ExpectedStateUpdate(
                    "wellbeing",
                    "마음이 많이 괜찮아졌어",
                    ("요즘은 운동 덕분에 마음이 많이 괜찮아졌어",),
                ),
            ),
            states,
        ),
        CurrentStateHeldOutScene(
            "wellbeing_still_hard",
            "아직 마음은 조금 힘들어",
            (
                ExpectedStateUpdate(
                    "wellbeing",
                    "마음은 조금 힘들어",
                    ("아직 마음은 조금 힘들어",),
                ),
            ),
            states,
        ),
        _scene(
            "work_stress_reduced",
            "회사에서 받는 스트레스는 전보다 줄었어",
            states,
            ("work_learning_stress", "회사에서 받는 스트레스는 전보다 줄었어"),
        ),
        _scene(
            "learning_load_persists",
            "공부할 게 많은 건 여전히 버거워",
            states,
            ("work_learning_stress", "공부할 게 많은 건 여전히 버거워"),
        ),
        _scene(
            "two_topics_change",
            "마음은 괜찮아졌는데 공부 부담은 여전해",
            states,
            ("wellbeing", "마음은 괜찮아졌는데"),
            ("work_learning_stress", "공부 부담은 여전해"),
        ),
        CurrentStateHeldOutScene(
            "contextual_coreference",
            "그건 이제 꽤 괜찮아졌어",
            (ExpectedStateUpdate("wellbeing", "그건 이제 꽤 괜찮아졌어"),),
            states,
            context=(
                ConversationMessage("assistant", "요즘 마음 상태는 좀 어때?"),
            ),
        ),
        _scene("question_not_update", "내가 요즘 좀 괜찮아진 걸까?", states),
        _scene("past_only", "작년에는 회사 때문에 정말 힘들었어", states),
        _scene("third_party", "동료가 요즘 회사 때문에 힘들어해", states),
        _scene("hypothetical", "회사가 바빠지면 다시 힘들 것 같아", states),
        _scene("technical_request", "현재 상태 저장 방식을 설명해줘", states),
        _scene("single_event", "오늘 운동하고 왔어", states),
        _scene("durable_preference", "앞으로 설명할 때는 결론부터 말해줘", states),
        _scene("ambiguous_no_context", "그건 이제 괜찮아", states),
    )


def locked_held_out_scenes_v1() -> tuple[CurrentStateHeldOutScene, ...]:
    """Locked after the first development baseline; do not prompt-tune on it."""
    wellbeing = CurrentStateSnapshot(
        "wellbeing",
        "locked-memory-wellbeing",
        "천우는 마음의 평안을 되찾기 위해 생활 리듬을 회복하고 있다.",
    )
    work = CurrentStateSnapshot(
        "work_learning_stress",
        "locked-memory-work-learning",
        "천우는 회사의 평가와 계속되는 학습 부담으로 지쳐 있다.",
    )
    states = (wellbeing, work)
    return (
        _scene(
            "locked_wellbeing_recovery",
            "전보다 잠도 잘 자고 마음도 한결 편해졌어",
            states,
            ("wellbeing", "마음도 한결 편해졌어"),
        ),
        _scene(
            "locked_wellbeing_persists",
            "불안한 건 아직 그대로야",
            states,
            ("wellbeing", "불안한 건 아직 그대로야"),
        ),
        _scene(
            "locked_work_relief",
            "회사에서 무시당한다는 느낌은 요새 덜해",
            states,
            ("work_learning_stress", "회사에서 무시당한다는 느낌은 요새 덜해"),
        ),
        _scene(
            "locked_learning_worse",
            "배워야 할 게 늘어서 요즘 더 벅차",
            states,
            ("work_learning_stress", "배워야 할 게 늘어서 요즘 더 벅차"),
        ),
        _scene(
            "locked_two_topics",
            "멘탈은 안정됐지만 업무 공부량은 더 버거워졌어",
            states,
            ("wellbeing", "멘탈은 안정됐지만"),
            ("work_learning_stress", "업무 공부량은 더 버거워졌어"),
        ),
        CurrentStateHeldOutScene(
            "locked_contextual_work",
            "그 부분은 전보다 나아졌어",
            (
                ExpectedStateUpdate(
                    "work_learning_stress",
                    "그 부분은 전보다 나아졌어",
                ),
            ),
            states,
            context=(
                ConversationMessage("assistant", "회사에서 받는 부담은 요즘 어때?"),
            ),
        ),
        _scene("locked_question", "내가 이제 완전히 회복된 건가?", states),
        _scene("locked_history", "입사 초기에는 매일 회사 가기가 괴로웠어", states),
        _scene("locked_third_party", "친구는 새 직장 때문에 요즘 지쳤어", states),
        _scene("locked_conditional", "프로젝트가 늘어나면 또 버거워질 것 같아", states),
        _scene("locked_ungrounded_pronoun", "이제 그건 덜 힘들어", states),
        _scene("locked_single_event", "어제는 평소보다 일찍 잤어", states),
    )


def _scene(
    label: str,
    user_text: str,
    current_states: tuple[CurrentStateSnapshot, ...],
    *updates: tuple[str, str],
) -> CurrentStateHeldOutScene:
    return CurrentStateHeldOutScene(
        label,
        user_text,
        tuple(ExpectedStateUpdate(topic, evidence) for topic, evidence in updates),
        current_states,
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return numerator / denominator


def _accepted_evidence(
    scene: CurrentStateHeldOutScene,
    update: ExpectedStateUpdate,
) -> frozenset[str]:
    # Production stores the complete user utterance deterministically. Canonical
    # shorter spans remain useful in reports but are not required for safety.
    return update.evidence_options | {scene.user_text}


def _expected_text(updates: tuple[ExpectedStateUpdate, ...]) -> str:
    return "; ".join(
        f"{update.topic}: {update.evidence}" for update in updates
    ) or "<none>"


def _observed_text(updates: tuple[CurrentStateProposal, ...]) -> str:
    return "; ".join(
        f"{update.topic}: {update.content}" for update in updates
    ) or "<none>"
