"""Text composition root. No CLI I/O, speech devices, or external model startup."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from companion.adapters.codex_gateway_worker import CodexTypedProjectWorker
from companion.adapters.codex_worker import CodexReadOnlyWorker
from companion.adapters.fake import (
    FakeChatModel,
    InMemoryConversationRepository,
)
from companion.adapters.llama_cpp import LlamaCppHttpChatModel
from companion.adapters.sqlite_repository import (
    ConversationRepositoryError,
    SqliteConversationRepository,
)
from companion.adapters.work_orchestration import (
    BoundedProjectInspectionExecutive,
)
from companion.beliefs import (
    ActiveBeliefRetriever,
    BeliefRepositoryError,
    SqliteBeliefRepository,
)
from companion.capabilities import (
    CapabilityRegistryError,
    PersistentSelfModel,
    SqliteCapabilityRegistry,
)
from companion.context import ConversationContextBuilder
from companion.core import CompanionCore
from companion.day_reflection import (
    ActiveDayReflectionRetriever,
    DayReflectionRepositoryError,
    SqliteDayReflectionRepository,
)
from companion.dialogue.policy import DialoguePolicyError, build_dialogue_policy
from companion.identity import CompanionIdentity, IdentityRepositoryError, JsonIdentityRepository
from companion.improvements import (
    ImprovementLedgerError,
    PersistentImprovementSnapshot,
    SqliteImprovementLedger,
)
from companion.memory import ActiveMemoryRetriever, MemoryRepositoryError, SqliteMemoryRepository
from companion.model_priority import ForegroundPriorityChatScheduler
from companion.open_loops import (
    ActiveOpenLoopRetriever,
    OpenLoopRepositoryError,
    SqliteOpenLoopRepository,
)
from companion.outcome import (
    OutcomeRepositoryError,
    SqliteOutcomeRepository,
)
from companion.owned_work import (
    AsyncOwnedWorkCoordinator,
    OwnedWorkUpdate,
    WinterOwnedProjectCanary,
)
from companion.owned_work_repository import (
    OwnedWorkRepositoryError,
    SqliteOwnedWorkRepository,
)
from companion.ports import ChatModel, ConversationRepository
from companion.reflection import (
    ReflectionRepositoryError,
    SqliteReflectionRepository,
)
from companion.turn_understanding import (
    SqliteTurnUnderstandingRepository,
    TurnUnderstandingRepositoryError,
)
from companion.verbal_style import (
    DEFAULT_PROFILE,
    VerbalStyleError,
    VerbalStylePlanner,
    VerbalStyleProfile,
    load_verbal_style,
)
from companion.winter_events import (
    SqliteWinterEventRepository,
    SystemClock,
    WinterEventRepositoryError,
)


class TextRuntimeError(RuntimeError):
    """An explicitly selected runtime resource could not be initialized."""


@dataclass(frozen=True)
class TextRuntimeConfig:
    """Python configuration shared by text entrypoints; CLI parsing stays outside."""

    backend: str = "local"
    model_url: str = "http://127.0.0.1:18080"
    conversation_db: Path | None = None
    turn_understanding_db: Path | None = None
    outcome_db: Path | None = None
    reflection_db: Path | None = None
    identity_path: Path | None = None
    memory_db: Path | None = None
    capability_db: Path | None = None
    improvement_db: Path | None = None
    belief_db: Path | None = None
    winter_event_db: Path | None = None
    day_reflection_db: Path | None = None
    dialogue_state_db: Path | None = None
    owned_work_db: Path | None = None
    verbal_style: str = DEFAULT_PROFILE
    dialogue_policy: str = "legacy"
    context_max_messages: int = 12
    context_max_characters: int = 4000
    codex_worker: bool = False
    winter_work_canary: bool = False
    codex_executable: str = "codex"
    codex_model: str = "gpt-5.4"
    interactive: bool = True
    project_root: Path = field(default_factory=Path.cwd)


@dataclass(frozen=True)
class TextResources:
    """Existing stores/policies, initialized without constructing a chat model."""

    repository: ConversationRepository
    turn_understanding_repository: SqliteTurnUnderstandingRepository | None
    outcome_repository: SqliteOutcomeRepository | None
    reflection_repository: SqliteReflectionRepository | None
    identity: CompanionIdentity | None
    memory_repository: SqliteMemoryRepository | None
    memory_retriever: ActiveMemoryRetriever | None
    self_model_provider: PersistentSelfModel | None
    improvement_snapshot_provider: PersistentImprovementSnapshot | None
    belief_repository: SqliteBeliefRepository | None
    belief_retriever: ActiveBeliefRetriever | None
    winter_event_repository: SqliteWinterEventRepository | None
    day_reflection_repository: SqliteDayReflectionRepository | None
    day_reflection_retriever: ActiveDayReflectionRetriever | None
    open_loop_repository: SqliteOpenLoopRepository | None
    open_loop_retriever: ActiveOpenLoopRetriever | None
    style_profile: VerbalStyleProfile


@dataclass(frozen=True)
class TextRuntime:
    core: CompanionCore
    resources: TextResources
    task_inbox: AsyncOwnedWorkCoordinator | None = None

    def close(self, timeout: float = 0.0) -> bool:
        """Observe/drain pending work within a bound, never cancel durable jobs.

        Stores open connections per operation; the HTTP model owns no server.
        CLI exit remains nonblocking. False means a daemon job is still running;
        persistent jobs/inbox retain their existing restart recovery semantics.
        This does not implement worker cancellation or thread preemption.
        """
        return self.task_inbox is None or self.task_inbox.wait_for_idle(timeout)


def build_chat_model(config: TextRuntimeConfig) -> ChatModel:
    if config.backend == "fake":
        return FakeChatModel()
    if config.backend != "local":
        raise TextRuntimeError(f"Unknown chat backend: {config.backend}")
    return LlamaCppHttpChatModel(base_url=config.model_url)


def build_conversation_repository(config: TextRuntimeConfig) -> ConversationRepository:
    database_path = config.conversation_db
    if database_path is None:
        return InMemoryConversationRepository()
    return SqliteConversationRepository(database_path)


def build_text_resources(config: TextRuntimeConfig) -> TextResources:
    try:
        repository = build_conversation_repository(config)
    except ConversationRepositoryError as error:
        raise TextRuntimeError(f"Conversation storage unavailable: {error}") from error
    try:
        turn_understanding_path = config.turn_understanding_db
        turn_understanding_repository = (
            SqliteTurnUnderstandingRepository(turn_understanding_path)
            if turn_understanding_path
            else None
        )
    except TurnUnderstandingRepositoryError as error:
        raise TextRuntimeError(f"Turn understanding unavailable: {error}") from error
    try:
        outcome_path = config.outcome_db
        outcome_repository = (
            SqliteOutcomeRepository(outcome_path) if outcome_path else None
        )
    except OutcomeRepositoryError as error:
        raise TextRuntimeError(f"Outcome storage unavailable: {error}") from error
    try:
        reflection_path = config.reflection_db
        reflection_repository = (
            SqliteReflectionRepository(reflection_path) if reflection_path else None
        )
    except ReflectionRepositoryError as error:
        raise TextRuntimeError(f"Reflection storage unavailable: {error}") from error
    try:
        identity_path = config.identity_path
        identity = JsonIdentityRepository(identity_path).load() if identity_path else None
    except IdentityRepositoryError as error:
        raise TextRuntimeError(f"Companion identity unavailable: {error}") from error
    try:
        memory_path = config.memory_db
        memory_repository = SqliteMemoryRepository(memory_path) if memory_path else None
        memory_retriever = ActiveMemoryRetriever(memory_repository) if memory_repository else None
    except MemoryRepositoryError as error:
        raise TextRuntimeError(f"Memory unavailable: {error}") from error
    try:
        capability_path = config.capability_db
        capability_registry = (
            SqliteCapabilityRegistry(capability_path) if capability_path else None
        )
        self_model_provider = (
            PersistentSelfModel(
                identity,
                capability_registry,
                runtime_capabilities=("conversation", "memory"),
            )
            if identity is not None and capability_registry is not None
            else None
        )
    except CapabilityRegistryError as error:
        raise TextRuntimeError(f"Capability registry unavailable: {error}") from error
    try:
        improvement_path = config.improvement_db
        improvement_snapshot_provider = (
            PersistentImprovementSnapshot(SqliteImprovementLedger(improvement_path))
            if improvement_path
            else None
        )
    except ImprovementLedgerError as error:
        raise TextRuntimeError(f"Improvement ledger unavailable: {error}") from error
    try:
        belief_path = config.belief_db
        belief_repository = SqliteBeliefRepository(belief_path) if belief_path else None
        belief_retriever = (
            ActiveBeliefRetriever(belief_repository) if belief_repository else None
        )
    except BeliefRepositoryError as error:
        raise TextRuntimeError(f"Belief storage unavailable: {error}") from error
    try:
        winter_event_path = config.winter_event_db
        winter_event_repository = (
            SqliteWinterEventRepository(winter_event_path)
            if winter_event_path
            else None
        )
    except WinterEventRepositoryError as error:
        raise TextRuntimeError(f"Winter lived continuity unavailable: {error}") from error
    try:
        day_reflection_path = config.day_reflection_db
        day_reflection_repository = (
            SqliteDayReflectionRepository(day_reflection_path)
            if day_reflection_path
            else None
        )
        day_reflection_retriever = (
            ActiveDayReflectionRetriever(day_reflection_repository)
            if day_reflection_repository
            else None
        )
    except DayReflectionRepositoryError as error:
        raise TextRuntimeError(f"Winter day reflection unavailable: {error}") from error
    try:
        dialogue_state_path = config.dialogue_state_db
        open_loop_repository = (
            SqliteOpenLoopRepository(dialogue_state_path)
            if dialogue_state_path
            else None
        )
        open_loop_retriever = (
            ActiveOpenLoopRetriever(open_loop_repository)
            if open_loop_repository
            else None
        )
    except OpenLoopRepositoryError as error:
        raise TextRuntimeError(f"Dialogue state unavailable: {error}") from error
    try:
        style_profile = load_verbal_style(config.verbal_style)
    except VerbalStyleError as error:
        raise TextRuntimeError(f"Verbal style unavailable: {error}") from error
    return TextResources(
        repository=repository,
        turn_understanding_repository=turn_understanding_repository,
        outcome_repository=outcome_repository,
        reflection_repository=reflection_repository,
        identity=identity,
        memory_repository=memory_repository,
        memory_retriever=memory_retriever,
        self_model_provider=self_model_provider,
        improvement_snapshot_provider=improvement_snapshot_provider,
        belief_repository=belief_repository,
        belief_retriever=belief_retriever,
        winter_event_repository=winter_event_repository,
        day_reflection_repository=day_reflection_repository,
        day_reflection_retriever=day_reflection_retriever,
        open_loop_repository=open_loop_repository,
        open_loop_retriever=open_loop_retriever,
        style_profile=style_profile,
    )


def build_text_runtime(
    config: TextRuntimeConfig,
    *,
    resources: TextResources | None = None,
    chat_model: ChatModel | None = None,
    completion_sink: Callable[[OwnedWorkUpdate], None] | None = None,
) -> TextRuntime:
    resources = resources if resources is not None else build_text_resources(config)
    try:
        dialogue_policy = build_dialogue_policy(
            config.dialogue_policy, style_planner=VerbalStylePlanner(resources.style_profile),
        )
    except DialoguePolicyError as error:
        raise TextRuntimeError(f"Dialogue policy unavailable: {error}") from error
    repository = resources.repository
    turn_understanding_repository = resources.turn_understanding_repository
    outcome_repository = resources.outcome_repository
    identity = resources.identity
    memory_repository = resources.memory_repository
    memory_retriever = resources.memory_retriever
    self_model_provider = resources.self_model_provider
    improvement_snapshot_provider = resources.improvement_snapshot_provider
    belief_retriever = resources.belief_retriever
    winter_event_repository = resources.winter_event_repository
    day_reflection_retriever = resources.day_reflection_retriever
    open_loop_repository = resources.open_loop_repository
    open_loop_retriever = resources.open_loop_retriever
    style_profile = resources.style_profile
    base_chat_model = chat_model if chat_model is not None else build_chat_model(config)
    priority_scheduler = (
        ForegroundPriorityChatScheduler(base_chat_model)
        if config.winter_work_canary and config.interactive
        else None
    )
    chat_model = (
        priority_scheduler.foreground()
        if priority_scheduler is not None
        else base_chat_model
    )
    work_chat_model = (
        priority_scheduler.background()
        if priority_scheduler is not None
        else base_chat_model
    )
    owned_work_canary = (
        WinterOwnedProjectCanary(
            config.project_root,
            BoundedProjectInspectionExecutive(work_chat_model),
            CodexTypedProjectWorker(
                config.project_root,
                executable=config.codex_executable,
                model=config.codex_model,
            ),
        )
        if config.winter_work_canary
        else None
    )
    owned_work_coordinator = None
    owned_work_executor = owned_work_canary
    if owned_work_canary is not None and config.interactive:
        try:
            owned_work_path = config.owned_work_db
            owned_work_coordinator = AsyncOwnedWorkCoordinator(
                owned_work_canary,
                completion_sink=completion_sink,
                repository=(
                    SqliteOwnedWorkRepository(owned_work_path)
                    if owned_work_path is not None
                    else None
                ),
                foreground_activity=(
                    priority_scheduler.mark_foreground_activity
                    if priority_scheduler is not None
                    else None
                ),
            )
        except OwnedWorkRepositoryError as error:
            raise TextRuntimeError(f"Winter owned work unavailable: {error}") from error
        owned_work_executor = None
    core = CompanionCore(
        chat_model,
        repository,
        ConversationContextBuilder(
            max_messages=config.context_max_messages,
            max_characters=config.context_max_characters,
        ),
        identity,
        memory_retriever,
        memory_repository,
        verbal_style_planner=VerbalStylePlanner(style_profile),
        dialogue_policy=dialogue_policy,
        belief_retriever=belief_retriever,
        open_loop_repository=open_loop_repository,
        open_loop_retriever=open_loop_retriever,
        turn_understanding_repository=turn_understanding_repository,
        outcome_repository=outcome_repository,
        turn_source="cli",
        winter_event_repository=winter_event_repository,
        clock=SystemClock() if winter_event_repository is not None else None,
        day_reflection_retriever=day_reflection_retriever,
        self_model_provider=self_model_provider,
        improvement_snapshot_provider=improvement_snapshot_provider,
        read_only_worker=(
            CodexReadOnlyWorker(
                config.project_root,
                executable=config.codex_executable,
                model=config.codex_model,
            )
            if config.codex_worker
            else None
        ),
        owned_work_executor=owned_work_executor,
        owned_work_submitter=owned_work_coordinator,
    )
    return TextRuntime(core, resources, owned_work_coordinator)
