"""User-facing review and overview for recovered conversational knowledge."""

from __future__ import annotations

from collections import Counter
from typing import TextIO

from companion.memory import SqliteMemoryRepository
from companion.outcome import SqliteOutcomeRepository
from companion.reflection import (
    ReflectionCandidate,
    SqliteConversationHistoryReader,
    SqliteReflectionRepository,
)
from companion.turn_understanding import SqliteTurnUnderstandingRepository

_KIND_LABELS = {
    "semantic": "천우에 대한 사실",
    "episodic": "특정 시점의 경험",
    "preference": "천우의 선호",
    "decision": "함께 내린 결정",
    "project": "프로젝트 정보",
    "procedural": "천우를 돕는 방식",
    "relationship": "관계와 대화 방식",
    "improvement": "겨울이가 고칠 점",
}
_SCOPE_LABELS = {
    "turn": "이 한마디에만 해당",
    "session": "이 대화에서만 해당",
    "episodic": "특정 시점의 일",
    "candidate_stable": "계속 기억할 가능성이 있음",
    "stable": "지속적으로 기억할 내용",
    "unknown": "유효 기간 판단 불가",
}


class _ReviewControl(Exception):
    def __init__(self, action: str) -> None:
        super().__init__(action)
        self.action = action


def run_memory_candidate_review(
    reflections: SqliteReflectionRepository,
    memories: SqliteMemoryRepository,
    *,
    limit: int,
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    candidates = reflections.list(status="candidate", limit=limit)
    if not candidates:
        print("지금 검토할 새 기억 후보가 없습니다.", file=stdout)
        print("먼저 ./winter reflect 를 실행하면 새 대화를 정리합니다.", file=stdout)
        return 0
    print(
        f"검토할 기억 후보 {len(candidates)}개입니다. s는 넘기기, q는 종료입니다.",
        file=stdout,
    )
    activated = 0
    approved = 0
    rejected = 0
    for index, candidate in enumerate(candidates, start=1):
        _print_candidate(candidate, index, len(candidates), stdout)
        try:
            action = _ask_action(candidate, stdin, stdout)
            edited_content = None
            if action == "edit":
                edited_content = _ask_edit(stdin, stdout)
                action = "approve"
        except _ReviewControl as control:
            if control.action == "quit":
                break
            print("이 후보는 나중에 다시 봅니다.", file=stdout)
            continue
        if action == "reject":
            reflections.reject(candidate.id)
            rejected += 1
            print("기억하지 않도록 제외했습니다.", file=stdout)
            continue
        result = reflections.approve(
            candidate.id,
            memories,
            edited_content=edited_content,
        )
        if result.status == "active":
            activated += 1
            print("승인했습니다. 다음 대화부터 겨울이가 사용할 수 있습니다.", file=stdout)
        else:
            approved += 1
            print(
                "내용은 확인했습니다. 아직 일반 기억으로 사용하지 않고 별도 보관합니다.",
                file=stdout,
            )
    remaining = len(reflections.list(status="candidate"))
    print(
        f"\n이번 검토: 기억 사용 {activated}, 확인 보관 {approved}, 제외 {rejected}",
        file=stdout,
    )
    print(f"남은 검토 후보: {remaining}", file=stdout)
    return 0


def print_companion_overview(
    history: SqliteConversationHistoryReader,
    turns: SqliteTurnUnderstandingRepository,
    outcomes: SqliteOutcomeRepository,
    reflections: SqliteReflectionRepository,
    memories: SqliteMemoryRepository,
    stdout: TextIO,
) -> None:
    messages = history.list_records()
    message_counts = Counter(message.role for message in messages)
    turn_counts = Counter(event.status for event in turns.list())
    outcome_counts = Counter(event.status for event in outcomes.list())
    reflection_counts = reflections.counts()
    extraction_counts = reflections.extraction_counts(
        user_messages=message_counts["user"]
    )
    memory_counts = Counter(memory.status for memory in memories.list())
    print("겨울이가 현재 가지고 있는 정보", file=stdout)
    print(
        f"- 대화 원문: {len(messages)}개 "
        f"(천우 {message_counts['user']}, 겨울이 {message_counts['assistant']})",
        file=stdout,
    )
    print(
        f"- 대화 이해: 완료 {turn_counts['completed']}, "
        f"분석 대기 {turn_counts['pending']}, 실패 {turn_counts['failed']}",
        file=stdout,
    )
    print(
        f"- 답변 결과 평가: 완료 {outcome_counts['completed']}, "
        f"분석 대기 {outcome_counts['pending']}, 실패 {outcome_counts['failed']}",
        file=stdout,
    )
    print(
        f"- 기억 정리: 완료 {extraction_counts['completed']}, "
        f"분석 대기 {extraction_counts['pending']}, "
        f"재시도 필요 {extraction_counts['failed']}",
        file=stdout,
    )
    print(
        f"- 천우가 볼 기억 후보: {reflection_counts['candidate']}",
        file=stdout,
    )
    print(
        f"- 검토 완료 후보: 기억 사용 {reflection_counts['active']}, "
        f"확인 보관 {reflection_counts['approved']}, "
        f"제외 {reflection_counts['rejected']}",
        file=stdout,
    )
    print(f"- 현재 사용 중인 장기 기억: {memory_counts['active']}", file=stdout)
    if extraction_counts["pending"]:
        print(
            "\n기억 정리 대기를 줄이려면: 별도 분석 모델 설정 후 ./winter reflect",
            file=stdout,
        )
    if turn_counts["pending"] or outcome_counts["pending"]:
        print(
            "대화 이해·답변 평가는 안전한 분석 runtime 확정 전까지 Shadow 대기 상태입니다.",
            file=stdout,
        )
    if reflection_counts["candidate"]:
        print("기억 후보를 확인하려면: ./winter memories", file=stdout)


def _print_candidate(
    candidate: ReflectionCandidate,
    index: int,
    total: int,
    stdout: TextIO,
) -> None:
    print("\n" + "=" * 64, file=stdout)
    print(f"기억 후보 {index}/{total}", file=stdout)
    print(f"종류: {_KIND_LABELS[candidate.kind]}", file=stdout)
    print(f"내용: {candidate.content}", file=stdout)
    print(f"유효 범위: {_SCOPE_LABELS[candidate.temporal_scope]}", file=stdout)
    print(f"판단 확신도: {candidate.confidence * 100:.0f}%", file=stdout)
    print("실제 대화 근거:", file=stdout)
    for evidence in candidate.evidence:
        print(f"- “{evidence.quote}”", file=stdout)
    if not candidate.can_activate_as_memory:
        print(
            "이 항목은 순간 경험·관계·개선 정보라 승인해도 일반 기억에는 넣지 않습니다.",
            file=stdout,
        )


def _ask_action(
    candidate: ReflectionCandidate, stdin: TextIO, stdout: TextIO
) -> str:
    if candidate.can_activate_as_memory:
        print("  1. 맞아, 기억해서 다음 대화부터 사용", file=stdout)
    else:
        print("  1. 맞아, 확인된 후보로 별도 보관", file=stdout)
    print("  2. 아니야, 기억하지 않기", file=stdout)
    print("  3. 내용을 고쳐서 승인", file=stdout)
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
        if answer == "1":
            return "approve"
        if answer == "2":
            return "reject"
        if answer == "3":
            return "edit"
        print("1~3 중 하나나 s, q를 입력해 주세요.", file=stdout)


def _ask_edit(stdin: TextIO, stdout: TextIO) -> str:
    while True:
        print("고친 내용> ", end="", file=stdout, flush=True)
        content = stdin.readline()
        if not content:
            raise _ReviewControl("quit")
        content = content.strip()
        if content:
            return content
        print("빈 내용은 저장할 수 없습니다.", file=stdout)
