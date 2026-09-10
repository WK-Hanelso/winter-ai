"""Interactive text CLI for the shared CompanionCore."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import fields
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import TextIO

from companion.adapters.belief_reflection_interpreter import (
    StructuredChatBeliefProposalInterpreter,
    StructuredChatBeliefSafetyReviewer,
)
from companion.adapters.day_reflection_interpreter import (
    StructuredChatDayReflectionInterpreter,
)
from companion.adapters.fake import (
    AdapterUnavailableError,
)
from companion.adapters.outcome_interpreter import StructuredChatOutcomeInterpreter
from companion.adapters.reflection_interpreter import (
    StructuredChatReflectionInterpreter,
)
from companion.adapters.sqlite_repository import (
    ConversationRepositoryError,
)
from companion.adapters.turn_interpreter import StructuredChatTurnInterpreter
from companion.application.bootstrap import (
    TextRuntimeConfig,
    TextRuntimeError,
    build_text_resources,
    build_text_runtime,
)
from companion.application.bootstrap import (
    build_chat_model as create_chat_model,
)
from companion.application.bootstrap import (
    build_conversation_repository as create_conversation_repository,
)
from companion.belief_reflection import (
    BeliefEvidenceWindowBuilder,
    BeliefReflectionError,
    BeliefReflectionRepositoryError,
    ShadowBeliefReflectionBatchService,
    ShadowBeliefReflectionService,
    SqliteBeliefProposalRepository,
)
from companion.beliefs import (
    BeliefRepositoryError,
    SqliteBeliefRepository,
)
from companion.contracts import ConversationMessage
from companion.core import CompanionCore
from companion.day_reflection import (
    DayReflectionError,
    DayReflectionRepositoryError,
    DayReflectionService,
    SqliteConversationDayReader,
    SqliteDayReflectionRepository,
)
from companion.dialogue.policy import POLICY_NAMES
from companion.lived_continuity import (
    render_lived_continuity,
    summarize_lived_continuity,
)
from companion.memory import MemoryRepositoryError, SqliteMemoryRepository
from companion.open_loops import (
    OpenLoopRepositoryError,
    SqliteOpenLoopRepository,
)
from companion.outcome import (
    FakeOutcomeInterpreter,
    OutcomeRepositoryError,
    ShadowOutcomeService,
    SqliteOutcomeRepository,
)
from companion.outcome_review import build_audit, print_audit, run_blind_review
from companion.owned_work import (
    OwnedWorkUpdate,
)
from companion.ports import ChatModel, ConversationRepository
from companion.reflection import (
    ConversationHistoryBackfill,
    ConversationReflectionService,
    FakeReflectionInterpreter,
    ReflectionRepositoryError,
    SqliteConversationHistoryReader,
    SqliteReflectionRepository,
)
from companion.reflection_review import (
    print_companion_overview,
    run_memory_candidate_review,
)
from companion.turn_understanding import (
    FakeTurnInterpreter,
    ShadowAnalysisService,
    SqliteTurnUnderstandingRepository,
    TurnUnderstandingRepositoryError,
)
from companion.verbal_style import (
    ALLOWED_PROFILES,
    DEFAULT_PROFILE,
)
from companion.winter_events import (
    SqliteWinterEventRepository,
    SystemClock,
    WinterEventError,
    WinterEventRepositoryError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dialogue-policy", choices=POLICY_NAMES, default="legacy")
    parser.add_argument("--codex-worker", action="store_true")
    parser.add_argument("--winter-work-canary", action="store_true")
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--codex-model", default="gpt-5.4")
    parser.add_argument(
        "--backend",
        choices=("fake", "local"),
        default="fake",
        help="selected ChatModel backend; fake is explicit default for offline development",
    )
    parser.add_argument(
        "--model-url",
        default="http://llm:8080",
        help="local llama.cpp server URL when --backend local is selected",
    )
    parser.add_argument("--winter-event-db", type=Path)
    parser.add_argument("--belief-reflection-db", type=Path)
    parser.add_argument("--day-reflection-db", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prompt", help="run one turn instead of an interactive session")
    actions.add_argument(
        "--show-history",
        action="store_true",
        help="print stored conversation messages and exit",
    )
    actions.add_argument("--show-identity", action="store_true")
    actions.add_argument("--list-memories", action="store_true")
    actions.add_argument(
        "--list-current-state-history",
        action="store_true",
        help="show current and previous user-state values as a timeline",
    )
    actions.add_argument("--memory-add", help="store explicit user memory as a candidate")
    actions.add_argument("--memory-approve", metavar="ID")
    actions.add_argument("--memory-activate", metavar="ID")
    actions.add_argument("--memory-deprecate", metavar="ID")
    actions.add_argument("--memory-replace", nargs=2, metavar=("ID", "CONTENT"))
    actions.add_argument(
        "--memory-delete",
        metavar="ID",
        help="permanently delete an unreferenced memory",
    )
    actions.add_argument("--list-beliefs", action="store_true")
    actions.add_argument(
        "--belief-add",
        nargs=2,
        metavar=("SUBJECT", "STANCE"),
        help="add an evidence-backed Winter judgement as a review candidate",
    )
    actions.add_argument("--belief-activate", metavar="ID")
    actions.add_argument("--belief-reject", metavar="ID")
    actions.add_argument(
        "--belief-revise",
        nargs=2,
        metavar=("ID", "STANCE"),
        help="create a candidate revision of an active belief",
    )
    actions.add_argument("--list-open-loops", action="store_true")
    actions.add_argument("--resolve-open-loop", metavar="ID")
    actions.add_argument("--dismiss-open-loop", metavar="ID")
    actions.add_argument(
        "--list-turn-understanding",
        action="store_true",
        help="list raw and completed Shadow turn analysis events",
    )
    actions.add_argument(
        "--analyze-pending-turns",
        action="store_true",
        help="analyze queued turns using the explicitly selected backend",
    )
    actions.add_argument("--list-outcomes", action="store_true")
    actions.add_argument(
        "--analyze-pending-outcomes",
        action="store_true",
        help="evaluate queued next-turn outcomes with the selected backend",
    )
    actions.add_argument(
        "--review-outcomes",
        action="store_true",
        help="blindly review completed next-turn outcomes before seeing model labels",
    )
    actions.add_argument(
        "--outcome-audit",
        action="store_true",
        help="show human review progress and model agreement",
    )
    actions.add_argument(
        "--reflect-history",
        action="store_true",
        help="recover and analyze historical conversations into review candidates",
    )
    actions.add_argument(
        "--review-memory-candidates",
        action="store_true",
        help="review evidence-backed conversational memory candidates",
    )
    actions.add_argument(
        "--companion-overview",
        action="store_true",
        help="show raw conversations, pending analysis and usable memory counts",
    )
    actions.add_argument(
        "--lived-continuity",
        action="store_true",
        help="show privacy-minimal evidence that makes up Winter's current day",
    )
    actions.add_argument(
        "--analyze-belief-reflection",
        action="store_true",
        help="analyze new evidence windows into Shadow-only Winter viewpoints",
    )
    actions.add_argument(
        "--list-belief-reflection",
        action="store_true",
        help="list Shadow-only Winter viewpoint observations",
    )
    actions.add_argument(
        "--close-winter-day",
        action="store_true",
        help="close one completed Korean day into a grounded Winter reflection",
    )
    actions.add_argument(
        "--list-winter-days",
        action="store_true",
        help="list stored Winter day reflections without loading a model",
    )
    parser.add_argument("--identity-path", type=Path)
    parser.add_argument("--memory-db", type=Path)
    parser.add_argument("--improvement-db", type=Path)
    parser.add_argument("--memory-kind", default="semantic")
    parser.add_argument("--current-state-topic")
    parser.add_argument("--belief-db", type=Path)
    parser.add_argument("--dialogue-state-db", type=Path)
    parser.add_argument("--turn-understanding-db", type=Path)
    parser.add_argument("--outcome-db", type=Path)
    parser.add_argument("--reflection-db", type=Path)
    parser.add_argument(
        "--winter-day",
        help="Korean local date to inspect with --lived-continuity (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--analysis-limit",
        type=_positive_int,
        default=10,
        help="maximum pending Shadow turns analyzed in one run (default: 10)",
    )
    parser.add_argument(
        "--review-limit",
        type=_positive_int,
        default=5,
        help="maximum conversations shown in one blind review (default: 5)",
    )
    parser.add_argument(
        "--reviewer",
        default="cheonu",
        help="local reviewer name used to keep independent audit labels",
    )
    parser.add_argument(
        "--memory-review-limit",
        type=_positive_int,
        default=10,
        help="maximum recovered memory candidates shown at once (default: 10)",
    )
    parser.add_argument(
        "--belief-rationale",
        help="short reason for --belief-add or --belief-revise",
    )
    parser.add_argument("--belief-confidence", type=float, default=0.5)
    parser.add_argument(
        "--belief-evidence",
        action="append",
        default=[],
        help="evidence reference; repeat for more than one",
    )
    parser.add_argument(
        "--verbal-style",
        choices=ALLOWED_PROFILES,
        default=DEFAULT_PROFILE,
        help="wording policy profile; reference_broadcast is measured from the Reference",
    )
    parser.add_argument(
        "--conversation-db",
        type=Path,
        help="explicit local SQLite path for persistent conversation history",
    )
    parser.add_argument(
        "--owned-work-db",
        type=Path,
        help="local SQLite path for pending Winter-owned work and results",
    )
    parser.add_argument(
        "--context-max-messages",
        type=_positive_int,
        default=12,
        help="maximum recent messages included in a local chat request (default: 12)",
    )
    parser.add_argument(
        "--context-max-characters",
        type=_positive_int,
        default=4000,
        help="maximum characters included in a local chat request (default: 4000)",
    )
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def text_runtime_config(args: argparse.Namespace) -> TextRuntimeConfig:
    defaults = TextRuntimeConfig()
    values = {
        item.name: getattr(args, item.name, getattr(defaults, item.name))
        for item in fields(TextRuntimeConfig)
        if item.name != "interactive"
    }
    return TextRuntimeConfig(**values, interactive=getattr(args, "prompt", None) is None)


def build_chat_model(args: argparse.Namespace) -> ChatModel:
    return create_chat_model(text_runtime_config(args))


def build_conversation_repository(args: argparse.Namespace) -> ConversationRepository:
    return create_conversation_repository(text_runtime_config(args))


def run(
    args: argparse.Namespace,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    config = text_runtime_config(args)
    try:
        resources = build_text_resources(config)
    except TextRuntimeError as error:
        print(str(error), file=stdout)
        return 1
    repository = resources.repository
    turn_understanding_repository = resources.turn_understanding_repository
    outcome_repository = resources.outcome_repository
    reflection_repository = resources.reflection_repository
    identity = resources.identity
    memory_repository = resources.memory_repository
    belief_repository = resources.belief_repository
    winter_event_repository = resources.winter_event_repository
    day_reflection_repository = resources.day_reflection_repository
    if getattr(args, "show_identity", False):
        print(
            identity.system_message() if identity else "No identity path selected.",
            file=stdout,
        )
        return 0
    reflection_actions = (
        "reflect_history",
        "review_memory_candidates",
        "companion_overview",
    )
    if any(getattr(args, name, False) for name in reflection_actions):
        return _handle_reflection(
            args,
            reflection_repository,
            memory_repository,
            turn_understanding_repository,
            outcome_repository,
            stdin,
            stdout,
        )
    memory_actions = (
        "list_memories",
        "list_current_state_history",
        "memory_add",
        "memory_approve",
        "memory_activate",
        "memory_deprecate",
        "memory_replace",
        "memory_delete",
    )
    if any(getattr(args, name, None) for name in memory_actions):
        return _handle_memory(args, stdout)
    belief_actions = (
        "list_beliefs",
        "belief_add",
        "belief_activate",
        "belief_reject",
        "belief_revise",
    )
    if any(getattr(args, name, None) for name in belief_actions):
        return _handle_belief(args, stdout)
    open_loop_actions = (
        "list_open_loops",
        "resolve_open_loop",
        "dismiss_open_loop",
    )
    if any(getattr(args, name, None) for name in open_loop_actions):
        return _handle_open_loop(args, stdout)
    if getattr(args, "list_turn_understanding", False) or getattr(
        args, "analyze_pending_turns", False
    ):
        return _handle_turn_understanding(
            args, turn_understanding_repository, stdout
        )
    outcome_actions = (
        "list_outcomes",
        "analyze_pending_outcomes",
        "review_outcomes",
        "outcome_audit",
    )
    if any(getattr(args, name, False) for name in outcome_actions):
        return _handle_outcomes(
            args,
            outcome_repository,
            turn_understanding_repository,
            winter_event_repository,
            stdin,
            stdout,
        )
    belief_reflection_actions = (
        "analyze_belief_reflection",
        "list_belief_reflection",
    )
    if any(getattr(args, name, False) for name in belief_reflection_actions):
        return _handle_belief_reflection(
            args,
            outcome_repository,
            winter_event_repository,
            stdout,
        )
    if getattr(args, "close_winter_day", False) or getattr(
        args, "list_winter_days", False
    ):
        return _handle_day_reflection(
            args,
            day_reflection_repository,
            winter_event_repository,
            stdout,
        )
    if getattr(args, "show_history", False):
        return _show_history(repository, stdout)
    if getattr(args, "lived_continuity", False):
        if winter_event_repository is None or belief_repository is None:
            print(
                "Winter lived continuity requires event and belief storage.",
                file=stdout,
            )
            return 1
        local_date = getattr(args, "winter_day", None)
        if not local_date:
            local_date = SystemClock().now().date().isoformat()
        try:
            proposal_path = getattr(args, "belief_reflection_db", None)
            proposal_repository = (
                SqliteBeliefProposalRepository(proposal_path)
                if proposal_path
                else None
            )
            summary = summarize_lived_continuity(
                winter_event_repository,
                belief_repository,
                local_date,
                proposal_repository,
            )
        except (
            BeliefReflectionRepositoryError,
            WinterEventError,
            WinterEventRepositoryError,
        ) as error:
            print(f"Winter lived continuity unavailable: {error}", file=stdout)
            return 1
        print(render_lived_continuity(summary), file=stdout)
        return 0
    def notify_owned_work(update: OwnedWorkUpdate) -> None:
        repository.append(ConversationMessage("assistant", update.text))
        print(f"\nCompanion> {update.text}", file=stdout, flush=True)

    try:
        runtime = build_text_runtime(
            config, resources=resources, completion_sink=notify_owned_work,
        )
    except TextRuntimeError as error:
        print(str(error), file=stdout)
        return 1
    if config.dialogue_policy != "legacy":
        print(f"Experimental dialogue policy: {config.dialogue_policy}", file=stdout)
    core = runtime.core
    try:
        if args.prompt is not None:
            return _run_turn(
                core,
                args.prompt,
                stdout,
                streaming=getattr(args, "winter_work_canary", False),
            )

        print("winter-ai CLI. 종료하려면 /exit 를 입력하세요.", file=stdout)
        while True:
            print("You> ", end="", file=stdout, flush=True)
            text = stdin.readline()
            if not text:
                return 0
            text = text.strip()
            if text == "/exit":
                return 0
            if not text:
                continue
            if _run_turn(
                core,
                text,
                stdout,
                streaming=getattr(args, "winter_work_canary", False),
            ):
                return 1

    finally:
        runtime.close()


def _run_turn(
    core: CompanionCore,
    text: str,
    stdout: TextIO,
    *,
    streaming: bool = False,
) -> int:
    try:
        if streaming:
            response = core.respond_to_text_streaming(
                text,
                lambda sentence: print(f"Companion> {sentence}", file=stdout),
            )
        else:
            response = core.respond_to_text(text)
    except AdapterUnavailableError as error:
        print(f"Companion unavailable: {error}", file=stdout)
        return 1
    if not streaming:
        print(f"Companion> {response.text}", file=stdout)
    for memory_id in response.memory_candidate_ids:
        print(f"Memory {memory_id} is active.", file=stdout)
    for memory_id in response.automatic_memory_ids:
        print(f"Memory {memory_id} was captured from a direct statement.", file=stdout)
    return 0


def _show_history(repository: ConversationRepository, stdout: TextIO) -> int:
    try:
        messages = repository.list_messages()
    except ConversationRepositoryError as error:
        print(f"Conversation storage unavailable: {error}", file=stdout)
        return 1
    for message in messages:
        print(f"{message.role}> {message.content}", file=stdout)
    return 0


def _handle_belief_reflection(
    args: argparse.Namespace,
    outcomes: SqliteOutcomeRepository | None,
    events: SqliteWinterEventRepository | None,
    stdout: TextIO,
) -> int:
    proposal_path = getattr(args, "belief_reflection_db", None)
    if proposal_path is None:
        print("Winter viewpoint Shadow requires --belief-reflection-db.", file=stdout)
        return 1
    try:
        observations = SqliteBeliefProposalRepository(proposal_path)
        if getattr(args, "list_belief_reflection", False):
            stored = observations.list()
            if not stored:
                print("아직 관찰된 겨울이 관점 후보가 없습니다.", file=stdout)
                return 0
            for observation in stored:
                proposal = observation.proposal
                if proposal is None:
                    print(
                        f"{observation.created_at} 후보 없음 "
                        f"({', '.join(observation.reason_codes)})",
                        file=stdout,
                    )
                    continue
                status = "통과(Shadow)" if observation.safe else "차단"
                reasons = ", ".join(observation.reason_codes) or "없음"
                print(
                    f"{observation.created_at} {status} {proposal.subject}: "
                    f"{proposal.stance} | confidence={proposal.confidence:.2f} "
                    f"| 이유={reasons}",
                    file=stdout,
                )
            return 0
        if args.backend != "local":
            print(
                "관점 분석은 실제 local 분석 모델을 명시해야 합니다. "
                "fake 결과는 실사용 DB에 기록하지 않습니다.",
                file=stdout,
            )
            return 2
        if outcomes is None or events is None:
            print(
                "Winter viewpoint Shadow requires outcome and Winter event storage.",
                file=stdout,
            )
            return 1
        chat_model = build_chat_model(args)
        reflection = ShadowBeliefReflectionService(
            StructuredChatBeliefProposalInterpreter(chat_model),
            StructuredChatBeliefSafetyReviewer(chat_model),
            observations,
            events,
        )
        result = ShadowBeliefReflectionBatchService(
            BeliefEvidenceWindowBuilder(outcomes, events),
            reflection,
            observations,
        ).run(limit=getattr(args, "analysis_limit", 10))
        print(
            "겨울이 관점 Shadow 분석: "
            f"근거 묶음 {result.windows}, 새 분석 {result.analyzed}, "
            f"기존 결과 {result.already_analyzed}, 다음 실행 대기 {result.deferred}",
            file=stdout,
        )
        print(
            f"결과: 안전 통과 {result.safe}, 차단 {result.blocked}, "
            f"후보 없음 {result.no_proposal}",
            file=stdout,
        )
        print(
            f"근거 연결: 완료 결과 {result.completed_outcomes}, "
            f"연결 {result.linked_outcomes}, 미연결 {result.unlinked_outcomes}, "
            f"제외 {result.excluded_outcomes}",
            file=stdout,
        )
        print(
            "통과 결과도 아직 대화·기억·Identity에는 영향을 주지 않습니다.",
            file=stdout,
        )
        return 0
    except (
        BeliefReflectionError,
        BeliefReflectionRepositoryError,
        OutcomeRepositoryError,
        WinterEventRepositoryError,
    ) as error:
        print(f"Winter viewpoint Shadow unavailable: {error}", file=stdout)
        return 1


def _handle_day_reflection(
    args: argparse.Namespace,
    repository: SqliteDayReflectionRepository | None,
    events: SqliteWinterEventRepository | None,
    stdout: TextIO,
) -> int:
    if repository is None:
        print("Winter day reflection requires --day-reflection-db.", file=stdout)
        return 1
    try:
        if getattr(args, "list_winter_days", False):
            reflections = repository.list()
            if not reflections:
                print("아직 닫힌 겨울이의 하루가 없습니다.", file=stdout)
                return 0
            labels = {
                "shared_moment": "함께한 일",
                "cheonu_change": "천우 변화 관찰",
                "open_thread": "이어갈 이야기",
                "winter_reflection": "겨울이의 생각",
                "next_cue": "다음 대화 단서",
            }
            for reflection in reflections:
                print(f"겨울이의 {reflection.local_date}", file=stdout)
                for point in reflection.points:
                    print(f"- {labels[point.kind]}: {point.content}", file=stdout)
            return 0
        if args.backend != "local":
            print(
                "하루 정리는 실제 local 대화 모델을 사용해야 합니다. "
                "fake 결과는 실사용 DB에 기록하지 않습니다.",
                file=stdout,
            )
            return 2
        conversation_path = getattr(args, "conversation_db", None)
        if conversation_path is None or events is None:
            print(
                "Winter day reflection requires conversation and event storage.",
                file=stdout,
            )
            return 1
        now = SystemClock().now()
        local_date = getattr(args, "winter_day", None)
        if not local_date:
            local_date = (now.date() - timedelta(days=1)).isoformat()
        result = DayReflectionService(
            SqliteConversationDayReader(conversation_path),
            events,
            StructuredChatDayReflectionInterpreter(build_chat_model(args)),
            repository,
        ).close(local_date, now=now)
        state = "새로 닫음" if result.created else "이미 닫힘"
        print(f"겨울이의 {local_date}: {state}", file=stdout)
        for point in result.reflection.points:
            print(f"- {point.kind}: {point.content}", file=stdout)
        print("다음 CLI·Web·Voice 대화부터 이 회고를 함께 봅니다.", file=stdout)
        return 0
    except (DayReflectionError, DayReflectionRepositoryError) as error:
        print(f"Winter day reflection unavailable: {error}", file=stdout)
        return 1


def _handle_memory(args: argparse.Namespace, stdout: TextIO) -> int:
    if not args.memory_db:
        print("Memory unavailable: --memory-db is required.", file=stdout)
        return 1
    try:
        repo = SqliteMemoryRepository(args.memory_db)
        if getattr(args, "list_current_state_history", False):
            history = repo.list_current_state_history(
                topic=getattr(args, "current_state_topic", None)
            )
            if not history:
                print("저장된 현재 상태 History가 없습니다.", file=stdout)
                return 0
            print("천우의 현재 상태 History", file=stdout)
            for memory in history:
                topic = memory.kind.removeprefix("current_state:")
                valid_to = "현재" if memory.status == "active" else memory.updated_at
                print(
                    f"- {topic} | {memory.status} | "
                    f"{memory.created_at} ~ {valid_to}: {memory.content}",
                    file=stdout,
                )
            return 0
        if getattr(args, "memory_add", None):
            memory = repo.add_candidate(kind=args.memory_kind, content=args.memory_add)
        elif getattr(args, "memory_approve", None):
            memory = repo.transition(args.memory_approve, "approved")
        elif getattr(args, "memory_activate", None):
            memory = repo.transition(args.memory_activate, "active")
        elif getattr(args, "memory_deprecate", None):
            memory = repo.transition(args.memory_deprecate, "deprecated")
        elif getattr(args, "memory_replace", None):
            memory = repo.replace(args.memory_replace[0], args.memory_replace[1])
        elif getattr(args, "memory_delete", None):
            deleted = repo.delete(args.memory_delete)
            print(f"Memory {deleted.id} was permanently deleted.", file=stdout)
            return 0
        else:
            for memory in repo.list():
                print(
                    f"{memory.id} {memory.status} {memory.kind} "
                    f"supersedes={memory.supersedes}: {memory.content}",
                    file=stdout,
                )
            return 0
    except MemoryRepositoryError as error:
        print(f"Memory unavailable: {error}", file=stdout)
        return 1
    print(f"Memory {memory.id} is {memory.status}.", file=stdout)
    return 0


def _handle_belief(args: argparse.Namespace, stdout: TextIO) -> int:
    if not getattr(args, "belief_db", None):
        print("Belief storage unavailable: --belief-db is required.", file=stdout)
        return 1
    try:
        repo = SqliteBeliefRepository(args.belief_db)
        rationale = getattr(args, "belief_rationale", None) or ""
        confidence = getattr(args, "belief_confidence", 0.5)
        evidence = tuple(getattr(args, "belief_evidence", ()))
        if getattr(args, "belief_add", None):
            belief = repo.add_candidate(
                subject=args.belief_add[0],
                stance=args.belief_add[1],
                rationale=rationale,
                confidence=confidence,
                evidence=evidence,
            )
        elif getattr(args, "belief_activate", None):
            belief = repo.transition(args.belief_activate, "active")
        elif getattr(args, "belief_reject", None):
            belief = repo.transition(args.belief_reject, "rejected")
        elif getattr(args, "belief_revise", None):
            belief = repo.revise(
                args.belief_revise[0],
                stance=args.belief_revise[1],
                rationale=rationale,
                confidence=confidence,
                evidence=evidence,
            )
        else:
            for belief in repo.list():
                evidence_text = ",".join(belief.evidence)
                print(
                    f"{belief.id} {belief.status} subject={belief.subject} "
                    f"confidence={belief.confidence:.2f} supersedes={belief.supersedes}: "
                    f"{belief.stance} | {belief.rationale} | evidence={evidence_text}",
                    file=stdout,
                )
            return 0
    except BeliefRepositoryError as error:
        print(f"Belief storage unavailable: {error}", file=stdout)
        return 1
    print(f"Belief {belief.id} is {belief.status}.", file=stdout)
    return 0


def _handle_open_loop(args: argparse.Namespace, stdout: TextIO) -> int:
    if not getattr(args, "dialogue_state_db", None):
        print("Dialogue state unavailable: --dialogue-state-db is required.", file=stdout)
        return 1
    try:
        repository = SqliteOpenLoopRepository(args.dialogue_state_db)
        if getattr(args, "resolve_open_loop", None):
            loop = repository.transition(args.resolve_open_loop, "resolved")
            print(f"Open loop {loop.id} is resolved.", file=stdout)
            return 0
        if getattr(args, "dismiss_open_loop", None):
            loop = repository.transition(args.dismiss_open_loop, "dismissed")
            print(f"Open loop {loop.id} is dismissed.", file=stdout)
            return 0
        for loop in repository.list():
            print(f"{loop.id} {loop.status}: {loop.content}", file=stdout)
        return 0
    except OpenLoopRepositoryError as error:
        print(f"Dialogue state unavailable: {error}", file=stdout)
        return 1


def _handle_turn_understanding(
    args: argparse.Namespace,
    repository: SqliteTurnUnderstandingRepository | None,
    stdout: TextIO,
) -> int:
    if repository is None:
        print(
            "Turn understanding unavailable: --turn-understanding-db is required.",
            file=stdout,
        )
        return 1
    try:
        if getattr(args, "analyze_pending_turns", False):
            interpreter = (
                FakeTurnInterpreter()
                if args.backend == "fake"
                else StructuredChatTurnInterpreter(build_chat_model(args))
            )
            result = ShadowAnalysisService(repository, interpreter).process_pending(
                limit=getattr(args, "analysis_limit", 10)
            )
            print(
                "Shadow analysis: "
                f"processed={result.processed} completed={result.completed} "
                f"failed={result.failed} deferred={result.deferred}",
                file=stdout,
            )
            return 1 if result.failed else 0
        for event in repository.list():
            print(
                f"{event.id} {event.status} source={event.source}: "
                f"{event.request.user_text}",
                file=stdout,
            )
            if event.understanding is not None:
                print(
                    json.dumps(
                        event.understanding.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    file=stdout,
                )
            elif event.error is not None:
                print(f"error={event.error}", file=stdout)
        return 0
    except TurnUnderstandingRepositoryError as error:
        print(f"Turn understanding unavailable: {error}", file=stdout)
        return 1


def _handle_outcomes(
    args: argparse.Namespace,
    repository: SqliteOutcomeRepository | None,
    turn_repository: SqliteTurnUnderstandingRepository | None,
    winter_event_repository: SqliteWinterEventRepository | None,
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    if repository is None:
        print(
            "Outcome storage unavailable: --outcome-db is required.",
            file=stdout,
        )
        return 1
    try:
        reviewer = getattr(args, "reviewer", "cheonu")
        if getattr(args, "review_outcomes", False):
            return run_blind_review(
                repository,
                reviewer=reviewer,
                limit=getattr(args, "review_limit", 5),
                stdin=stdin,
                stdout=stdout,
            )
        if getattr(args, "outcome_audit", False):
            print_audit(build_audit(repository, reviewer=reviewer), stdout)
            return 0
        if getattr(args, "analyze_pending_outcomes", False):
            if turn_repository is None:
                print(
                    "Outcome analysis unavailable: --turn-understanding-db is required.",
                    file=stdout,
                )
                return 1
            interpreter = (
                FakeOutcomeInterpreter()
                if args.backend == "fake"
                else StructuredChatOutcomeInterpreter(build_chat_model(args))
            )
            result = ShadowOutcomeService(
                repository,
                turn_repository,
                interpreter,
                winter_event_repository,
                SystemClock() if winter_event_repository is not None else None,
            ).process_pending(limit=getattr(args, "analysis_limit", 10))
            print(
                "Shadow outcomes: "
                f"processed={result.processed} completed={result.completed} "
                f"failed={result.failed} deferred={result.deferred}",
                file=stdout,
            )
            return 1 if result.failed else 0
        for event in repository.list():
            outcome = event.assessment.outcome if event.assessment is not None else "-"
            print(
                f"{event.id} {event.status} outcome={outcome} "
                f"prior={event.prior_turn_id} next={event.next_turn_id}: "
                f"{event.next_user_text}",
                file=stdout,
            )
            if event.assessment is not None:
                print(
                    json.dumps(
                        event.assessment.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    file=stdout,
                )
            elif event.error is not None:
                print(f"error={event.error}", file=stdout)
        return 0
    except (OutcomeRepositoryError, TurnUnderstandingRepositoryError) as error:
        print(f"Outcome storage unavailable: {error}", file=stdout)
        return 1


def _handle_reflection(
    args: argparse.Namespace,
    reflections: SqliteReflectionRepository | None,
    memories: SqliteMemoryRepository | None,
    turns: SqliteTurnUnderstandingRepository | None,
    outcomes: SqliteOutcomeRepository | None,
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    conversation_path = getattr(args, "conversation_db", None)
    if (
        reflections is None
        or memories is None
        or turns is None
        or outcomes is None
        or conversation_path is None
    ):
        print(
            "Conversation recovery unavailable: conversation, memory, turn, "
            "outcome and reflection databases are required.",
            file=stdout,
        )
        return 1
    history = SqliteConversationHistoryReader(conversation_path)
    try:
        if getattr(args, "review_memory_candidates", False):
            return run_memory_candidate_review(
                reflections,
                memories,
                limit=getattr(args, "memory_review_limit", 10),
                stdin=stdin,
                stdout=stdout,
            )
        if getattr(args, "companion_overview", False):
            print_companion_overview(
                history,
                turns,
                outcomes,
                reflections,
                memories,
                stdout,
            )
            return 0
        backfill = ConversationHistoryBackfill(history, turns).run()
        reflection_interpreter = (
            FakeReflectionInterpreter()
            if args.backend == "fake"
            else StructuredChatReflectionInterpreter(build_chat_model(args))
        )
        reflected = ConversationReflectionService(
            history,
            reflections,
            reflection_interpreter,
            memories,
        ).process_pending(
            limit=getattr(args, "analysis_limit", 10),
            progress=lambda completed, total: print(
                f"기억 정리 진행: {completed}/{total}",
                file=stdout,
                flush=True,
            ),
        )
        harvested = reflections.harvest(turns.list(status="completed"))
        pending_turns = len(turns.list(status="pending"))
        pending_outcomes = len(outcomes.list(status="pending"))
        candidates = len(reflections.list(status="candidate"))
        print(
            f"대화 원문 연결: 천우 발화 {backfill.user_messages}, "
            f"기존 연결 {backfill.already_linked + backfill.linked_existing}, "
            f"새 분석 등록 {backfill.enqueued}",
            file=stdout,
        )
        print(
            f"이번 기억 정리: 완료 {reflected.completed}, 실패 {reflected.failed}, "
            f"연결 보류 {reflected.deferred}, 후보 제외 경고 {reflected.warnings}",
            file=stdout,
        )
        print(
            f"이번 후보 발견: 새 후보 "
            f"{reflected.created_candidates + harvested.created_candidates}, "
            f"근거 추가 {reflected.updated_candidates + harvested.updated_candidates}",
            file=stdout,
        )
        print(
            f"현재 남은 작업: 대화 분석 {pending_turns}, "
            f"답변 평가 {pending_outcomes}, 천우 검토 후보 {candidates}",
            file=stdout,
        )
        if candidates:
            print("기억 후보 확인: ./winter memories", file=stdout)
        return 1 if reflected.failed else 0
    except (
        ReflectionRepositoryError,
        MemoryRepositoryError,
        TurnUnderstandingRepositoryError,
        OutcomeRepositoryError,
    ) as error:
        print(f"Conversation recovery unavailable: {error}", file=stdout)
        return 1
def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
