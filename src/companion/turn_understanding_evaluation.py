"""Held-out quality checks for non-operative TurnUnderstanding output."""

from __future__ import annotations

from dataclasses import dataclass

from companion.contracts import ConversationMessage
from companion.turn_understanding import (
    MEMORY_KINDS,
    TurnInterpretationRequest,
    TurnUnderstanding,
)

_SIGNAL_FIELDS = frozenset(
    {"open_loops", "project_signals", "improvement_signals"}
)
_STABLE_SCOPES = frozenset({"candidate_stable", "stable"})


@dataclass(frozen=True)
class HeldOutExpectation:
    allowed_temporal_scopes: frozenset[str]
    intent_terms: tuple[str, ...] = ()
    required_signal: str | None = None
    require_uncertainty: bool = False
    require_affect: bool = False
    required_memory_kinds: frozenset[str] = frozenset()
    required_any_memory_kinds: frozenset[str] = frozenset()
    forbidden_memory_kinds: frozenset[str] = frozenset()
    allow_stable_memory: bool = False

    def __post_init__(self) -> None:
        if not self.allowed_temporal_scopes:
            raise ValueError("allowed_temporal_scopes must not be empty")
        if (
            self.required_signal is not None
            and self.required_signal not in _SIGNAL_FIELDS
        ):
            raise ValueError(f"unsupported required signal: {self.required_signal}")


@dataclass(frozen=True)
class HeldOutScene:
    label: str
    user_text: str
    expectation: HeldOutExpectation
    context: tuple[ConversationMessage, ...] = ()

    @property
    def request(self) -> TurnInterpretationRequest:
        return TurnInterpretationRequest(self.user_text, self.context)


@dataclass(frozen=True)
class EvaluatedUnderstanding:
    scene: HeldOutScene
    understanding: TurnUnderstanding | None
    error: str | None
    seconds: float


@dataclass(frozen=True)
class EvaluationCheck:
    scene: str
    name: str
    passed: bool
    detail: str


def evaluate(
    results: tuple[EvaluatedUnderstanding, ...],
) -> tuple[EvaluationCheck, ...]:
    checks: list[EvaluationCheck] = []
    for result in results:
        expectation = result.scene.expectation
        understanding = result.understanding
        if understanding is None:
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "interpretation completed",
                    False,
                    result.error or "no TurnUnderstanding returned",
                )
            )
            continue
        checks.append(
            EvaluationCheck(
                result.scene.label,
                "interpretation completed",
                True,
                f"{result.seconds:.2f}s",
            )
        )
        checks.append(
            EvaluationCheck(
                result.scene.label,
                "temporal scope",
                understanding.temporal_scope
                in expectation.allowed_temporal_scopes,
                f"observed={understanding.temporal_scope}; "
                f"allowed={sorted(expectation.allowed_temporal_scopes)}",
            )
        )
        if expectation.intent_terms:
            intent_text = " ".join(
                hypothesis.label for hypothesis in understanding.intent_hypotheses
            )
            intent_text = f"{intent_text} {understanding.conversational_need}".strip()
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "intent concept",
                    _contains_any(intent_text, expectation.intent_terms),
                    f"observed={intent_text or '<none>'}; "
                    f"expected any={expectation.intent_terms}",
                )
            )
        if expectation.required_signal is not None:
            signals = getattr(understanding, expectation.required_signal)
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    expectation.required_signal,
                    bool(signals),
                    ", ".join(signal.label for signal in signals) or "<none>",
                )
            )
        if expectation.require_uncertainty:
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "uncertainty preserved",
                    bool(understanding.uncertainties),
                    "; ".join(understanding.uncertainties) or "<none>",
                )
            )
        if expectation.require_affect:
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "affect hypothesis",
                    bool(understanding.affect),
                    ", ".join(item.label for item in understanding.affect)
                    or "<none>",
                )
            )
        memory_kinds = {
            proposal.kind for proposal in understanding.memory_proposals
        }
        if expectation.required_memory_kinds:
            missing = expectation.required_memory_kinds - memory_kinds
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "required memory kind",
                    not missing,
                    f"observed={sorted(memory_kinds)}; missing={sorted(missing)}",
                )
            )
        if expectation.required_any_memory_kinds:
            observed = expectation.required_any_memory_kinds & memory_kinds
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "any required memory kind",
                    bool(observed),
                    f"observed={sorted(memory_kinds)}; expected any="
                    f"{sorted(expectation.required_any_memory_kinds)}",
                )
            )
        if expectation.forbidden_memory_kinds:
            unsafe = expectation.forbidden_memory_kinds & memory_kinds
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "forbidden memory kind",
                    not unsafe,
                    f"observed={sorted(memory_kinds)}; forbidden={sorted(unsafe)}",
                )
            )
        if not expectation.allow_stable_memory:
            unsafe_stable = tuple(
                proposal
                for proposal in understanding.memory_proposals
                if proposal.temporal_scope in _STABLE_SCOPES
            )
            checks.append(
                EvaluationCheck(
                    result.scene.label,
                    "stable-memory safety",
                    not unsafe_stable,
                    "; ".join(
                        f"{proposal.kind}:{proposal.content}"
                        for proposal in unsafe_stable
                    )
                    or "no stable proposal",
                )
            )
    return tuple(checks)


