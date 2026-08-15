from collections.abc import Callable
from dataclasses import dataclass

from companion.beliefs import belief_context
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ConversationMessage
from companion.conversation_recall import ConversationRecallRetriever, recall_context
from companion.dialogue_act import classify as classify_dialogue_act
from companion.dialogue_director import DialogueDirector, DialoguePlan
from companion.grounding import GroundingPolicy
from companion.identity import CompanionIdentity
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
from companion.ports import (
    BeliefRetriever,
    ChatModel,
    ConversationRepository,
    MemoryCandidateRepository,
    OutcomeRepository,
    TurnUnderstandingRepository,
)
from companion.response import CompanionResponse, VerbalStylePlan
from companion.response_review import ResponseReviewer
from companion.speech_segments import split_sentences
from companion.turn_understanding import TurnInterpretationRequest
from companion.verbal_style import VerbalStylePlanner
from companion.voice_profile import ProsodyPlanner

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
    shadow_turn_id: str | None


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

    def respond_to_text(self, text: str) -> CompanionResponse:
        turn = self._prepare(text)
        if turn.dialogue_plan.behavior == "acknowledge_memory":
            return self._finish(turn, "")
        result = self._chat_model.generate(turn.request)
        answer = self._review_and_repair(turn, result.text)
        return self._finish(turn, answer)

    def respond_to_text_streaming(
        self, text: str, on_sentence: Callable[[str], None]
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

    def _review_and_repair(self, turn: _Turn, draft: str) -> str:
        bounded = " ".join(split_sentences(draft)[: _sentence_limit(turn)])
        repair = self._response_reviewer.repair_instruction(
            turn.dialogue_plan.behavior,
            bounded,
            focus=turn.dialogue_plan.focus,
            memory_focus=turn.memory_focus,
        )
        if repair is None:
            return bounded
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
        self._conversation_repository.append(ConversationMessage(role="user", content=text))
        candidate_ids: tuple[str, ...] = ()
        candidate_content = extract_explicit_memory_content(text)
        if candidate_content is not None and self._memory_repository is not None:
            candidate = self._memory_repository.remember_explicit(
                kind=classify_explicit_memory_kind(candidate_content),
                content=candidate_content,
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
                memory = self._memory_repository.remember_direct_statement(statement)
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
        if self._turn_understanding_repository is not None:
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
            shadow_turn_id=shadow_turn_id,
        )

    def _finish(self, turn: _Turn, answer: str) -> CompanionResponse:
        bounded = " ".join(split_sentences(answer)[: _sentence_limit(turn)])
        response_text = bounded
        if turn.candidate_ids:
            response_text = (
                f"{response_text}\n{_MEMORY_SAVED_NOTICE}"
                if response_text
                else _MEMORY_SAVED_NOTICE
            )
        self._conversation_repository.append(
            ConversationMessage(role="assistant", content=response_text)
        )
        if self._outcome_repository is not None and turn.shadow_turn_id is not None:
            self._outcome_repository.record_response(
                turn.shadow_turn_id, response_text
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
        )


def _sentence_limit(turn: _Turn) -> int:
    return turn.dialogue_plan.sentence_limit or turn.style.max_sentences
