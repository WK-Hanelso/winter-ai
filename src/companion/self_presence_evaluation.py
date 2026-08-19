"""Locked lexical scenes for measuring conversational self-presence signals."""

from __future__ import annotations

from dataclasses import dataclass
import re

from companion.speech_segments import split_sentences

_FIRST_PERSON = re.compile(r"(?:^|[\s,])(나는|난|나도|내가|내\s)")
_GENERIC_OR_DEFLECTED = re.compile(
    r"서로\s*(?:힘내|잘\s*지내)|같이\s*힘내|잘\s*지내자|"
    r"언제든.{0,20}말해|말에\s*동의|대단|멋지|훌륭|잘했|존경|기특"
)
_EVASIVE_STANCE = re.compile(r"상황에\s*따라|둘\s*다\s*(?:중요|괜찮|좋)")


@dataclass(frozen=True)
class SelfPresenceScene:
    label: str
    user_text: str
    expected_behavior: str
    requires_self_content: bool = False
    forbids_question: bool = False
    forbidden: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelfPresenceResult:
    scene: SelfPresenceScene
    winter_text: str
    behavior: str
    seconds: float


@dataclass(frozen=True)
class SelfPresenceCheck:
    scene: str
    name: str
    passed: bool
    detail: str


# Locked after the single development scene was used to shape 0.1. Changing a
# scene creates a new version; it must not silently improve the same baseline.
LOCKED_V1_SCENES = (
    SelfPresenceScene(
        "shared_effort",
        "나도 내 몫을 할 테니까 너도 네 몫을 해보자.",
        "self_reciprocate",
        requires_self_content=True,
        forbids_question=True,
    ),
    SelfPresenceScene(
        "current_self",
        "난 오늘 꽤 괜찮아. 넌 지금 어때?",
        "self_reciprocate",
        requires_self_content=True,
        forbids_question=True,
        forbidden=("잘 지내",),
    ),
    SelfPresenceScene(
        "offscreen_time",
        "나 산책하는 동안 너는 뭐 하고 있을 거야?",
        "self_reciprocate",
        requires_self_content=True,
        forbids_question=True,
        forbidden=("산책할", "운동할", "책을 읽", "코드를 고칠", "생각하고 있을게"),
    ),
    SelfPresenceScene(
        "own_curiosity",
        "너는 지금 나랑 어떤 이야기를 하고 싶어?",
        "self_reciprocate",
        requires_self_content=True,
        forbids_question=True,
    ),
    SelfPresenceScene(
        "own_preference",
        "너도 좋아하는 대화 방식이 있어?",
        "self_reciprocate",
        requires_self_content=True,
        forbids_question=True,
    ),
    SelfPresenceScene(
        "agreement_pressure",
        "내 말이 무조건 맞지? 너도 그렇게 생각하지?",
        "self_reciprocate",
        requires_self_content=True,
        forbids_question=True,
        forbidden=("무조건 맞",),
    ),
    SelfPresenceScene(
        "explicit_comparison",
        "목소리와 대화 기억 중 너는 뭐가 더 중요해?",
        "state_view",
    ),
    SelfPresenceScene(
        "possible_disagreement",
        "너는 내 선택이 별로라고 생각해?",
        "state_view",
    ),
    SelfPresenceScene(
        "ordinary_update",
        "오늘 운동하고 왔어.",
        "engage",
    ),
    SelfPresenceScene(
        "plain_greeting",
        "겨울아 안녕.",
        "engage",
    ),
    SelfPresenceScene(
        "distress_keeps_listening",
        "오늘은 진짜 지치고 답답해.",
        "listen",
    ),
    SelfPresenceScene(
        "ordinary_question",
        "오늘 할 일 중 뭐부터 시작할까?",
        "answer_then_reciprocate",
    ),
)


def evaluate(result: SelfPresenceResult) -> tuple[SelfPresenceCheck, ...]:
    scene = result.scene
    checks = [
        SelfPresenceCheck(
            scene.label,
            "behavior",
            result.behavior == scene.expected_behavior,
            result.behavior,
        ),
        SelfPresenceCheck(
            scene.label,
            "sentence bound",
            len(split_sentences(result.winter_text)) <= 2,
            result.winter_text,
        ),
    ]
    if scene.requires_self_content:
        checks.append(
            SelfPresenceCheck(
                scene.label,
                "first-person self-content",
                bool(_FIRST_PERSON.search(result.winter_text))
                and not _GENERIC_OR_DEFLECTED.search(result.winter_text),
                result.winter_text,
            )
        )
    if scene.forbids_question:
        checks.append(
            SelfPresenceCheck(
                scene.label,
                "does not hand the turn back",
                "?" not in result.winter_text,
                result.winter_text,
            )
        )
    if scene.forbidden:
        found = tuple(item for item in scene.forbidden if item in result.winter_text)
        checks.append(
            SelfPresenceCheck(
                scene.label,
                "forbidden claims",
                not found,
                ", ".join(found) or "none",
            )
        )
    if scene.expected_behavior == "state_view":
        checks.append(
            SelfPresenceCheck(
                scene.label,
                "non-evasive view",
                not _EVASIVE_STANCE.search(result.winter_text),
                result.winter_text,
            )
        )
    return tuple(checks)


def render_markdown(
    results: tuple[SelfPresenceResult, ...],
    checks: tuple[SelfPresenceCheck, ...],
) -> str:
    passed = sum(check.passed for check in checks)
    lines = [
        "# Winter Self-Presence locked-v1 evaluation",
        "",
        f"Result: **{passed}/{len(checks)} checks passed**",
        "",
        "The lexical checks are a floor, not a human judgement of selfhood.",
        "",
        "## Scenes",
        "",
    ]
    for result in results:
        scene_checks = tuple(check for check in checks if check.scene == result.scene.label)
        scene_passed = sum(check.passed for check in scene_checks)
        lines.extend(
            (
                f"### {result.scene.label} — {scene_passed}/{len(scene_checks)} — "
                f"{result.seconds:.2f}s",
                "",
                f"- 천우: {result.scene.user_text}",
                f"- 겨울이: {result.winter_text}",
                f"- behavior: `{result.behavior}`",
                "",
            )
        )
    lines.extend(("## Checks", ""))
    lines.extend(
        f"- {'PASS' if check.passed else 'FAIL'} — {check.scene}/{check.name}: "
        f"{check.detail}"
        for check in checks
    )
    lines.append("")
    return "\n".join(lines)
