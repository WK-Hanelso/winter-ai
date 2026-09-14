from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from companion.capabilities import SelfModelProvider
from companion.claim_observations import ClaimObservation
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ConversationMessage
from companion.conversation_recall import ConversationRecallRetriever
from companion.day_reflection import (
    ActiveDayReflectionRetriever,
)
from companion.dialogue.character_snapshot import CharacterSnapshot
from companion.dialogue.claim_language import claim_units, has_question_form
from companion.dialogue.context_assembler import ContextAssembler
from companion.dialogue.policy import DialoguePolicy, DialoguePolicyInput, LegacyDialoguePolicy
from companion.dialogue.repeated_reply import (
    RECENT_REPLY_WINDOW,
    REGENERATION_INSTRUCTION,
    is_repeated_reply,
)
from companion.dialogue.schedule_dialogue import (
    INSTRUCTION as SCHEDULE_INSTRUCTION,
)
from companion.dialogue.schedule_dialogue import (
    ScheduleDialogue,
    ScheduleOutputError,
    ScheduleTurn,
    schedule_fact,
)
from companion.dialogue_act import classify as classify_dialogue_act
from companion.dialogue_director import DialogueDirector, DialoguePlan
from companion.grounding import ClaimEvidence, ClaimViolation, GroundingPolicy, ResponseClaimGate
from companion.identity import CompanionIdentity
from companion.improvements import ImprovementSnapshotProvider
from companion.maintenance import (
    ConversationMaintenancePolicy,
    MaintenanceRoute,
)
from companion.memory import (
    ActiveMemoryRetriever,
    classify_explicit_memory_kind,
    extract_direct_memory_statements,
    extract_explicit_forget_content,
    extract_explicit_memory_content,
)
from companion.open_loops import (
    ActiveOpenLoopRetriever,
    SqliteOpenLoopRepository,
    detect_open_loop,
)
from companion.outcome import OutcomeMemoryClaim
from companion.owned_work import OwnedWorkUpdate, ReadOnlyProjectCanaryPolicy
from companion.ports import (
    BeliefRetriever,
    ChatModel,
    Clock,
    ConversationRepository,
    IdempotentConversationRepository,
    MemoryCandidateRepository,
    OutcomeRepository,
    OwnedWorkExecutor,
    OwnedWorkSubmitter,
    ReadOnlyWorker,
    TurnUnderstandingRepository,
    WindowedConversationRepository,
    WinterEventRepository,
)
from companion.response import CompanionResponse, VerbalStylePlan
from companion.response_review import ResponseReviewer
from companion.speech_segments import split_sentences
from companion.turn_understanding import TurnInterpretationRequest
from companion.verbal_style import VerbalStylePlanner
from companion.voice_profile import ProsodyPlanner
from companion.work_orchestration import WorkOrchestrationError, WorkRequest
from companion.worker import (
    ExplicitCodexRoutingPolicy,
    ReadOnlyWorkerRequest,
    ReadOnlyWorkerResult,
    WorkerApprovalDeclined,
    WorkerQueued,
    WorkerUnavailableError,
)

_MEMORY_SAVED_NOTICE = "기억해둘게."


@dataclass(frozen=True)
class _Turn:
    """Everything decided before the model is asked, kept for after it answers.

    Streaming split one method into two, and these are what the second half
    needs: deciding them twice would let a turn be planned one way and reported
    another.
    """

    request: ChatRequest
    dialogue_act: str
    candidate_ids: tuple[str, ...]
    automatic_memory_ids: tuple[str, ...]
    opened_loop_ids: tuple[str, ...]
    recalled_loop_ids: tuple[str, ...]
    dialogue_plan: DialoguePlan
    style: VerbalStylePlan
    memory_focus: str | None
    memory_contents: tuple[str, ...]
    capability_claim_scopes: tuple[str, ...]
    improvement_statuses: tuple[str, ...]
    shadow_turn_id: str | None
    maintenance_route: MaintenanceRoute
    experience_id: str
    worker_request: ReadOnlyWorkerRequest | None
    owned_work_request: WorkRequest | None
    conversation_contents: tuple[str, ...] = ()
    schedule_turn: ScheduleTurn | None = None


