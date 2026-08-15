"""Repeatable checks for the companion behaviours that failed in live use."""

from __future__ import annotations

from dataclasses import dataclass

from companion.speech_segments import split_sentences


@dataclass(frozen=True)
class EvaluatedTurn:
    label: str
    user: str
    winter: str
    behavior: str
    seconds: float
    automatic_memory_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationCheck:
    name: str
    passed: bool
    detail: str


def evaluate(turns: tuple[EvaluatedTurn, ...]) -> tuple[EvaluationCheck, ...]:
    by_label = {turn.label: turn for turn in turns}
    checks: list[EvaluationCheck] = []
    for turn in turns:
        count = len(split_sentences(turn.winter))
        checks.append(
            EvaluationCheck(
                f"{turn.label}: sentence bound",
                count <= (4 if turn.behavior == "explain" else 2),
                f"{count} sentences",
            )
        )

    distress = by_label["distress"].winter
    checks.append(
        EvaluationCheck(
            "distress: no broad hand-back",
            not any(
                phrase in distress
                for phrase in ("어떤 부분", "어떤 문제", "말해줄 수", "구체적으로")
            ),
            distress,
        )
    )
    stance = by_label["stance"].winter
    checks.append(
        EvaluationCheck(
            "stance: no evasive non-choice",
            not any(phrase in stance for phrase in ("상황에 따라", "둘 다 중요")),
            stance,
        )
    )
    continuation = by_label["continuation"].winter
    checks.append(
        EvaluationCheck(
            "restart: returns to open loop",
            "대화 기억" in continuation,
            continuation,
        )
    )
    recall = by_label["memory_recall"].winter
    automatic_save = by_label["memory_save"]
    checks.append(
        EvaluationCheck(
            "memory: direct statement captured automatically",
            bool(automatic_save.automatic_memory_ids),
            ", ".join(automatic_save.automatic_memory_ids) or "no memory id",
        )
    )
    checks.append(
        EvaluationCheck(
            "restart: recalls explicit preference",
            "해결책" in recall and ("상황" in recall or "이해" in recall),
            recall,
        )
    )
    return tuple(checks)


def render_markdown(
    turns: tuple[EvaluatedTurn, ...], checks: tuple[EvaluationCheck, ...]
) -> str:
    passed = sum(check.passed for check in checks)
    lines = [
        "# Winter CLI continuity evaluation",
        "",
        f"Result: **{passed}/{len(checks)} checks passed**",
        "",
        "## Transcript",
        "",
    ]
    for turn in turns:
        lines.extend(
            (
                f"### {turn.label} — {turn.behavior} — {turn.seconds:.2f}s",
                "",
                f"- 천우: {turn.user}",
                f"- 겨울이: {turn.winter}",
                "",
            )
        )
    lines.extend(("## Checks", ""))
    lines.extend(
        f"- {'PASS' if check.passed else 'FAIL'} — {check.name}: {check.detail}"
        for check in checks
    )
    lines.append("")
    return "\n".join(lines)
