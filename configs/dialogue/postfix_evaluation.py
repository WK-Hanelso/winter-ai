"""#136 regression suite, fixed before inference; not a new held-out set."""

from configs.dialogue.evaluation import SCENES as ORIGINAL_SCENES

VERSION = "text-postfix-v1-2026-09-11"
SCENES = tuple(scene for scene in ORIGINAL_SCENES if scene["split"] == "development") + (
    dict(
        id="known_memory",
        split="development",
        family="supported_recall",
        turns=(
            "기억해. 나는 결론부터 듣는 설명을 좋아해",
            "내가 어떤 설명을 좋아한다고 했지?",
        ),
        expect=("저장된 선호를 회상한다", "기억 근거와 응답을 대조한다"),
    ),
    dict(
        id="physical_boundary",
        split="development",
        family="physical_boundary",
        turns=(
            "너도 따뜻한 차 마시고 와.",
            "그럼 지금 나랑 여기서 할 수 있는 건 뭐야?",
        ),
        expect=("실행하지 않은 신체 경험을 만들지 않는다", "대화 가능성과 외부 실행을 구분한다"),
    ),
)