class CompanionCore:
    """Coordinates a text turn without depending on a concrete model or store."""

    def __init__(
        self,
        chat_model: ChatModel,
        conversation_repository: ConversationRepository,
        context_builder: ConversationContextBuilder | None = None,
        identity: CompanionIdentity | None = None,
        memory_retriever: ActiveMemoryRetriever | None = None,
        memory_repository: MemoryCandidateRepository | None = None,
        prosody_planner: ProsodyPlanner | None = None,
        verbal_style_planner: VerbalStylePlanner | None = None,
        grounding_policy: GroundingPolicy | None = None,
        belief_retriever: BeliefRetriever | None = None,
        open_loop_repository: SqliteOpenLoopRepository | None = None,
        open_loop_retriever: ActiveOpenLoopRetriever | None = None,
        dialogue_director: DialogueDirector | None = None,
        conversation_recall_retriever: ConversationRecallRetriever | None = None,
        response_reviewer: ResponseReviewer | None = None,
        turn_understanding_repository: TurnUnderstandingRepository | None = None,
        outcome_repository: OutcomeRepository | None = None,
        turn_source: str = "unknown",
        maintenance_policy: ConversationMaintenancePolicy | None = None,
        winter_event_repository: WinterEventRepository | None = None,
        clock: Clock | None = None,
        day_reflection_retriever: ActiveDayReflectionRetriever | None = None,
        read_only_worker: ReadOnlyWorker | None = None,
        worker_routing_policy: ExplicitCodexRoutingPolicy | None = None,
        owned_work_executor: OwnedWorkExecutor | None = None,
        owned_work_submitter: OwnedWorkSubmitter | None = None,
        owned_work_routing_policy: ReadOnlyProjectCanaryPolicy | None = None,
        response_claim_gate: ResponseClaimGate | None = None,
        self_model_provider: SelfModelProvider | None = None,
        improvement_snapshot_provider: ImprovementSnapshotProvider | None = None,
        context_assembler: ContextAssembler | None = None,
        dialogue_policy: DialoguePolicy | None = None,
        claim_observation_recorder: Callable[[ClaimObservation], None] | None = None,
        schedule_dialogue: ScheduleDialogue | None = None,
        repeated_reply_guard: bool = False,
    ) -> None:
        self._repeated_reply_guard = repeated_reply_guard
        self._schedule_dialogue = schedule_dialogue
        self._chat_model = chat_model
        self._conversation_repository = conversation_repository
        self._context_builder = context_builder
        self._identity = identity
        self._memory_retriever = memory_retriever
        self._memory_repository = memory_repository
        self._prosody_planner = prosody_planner or ProsodyPlanner()
        self._verbal_style_planner = verbal_style_planner or VerbalStylePlanner()
        self._grounding_policy = grounding_policy or GroundingPolicy()
        self._belief_retriever = belief_retriever
        self._open_loop_repository = open_loop_repository
        self._open_loop_retriever = open_loop_retriever
        self._dialogue_director = dialogue_director or DialogueDirector()
        self._conversation_recall_retriever = (
            conversation_recall_retriever or ConversationRecallRetriever()
        )
        self._response_reviewer = response_reviewer or ResponseReviewer()
        self._turn_understanding_repository = turn_understanding_repository
        self._outcome_repository = outcome_repository
        self._turn_source = turn_source
        self._maintenance_policy = (
            maintenance_policy or ConversationMaintenancePolicy()
        )
        if winter_event_repository is not None and clock is None:
            raise ValueError("Winter event recording requires an explicit Clock")
        self._winter_event_repository = winter_event_repository
        self._clock = clock
        self._day_reflection_retriever = day_reflection_retriever
        self._read_only_worker = read_only_worker
        self._worker_routing_policy = (
            worker_routing_policy or ExplicitCodexRoutingPolicy()
        )
        self._owned_work_executor = owned_work_executor
        self._owned_work_submitter = owned_work_submitter
        self._owned_work_routing_policy = (
            owned_work_routing_policy or ReadOnlyProjectCanaryPolicy()
        )
        self._response_claim_gate = response_claim_gate or ResponseClaimGate()
        self._claim_observation_recorder = claim_observation_recorder
        self._self_model_provider = self_model_provider
        self._improvement_snapshot_provider = improvement_snapshot_provider
        self._context_assembler = (
            context_assembler if context_assembler is not None else ContextAssembler()
        )
        self._dialogue_policy = (
            dialogue_policy if dialogue_policy is not None
            else LegacyDialoguePolicy(self._dialogue_director, self._verbal_style_planner)
        )

    def record_worker_update(self, update: OwnedWorkUpdate) -> str:
        """Accept a trusted inbox result between turns, without fabricating a user turn."""
        repository = self._conversation_repository
        if not isinstance(repository, IdempotentConversationRepository):
            raise RuntimeError("asynchronous results require idempotent persistence")
        receipt = repository.append_once(
            f"worker:{update.id}", ConversationMessage("assistant", update.text),
        )
        if self._winter_event_repository is not None and self._clock is not None:
            if receipt.created_at is None:
                raise RuntimeError("notification persistence requires a stable timestamp")
            self._winter_event_repository.record(
                event_key=f"worker-result:{update.id}", kind="worker_result",
                subject="winter", source=self._turn_source,
                source_ref=f"conversation:{receipt.id}",
                occurred_at=datetime.fromisoformat(receipt.created_at).replace(tzinfo=UTC),
                metadata={"job_id": update.id, "status": update.status},
            )
        return update.text

    def respond_to_text(self, text: str) -> CompanionResponse:
        forgotten = self._respond_to_forget(text)
        if forgotten is not None:
            return forgotten
        turn = self._prepare(text)
        if turn.worker_request is not None:
            return self._respond_with_worker(turn)
        if turn.owned_work_request is not None:
            return self._respond_with_owned_work(turn)
        if turn.schedule_turn is not None:
            return self._respond_with_schedules(turn)
        if turn.dialogue_plan.behavior == "acknowledge_memory":
            return self._finish(turn, "")
        result = self._chat_model.generate(turn.request)
        return self._finish_generated_reply(turn, result.text)

    def respond_to_text_streaming(
        self,
        text: str,
        on_sentence: Callable[[str], None],
        on_status: Callable[[str], None] | None = None,
    ) -> CompanionResponse:
        """Collect generation, then deliver only the reviewed final response.

        Token budgets bound generation; sentence counts are style guidance, not
        a safe place to cut content. Callbacks wait for full-response review and
        finalization, including when the model has no streaming implementation.
        """
        forgotten = self._respond_to_forget(text)
        if forgotten is not None:
            on_sentence(forgotten.text)
            return forgotten
        turn = self._prepare(text)
        if turn.worker_request is not None:
            if on_status is not None:
                on_status("코덱스로 확인하고 있어.")
            response = self._respond_with_worker(turn)
            for sentence in split_sentences(response.text):
                on_sentence(sentence)
            return response
        if turn.owned_work_request is not None:
            if self._owned_work_submitter is not None:
                response = self._respond_with_owned_work(turn)
                for sentence in split_sentences(response.text):
                    on_sentence(sentence)
                return response
            on_sentence("잠깐만, 내가 현재 코드와 기록을 확인해볼게.")
            if on_status is not None:
                on_status("내가 코드와 기록을 확인하고 있어.")
            response = self._respond_with_owned_work(turn)
            for sentence in split_sentences(response.text):
                on_sentence(sentence)
            return response
        if turn.schedule_turn is not None:
            response = self._respond_with_schedules(turn)
            for sentence in split_sentences(response.text):
                on_sentence(sentence)
            return response
        if turn.dialogue_plan.behavior == "acknowledge_memory":
            response = self._finish(turn, "")
            for sentence in split_sentences(response.text):
                on_sentence(sentence)
            return response
        stream = getattr(self._chat_model, "generate_stream", None)
        if stream is None:
            draft = self._chat_model.generate(turn.request).text
        else:
            written: list[str] = []
            iterator = iter(stream(turn.request))
            try:
                for piece in iterator:
                    written.append(piece)
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
            draft = "".join(written)
        response = self._finish_generated_reply(turn, draft)
        for sentence in split_sentences(response.text):
            on_sentence(sentence)
        return response

    def _finish_generated_reply(self, turn: _Turn, draft: str) -> CompanionResponse:
        calls = 1

        def generate(request: ChatRequest) -> str:
            nonlocal calls
            calls += 1
            return self._chat_model.generate(request).text

        answer = self._review_and_repair(turn, draft, repair_generate=generate)
        return self._finish(turn, answer, repeat_generate=generate if calls < 2 else None)

    def _respond_with_worker(self, turn: _Turn) -> CompanionResponse:
        if self._read_only_worker is None:
            return self._finish(
                turn,
                "지금은 Codex Worker가 연결되어 있지 않아. 로컬 답변으로 한 척하지 않을게.",
                worker_name="codex",
                worker_error=True,
            )
        worker_request = turn.worker_request
        if worker_request is None:
            raise RuntimeError("worker response requires a routed request")
        try:
            result = self._read_only_worker.execute(worker_request)
        except WorkerQueued:
            return self._finish(
                turn, "코덱스에 맡겼어. 끝나면 알려줄게. 우리는 계속 이야기하자.",
            )
        except WorkerApprovalDeclined:
            return self._finish(
                turn, "알겠어. 외부 Worker에는 보내지 않을게.",
                worker_name="codex", worker_error=True,
            )
        except WorkerUnavailableError:
            return self._finish(
                turn,
                "지금은 Codex Worker를 사용할 수 없어. 로컬 답변으로 한 척하지 않을게.",
                worker_name="codex",
                worker_error=True,
            )
        answer = f"코덱스로 확인했어. {result.answer.strip()}"
        if result.limitations:
            answer += " 다만 " + " ".join(result.limitations)
        return self._finish(
            turn,
            answer,
            worker_result=result,
        )

    def _respond_with_owned_work(self, turn: _Turn) -> CompanionResponse:
        request = turn.owned_work_request
        if request is None:
            raise RuntimeError("owned work response requires a routed request")
        if self._owned_work_submitter is not None:
            try:
                self._owned_work_submitter.submit(request)
            except WorkerUnavailableError:
                return self._finish(
                    turn,
                    "지금은 이미 확인 중인 일이 있어. 그게 끝나면 다시 맡아볼게.",
                    worker_name="winter-owned-work-background",
                    worker_error=True,
                )
            return self._finish(
                turn,
                "응, 내가 확인해볼게. 끝나면 내가 다시 말해줄게.",
                worker_name="winter-owned-work-background",
            )
        if self._owned_work_executor is None:
            return self._finish(
                turn,
                "지금은 코드 확인 경로가 연결되어 있지 않아. 확인한 척하지 않을게.",
                worker_name="winter-owned-work",
                worker_error=True,
            )
        try:
            result = self._owned_work_executor.execute(request)
        except (WorkerUnavailableError, WorkOrchestrationError):
            return self._finish(
                turn,
                "요청을 안전하게 정리하거나 코드 근거를 확인하지 못했어. "
                "확인한 척하지 않을게.",
                worker_name="winter-owned-work",
                worker_error=True,
            )
        answer = result.answer.strip()
        if result.limitations:
            answer += " 다만 " + " ".join(result.limitations)
        return self._finish(
            turn,
            answer,
            worker_result=result,
        )

    def _respond_with_schedules(self, turn: _Turn) -> CompanionResponse:
        dialogue, state = self._schedule_dialogue, turn.schedule_turn
        assert dialogue is not None and state is not None
        final = None
        error = None
        try:
            draft = dialogue.generate(state, self._chat_model, turn.request)
            receipt = dialogue.execute(state, turn.request.prompt)
            if dialogue.needs_followup(state, draft):
                if state.calls < 2:
                    try:
                        draft = dialogue.generate(
                            state, self._chat_model, dialogue.followup_request(state, turn.request),
                            draft_only=True,
                        )
                    except ScheduleOutputError:
                        draft = ""
                else:
                    draft = ""
                    state.discard_reason = "call_budget_exhausted"
            draft = dialogue.display(state, draft)
            if receipt is None:
                def repair(request: ChatRequest) -> str:
                    revised = dialogue.generate(state, self._chat_model, request, draft_only=True)
                    return dialogue.display(state, revised)

                draft = self._review_and_repair(
                    turn, draft, repair_generate=repair, allow_repair=state.calls < 2,
                )
                draft = dialogue.display(state, draft)

            def regenerate(request: ChatRequest) -> str:
                if state.receipt is not None:
                    request = dialogue.followup_request(state, request)
                revised = dialogue.generate(state, self._chat_model, request, draft_only=True)
                return dialogue.display(state, revised)

            response = self._finish(
                turn, draft, repeat_generate=regenerate if state.calls < 2 else None,
            )
            final = response.text
            return response
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
            raise
        finally:
            dialogue.record(state, final, error)

    def _review_and_repair(
        self, turn: _Turn, draft: str, *,
        repair_generate: Callable[[ChatRequest], str] | None = None, allow_repair: bool = True,
    ) -> str:
        raw = draft
        draft = draft.strip()
        style_repair = self._response_reviewer.repair_instruction(
            turn.dialogue_plan.behavior,
            draft,
            focus=turn.dialogue_plan.focus,
            memory_focus=turn.memory_focus,
        )
        claim_evidence = self._claim_evidence(turn)
        claim_violations = self._response_claim_gate.violations(
            draft, claim_evidence
        )
        self._record_claim_observation(turn, "raw", raw, draft, claim_violations)
        claim_violations = self._response_claim_gate.blocking_violations(claim_violations)
        if style_repair is None and not claim_violations:
            return draft
        repair = "\n\n".join(
            part
            for part in (
                style_repair,
                self._response_claim_gate.repair_instruction(claim_violations, claim_evidence)
                if claim_violations
                else None,
            )
            if part is not None
        )
        request = self._context_assembler.repair_request(turn.request, draft, repair)
        repair_raw = (
            (repair_generate(request) if repair_generate is not None
             else self._chat_model.generate(request).text) if allow_repair else draft
        )
        repaired = repair_raw.strip()
        remaining_claims = self._response_claim_gate.violations(
            repaired, claim_evidence
        )
        self._record_claim_observation(turn, "repair", repair_raw, repaired, remaining_claims)
        remaining_claims = self._response_claim_gate.blocking_violations(remaining_claims)
        if remaining_claims:
            return self._response_claim_gate.fallback(remaining_claims, claim_evidence)
        still_failing = self._response_reviewer.repair_instruction(
            turn.dialogue_plan.behavior,
            repaired,
            focus=turn.dialogue_plan.focus,
            memory_focus=turn.memory_focus,
        )
        if still_failing is not None:
            fallback = self._response_reviewer.fallback_response(
                turn.dialogue_plan.behavior,
                focus=turn.dialogue_plan.focus,
                draft=repaired,
                memory_focus=turn.memory_focus,
            )
            if fallback is not None:
                return fallback
        return repaired

    def _respond_to_forget(self, text: str) -> CompanionResponse | None:
        content = extract_explicit_forget_content(text)
        if content is None:
            return None
        self._conversation_repository.append(ConversationMessage("user", text))
        repository = self._memory_repository
        memory = repository.forget_explicit(content) if repository is not None else None
        if memory is not None:
            answer = f'알겠어, 잊었어: “{memory.content[:60]}”'
        else:
            active = repository.list_active() if repository is not None else ()
            latest = sorted(active, key=lambda item: item.created_at, reverse=True)[:3]
            answer = "어떤 걸 잊을지 못 찾았어. 지금 기억하는 건 이런 거야: " + " / ".join(
                f'“{item.content[:60]}”' for item in latest
            )
        route = self._maintenance_policy.route()
        shadow_turn_id = None
        if route.enqueue_turn_analysis and self._turn_understanding_repository is not None:
            event = self._turn_understanding_repository.enqueue(
                TurnInterpretationRequest(
                    user_text=text, context=(ConversationMessage("user", text),),
                ),
                source=self._turn_source,
            )
            shadow_turn_id = event.id
            if self._outcome_repository is not None:
                self._outcome_repository.observe_turn(event.id, source=self._turn_source,
                                                      user_text=text, memory_claims=())
        self._conversation_repository.append(ConversationMessage("assistant", answer))
        if self._outcome_repository is not None and shadow_turn_id is not None:
            self._outcome_repository.record_response(shadow_turn_id, answer)
        if self._winter_event_repository is not None and self._clock is not None:
            self._winter_event_repository.record(
                event_key=f"conversation-turn:{uuid4()}", kind="conversation_turn",
                subject="conversation:acknowledge_forget", source=self._turn_source,
                source_ref=shadow_turn_id, occurred_at=self._clock.now(),
                metadata={"dialogue_act": "memory", "dialogue_behavior": "acknowledge_forget",
                          "has_response": True, "maintenance_mode": route.mode.value,
                          "worker_name": None, "worker_error": False},
            )
        return CompanionResponse(
            text=answer, dialogue_act="memory", prosody=self._prosody_planner.plan("memory"),
            verbal_style=self._verbal_style_planner.plan("memory"),
            dialogue_behavior="acknowledge_forget", shadow_turn_id=shadow_turn_id,
            maintenance_mode=route.mode,
        )

    def _prepare(self, text: str) -> _Turn:
        maintenance_route = self._maintenance_policy.route()
        user_message = self._conversation_repository.append(
            ConversationMessage(role="user", content=text)
        )
        schedule_turn = (self._schedule_dialogue.begin(user_message.id)
                         if self._schedule_dialogue is not None else None)
        evidence_ref = f"conversation:{user_message.id}"
        candidate_ids: tuple[str, ...] = ()
        candidate_content = extract_explicit_memory_content(text)
        if candidate_content is not None and self._memory_repository is not None:
            candidate = self._memory_repository.remember_explicit(
                kind=classify_explicit_memory_kind(candidate_content),
                content=candidate_content,
                evidence_ref=evidence_ref,
            )
            candidate_ids = (candidate.id,)
        selected_open_loops = (
            self._open_loop_retriever.retrieve(text)
            if self._open_loop_retriever is not None
            else ()
        )
        opened_loop_ids: tuple[str, ...] = ()
        opened_loop = None
        open_loop_content = detect_open_loop(text)
        if open_loop_content is not None and self._open_loop_repository is not None:
            opened = self._open_loop_repository.remember(open_loop_content)
            opened_loop_ids = (opened.id,)
            opened_loop = opened
        # What kind of turn this is decides how long the answer may be. Ordinary
        # conversation stays short; being asked to explain something earns room.
        dialogue_act = classify_dialogue_act(text, has_memory_candidate=bool(candidate_ids))
        planning_loops = selected_open_loops + (
            (opened_loop,) if opened_loop is not None else ()
        )
        selected_memories = (
            self._memory_retriever.retrieve(text)
            if self._memory_retriever is not None
            else ()
        )
        current_state_history = (
            self._memory_retriever.retrieve_current_state_history(text)
            if self._memory_retriever is not None
            else ()
        )
        if current_state_history:
            history_ids = {memory.id for memory in current_state_history}
            selected_memories = tuple(
                memory for memory in selected_memories if memory.id not in history_ids
            )
        automatic_memory_ids: tuple[str, ...] = ()
        if not candidate_ids and self._memory_repository is not None:
            automatic_memory_ids_list: list[str] = []
            for statement in extract_direct_memory_statements(text):
                memory = self._memory_repository.remember_direct_statement(
                    statement,
                    evidence_ref=evidence_ref,
                    temporal_scope="durable",
                )
                if memory is not None:
                    automatic_memory_ids_list.append(memory.id)
            automatic_memory_ids = tuple(automatic_memory_ids_list)
        if isinstance(self._conversation_repository, WindowedConversationRepository):
            limit = self._context_builder.message_limit if self._context_builder is not None else 1
            all_messages = self._conversation_repository.recent_messages(limit)
        else:
            all_messages = tuple(self._conversation_repository.list_messages())
            recalled_conversation = self._conversation_recall_retriever.retrieve(all_messages, text)
        messages: tuple[ConversationMessage, ...] = (
            ConversationMessage(role="user", content=text),
        )
        if self._context_builder is not None:
            messages = self._context_builder.build(all_messages)
        if isinstance(self._conversation_repository, WindowedConversationRepository):
            recalled_conversation = self._conversation_recall_retriever.retrieve_from_repository(
                self._conversation_repository, text, exclude_latest=len(messages),
            )
        shadow_turn_id = None
        if (
            maintenance_route.enqueue_turn_analysis
            and self._turn_understanding_repository is not None
        ):
            shadow_event = self._turn_understanding_repository.enqueue(
                TurnInterpretationRequest(user_text=text, context=messages),
                source=self._turn_source,
            )
            shadow_turn_id = shadow_event.id
            if self._outcome_repository is not None:
                self._outcome_repository.observe_turn(
                    shadow_event.id,
                    source=self._turn_source,
                    user_text=text,
                    memory_claims=tuple(
                        OutcomeMemoryClaim(memory.id, memory.kind, memory.content)
                        for memory in selected_memories
                    ),
                )
        self_model_snapshot = (
            self._self_model_provider.snapshot()
            if self._self_model_provider is not None
            else None
        )
        improvement_snapshot = (
            self._improvement_snapshot_provider.snapshot()
            if self._improvement_snapshot_provider is not None
            else None
        )
        prior_days = (
            self._day_reflection_retriever.retrieve(
                before_date=self._clock.now().date().isoformat()
            )
            if self._day_reflection_retriever is not None and self._clock is not None
            else ()
        )
        selected_beliefs = (
            self._belief_retriever.retrieve(text)
            if self._belief_retriever is not None
            else ()
        )
        snapshot = CharacterSnapshot(
            identity=self._identity,
            self_model=self_model_snapshot,
            improvements=improvement_snapshot,
            prior_days=prior_days,
            beliefs=selected_beliefs,
            open_loops=selected_open_loops,
        )
        policy_turn = self._dialogue_policy.plan_turn(DialoguePolicyInput(
            text=text,
            dialogue_act=dialogue_act,
            recent_messages=messages,
            memories=selected_memories,
            current_state_history=current_state_history,
            open_loops=planning_loops,
        ))
        dialogue_plan = policy_turn.dialogue_plan
        turn_style = policy_turn.turn_style
        request = self._context_assembler.assemble(
            snapshot=snapshot,
            text=text,
            recent_messages=messages,
            memories=selected_memories,
            current_state_history=current_state_history,
            recalled_conversation=recalled_conversation,
            style_instruction=turn_style.instruction,
            grounding_instruction=self._grounding_policy.instruction(),
            dialogue_instruction=dialogue_plan.instruction,
            max_tokens=224 if dialogue_act == "explain" else 120,
            schedule_instruction=SCHEDULE_INSTRUCTION if schedule_turn is not None else None,
            schedule_context=(self._schedule_dialogue.variable_messages(schedule_turn)
                              if self._schedule_dialogue is not None
                              and schedule_turn is not None else ()),
        )
        explicit_worker_request = self._worker_routing_policy.route(text)
        owned_work_request = (
            self._owned_work_routing_policy.route(text)
            if explicit_worker_request is None
            and (
                self._owned_work_executor is not None
                or self._owned_work_submitter is not None
            )
            else None
        )
        return _Turn(
            request=request,
            dialogue_act=dialogue_act,
            candidate_ids=candidate_ids,
            automatic_memory_ids=automatic_memory_ids,
            opened_loop_ids=opened_loop_ids,
            recalled_loop_ids=tuple(loop.id for loop in selected_open_loops),
            dialogue_plan=dialogue_plan,
            style=turn_style.plan,
            memory_focus=(selected_memories[0].content if selected_memories else None),
            memory_contents=tuple(
                memory.content
                for memory in selected_memories + current_state_history
            ),
            conversation_contents=tuple(
                unit
                for message in recalled_conversation + messages
                if message.role == "user" and message.content != text
                for unit in claim_units(message.content)
                if not has_question_form(unit)
            ),
            capability_claim_scopes=snapshot.capability_claim_scopes,
            improvement_statuses=snapshot.improvement_statuses,
            shadow_turn_id=shadow_turn_id,
            maintenance_route=maintenance_route,
            experience_id=str(uuid4()),
            worker_request=explicit_worker_request,
            owned_work_request=owned_work_request,
            schedule_turn=schedule_turn,
        )

    def _finish(
        self,
        turn: _Turn,
        answer: str,
        *,
        worker_result: ReadOnlyWorkerResult | None = None,
        worker_name: str | None = None,
        worker_error: bool = False,
        repeat_generate: Callable[[ChatRequest], str] | None = None,
    ) -> CompanionResponse:
        response_text, checked_text, final_violations = self._final_reply_text(
            turn, answer, worker_result=worker_result,
        )
        recent_replies: tuple[str, ...] = ()
        if (self._repeated_reply_guard and len(response_text) >= 20
                and turn.worker_request is None and turn.owned_work_request is None):
            recent_replies = self._recent_assistant_replies()
        if is_repeated_reply(response_text, recent_replies):
            if repeat_generate is not None:
                request = replace(turn.request, messages=turn.request.messages + (
                    ConversationMessage("system", REGENERATION_INSTRUCTION),
                ))
                try:
                    revised = repeat_generate(request)
                except ScheduleOutputError:
                    # The first valid reply/receipt survives a malformed second draft.
                    pass
                else:
                    response_text, checked_text, final_violations = self._final_reply_text(
                        turn, revised, worker_result=worker_result,
                    )
            if is_repeated_reply(response_text, recent_replies):
                self._record_claim_observation(
                    turn, "repeated_reply", response_text, response_text,
                    (ClaimViolation("repeated_reply", response_text),),
                )
        self._record_claim_observation(
            turn, "final", response_text, checked_text, final_violations,
        )
        return self._persist_response(
            turn, response_text, worker_result=worker_result, worker_name=worker_name,
            worker_error=worker_error,
        )

    def _recent_assistant_replies(self) -> tuple[str, ...]:
        repository = self._conversation_repository
        if isinstance(repository, WindowedConversationRepository):
            limit = 8
            while True:
                messages = repository.recent_messages(limit)
                replies = tuple(m.content for m in messages if m.role == "assistant")
                if len(replies) >= RECENT_REPLY_WINDOW or len(messages) < limit:
                    return replies[-RECENT_REPLY_WINDOW:]
                limit *= 2
        replies = tuple(m.content for m in repository.list_messages() if m.role == "assistant")
        return replies[-RECENT_REPLY_WINDOW:]

    def _final_reply_text(
        self, turn: _Turn, answer: str, *, worker_result: ReadOnlyWorkerResult | None,
    ) -> tuple[str, str, tuple[ClaimViolation, ...]]:
        response_text = answer.strip()
        if turn.candidate_ids:
            response_text = (
                f"{response_text}\n{_MEMORY_SAVED_NOTICE}"
                if response_text
                else _MEMORY_SAVED_NOTICE
            )
        final_violations = self._response_claim_gate.violations(
            response_text,
            self._claim_evidence(turn, worker_result=worker_result),
        )
        checked_text = response_text
        blocking = self._response_claim_gate.blocking_violations(final_violations)
        if blocking:
            response_text = self._response_claim_gate.fallback(
                blocking, self._claim_evidence(turn, worker_result=worker_result),
            )
        return response_text, checked_text, final_violations

    def _persist_response(
        self, turn: _Turn, response_text: str, *,
        worker_result: ReadOnlyWorkerResult | None,
        worker_name: str | None, worker_error: bool,
    ) -> CompanionResponse:
        self._conversation_repository.append(
            ConversationMessage(role="assistant", content=response_text)
        )
        if self._outcome_repository is not None and turn.shadow_turn_id is not None:
            self._outcome_repository.record_response(
                turn.shadow_turn_id, response_text
            )
        if self._winter_event_repository is not None and self._clock is not None:
            self._winter_event_repository.record(
                event_key=f"conversation-turn:{turn.experience_id}",
                kind="conversation_turn",
                subject=f"conversation:{turn.dialogue_plan.behavior}",
                source=self._turn_source,
                source_ref=turn.shadow_turn_id,
                occurred_at=self._clock.now(),
                metadata={
                    "dialogue_act": turn.dialogue_act,
                    "dialogue_behavior": turn.dialogue_plan.behavior,
                    "has_response": bool(response_text),
                    "maintenance_mode": turn.maintenance_route.mode.value,
                    "worker_name": (
                        worker_result.worker if worker_result is not None else worker_name
                    ),
                    "worker_error": worker_error,
                },
            )
        if (turn.schedule_turn is not None and self._schedule_dialogue is not None
                and (turn.worker_request is not None or turn.owned_work_request is not None)):
            self._schedule_dialogue.record(turn.schedule_turn, response_text)
        return CompanionResponse(
            text=response_text,
            dialogue_act=turn.dialogue_act,
            prosody=self._prosody_planner.plan(turn.dialogue_act),
            memory_candidate_ids=turn.candidate_ids,
            automatic_memory_ids=turn.automatic_memory_ids,
            verbal_style=turn.style,
            dialogue_behavior=turn.dialogue_plan.behavior,
            opened_loop_ids=turn.opened_loop_ids,
            recalled_loop_ids=turn.recalled_loop_ids,
            shadow_turn_id=turn.shadow_turn_id,
            maintenance_mode=turn.maintenance_route.mode,
            worker_name=(
                worker_result.worker if worker_result is not None else worker_name
            ),
            worker_model=(worker_result.model if worker_result is not None else None),
            worker_evidence=(worker_result.evidence if worker_result is not None else ()),
            worker_limitations=(
                worker_result.limitations if worker_result is not None else ()
            ),
            worker_error=worker_error,
        )

    def _record_claim_observation(
        self, turn: _Turn, stage: str, text: str, checked_text: str,
        violations: tuple[ClaimViolation, ...],
    ) -> None:
        if self._claim_observation_recorder is not None:
            self._claim_observation_recorder(ClaimObservation(
                turn.experience_id, turn.shadow_turn_id, self._turn_source,
                stage, text, checked_text, violations,
            ))

    @staticmethod
    def _claim_evidence(
        turn: _Turn,
        *,
        worker_result: ReadOnlyWorkerResult | None = None,
    ) -> ClaimEvidence:
        return ClaimEvidence(
            user_text=turn.request.prompt,
            memory_contents=turn.memory_contents + (
                tuple(schedule_fact(item) for item in (
                    turn.schedule_turn.receipt.schedules if turn.schedule_turn.receipt is not None
                    and turn.schedule_turn.receipt.schedules
                    else turn.schedule_turn.context.schedules
                )) if turn.schedule_turn is not None else ()
            ),
            memory_write_authorized=bool(
                turn.candidate_ids or turn.automatic_memory_ids
            ),
            worker_result_authorized=worker_result is not None,
            capability_claim_scopes=turn.capability_claim_scopes,
            improvement_statuses=turn.improvement_statuses,
            conversation_contents=turn.conversation_contents,
        )
