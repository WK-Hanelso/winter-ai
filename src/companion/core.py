from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from companion.beliefs import belief_context
from companion.capabilities import SelfModelProvider
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ConversationMessage
from companion.conversation_recall import ConversationRecallRetriever, recall_context
from companion.day_reflection import (
    ActiveDayReflectionRetriever,
    day_reflection_context,
)
from companion.dialogue_act import classify as classify_dialogue_act
from companion.dialogue_director import DialogueDirector, DialoguePlan
from companion.grounding import ClaimEvidence, GroundingPolicy, ResponseClaimGate
from companion.identity import CompanionIdentity
from companion.improvements import ImprovementSnapshotProvider
from companion.maintenance import (
    ConversationMaintenancePolicy,
    MaintenanceRoute,
)
from companion.memory import (
    ActiveMemoryRetriever,
    classify_explicit_memory_kind,
    current_state_history_context,
    extract_direct_memory_statements,
    extract_explicit_memory_content,
    memory_context,
)
from companion.open_loops import (
    ActiveOpenLoopRetriever,
    SqliteOpenLoopRepository,
    detect_open_loop,
    open_loop_context,
)
from companion.outcome import OutcomeMemoryClaim
from companion.owned_work import ReadOnlyProjectCanaryPolicy
from companion.ports import (
    BeliefRetriever,
    ChatModel,
    Clock,
    ConversationRepository,
    MemoryCandidateRepository,
    OutcomeRepository,
    OwnedWorkExecutor,
    OwnedWorkSubmitter,
    ReadOnlyWorker,
    TurnUnderstandingRepository,
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
    ) -> None:
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
        self._self_model_provider = self_model_provider
        self._improvement_snapshot_provider = improvement_snapshot_provider

    def respond_to_text(self, text: str) -> CompanionResponse:
        turn = self._prepare(text)
        if turn.worker_request is not None:
            return self._respond_with_worker(turn)
        if turn.owned_work_request is not None:
            return self._respond_with_owned_work(turn)
        if turn.dialogue_plan.behavior == "acknowledge_memory":
            return self._finish(turn, "")
        result = self._chat_model.generate(turn.request)
        answer = self._review_and_repair(turn, result.text)
        return self._finish(turn, answer)

    def respond_to_text_streaming(
        self,
        text: str,
        on_sentence: Callable[[str], None],
        on_status: Callable[[str], None] | None = None,
    ) -> CompanionResponse:
        """Answer, handing each finished sentence over as it is written.

        The voice path cannot start on a sentence that does not exist yet, and
        waiting for the whole answer costs two and a half to four seconds before
        anything is said. ``on_sentence`` is called with each completed
        sentence; the response returned at the end is the same one the
        non-streaming path returns.

        A model without a streaming path is not an error — it answers all at
        once and every sentence is handed over then.
        """
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
        if turn.dialogue_plan.behavior == "acknowledge_memory":
            on_sentence(_MEMORY_SAVED_NOTICE)
            return self._finish(turn, "")
        stream = getattr(self._chat_model, "generate_stream", None)
        if stream is None:
            answer = self._chat_model.generate(turn.request).text
            for sentence in split_sentences(answer):
                on_sentence(sentence)
            return self._finish(turn, answer)
        written: list[str] = []
        iterator = iter(stream(turn.request))
        try:
            for piece in iterator:
                written.append(piece)
                sentences = split_sentences("".join(written))
                # One extra sentence proves the planned answer is complete;
                # stop the server before it writes a paragraph we will discard.
                if len(sentences) > _sentence_limit(turn):
                    break
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        answer = self._review_and_repair(turn, "".join(written))
        for sentence in split_sentences(answer)[: _sentence_limit(turn)]:
            on_sentence(sentence)
        return self._finish(turn, answer)

    def _respond_with_worker(self, turn: _Turn) -> CompanionResponse:
        if self._read_only_worker is None:
            return self._finish(
                turn,
                "지금은 Codex Worker가 연결되어 있지 않아. 로컬 답변으로 한 척하지 않을게.",
                preserve_full=True,
                worker_name="codex",
                worker_error=True,
            )
        worker_request = turn.worker_request
        if worker_request is None:
            raise RuntimeError("worker response requires a routed request")
        try:
            result = self._read_only_worker.execute(worker_request)
        except WorkerUnavailableError:
            return self._finish(
                turn,
                "지금은 Codex Worker를 사용할 수 없어. 로컬 답변으로 한 척하지 않을게.",
                preserve_full=True,
                worker_name="codex",
                worker_error=True,
            )
        answer = f"코덱스로 확인했어. {result.answer.strip()}"
        if result.limitations:
            answer += " 다만 " + " ".join(result.limitations)
        return self._finish(
            turn,
            answer,
            preserve_full=True,
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
                    preserve_full=True,
                    worker_name="winter-owned-work-background",
                    worker_error=True,
                )
            return self._finish(
                turn,
                "응, 내가 확인해볼게. 끝나면 내가 다시 말해줄게.",
                preserve_full=True,
                worker_name="winter-owned-work-background",
            )
        if self._owned_work_executor is None:
            return self._finish(
                turn,
                "지금은 코드 확인 경로가 연결되어 있지 않아. 확인한 척하지 않을게.",
                preserve_full=True,
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
                preserve_full=True,
                worker_name="winter-owned-work",
                worker_error=True,
            )
        answer = result.answer.strip()
        if result.limitations:
            answer += " 다만 " + " ".join(result.limitations)
        return self._finish(
            turn,
            answer,
            preserve_full=True,
            worker_result=result,
        )

    def _review_and_repair(self, turn: _Turn, draft: str) -> str:
        bounded = " ".join(split_sentences(draft)[: _sentence_limit(turn)])
        style_repair = self._response_reviewer.repair_instruction(
            turn.dialogue_plan.behavior,
            bounded,
            focus=turn.dialogue_plan.focus,
            memory_focus=turn.memory_focus,
        )
        claim_evidence = self._claim_evidence(turn)
        claim_violations = self._response_claim_gate.violations(
            bounded, claim_evidence
        )
        if style_repair is None and not claim_violations:
            return bounded
        repair = "\n\n".join(
            part
            for part in (
                style_repair,
                self._response_claim_gate.repair_instruction(claim_violations)
                if claim_violations
                else None,
            )
            if part is not None
        )
        request = ChatRequest(
            prompt=turn.request.prompt,
            messages=turn.request.messages
            + (
                ConversationMessage("assistant", bounded),
                ConversationMessage("system", repair),
            ),
            max_tokens=turn.request.max_tokens,
        )
        repaired = self._chat_model.generate(request).text
        repaired = " ".join(split_sentences(repaired)[: _sentence_limit(turn)])
        remaining_claims = self._response_claim_gate.violations(
            repaired, claim_evidence
        )
        if remaining_claims:
            return self._response_claim_gate.fallback(remaining_claims)
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

    def _prepare(self, text: str) -> _Turn:
        maintenance_route = self._maintenance_policy.route()
        user_message = self._conversation_repository.append(
            ConversationMessage(role="user", content=text)
        )
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
        dialogue_plan = self._dialogue_director.plan(
            text,
            dialogue_act,
            planning_loops,
            has_memory=bool(selected_memories or current_state_history),
        )
        all_messages = tuple(self._conversation_repository.list_messages())
        recalled_conversation = self._conversation_recall_retriever.retrieve(
            all_messages, text
        )
        messages: tuple[ConversationMessage, ...] = (
            ConversationMessage(role="user", content=text),
        )
        if self._context_builder is not None:
            messages = self._context_builder.build(all_messages)
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
        # Built in one place and in a deliberate order. Repeated prepends put the
        # last-added block first, which pushed identity behind style and memory.
        # Identity leads because it frames everything else; style sits closest to
        # the turn being generated, where a register instruction is least likely
        # to be dropped.
        system_messages: list[ConversationMessage] = []
        if self._identity is not None:
            system_messages.append(
                ConversationMessage("system", self._identity.system_message())
            )
        self_model_snapshot = (
            self._self_model_provider.snapshot()
            if self._self_model_provider is not None
            else None
        )
        if self_model_snapshot is not None:
            system_messages.append(
                ConversationMessage("system", self_model_snapshot.system_message())
            )
        improvement_snapshot = (
            self._improvement_snapshot_provider.snapshot()
            if self._improvement_snapshot_provider is not None
            else None
        )
        if improvement_snapshot is not None:
            system_messages.append(
                ConversationMessage("system", improvement_snapshot.system_message())
            )
        if self._day_reflection_retriever is not None and self._clock is not None:
            prior_days = self._day_reflection_retriever.retrieve(
                before_date=self._clock.now().date().isoformat()
            )
            if prior_days:
                system_messages.append(
                    ConversationMessage(
                        "system",
                        day_reflection_context(prior_days),
                    )
                )
        if selected_memories:
            system_messages.append(
                ConversationMessage("system", memory_context(selected_memories))
            )
        if current_state_history:
            system_messages.append(
                ConversationMessage(
                    "system",
                    current_state_history_context(current_state_history),
                )
            )
        if self._belief_retriever is not None:
            selected_beliefs = self._belief_retriever.retrieve(text)
            if selected_beliefs:
                system_messages.append(
                    ConversationMessage("system", belief_context(selected_beliefs))
                )
        if recalled_conversation:
            system_messages.append(
                ConversationMessage("system", recall_context(recalled_conversation))
            )
        if selected_open_loops:
            system_messages.append(
                ConversationMessage("system", open_loop_context(selected_open_loops))
            )
        turn_style = self._verbal_style_planner.plan_turn(dialogue_act)
        if turn_style.instruction is not None:
            system_messages.append(ConversationMessage("system", turn_style.instruction))
        # Grounding and the selected response move share the final block. Small
        # models visibly weakened whichever of the two appeared earlier.
        grounding_instruction = self._grounding_policy.instruction()
        final_instruction = "\n\n".join(
            part
            for part in (grounding_instruction, dialogue_plan.instruction)
            if part
        )
        system_messages.append(
            ConversationMessage("system", final_instruction)
        )
        messages = tuple(system_messages) + messages
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
            request=ChatRequest(
                prompt=text,
                messages=messages,
                max_tokens=224 if dialogue_act == "explain" else 120,
            ),
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
            capability_claim_scopes=(
                self_model_snapshot.authorized_claim_scopes
                if self_model_snapshot is not None
                else ()
            ),
            improvement_statuses=(
                tuple(item.status for item in improvement_snapshot.items)
                if improvement_snapshot is not None
                else ()
            ),
            shadow_turn_id=shadow_turn_id,
            maintenance_route=maintenance_route,
            experience_id=str(uuid4()),
            worker_request=explicit_worker_request,
            owned_work_request=owned_work_request,
        )

    def _finish(
        self,
        turn: _Turn,
        answer: str,
        *,
        preserve_full: bool = False,
        worker_result: ReadOnlyWorkerResult | None = None,
        worker_name: str | None = None,
        worker_error: bool = False,
    ) -> CompanionResponse:
        bounded = (
            " ".join(split_sentences(answer))
            if preserve_full
            else " ".join(split_sentences(answer)[: _sentence_limit(turn)])
        )
        response_text = bounded
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
        if final_violations:
            response_text = self._response_claim_gate.fallback(final_violations)
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

    @staticmethod
    def _claim_evidence(
        turn: _Turn,
        *,
        worker_result: ReadOnlyWorkerResult | None = None,
    ) -> ClaimEvidence:
        return ClaimEvidence(
            user_text=turn.request.prompt,
            memory_contents=turn.memory_contents,
            memory_write_authorized=bool(
                turn.candidate_ids or turn.automatic_memory_ids
            ),
            worker_result_authorized=worker_result is not None,
            capability_claim_scopes=turn.capability_claim_scopes,
            improvement_statuses=turn.improvement_statuses,
        )


def _sentence_limit(turn: _Turn) -> int:
    return turn.dialogue_plan.sentence_limit or turn.style.max_sentences