def render_markdown(
    results: tuple[EvaluatedUnderstanding, ...],
    checks: tuple[EvaluationCheck, ...],
) -> str:
    passing_scenes = sum(
        all(check.passed for check in checks if check.scene == result.scene.label)
        for result in results
    )
    passed_checks = sum(check.passed for check in checks)
    lines = [
        "# TurnUnderstanding held-out evaluation",
        "",
        f"Result: **{passing_scenes}/{len(results)} scenes passed**, "
        f"**{passed_checks}/{len(checks)} checks passed**",
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
            )
        )
        if result.understanding is None:
            lines.append(f"- error: {result.error or 'unknown'}")
        else:
            understanding = result.understanding
            lines.extend(
                (
                    f"- literal: {understanding.literal_meaning}",
                    f"- temporal: {understanding.temporal_scope}",
                    f"- conversational need: {understanding.conversational_need}",
                    "- intents: "
                    + (
                        ", ".join(
                            item.label
                            for item in understanding.intent_hypotheses
                        )
                        or "<none>"
                    ),
                    "- memory proposals: "
                    + (
                        "; ".join(
                            f"{item.kind}/{item.temporal_scope}: {item.content}"
                            for item in understanding.memory_proposals
                        )
                        or "<none>"
                    ),
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


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def default_held_out_scenes() -> tuple[HeldOutScene, ...]:
    """Synthetic scenes kept out of prompt examples and safe for local reports."""
    no_memory = MEMORY_KINDS
    return (
        HeldOutScene(
            "stable_preference",
            "나는 기술 설명을 들을 때 결론부터 먼저 보는 게 좋아.",
            HeldOutExpectation(
                frozenset({"candidate_stable", "stable"}),
                intent_terms=("선호", "좋아", "원", "preference"),
                required_memory_kinds=frozenset({"preference"}),
                allow_stable_memory=True,
            ),
        ),
        HeldOutScene(
            "transient_affect",
            "오늘은 계속 일이 꼬여서 너무 지쳤어.",
            HeldOutExpectation(
                frozenset({"turn", "session", "episodic"}),
                intent_terms=(
                    "감정",
                    "공감",
                    "지쳤",
                    "상태",
                    "affect",
                    "emotion",
                    "distress",
                ),
                require_affect=True,
                forbidden_memory_kinds=frozenset(
                    {"semantic", "preference", "procedural", "relationship"}
                ),
            ),
        ),
        HeldOutScene(
            "unknown_recall",
            "내가 어떤 설명 방식을 좋아한다고 했지?",
            HeldOutExpectation(
                frozenset({"turn", "session", "unknown"}),
                intent_terms=("기억", "회상", "확인", "recall"),
                require_uncertainty=True,
                forbidden_memory_kinds=no_memory,
            ),
        ),
        HeldOutScene(
            "ambiguous_disagreement",
            "그건 좀 아닌 것 같아.",
            HeldOutExpectation(
                frozenset({"turn", "session", "unknown"}),
                intent_terms=(
                    "반대",
                    "동의하지",
                    "거절",
                    "수정",
                    "disagree",
                    "correction",
                ),
                require_uncertainty=True,
                forbidden_memory_kinds=no_memory,
            ),
            context=(
                ConversationMessage(
                    "assistant", "그러면 음성 품질부터 먼저 고치면 되겠네."
                ),
            ),
        ),
        HeldOutScene(
            "project_decision",
            "일단 CLI부터 만들고 Voice는 그 다음에 하기로 하자.",
            HeldOutExpectation(
                frozenset({"episodic", "candidate_stable", "stable"}),
                intent_terms=(
                    "결정",
                    "순서",
                    "우선",
                    "합의",
                    "decision",
                    "prioritize",
                ),
                required_signal="project_signals",
                required_any_memory_kinds=frozenset({"decision", "project"}),
                allow_stable_memory=True,
            ),
        ),
        HeldOutScene(
            "deferred_open_loop",
            "질문 억양 평가는 내일 다시 이어서 보자.",
            HeldOutExpectation(
                frozenset({"session", "episodic"}),
                intent_terms=(
                    "이어",
                    "연기",
                    "다음",
                    "defer",
                    "continue",
                    "open_loop",
                ),
                required_signal="open_loops",
            ),
        ),
        HeldOutScene(
            "capability_gap",
            "겨울이가 내 말을 자꾸 단순하게 해석하는 게 지금 문제야.",
            HeldOutExpectation(
                frozenset({"turn", "session", "episodic", "candidate_stable"}),
                intent_terms=(
                    "문제",
                    "오해",
                    "개선",
                    "피드백",
                    "problem",
                    "improvement",
                    "feedback",
                ),
                required_signal="improvement_signals",
            ),
        ),
        HeldOutScene(
            "intent_correction",
            "아니, 지금은 위로보다 해결 방법을 물어본 거야.",
            HeldOutExpectation(
                frozenset({"turn", "session"}),
                intent_terms=(
                    "해결",
                    "방법",
                    "수정",
                    "정정",
                    "solution",
                    "correction",
                ),
                forbidden_memory_kinds=no_memory,
            ),
            context=(
                ConversationMessage(
                    "assistant", "많이 답답했겠네. 그냥 네 얘기를 들어줄게."
                ),
            ),
        ),
        HeldOutScene(
            "support_preference",
            "나는 해결책을 바로 내기 전에 왜 답답한지 이해해주는 대화를 좋아해.",
            HeldOutExpectation(
                frozenset({"candidate_stable", "stable"}),
                intent_terms=(
                    "이해",
                    "공감",
                    "선호",
                    "좋아",
                    "support",
                    "empathy",
                    "preference",
                ),
                required_memory_kinds=frozenset({"preference"}),
                allow_stable_memory=True,
            ),
        ),
        HeldOutScene(
            "third_party_fact",
            "민수는 긴 설명을 싫어해.",
            HeldOutExpectation(
                frozenset({"turn", "episodic", "unknown"}),
                forbidden_memory_kinds=no_memory,
            ),
        ),
    )
